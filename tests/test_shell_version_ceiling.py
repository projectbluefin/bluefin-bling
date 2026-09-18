"""Compatibility-ceiling agreement across every extension under ``extensions/``.

``metadata.json`` declares ``shell-version``, and GNOME Shell refuses to load an
extension whose list does not contain the running release. The repo enforces the
*floor* of that list from one place -- ``extension_manifest.MIN_SHELL_VERSION``
is 45 because 45 is the first ESM-only release, and
``test_extension_metadata.py`` asserts every extension clears it.

The *ceiling* has no such owner. It is restated independently in every
``extensions/*/metadata.json``, no constant names it, no document states it, and
nothing compares the lists against each other. The nearest thing to a check is a
manual one-liner in ``docs/skills/gnome-shell-extension-dev.md`` (line 182) that
prints the three lists for a human to eyeball.

That matters because the extensions do not ship independently: they are staged
into one image, which boots exactly one GNOME Shell. When that Shell is bumped
and only some ``metadata.json`` files are extended, the laggards stop loading on
the new image -- silently, since a refused extension produces no build failure
and no test failure, only an extension that is no longer there. The reverse edit
is just as quiet: raising one extension past the others advertises support for a
release its siblings will not run on.

Two invariants follow, and neither hardcodes a GNOME version, so a future bump
needs no edit here:

* every extension supports the newest release *any* extension claims;
* each list is contiguous, because a gap claims support on both sides of a
  release the extension will not load on.

Floors are deliberately *not* required to agree. A new extension may legitimately
need a later baseline -- ``-st-accent-color`` is GNOME 47+, and light-style
already drops that one declaration on 45/46 rather than raising its floor -- so
demanding a uniform floor would punish exactly the compatibility work the repo
wants. Only the ceiling is load-bearing for the shipped image.
"""

from __future__ import annotations

import unittest

from extension_manifest import extension_dirs, load_metadata


def declared_versions(metadata: dict) -> list[int]:
    """Return ``shell-version`` as sorted ints.

    ``test_extension_metadata.py`` owns the shape of the field (list, numeric
    strings, ascending, deduplicated, >= the floor). This helper only needs the
    numbers, so it sorts defensively rather than restating those assertions and
    producing a second, divergent copy of that contract.
    """
    return sorted(int(entry) for entry in metadata["shell-version"])


def gaps(versions: list[int]) -> list[int]:
    """Return releases missing from the span *versions* claims to cover."""
    if not versions:
        return []
    return [v for v in range(versions[0], versions[-1] + 1) if v not in versions]


def ceilings() -> dict[str, int]:
    """Map extension folder name to the newest release it declares."""
    result: dict[str, int] = {}
    for ext_dir in extension_dirs():
        versions = declared_versions(load_metadata(ext_dir))
        if versions:
            result[ext_dir.name] = versions[-1]
    return result


class TestHelpers(unittest.TestCase):
    """Synthetic coverage so a helper regression cannot pass unnoticed.

    Mirrors the approach in ``test_extension_manifest.py``: the invariants below
    read live files, so on a healthy tree they pass no matter what the helpers
    return. These fix the helpers against known inputs instead.
    """

    def test_declared_versions_sorts_and_converts(self):
        self.assertEqual(
            declared_versions({"shell-version": ["46", "45", "50"]}), [45, 46, 50]
        )

    def test_no_gaps_in_a_contiguous_span(self):
        self.assertEqual(gaps([45, 46, 47]), [])

    def test_gaps_reports_every_missing_release(self):
        self.assertEqual(gaps([45, 48, 50]), [46, 47, 49])

    def test_gaps_tolerates_degenerate_input(self):
        self.assertEqual(gaps([]), [])
        self.assertEqual(gaps([50]), [])


class TestCeilingAgreement(unittest.TestCase):
    def test_more_than_one_extension_participates(self):
        """Guards the suite below, which is vacuous for a single extension."""
        self.assertGreater(
            len(ceilings()),
            1,
            "fewer than two extensions declare shell-version; the agreement "
            "checks below would pass without comparing anything",
        )

    def test_every_extension_supports_the_newest_declared_release(self):
        declared = ceilings()
        newest = max(declared.values())
        laggards = sorted(
            name for name, ceiling in declared.items() if ceiling != newest
        )
        for name in laggards:
            with self.subTest(extension=name):
                self.fail(
                    f"extensions/{name} declares shell-version up to "
                    f"{declared[name]} while another extension declares "
                    f"{newest}. These extensions ship into one image running one "
                    f"GNOME Shell, so on {newest} this one is simply not loaded "
                    f"— no build error, no runtime error, just a missing "
                    f"extension. Add '{newest}' to "
                    f"extensions/{name}/metadata.json, or lower the others if "
                    f"{newest} is not actually supported yet."
                )

    def test_declared_ranges_are_contiguous(self):
        for ext_dir in extension_dirs():
            versions = declared_versions(load_metadata(ext_dir))
            missing = gaps(versions)
            with self.subTest(extension=ext_dir.name):
                if missing:
                    self.fail(
                        f"extensions/{ext_dir.name} claims GNOME "
                        f"{versions[0]}–{versions[-1]} but omits {missing}; a "
                        "hole in the range means the extension refuses to load "
                        "on a release it brackets on both sides"
                    )


if __name__ == "__main__":
    unittest.main()
