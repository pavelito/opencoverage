import time
from datetime import datetime, timezone
from typing import (
    Any,
    AsyncIterator,
    Dict,
    List,
    Optional,
)

import jwt
import pydantic
from cryptography.hazmat.backends import default_backend

from opencoverage.settings import Settings
from opencoverage.types import Pull

from .base import SCMClient
from .exceptions import APIException, AuthorizationException, NotFoundException


class GithubUser(pydantic.BaseModel):
    login: str
    id: int
    avatar_url: str
    gravatar_id: str
    url: str
    type: str
    site_admin: bool


class GithubRepo(pydantic.BaseModel):
    id: int
    name: str
    full_name: str
    private: bool
    owner: GithubUser
    description: str
    fork: bool
    url: str
    created_at: str
    updated_at: str


class GithubRef(pydantic.BaseModel):
    label: str
    ref: str
    sha: str
    user: GithubUser
    repo: GithubRepo


class GithubPull(pydantic.BaseModel):
    url: str
    id: str
    diff_url: str
    patch_url: str
    number: int
    state: str
    title: str
    user: GithubUser
    created_at: str
    updated_at: str
    closed_at: Optional[str]
    merged_at: Optional[str]
    merge_commit_sha: str
    assignee: Optional[GithubUser]
    assignees: List[GithubUser]
    requested_reviewers: List[GithubUser]
    draft: bool
    commits_url: str
    head: GithubRef
    base: GithubRef


class GithubCheckOutput(pydantic.BaseModel):
    title: Optional[str]
    summary: Optional[str]
    text: Optional[str]
    annotations_count: int
    annotations_url: Optional[str]


class GithubApp(pydantic.BaseModel):
    created_at: datetime
    description: str
    external_url: str
    id: int
    name: str


class GithubCheck(pydantic.BaseModel):
    id: int
    head_sha: str
    node_id: str
    external_id: str
    url: str
    html_url: str
    details_url: str
    status: str
    conclusion: Optional[str]
    started_at: datetime
    completed_at: Optional[datetime]
    name: str

    app: GithubApp


class GithubChecks(pydantic.BaseModel):
    check_runs: List[GithubCheck]
    total_count: int


class GithubAccessData(pydantic.BaseModel):
    token: str
    expires_at: datetime
    permissions: Dict[str, str]
    repository_selection: str


class GithubComment(pydantic.BaseModel):
    id: int
    body: str
    user: GithubUser


GITHUB_API_URL = "https://api.github.com"


class Github(SCMClient):
    _access_data: Optional[GithubAccessData]
    _jwt_token: Optional[str]

    def __init__(self, settings: Settings):
        super().__init__(settings)
        if settings.github_app_pem_file is None:
            raise TypeError("Must configure github_app_pem_file")
        with open(settings.github_app_pem_file, "rb") as fi:
            self._private_key = default_backend().load_pem_private_key(fi.read(), None)
        self._jwt_token = None
        self._jwt_expiration = 0
        self._access_data = None

    def _get_jwt_token(self) -> str:
        time_since_epoch_in_seconds = int(time.time())
        if self._jwt_token is None or self._jwt_expiration < (
            time_since_epoch_in_seconds - 10
        ):
            self._jwt_expiration = time_since_epoch_in_seconds + (10 * 60)
            self._jwt_token = jwt.encode(
                {
                    # issued at time
                    "iat": time_since_epoch_in_seconds,
                    # JWT expiration time (10 minute maximum)
                    "exp": self._jwt_expiration,
                    # GitHub App's identifier
                    "iss": self.settings.github_app_id,
                },
                self._private_key,
                algorithm="RS256",
            )
        return self._jwt_token

    async def _get_access_token(self) -> str:
        now = datetime.utcnow().replace(tzinfo=timezone.utc)
        if self._access_data is None or self._access_data.expires_at > now:
            url = f"{GITHUB_API_URL}/app/installations/{self.settings.github_installation_id}/access_tokens"
            jwt_token = self._get_jwt_token()
            async with self.session.post(
                url,
                headers={
                    "Accepts": "application/vnd.github.v3+json",
                    "Authorization": f"Bearer {jwt_token}",
                },
            ) as resp:
                if resp.status != 201:
                    text = await resp.text()
                    raise APIException(
                        f"Could not authenticate with pem: {resp.status}: {text}"
                    )
                data = await resp.json()
                self._access_data = GithubAccessData.parse_obj(data)
        return self._access_data.token

    async def _prepare_request(
        self,
        *,
        url: str,
        method: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, str]] = None,
        json: Optional[Dict[str, Any]] = None,
    ):
        func = getattr(self.session, method.lower())
        if headers is None:
            headers = {}
        token = await self._get_access_token()
        headers["Authorization"] = f"token {token}"
        return func(url, headers=headers, params=params or {}, json=json)

    async def get_pulls(self, org: str, repo: str, commit_hash: str) -> List[Pull]:
        url = f"{GITHUB_API_URL}/repos/{org}/{repo}/commits/{commit_hash}/pulls"
        async with await self._prepare_request(
            url=url,
            method="get",
            headers={"Accept": "application/vnd.github.groot-preview"},
        ) as resp:
            if resp.status == 422:
                # no pulls found
                return []
            if resp.status == 401:
                text = await resp.json()
                raise AuthorizationException(f"API Unauthorized: {text}")

            data = await resp.json()
            pulls = []
            for item in data:
                gpull = GithubPull.parse_obj(item)
                pulls.append(
                    Pull(base=gpull.base.ref, head=gpull.head.ref, id=gpull.number)
                )
        return pulls

    async def get_pull_diff(self, org: str, repo: str, id: int) -> str:
        url = f"{GITHUB_API_URL}/repos/{org}/{repo}/pulls/{id}"
        async with await self._prepare_request(
            url=url,
            method="get",
            headers={"Accept": "application/vnd.github.v3.diff"},
        ) as resp:
            if resp.status == 401:
                text = await resp.json()
                raise AuthorizationException(f"API Unauthorized: {text}")
            data = await resp.text()
        return data

    async def create_check(self, org: str, repo: str, commit: str) -> str:
        url = f"{GITHUB_API_URL}/repos/{org}/{repo}/check-runs"
        async with await self._prepare_request(
            url=url,
            method="post",
            headers={"Accept": "application/vnd.github.v3+json"},
            json={"head_sha": commit, "name": "coverage", "status": "in_progress"},
        ) as resp:
            if resp.status != 201:
                text = await resp.text()
                raise AuthorizationException(
                    f"Error creating check: {resp.status}: {text}"
                )
            check = GithubCheck.parse_obj(await resp.json())
            return str(check.id)

    async def update_check(
        self,
        org: str,
        repo: str,
        check_id: str,
        running: bool = False,
        success: bool = False,
    ) -> None:
        url = f"{GITHUB_API_URL}/repos/{org}/{repo}/check-runs/{check_id}"
        if success:
            conclusion = "success"
        else:
            conclusion = "failure"
        if running:
            status = "in_progress"
        else:
            status = "completed"
        async with await self._prepare_request(
            url=url,
            method="patch",
            headers={"Accept": "application/vnd.github.v3+json"},
            json={"status": status, "conclusion": conclusion},
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise APIException(f"Error update check: {resp.status}: {text}")

    async def create_comment(self, org: str, repo: str, pull_id: int, text: str) -> str:
        url = f"{GITHUB_API_URL}/repos/{org}/{repo}/issues/{pull_id}/comments"
        async with await self._prepare_request(
            url=url,
            method="post",
            headers={"Accept": "application/vnd.github.v3+json"},
            json={"body": text},
        ) as resp:
            if resp.status != 201:
                text = await resp.text()
                raise APIException(f"Error update check: {resp.status}: {text}")
            ob = GithubComment.parse_obj(await resp.json())
            return str(ob.id)

    async def update_comment(
        self, org: str, repo: str, comment_id: str, text: str
    ) -> None:
        url = f"{GITHUB_API_URL}/repos/{org}/{repo}/issues/comments/{comment_id}"
        async with await self._prepare_request(
            url=url,
            method="patch",
            headers={"Accept": "application/vnd.github.v3+json"},
            json={"body": text},
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise APIException(f"Error update check: {resp.status}: {text}")

    async def download_file(
        self, org: str, repo: str, commit: str, filename: str
    ) -> AsyncIterator[bytes]:
        url = f"{GITHUB_API_URL}/repos/{org}/{repo}/contents/{filename}"
        async with await self._prepare_request(
            url=url,
            method="get",
            params={"ref": commit},
            headers={"Accept": "application/vnd.github.v3.raw"},
        ) as resp:
            if resp.status == 401:
                text = await resp.json()
                raise AuthorizationException(f"API Unauthorized: {text}")
            if resp.status == 404:
                text = await resp.json()
                raise NotFoundException(f"File not found: {text}")
            while chunk := await resp.content.read(1024):
                yield chunk
