#!/usr/bin/env python3
"""Channel registry and canonical skill-tree hashing contracts."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _test_support import load_module

SCRIPTS = Path(__file__).resolve().parent.parent
REGISTRY_PATH = SCRIPTS / "capability_registry.py"


class CapabilityRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_module(REGISTRY_PATH, "capability_registry_under_test")

    def test_windows_path_descriptor_identity_ignores_incompatible_ctime(self):
        path_stat = mock.Mock(
            st_dev=1, st_ino=2, st_mode=3, st_nlink=1, st_size=4,
            st_mtime_ns=5, st_ctime_ns=6,
        )
        descriptor_stat = mock.Mock(
            st_dev=1, st_ino=2, st_mode=3, st_nlink=1, st_size=4,
            st_mtime_ns=5, st_ctime_ns=7,
        )
        with mock.patch.object(self.registry.os, "name", "nt"):
            self.assertEqual(
                self.registry._tree_identity(path_stat, path_descriptor_boundary=True),
                self.registry._tree_identity(descriptor_stat, path_descriptor_boundary=True),
            )
            self.assertNotEqual(
                self.registry._tree_identity(path_stat),
                self.registry._tree_identity(descriptor_stat),
            )

    def test_tree_hash_can_use_canonical_executable_paths(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "skill"
            root.mkdir()
            script = root / "scripts/run.sh"
            script.parent.mkdir()
            script.write_text("#!/bin/sh\n", encoding="utf-8")
            script.chmod(script.stat().st_mode & ~0o111)
            non_executable = self.registry.canonical_tree_sha256(
                root, executable_paths=set()
            )
            executable = self.registry.canonical_tree_sha256(
                root, executable_paths={"scripts/run.sh"}
            )
            self.assertNotEqual(non_executable, executable)

    def test_canonical_tree_hash_is_order_independent_and_tracks_bytes_type_and_execute_bit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "skill"
            root.mkdir()
            (root / "z.txt").write_bytes(b"z\x00bytes")
            (root / "a.txt").write_bytes(b"alpha")
            first = self.registry.canonical_tree_sha256(root)
            self.assertEqual(first, self.registry.canonical_tree_sha256(root))
            (root / "a.txt").chmod((root / "a.txt").stat().st_mode | stat.S_IXUSR)
            executable = self.registry.canonical_tree_sha256(root)
            self.assertNotEqual(first, executable)
            (root / "a.txt").chmod((root / "a.txt").stat().st_mode & ~0o111)
            self.assertEqual(first, self.registry.canonical_tree_sha256(root))
            (root / "z.txt").write_bytes(b"changed")
            self.assertNotEqual(first, self.registry.canonical_tree_sha256(root))

    def test_hash_rejects_special_files_and_unsafe_symlinks(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "skill"
            root.mkdir()
            (root / "SKILL.md").write_text("safe", encoding="utf-8")
            (root / "outside").symlink_to(Path(td).parent)
            with self.assertRaisesRegex(self.registry.RegistryError, "symlink"):
                self.registry.canonical_tree_sha256(root)
            (root / "outside").unlink()
            fifo = root / "pipe"
            os.mkfifo(fifo)
            with self.assertRaisesRegex(self.registry.RegistryError, "special"):
                self.registry.canonical_tree_sha256(root)

    def test_tree_scan_bounds_entries_depth_file_and_total_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "skill"
            root.mkdir()
            (root / "SKILL.md").write_text("safe", encoding="utf-8")
            (root / "extra.txt").write_text("extra", encoding="utf-8")
            with mock.patch.object(self.registry, "TREE_SCAN_MAX_ENTRIES", 1, create=True):
                with self.assertRaisesRegex(self.registry.RegistryError, "entry limit"):
                    self.registry.inspect_skill_tree(root)
            with mock.patch.object(self.registry, "TREE_SCAN_MAX_DEPTH", 0, create=True):
                nested = root / "nested"
                nested.mkdir()
                with self.assertRaisesRegex(self.registry.RegistryError, "depth limit"):
                    self.registry.inspect_skill_tree(root)
                nested.rmdir()
            with mock.patch.object(self.registry, "TREE_SCAN_MAX_FILE_BYTES", 3, create=True):
                with self.assertRaisesRegex(self.registry.RegistryError, "file byte limit"):
                    self.registry.inspect_skill_tree(root)
            with mock.patch.object(self.registry, "TREE_SCAN_MAX_TOTAL_BYTES", 7, create=True):
                with self.assertRaisesRegex(self.registry.RegistryError, "total byte limit"):
                    self.registry.inspect_skill_tree(root)
            with self.assertRaisesRegex(self.registry.RegistryError, "aggregate tree entry limit"):
                self.registry.inspect_skill_tree(root, budget={
                    "entries": 0, "bytes": 0, "maxEntries": 1, "maxBytes": 1024,
                })
            with self.assertRaisesRegex(self.registry.RegistryError, "aggregate tree byte limit"):
                self.registry.inspect_skill_tree(root, budget={
                    "entries": 0, "bytes": 0, "maxEntries": 100, "maxBytes": 3,
                })

    def test_tree_scan_rejects_hardlinked_regular_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "skill"
            root.mkdir()
            outside = Path(td) / "outside.md"
            outside.write_text("safe", encoding="utf-8")
            os.link(outside, root / "SKILL.md")
            with self.assertRaisesRegex(self.registry.RegistryError, "hardlink"):
                self.registry.inspect_skill_tree(root)

    def test_tree_scan_detects_directory_entry_added_after_inventory(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "skill"
            root.mkdir()
            (root / "SKILL.md").write_text("safe", encoding="utf-8")
            real_scandir = self.registry.os.scandir
            calls = 0

            def racing_scandir(path):
                nonlocal calls
                entries = list(real_scandir(path))
                calls += 1
                if calls == 1:
                    (root / "late.txt").write_text("late", encoding="utf-8")
                return entries

            with mock.patch.object(self.registry.os, "scandir", side_effect=racing_scandir):
                with self.assertRaisesRegex(self.registry.RegistryError, "parent directory changed"):
                    self.registry.inspect_skill_tree(root)

    def test_tree_scan_does_not_follow_regular_file_replaced_by_symlink_before_open(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "skill"
            root.mkdir()
            skill = root / "SKILL.md"
            skill.write_text("safe", encoding="utf-8")
            outside = Path(td) / "outside"
            outside.write_text("outside secret bytes", encoding="utf-8")
            original = root / "original"
            real_open = self.registry.os.open
            swapped = False

            def racing_open(path, flags, *args, **kwargs):
                nonlocal swapped
                if Path(path).name == skill.name and not swapped:
                    skill.rename(original)
                    skill.symlink_to(outside)
                    swapped = True
                return real_open(path, flags, *args, **kwargs)

            with mock.patch.object(self.registry.os, "open", side_effect=racing_open):
                with self.assertRaisesRegex(self.registry.RegistryError, "cannot open .*tree file"):
                    self.registry.inspect_skill_tree(root)
            self.assertTrue(swapped, "the test must exercise the pre-open replacement race")

    def test_tree_scan_pins_parent_directory_before_reading_files(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = base / "skill"
            root.mkdir()
            (root / "SKILL.md").write_text("safe", encoding="utf-8")
            replacement = base / "replacement"
            replacement.mkdir()
            (replacement / "SKILL.md").write_text("outside secret bytes", encoding="utf-8")
            moved = base / "moved"
            real_open = self.registry.os.open
            swapped = False

            def racing_open(path, flags, *args, **kwargs):
                nonlocal swapped
                if (Path(path) == root and flags & getattr(os, "O_DIRECTORY", 0)
                        and not swapped):
                    root.rename(moved)
                    root.symlink_to(replacement, target_is_directory=True)
                    swapped = True
                return real_open(path, flags, *args, **kwargs)

            with mock.patch.object(self.registry.os, "open", side_effect=racing_open):
                with self.assertRaisesRegex(self.registry.RegistryError, "parent directory"):
                    self.registry.inspect_skill_tree(root)
            self.assertTrue(swapped, "the test must replace the inventoried tree root")

    def test_hash_skills_cli_prints_canonical_hashes_for_each_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            one = root / "skill-one"
            one.mkdir()
            one.joinpath("SKILL.md").write_text(
                '---\nname: skill-one\ndescription: "d"\n---\n', encoding="utf-8"
            )
            two = root / "skill-two"
            two.mkdir()
            two.joinpath("SKILL.md").write_text(
                '---\nname: skill-two\ndescription: "d"\n---\n', encoding="utf-8"
            )
            result = subprocess.run(
                [sys.executable, str(REGISTRY_PATH), "--hash-skills", str(one), str(two)],
                capture_output=True, text=True, check=True,
            )
            payload = json.loads(result.stdout)
            self.assertEqual(set(payload), {str(one), str(two)})
            for skill_dir in (one, two):
                row = payload[str(skill_dir)]
                self.assertEqual(set(row), {"skillMdSha256", "treeSha256"})
                self.assertEqual(row["skillMdSha256"], self.registry.sha256_file(skill_dir / "SKILL.md"))
                self.assertEqual(row["treeSha256"], self.registry.canonical_tree_sha256(skill_dir))
            self.assertNotEqual(
                payload[str(one)]["treeSha256"], payload[str(two)]["treeSha256"]
            )

    def test_hash_skills_cli_fails_loud_on_missing_skill(self):
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "does-not-exist"
            result = subprocess.run(
                [sys.executable, str(REGISTRY_PATH), "--hash-skills", str(missing)],
                capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("capability registry error", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
