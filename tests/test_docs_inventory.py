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
here -- see issue #47. This module covers only the ``README.md`` inventories,
so issue #47 stays open for the ``AGENTS.md`` remainder it enumerates and must
not be auto-closed by this work.
"""

from __future__ import annotations

import re
import subprocess
import unittest

from extension_manifest import REPO_ROOT, extension_dirs, load_metadata

SKILLS_DIR = REPO_ROOT / "docs" / "skills"
SKILL_ROUTER = REPO_ROOT / "docs" / "SKILL.md"
README = REPO_ROOT / "README.md"

# Matches a skill doc reference in either supported layout:
# docs/skills/<name>.md or docs/skills/<name>/SKILL.md.
SKILL_REF_RE = re.compile(r"docs/skills/(?:[a-zA-Z0-9._-]+/)?[a-zA-Z0-9._-]+\.md")


def skill_docs() -> list[str]:
    """Return every skill doc as a repo-relative path, sorted.

    Two layouts count: the flat ``docs/skills/<name>.md`` file, and the
    per-skill directory ``docs/skills/<name>/SKILL.md`` that
    ``projectbluefin/common`` migrates oversized skills to. Dotfiles are
    excluded in both layouts so hidden files and editor lock files such as
    ``.#foo.md`` are never mistaken for skill docs.
    """
    if not SKILLS_DIR.is_dir():
        return []
    found = list(SKILLS_DIR.glob("[!.]*.md")) + list(SKILLS_DIR.glob("[!.]*/SKILL.md"))
    return sorted(p.relative_to(REPO_ROOT).as_posix() for p in found)


def tracked_top_level_entries() -> set[str] | None:
    """Return git's tracked top-level names, or None when git cannot answer.

    ``git ls-files`` reports the index -- the repository's own inventory of
    tracked content -- so no ignore list has to be maintained here: packaging
    residue (``power-status-color.zip``), OS droppings (``.DS_Store``) and
    local tooling (``.venv/``) are untracked and never reach the
    structure-tree assertion, while a freshly ``git add``-ed top-level file
    does, from the moment it is staged.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=REPO_ROOT,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        paths = result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None
    # ls-files reports full paths; the structure tree names top-level entries.
    return {path.split("/", 1)[0] for path in paths.split("\0") if path}


def top_level_entries() -> list[str]:
    """Return every top-level repository entry the structure tree should name."""
    tracked = tracked_top_level_entries()
    if tracked is not None:
        return sorted(tracked)
    # No git available (a source tarball, say): fall back to the directory
    # walk. ".git" is git's own storage rather than repository content, so it
    # is the one name the tree is never expected to document.
    return sorted(p.name for p in REPO_ROOT.iterdir() if p.name != ".git")


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
            f"{SKILLS_DIR} has no skill docs; the skills catalog contract is meaningless",
        )

    def test_readme_lists_every_skill_doc(self):
        text = readme_text()
        for name in skill_docs():
            with self.subTest(skill=name):
                # Explicit fail() rather than assertIn(): assertIn renders the
                # whole README before the message and buries the diagnosis.
                if name not in text:
                    self.fail(
                        f"README.md Skills Catalog does not list {name}; "
                        "an agent routed via the README will never load it"
                    )

    def test_readme_lists_no_missing_skill_doc(self):
        listed = set(SKILL_REF_RE.findall(readme_text()))
        present = set(skill_docs())
        for name in sorted(listed - present):
            with self.subTest(skill=name):
                self.fail(f"README.md links {name}, which does not exist")

    def test_skill_router_routes_every_skill_doc(self):
        router = SKILL_ROUTER.read_text(encoding="utf-8")
        for name in skill_docs():
            with self.subTest(skill=name):
                if name not in router:
                    self.fail(
                        f"docs/SKILL.md does not route {name}; the router is "
                        "the documented agent entry point, so the doc is unreachable"
                    )

    def test_skill_router_routes_no_missing_skill_doc(self):
        routed = set(SKILL_REF_RE.findall(SKILL_ROUTER.read_text(encoding="utf-8")))
        present = set(skill_docs())
        for name in sorted(routed - present):
            with self.subTest(skill=name):
                self.fail(f"docs/SKILL.md routes {name}, which does not exist")


class TestRepositoryStructureTree(unittest.TestCase):
    def test_structure_section_is_present(self):
        try:
            tree = structure_tree()
        except ValueError as exc:
            self.fail(str(exc))
        self.assertTrue(tree.strip(), "README.md Repository Structure tree is empty")

    def test_tree_names_every_top_level_entry(self):
        try:
            tree = structure_tree()
        except ValueError as exc:
            self.fail(str(exc))
        # Substring containment: a top-level name is enough of a signal here,
        # and the tree is a code fence rather than prose, so the vacuous-match
        # risk that applies to the roster checks below is negligible.
        for name in top_level_entries():
            with self.subTest(entry=name):
                if name not in tree:
                    self.fail(
                        f"README.md Repository Structure omits top-level '{name}'; "
                        "the tree describes a repository that does not exist"
                    )


class TestExtensionRoster(unittest.TestCase):
    # These two scan the whole README for a bare substring, so an extension
    # named after a common word ("power", "style") could match existing prose
    # and pass vacuously. The uuid check below is the load-bearing one: a uuid
    # cannot appear by accident.
    def test_readme_documents_every_extension(self):
        text = readme_text()
        for ext_dir in extension_dirs():
            with self.subTest(extension=ext_dir.name):
                if ext_dir.name not in text:
                    self.fail(f"extensions/{ext_dir.name} ships but README.md never names it")

    def test_readme_documents_every_extension_uuid(self):
        text = readme_text()
        for ext_dir in extension_dirs():
            uuid = load_metadata(ext_dir)["uuid"]
            with self.subTest(extension=ext_dir.name):
                if uuid not in text:
                    self.fail(
                        f"extensions/{ext_dir.name} uuid {uuid} is absent from README.md; "
                        "the documented install and enable commands cannot be correct"
                    )


if __name__ == "__main__":
    unittest.main()
