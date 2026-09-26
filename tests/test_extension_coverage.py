"""Executed-coverage gate: every shipped JS source must actually be run.

Every *structural* gate in this suite derives its subject from the filesystem —
``test_extension_metadata.py``, ``test_extension_schemas.py``,
``test_extension_sources.py``, ``test_extension_styles.py``,
``test_shell_version_ceiling.py`` and ``test_docs_inventory.py`` all loop over
``extension_dirs()``, so a newly added extension folder is validated without
anyone editing a list.

*Executed* coverage was the one exception: which sources actually run was a
hand-maintained pairing between a test module and a harness, each naming its
own file with a module-level constant. Nothing asserted the pairing was
complete, so a new extension folder — or a new module split out of an existing
one — could arrive fully gated on shape (metadata, schema keys, ESM form,
``node --check``, stylesheet class agreement, ``enable()``/``disable()``
symmetry) with zero lines of its logic ever executed, while
``validate extensions`` stayed green. The structural gates are deliberately
shape-only: ``test_extension_sources.py`` greps for an ``enable()``/``disable()``
pair, it does not run them.

This module closes that gap the same way every other gate here closes one: by
discovering both sides. For every JS source under ``extensions/``, some
``tests/*_harness.mjs`` must resolve that path (``harness_covers_source()``),
and some ``tests/test_*.py`` must spawn that harness through node
(``harness_is_exercised()``).

A deliberate exception belongs in ``UNCOVERED_SOURCES`` below — a shrinking
ratchet, not a permanent allowlist. ``TestRatchetIsAccurate`` fails if an entry
there is actually covered (stale) or if it is missing coverage information that
does not match reality, so the ratchet cannot rot silently in either direction.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from extension_manifest import (
    REPO_ROOT,
    extension_dirs,
    harness_covers_source,
    harness_files,
    harness_is_exercised,
    js_sources,
    test_modules,
)

# Harnesses that exercise shared test infrastructure rather than a source
# shipped under extensions/. tests/gnome_module_loader.mjs is the shim every
# other harness imports; testing it directly against synthetic sources is the
# harness's whole purpose, so it never "covers" an extensions/ path and must
# not be required to.
INFRASTRUCTURE_HARNESSES = frozenset({"gnome_module_loader_harness.mjs"})

# Deliberate, reviewable exceptions to the coverage gate below. Empty today —
# every shipped source has a harness. An entry here must name the source
# relative to the repo root and explain why it is not yet covered; when
# coverage lands, remove the entry in the same PR (TestRatchetIsAccurate fails
# otherwise, so a stale entry cannot survive a review that only reads the
# diff).
UNCOVERED_SOURCES: frozenset[str] = frozenset()


def _all_sources() -> list[Path]:
    sources: list[Path] = []
    for ext_dir in extension_dirs():
        sources.extend(js_sources(ext_dir))
    return sources


def _repo_relative(source: Path) -> str:
    return str(source.resolve().relative_to(REPO_ROOT))


class TestEveryExtensionSourceIsExecuted(unittest.TestCase):
    """The gate the issue asks for: coverage is discovered, not enumerated."""

    def test_more_than_one_extension_participates(self):
        """Guards the suite below, which is vacuous with only one extension."""
        self.assertGreater(
            len(extension_dirs()),
            1,
            "only one extension exists — the coverage gate cannot prove it "
            "discriminates covered from uncovered until a second one does",
        )

    def test_every_source_is_covered_by_a_harness(self):
        harnesses = [h for h in harness_files() if h.name not in INFRASTRUCTURE_HARNESSES]
        harness_texts = {h: h.read_text(encoding="utf-8") for h in harnesses}
        for source in _all_sources():
            rel = _repo_relative(source)
            if rel in UNCOVERED_SOURCES:
                continue
            with self.subTest(source=rel):
                covering = [
                    h for h, text in harness_texts.items() if harness_covers_source(text, source)
                ]
                self.assertTrue(
                    covering,
                    f"{rel} is shipped but no tests/*_harness.mjs resolves its path — "
                    "it is validated for shape only and zero lines of it ever execute. "
                    "Add a behavior harness, or add this path to UNCOVERED_SOURCES with "
                    "a stated reason.",
                )

    def test_every_covering_harness_is_actually_spawned(self):
        """A harness that covers a source but that no test runs is dead weight.

        ``harness_covers_source`` only proves a harness *names* the path; a
        harness sitting unused would let the assertion above pass while the
        source still executes zero times when the suite runs.
        """
        test_texts = {str(p): p.read_text(encoding="utf-8") for p in test_modules()}
        for harness in harness_files():
            if harness.name in INFRASTRUCTURE_HARNESSES:
                continue
            with self.subTest(harness=harness.name):
                self.assertTrue(
                    harness_is_exercised(harness, test_texts),
                    f"tests/{harness.name} exists but no tests/test_*.py spawns it via "
                    "node — a harness nothing runs proves nothing",
                )


class TestRatchetIsAccurate(unittest.TestCase):
    """UNCOVERED_SOURCES must describe the tree, or it is not trustworthy.

    A ratchet only works as a shrinking list of *real* gaps. An entry that
    is actually covered hides that the gate is weaker than the code reads —
    the next author trusts the comment instead of the filesystem. An entry
    that no longer exists on disk is dead weight nobody will notice to prune.
    """

    def test_every_entry_names_a_real_source(self):
        existing = {_repo_relative(source) for source in _all_sources()}
        for entry in UNCOVERED_SOURCES:
            with self.subTest(entry=entry):
                self.assertIn(
                    entry,
                    existing,
                    f"UNCOVERED_SOURCES names {entry!r}, which is not a JS source "
                    "under extensions/ today — remove the stale entry",
                )

    def test_every_entry_is_genuinely_uncovered(self):
        harnesses = [h for h in harness_files() if h.name not in INFRASTRUCTURE_HARNESSES]
        harness_texts = {h: h.read_text(encoding="utf-8") for h in harnesses}
        by_rel = {_repo_relative(source): source for source in _all_sources()}
        for entry in UNCOVERED_SOURCES:
            source = by_rel.get(entry)
            if source is None:
                continue  # already reported by test_every_entry_names_a_real_source
            with self.subTest(entry=entry):
                covering = [
                    h for h, text in harness_texts.items() if harness_covers_source(text, source)
                ]
                self.assertFalse(
                    covering,
                    f"UNCOVERED_SOURCES lists {entry!r}, but "
                    f"{[h.name for h in covering]} already covers it — remove the "
                    "stale exception",
                )


class TestHelpers(unittest.TestCase):
    """Synthetic coverage so a helper regression cannot pass unnoticed.

    Mirrors the approach in ``test_extension_manifest.py``: the invariants
    above read live files, so on a healthy tree they pass no matter what the
    helpers return. These fix the helpers against known inputs instead.
    """

    def test_harness_covers_source_matches_join_style_path(self):
        source = REPO_ROOT / "extensions" / "light-style" / "extension.js"
        harness = (
            "const EXTENSION_JS = join(HERE, '..', 'extensions', "
            "'light-style', 'extension.js');"
        )
        self.assertTrue(harness_covers_source(harness, source))

    def test_harness_covers_source_rejects_unrelated_path(self):
        source = REPO_ROOT / "extensions" / "light-style" / "extension.js"
        harness = (
            "const EXTENSION_JS = join(HERE, '..', 'extensions', "
            "'power-status-color', 'extension.js');"
        )
        self.assertFalse(harness_covers_source(harness, source))

    def test_harness_covers_source_matches_double_quotes(self):
        source = REPO_ROOT / "extensions" / "syncthing-toggle" / "toggle.js"
        harness = 'const TOGGLE_JS = join(HERE, "..", "extensions", "syncthing-toggle", "toggle.js");'
        self.assertTrue(harness_covers_source(harness, source))

    def test_harness_is_exercised_requires_subprocess_and_node(self):
        harness = Path("light_style_harness.mjs")
        spawns_it = {
            "test_light_style.py": (
                'HARNESS = Path(__file__).resolve().parent / "light_style_harness.mjs"\n'
                "subprocess.run([NODE, str(HARNESS)])\n"
            )
        }
        self.assertTrue(harness_is_exercised(harness, spawns_it))

    def test_harness_is_exercised_rejects_bare_mention(self):
        harness = Path("light_style_harness.mjs")
        just_mentions_it = {
            "README.md": "See tests/light_style_harness.mjs for the rewrite.\n"
        }
        self.assertFalse(harness_is_exercised(harness, just_mentions_it))


if __name__ == "__main__":
    unittest.main()
