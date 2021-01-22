from unittest.mock import Mock, patch
from opencoverage import parser
from lxml import etree
import pytest


def test_get_el_raises_parsing_exception():
    dom = etree.fromstring("<div></div>")
    with pytest.raises(parser.ParsingException):
        parser.get_el(dom, "foobar")


def test_parse_coverage_invalid_xml_data():
    with pytest.raises(parser.ParsingException):
        parser.parse_raw_coverage_data(
            b"""
foobar
<<<<<< network
# path=/path/to/something
{BAD DATA FILE}

<<<<<< EOF
"""  # noqa
        )


def test_parse_coverage_ignore_invalid_paths():
    result = parser.parse_raw_coverage_data(
        b"""
foobar
<<<<<< network
# path=/path/to/coverage.xml
<?xml version="1.0" ?>
<coverage version="5.3.1" timestamp="1610313969570"
          lines-valid="31160" lines-covered="27879" line-rate="0.8947"
          branches-covered="0" branches-valid="0" branch-rate="0" complexity="0">
	<sources>
		<source>/some/path</source>
	</sources>
	<packages>
		<package name="something" line-rate="0.9112" branch-rate="0" complexity="0">
			<classes>
				<class name="fooobar.py" filename="fooobar.py" complexity="0" line-rate="1" branch-rate="0">
					<methods/>
					<lines>
						<line number="2" hits="1"/>
					</lines>
				</class>
            </classes>
        </package>
    </packages>
</coverage>

<<<<<< EOF
"""  # noqa
    )
    assert len(result["file_coverage"]) == 0


def test_parse_coverage_data():
    result = parser.parse_raw_coverage_data(
        b"""
guillotina/__init__.py
<<<<<< network
# path=/Users/nathan/personal/guillotina_clone/coverage.xml
<?xml version="1.0" ?>
<coverage version="5.3.1" timestamp="1610313969570" lines-valid="31160" lines-covered="27879" line-rate="0.8947" branches-covered="0" branches-valid="0" branch-rate="0" complexity="0">
	<!-- Generated by coverage.py: https://coverage.readthedocs.io -->
	<!-- Based on https://raw.githubusercontent.com/cobertura/web/master/htdocs/xml/coverage-04.dtd -->
	<sources>
		<source>/Users/nathan/personal/guillotina_clone/guillotina</source>
	</sources>
	<packages>
		<package name="guillotina" line-rate="0.9112" branch-rate="0" complexity="0">
			<classes>
				<class name="__init__.py" filename="__init__.py" complexity="0" line-rate="1" branch-rate="0">
					<methods/>
					<lines>
						<line number="2" hits="1"/>
						<line number="3" hits="1"/>
						<line number="4" hits="1"/>
						<line number="5" hits="1"/>
						<line number="6" hits="1"/>
						<line number="7" hits="1"/>
						<line number="8" hits="1"/>
						<line number="9" hits="1"/>
						<line number="11" hits="1"/>
						<line number="12" hits="1"/>
						<line number="15" hits="1"/>
						<line number="19" hits="1"/>
					</lines>
				</class>
            </classes>
        </package>
    </packages>
</coverage>

<<<<<< EOF
"""  # noqa
    )

    assert result["line_rate"] == 0.8947
    assert result["lines_covered"] == 27879
    assert result["lines_valid"] == 31160
    assert result["timestamp"] == 1610313969570

    assert result["branch_rate"] == 0.0
    assert result["branches_covered"] == 0
    assert result["branches_valid"] == 0
    assert result["complexity"] == 0

    assert len(result["file_coverage"]) == 1

    fcov = result["file_coverage"]["guillotina/__init__.py"]
    assert fcov["branch_rate"] == 0.0
    assert fcov["complexity"] == 0.0
    assert fcov["line_rate"] == 1.0
    assert len(fcov["lines"]) == 12


def test_parse_diff():
    diff = parser.parse_diff(
        """diff --git a/guillotina/addons.py b/guillotina/addons.py
index 8ad9304b..de0e1d25 100644
--- a/guillotina/addons.py
+++ b/guillotina/addons.py
@@ -29,6 +29,7 @@ async def install(container, addon):
         await install(container, dependency)
     await apply_coroutine(handler.install, container, request)
     registry = task_vars.registry.get()
+    registry
     config = registry.for_interface(IAddons)
     config["enabled"] |= {addon}

"""
    )
    assert len(diff) == 1
    assert diff[0]["filename"] == "guillotina/addons.py"
    assert diff[0]["lines"] == [32]


def test_parse_diff_ignore_binary():
    bpatch = Mock()
    bpatch.is_binary_file = True
    dpatch = Mock()
    dpatch.is_binary_file = False
    dpatch.is_removed_file = True
    with patch("opencoverage.parser.PatchSet", return_value=[bpatch, dpatch]):
        diff = parser.parse_diff("diff")
        assert len(diff) == 0