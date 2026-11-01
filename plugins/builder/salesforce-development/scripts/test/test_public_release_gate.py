#!/usr/bin/env python3
"""Offline release-gate tests for the publishable Salesforce plugin tree."""
from __future__ import annotations

# Importing the verifier must not create a transient __pycache__ inside the
# publishable tree that the integration test scans below.
import sys
sys.dont_write_bytecode = True

import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from _test_support import load_module

SCRIPTS = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = SCRIPTS.parent
REPO_ROOT = PLUGIN_ROOT.parents[2]
WORKFLOW = REPO_ROOT / ".github/workflows/release-to-public.yml"


class PublicReleaseGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(SCRIPTS))
        cls.gate = load_module(
            SCRIPTS / "verify-public-plugin-release.py",
            "verify_public_plugin_release_tests",
        )

    def copy_release_candidate(self, destination: Path) -> None:
        shutil.copytree(
            PLUGIN_ROOT,
            destination,
            ignore=shutil.ignore_patterns(".sf", ".pytest_cache", "__pycache__", "*.pyc"),
        )

    def run_gate(self, plugin_root: Path):
        command = [
            "python3", str(plugin_root / "scripts/verify-public-plugin-release.py"),
            "--plugin-root", str(plugin_root),
            "--authoring-root", str(REPO_ROOT / "skills"),
        ]
        return subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
        )

    @staticmethod
    def make_empty_tree(root: Path) -> tuple[Path, Path]:
        """Create the smallest roots accepted by the release verifier."""
        plugin = root / "plugin"
        authoring = root / "authoring"
        (plugin / "skills").mkdir(parents=True)
        authoring.mkdir()
        return plugin, authoring

    @staticmethod
    def stage_release_ci(synthetic_repo: Path) -> None:
        """Give a synthetic repo the release-ci modules the verifier shells out to.

        The verifier reads skill descriptions through public-manifest.mjs, located
        relative to the authoring root's parent.
        """
        release_ci = synthetic_repo / "scripts/release-ci"
        release_ci.mkdir(parents=True, exist_ok=True)
        for module in ("public-manifest.mjs", "sanitize-public-skills.mjs"):
            shutil.copy(REPO_ROOT / "scripts/release-ci" / module, release_ci / module)

    @staticmethod
    def write_skill(root: Path, name: str, description: str) -> Path:
        skill = root / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            f'---\nname: {name}\ndescription: "{description}"\n---\nbody\n',
            encoding="utf-8",
        )
        return skill

    def verify_synthetic(
        self,
        plugin: Path,
        authoring: Path,
        *,
        public_root: Path | None = None,
        public_names: tuple[str, ...] = (),
    ):
        """Run leak/tree verification without the unrelated real catalog."""
        manifest = {"skills": [{"name": name} for name in public_names]}
        self.stage_release_ci(authoring.parent)
        with (
            patch.object(self.gate, "_load_public_manifest", return_value=manifest),
            patch.object(self.gate.plugin_catalog, "check", return_value=True),
            patch.object(self.gate.plugin_catalog, "held_plugin_descriptions", return_value={}),
        ):
            return self.gate.verify(plugin, authoring, public_root)

    def test_current_publishable_tree_passes_catalog_and_description_gate(self):
        with tempfile.TemporaryDirectory() as td:
            plugin = Path(td) / "salesforce-development"
            self.copy_release_candidate(plugin)
            result = self.run_gate(plugin)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("public plugin release gate passed", result.stdout)

    def test_stale_plugin_catalog_artifact_fails_before_the_leak_scan(self):
        with tempfile.TemporaryDirectory() as td:
            plugin, authoring = self.make_empty_tree(Path(td))
            error = self.gate.plugin_catalog.PluginCatalogError("plugin catalog artifact is stale")
            with (
                patch.object(self.gate, "_load_public_manifest", return_value={"skills": []}),
                patch.object(self.gate.plugin_catalog, "check", side_effect=error),
                patch.object(self.gate, "_release_files") as release_files,
                self.assertRaisesRegex(
                    self.gate.plugin_catalog.PluginCatalogError,
                    "plugin catalog artifact is stale",
                ),
            ):
                self.gate.verify(plugin, authoring)
            release_files.assert_not_called()

    def test_public_only_description_anywhere_in_release_tree_fails(self):
        with tempfile.TemporaryDirectory() as td:
            plugin, authoring = self.make_empty_tree(Path(td))
            description = "Private authoring description that must not ship in the public plugin."
            self.write_skill(authoring, "protected-skill", description)
            (plugin / "LEAK.txt").write_text(description, encoding="utf-8")
            with self.assertRaisesRegex(
                self.gate.registry.RegistryError,
                "description leak: protected-skill: LEAK.txt",
            ):
                self.verify_synthetic(plugin, authoring)

    def test_previous_public_description_from_public_tree_is_protected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plugin, authoring = self.make_empty_tree(root)
            public_root = root / "public-skills"
            public_root.mkdir()
            description = (
                "Use this previous public release description to review Salesforce "
                "architecture safely without exposing stale catalog prose to runtime consumers."
            )
            self.write_skill(public_root, "previous-public-skill", description)
            (plugin / "STALE.md").write_text(description, encoding="utf-8")
            with self.assertRaisesRegex(
                self.gate.registry.RegistryError,
                "description leak: previous-public-skill: STALE.md",
            ):
                self.verify_synthetic(
                    plugin,
                    authoring,
                    public_root=public_root,
                    public_names=("previous-public-skill",),
                )

    def test_json_escaped_full_description_fails(self):
        # A protected description present ONLY in JSON-escaped form — its raw UTF-8
        # bytes never appear verbatim — must still be caught, via the JSON-*decoded*
        # string check rather than the raw-bytes check. Escaping one interior space to
        # a backslash-u-0020 unicode escape makes the raw description provably absent,
        # isolating the decoded path.
        with tempfile.TemporaryDirectory() as td:
            plugin, authoring = self.make_empty_tree(Path(td))
            description = "Protected JSON description with spaces for escape testing."
            self.write_skill(authoring, "protected-json-skill", description)
            self.assertIn(" ", description)
            literal = json.dumps(description).replace(" ", "\\u0020", 1)
            leak = plugin / "LEAK.json"
            leak.write_text('{"nested": [' + literal + "]}", encoding="utf-8")
            self.assertNotIn(description.encode("utf-8"), leak.read_bytes())
            with self.assertRaisesRegex(
                self.gate.registry.RegistryError,
                "description leak: protected-json-skill: LEAK.json",
            ):
                self.verify_synthetic(plugin, authoring)

    def test_malformed_json_in_release_tree_fails_closed(self):
        # A publishable .json that won't parse can't be scanned for a JSON-escaped
        # leak, so the gate must fail closed — not fall back to the raw-bytes check
        # alone (which would miss an escaped form).
        with tempfile.TemporaryDirectory() as td:
            plugin, authoring = self.make_empty_tree(Path(td))
            (plugin / "broken.json").write_text('{"nested": [', encoding="utf-8")
            with self.assertRaisesRegex(
                self.gate.registry.RegistryError,
                "broken.json: unparseable JSON",
            ):
                self.verify_synthetic(plugin, authoring)

    def test_cli_returns_failure_and_reports_release_violation(self):
        """The CLI boundary must preserve a verifier failure as exit 1 + stderr."""
        with tempfile.TemporaryDirectory() as td:
            plugin, authoring = self.make_empty_tree(Path(td))
            (plugin / "broken.json").write_text('{"nested": [', encoding="utf-8")
            self.stage_release_ci(authoring.parent)
            stderr = io.StringIO()
            with (
                patch.object(self.gate, "_load_public_manifest", return_value={"skills": []}),
                patch.object(self.gate.plugin_catalog, "check", return_value=True),
                patch.object(self.gate.plugin_catalog, "held_plugin_descriptions", return_value={}),
                redirect_stderr(stderr),
            ):
                result = self.gate.main([
                    "--plugin-root", str(plugin),
                    "--authoring-root", str(authoring),
                ])
            self.assertEqual(result, 1)
            self.assertIn("public plugin release gate failed", stderr.getvalue())
            self.assertIn("broken.json: unparseable JSON", stderr.getvalue())

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_symlink_in_release_tree_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            plugin, _ = self.make_empty_tree(Path(td))
            os.symlink("/etc/hostname", plugin / "LINK.md")
            with self.assertRaisesRegex(self.gate.registry.RegistryError, "link or special file"):
                list(self.gate._release_files(plugin))

    @unittest.skipUnless(hasattr(os, "link"), "hardlinks unavailable")
    def test_hardlinked_release_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            plugin, _ = self.make_empty_tree(Path(td))
            source = plugin / "README.md"
            source.write_text("fixture", encoding="utf-8")
            os.link(source, plugin / "HARDLINK.md")
            with self.assertRaisesRegex(self.gate.registry.RegistryError, "link or special file"):
                list(self.gate._release_files(plugin))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFOs unavailable")
    def test_special_file_in_release_tree_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            plugin, _ = self.make_empty_tree(Path(td))
            os.mkfifo(plugin / "PIPE")
            with self.assertRaisesRegex(self.gate.registry.RegistryError, "link or special file"):
                list(self.gate._release_files(plugin))

    def test_release_tree_depth_limit_is_enforced(self):
        with tempfile.TemporaryDirectory() as td:
            plugin, _ = self.make_empty_tree(Path(td))
            deep = plugin.joinpath(*(["d"] * 34))  # exceeds _RELEASE_MAX_DEPTH (32)
            deep.mkdir(parents=True)
            (deep / "f.txt").write_text("x", encoding="utf-8")
            with self.assertRaisesRegex(self.gate.registry.RegistryError, "depth limit"):
                list(self.gate._release_files(plugin))

    def test_transient_directory_is_rejected_not_silently_excluded(self):
        with tempfile.TemporaryDirectory() as td:
            plugin, _ = self.make_empty_tree(Path(td))
            leak = plugin / ".sf/LEAK.txt"
            leak.parent.mkdir()
            leak.write_text("not publishable", encoding="utf-8")
            with self.assertRaisesRegex(self.gate.registry.RegistryError, r"\.sf: transient"):
                list(self.gate._release_files(plugin))

    def test_publish_workflow_runs_source_pre_copy_and_copied_gates_in_order(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        command = "verify-public-plugin-release.py"
        source_gate = workflow.index("Verify source plugin release tree")
        clone = workflow.index("git clone ")
        self.assertIn("Verify previous public descriptions before copy", workflow)
        pre_copy_gate = workflow.index("Verify previous public descriptions before copy")
        bootstrap = workflow.index("# Idempotent bootstrap:")
        copied_gate = workflow.index("Verify copied public plugin release tree")
        self.assertGreaterEqual(workflow.count(command), 3)
        self.assertLess(source_gate, clone)
        self.assertLess(clone, pre_copy_gate)
        self.assertLess(pre_copy_gate, bootstrap)
        self.assertLess(bootstrap, copied_gate)
        pre_copy_block = workflow[pre_copy_gate:bootstrap]
        self.assertIn('--public-root "skills"', pre_copy_block)


if __name__ == "__main__":
    unittest.main(verbosity=2)
