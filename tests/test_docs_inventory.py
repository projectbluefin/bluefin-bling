"""Documentation-inventory drift gate.

``tests/extension_manifest.py`` states the repo principle: nothing is hardcoded,
so a newly added extension folder is validated without anyone editing a list.
The docs opted out of that principle -- ``README.md`` hand-maintains the skills
catalog, the repository-structure tree and the extension roster, and nothing
compared them against the filesystem that actually owns those inventories.

These tests close that gap. They do not check prose; they check that every
filesystem entity the README claims to enumerate is actually enumerated. A new
skill doc, a new top-level file or a new extension therefore fails here until
the README is updated, instead of silently making the README wrong.

``AGENTS.md`` carries the same class of drift and is intentionally out of scope
here -- see issue #47.
"""

from __future__ import annotations

import re
import unittest

from extension_manifest import REPO_ROOT, extension_dirs, load_metadata

SKILLS_DIR = REPO_ROOT / "docs" / "skills"
SKILL_ROUTER = REPO_ROOT / "docs" / "SKILL.md"
README = REPO_ROOT / "README.md"

# Directories that are build/test residue rather than repository structure, so
# the structure tree is not expected to name them.
IGNORED_TOP_LEVEL = {".git", "__pycache__", ".pytest_cache", "node_modules"}


def skill_docs() -> list[str]:
    """Return every skill doc filename under docs/skills/, sorted."""
    if not SKILLS_DIR.is_dir():
        return []
    return sorted(p.name for p in SKILLS_DIR.glob("*.md"))


def top_level_entries() -> list[str]:
    """Return every top-level repository entry the structure tree should name."""
    return sorted(p.name for p in REPO_ROOT.iterdir() if p.name not in IGNORED_TOP_LEVEL)


def readme_text() -> str:
    return README.read_text(encoding="utf-8")


def structure_tree() -> str:
    """Return the fenced tree that follows the README Repository Structure heading.

    Raises AssertionError-friendly ValueError if the section or its fence is
    gone, so a restructured README fails loudly rather than vacuously passing.
    """
    text = readme_text()
    heading = re.search(r"^##\s+Repository Structure\s*$", text, re.MULTILINE)
    if heading is None:
        raise ValueError("README.md has no '## Repository Structure' section")
    fence = re.search(r"```[^\n]*\n(?P<body>.*?)```", text[heading.end() :], re.DOTALL)
    if fence is None:
        raise ValueError("README.md '## Repository Structure' section has no fenced tree")
    return fence.group("body")


class TestSkillsCatalog(unittest.TestCase):
    def test_skill_docs_exist(self):
        self.assertTrue(
            skill_docs(),
            f"{SKILLS_DIR} has no *.md files; the skills catalog contract is meaningless",
        )

    def test_readme_lists_every_skill_doc(self):
        text = readme_text()
        for name in skill_docs():
            with self.subTest(skill=name):
                self.assertIn(
                    f"docs/skills/{name}",
                    text,
                    f"README.md Skills Catalog does not list docs/skills/{name}; "
                    "an agent routed via the README will never load it",
                )

    def test_readme_lists_no_missing_skill_doc(self):
        listed = set(re.findall(r"docs/skills/([a-zA-Z0-9._-]+\.md)", readme_text()))
        present = set(skill_docs())
        for name in sorted(listed - present):
            with self.subTest(skill=name):
                self.fail(f"README.md links docs/skills/{name}, which does not exist")

    def test_skill_router_routes_every_skill_doc(self):
        router = SKILL_ROUTER.read_text(encoding="utf-8")
        for name in skill_docs():
            with self.subTest(skill=name):
                self.assertIn(
                    f"docs/skills/{name}",
                    router,
                    f"docs/SKILL.md does not route docs/skills/{name}; the router is "
                    "the documented agent entry point, so the doc is unreachable",
                )

    def test_skill_router_routes_no_missing_skill_doc(self):
        routed = set(
            re.findall(r"docs/skills/([a-zA-Z0-9._-]+\.md)", SKILL_ROUTER.read_text("utf-8"))
        )
        present = set(skill_docs())
        for name in sorted(routed - present):
            with self.subTest(skill=name):
                self.fail(f"docs/SKILL.md routes docs/skills/{name}, which does not exist")


class TestRepositoryStructureTree(unittest.TestCase):
    def test_structure_section_is_present(self):
        try:
            tree = structure_tree()
        except ValueError as exc:
            self.fail(str(exc))
        self.assertTrue(tree.strip(), "README.md Repository Structure tree is empty")

    def test_tree_names_every_top_level_entry(self):
        tree = structure_tree()
        for name in top_level_entries():
            with self.subTest(entry=name):
                self.assertIn(
                    name,
                    tree,
                    f"README.md Repository Structure omits top-level '{name}'; "
                    "the tree describes a repository that does not exist",
                )


class TestExtensionRoster(unittest.TestCase):
    def test_readme_documents_every_extension(self):
        text = readme_text()
        for ext_dir in extension_dirs():
            with self.subTest(extension=ext_dir.name):
                self.assertIn(
                    ext_dir.name,
                    text,
                    f"extensions/{ext_dir.name} ships but README.md never names it",
                )

    def test_readme_documents_every_extension_uuid(self):
        text = readme_text()
        for ext_dir in extension_dirs():
            uuid = load_metadata(ext_dir)["uuid"]
            with self.subTest(extension=ext_dir.name):
                self.assertIn(
                    uuid,
                    text,
                    f"extensions/{ext_dir.name} uuid {uuid} is absent from README.md; "
                    "the documented install and enable commands cannot be correct",
                )


if __name__ == "__main__":
    unittest.main()
