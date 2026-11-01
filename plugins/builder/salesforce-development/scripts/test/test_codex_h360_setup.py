"""Offline proof for saved Codex H360 opt-in and desktop Node lookup."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from _test_support import load_module

SCRIPTS = Path(__file__).resolve().parent.parent
launcher = load_module(SCRIPTS / "codex_mcp_launcher.py", "h360_setup_launcher")
context = load_module(SCRIPTS / "sf_context.py", "h360_setup_context")


class SavedSetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        home = mock.patch.object(Path, "home", return_value=self.home)
        home.start()
        self.addCleanup(home.stop)
        env = mock.patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    @property
    def config(self):
        return self.home / ".sf/salesforce-development/headless-360.json"

    def save(self, text):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text(text)

    def test_absent_and_malformed_settings_fail_closed(self):
        self.assertFalse(launcher._headless_360_enabled({}))
        for text in ('{', '[]', 'null', '{"enabled":"true"}', '{"enabled":1}'):
            with self.subTest(text=text):
                self.save(text)
                self.assertFalse(launcher._headless_360_enabled({}))

    def test_recursive_settings_fail_closed_in_launcher_and_codex_hook(self):
        self.save('{"enabled":true}')
        with mock.patch.object(launcher.json, "loads", side_effect=RecursionError("nested JSON")), \
                mock.patch.dict(os.environ, {"SF_AGENT_CLIENT": "codex"}):
            self.assertFalse(launcher._headless_360_enabled({}))
            self.assertFalse(context._headless_360_enabled())

    def test_saved_setting_survives_a_new_process_without_optin_environment(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "codex_mcp_launcher.py"), "--headless-360", "enable"],
            env={"HOME": str(self.home), "PATH": os.defpath},
            text=True, capture_output=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(json.loads(self.config.read_text())["enabled"])
        self.assertEqual(str(SCRIPTS.parent), json.loads(self.config.read_text())["plugin_root"])
        status = subprocess.run(
            [sys.executable, str(SCRIPTS / "codex_mcp_launcher.py"), "--headless-360", "status"],
            env={"HOME": str(self.home), "PATH": os.defpath},
            text=True, capture_output=True, timeout=10)
        self.assertEqual(0, status.returncode, status.stderr)
        self.assertTrue(json.loads(status.stdout)["enabled"])

    def test_enable_disable_and_environment_override(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, launcher.main(["--headless-360", "enable"]))
        self.assertTrue(launcher._headless_360_enabled({}))
        self.assertFalse(launcher._headless_360_enabled({"SF_HEADLESS_360_ENABLED": "disabled"}))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, launcher.main(["--headless-360", "disable"]))
        self.assertFalse(launcher._headless_360_enabled({}))
        self.assertTrue(launcher._headless_360_enabled({"SF_HEADLESS_360_ENABLED": "enabled"}))

    def test_registration_requires_current_root_and_consistent_manifests(self):
        self.save(json.dumps({"enabled": True, "plugin_root": "/old-version"}))
        self.assertFalse(launcher._headless_360_launcher_registered())
        self.save(json.dumps({"enabled": True, "plugin_root": str(SCRIPTS.parent)}))
        self.assertTrue(launcher._headless_360_launcher_registered())
        original_read = Path.read_text
        for text in ('[]', '{', '{"name":"salesforce-development","version":"stale"}'):
            def read_manifest(path, *args, **kwargs):
                if path.parent.name == ".codex-plugin":
                    return text
                return original_read(path, *args, **kwargs)
            with self.subTest(text=text), mock.patch.object(Path, "read_text", read_manifest):
                self.assertFalse(launcher._headless_360_launcher_registered())

    def test_status_does_not_create_settings(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, launcher.main(["--headless-360", "status"]))
        self.assertFalse(self.config.exists())

    def test_setting_is_private_on_posix(self):
        if os.name != "posix":
            self.skipTest("POSIX permissions")
        with contextlib.redirect_stdout(io.StringIO()):
            launcher.main(["--headless-360", "enable"])
        self.assertEqual(0o600, self.config.stat().st_mode & 0o777)

    def test_codex_hook_and_launcher_agree_without_changing_claude(self):
        self.save('{"enabled":true}')
        with mock.patch.dict(os.environ, {"SF_AGENT_CLIENT": "codex"}):
            self.assertTrue(context._headless_360_enabled())
            self.assertIn("dispatch_readonly", context._org_values_directive())
        self.assertFalse(context._headless_360_enabled())
        with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_OPTION_HEADLESS_360_ENABLED": "enabled"}):
            self.assertTrue(context._headless_360_enabled())

    def test_saved_enabled_still_requires_bounded_project_and_org(self):
        self.save('{"enabled":true}')
        with mock.patch.object(launcher.os, "execvp") as execute, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(2, launcher.main(["salesforce-headless-360"]))
        execute.assert_not_called()
        project = self.home / "project"
        project.mkdir()
        (project / "sfdx-project.json").write_text('{}')
        with mock.patch.dict(os.environ, {"CODEX_WORKSPACE_ROOT": str(project)}), \
                mock.patch.object(launcher.os, "execvp") as execute, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(2, launcher.main(["salesforce-headless-360"]))
        execute.assert_not_called()


class NodeLookupTests(unittest.TestCase):
    def test_native_manifest_preserves_the_host_working_directory(self):
        manifest = json.loads((SCRIPTS.parent / ".codex-plugin/plugin.json").read_text())
        server = manifest["mcpServers"]["salesforce-headless-360"]
        self.assertNotIn("cwd", server)
        self.assertEqual(["-I", "-c"], server["args"][:2])
        self.assertEqual(4, len(server["args"]))
        self.assertEqual((SCRIPTS / "codex_h360_bootstrap.py").read_text(), server["args"][2])

    def test_existing_path_has_precedence(self):
        with mock.patch.object(launcher.shutil, "which", return_value="/existing/node") as which:
            self.assertEqual("/existing/node", launcher.resolve_node({"PATH": "/existing"}))
        self.assertEqual("/existing", which.call_args_list[0].kwargs["path"])

    def test_missing_node_reports_actionable_error(self):
        with mock.patch.object(launcher.shutil, "which", return_value=None), \
                mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(launcher, "_headless_360_enabled", return_value=False), \
                contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(2, launcher.main(["salesforce-headless-360"]))
        self.assertIn("Node.js", stderr.getvalue())

    @unittest.skipUnless(os.name == "posix", "Desktop POSIX launch regression")
    def test_real_handshake_with_saved_optin_and_node_outside_desktop_path(self):
        node = launcher.resolve_node(dict(os.environ))
        if node is None:
            self.skipTest("Node required for real launcher handshake")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            scripts = root / "plugin/scripts"
            scripts.mkdir(parents=True)
            for filename in ("codex_mcp_launcher.py", "sf-headless-360-optin.js"):
                shutil.copy2(SCRIPTS / filename, scripts / filename)
            # A local fake proxy proves forwarding without auth or network calls.
            (scripts / "sf-mcp-proxy.bundled.js").write_text('''
const readline = require('node:readline');
readline.createInterface({input: process.stdin}).on('line', line => {
  const request = JSON.parse(line);
  console.log(JSON.stringify({jsonrpc: '2.0', id: request.id, result: {
    enabled: process.env.SF_HEADLESS_360_ENABLED,
    org: process.env.SF_TARGET_ORG, cwd: process.cwd()
  }}));
});
''')
            home = root / "home"
            config = home / ".sf/salesforce-development/headless-360.json"
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({"enabled": True, "plugin_root": str(scripts.parent)}))
            manifest = json.loads((SCRIPTS.parent / ".codex-plugin/plugin.json").read_text())
            (scripts.parent / ".codex-plugin").mkdir()
            (scripts.parent / ".codex-plugin/plugin.json").write_text(json.dumps(manifest))
            (scripts.parent / ".claude-plugin").mkdir()
            (scripts.parent / ".claude-plugin/plugin.json").write_text(json.dumps(manifest))
            bindir = home / ".local/bin"
            bindir.mkdir(parents=True)
            (bindir / "node").symlink_to(node)
            project = root / "project"
            (project / ".sf").mkdir(parents=True)
            (project / "sfdx-project.json").write_text('{}')
            (project / ".sf/config.json").write_text('{"target-org":"scratch"}')
            # PATH intentionally excludes both the shell's Node and ~/.local/bin.
            # A Dock-launched host has a stale PWD and no exported workspace root.
            # The selected thread's actual process cwd is the project authority.
            env = {"HOME": str(home), "PATH": str(root / "empty-bin"), "PWD": "/"}
            with subprocess.Popen(
                    [sys.executable, *manifest["mcpServers"]["salesforce-headless-360"]["args"]],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, env=env, cwd=project) as process:
                try:
                    out, err = process.communicate('{"jsonrpc":"2.0","id":1,"method":"initialize"}\n', timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                    raise
            self.assertEqual(0, process.returncode, err)
            result = json.loads(out)["result"]
            self.assertEqual("enabled", result["enabled"])
            self.assertEqual("scratch", result["org"])
            self.assertEqual(str(project.resolve()), result["cwd"])


class NativeBootstrapTests(unittest.TestCase):
    def run_native(self, home, config=None, overrides=None, requests=None):
        manifest = json.loads((SCRIPTS.parent / ".codex-plugin/plugin.json").read_text())
        if config is not None:
            path = home / ".sf/salesforce-development/headless-360.json"
            path.parent.mkdir(parents=True)
            path.write_text(config if isinstance(config, str) else json.dumps(config))
        environment = {"HOME": str(home), "PATH": "/missing-node"}
        environment.update(overrides or {})
        return subprocess.run(
            [sys.executable, *manifest["mcpServers"]["salesforce-headless-360"]["args"]],
            env=environment, cwd=home,
            input=requests if requests is not None else '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n'
                  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n',
            text=True, capture_output=True, timeout=10)

    def test_no_saved_setup_completes_disabled_handshake_without_node(self):
        with tempfile.TemporaryDirectory() as raw:
            result = self.run_native(Path(raw))
        self.assertEqual(0, result.returncode, result.stderr)
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([], replies[1]["result"]["tools"])

    def test_disabled_startup_never_imports_project_json_module(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            marker = home / "untrusted-module-ran"
            (home / "json.py").write_text(
                "with open(" + repr(str(marker)) + ", 'w') as stream: stream.write('ran')\n"
                "raise RuntimeError('project json.py executed')\n")
            result = self.run_native(home)
            self.assertFalse(marker.exists(), result.stderr)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([], json.loads(result.stdout.splitlines()[1])["result"]["tools"])

    def test_enabled_launcher_never_imports_project_runtime_modules(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            package = home / "package"
            (package / ".codex-plugin").mkdir(parents=True)
            (package / "scripts").mkdir()
            version = json.loads((SCRIPTS.parent / ".codex-plugin/plugin.json").read_text())["version"]
            (package / ".codex-plugin/plugin.json").write_text(json.dumps(
                {"name": "salesforce-development", "version": version}))
            (package / ".claude-plugin").mkdir()
            (package / ".claude-plugin/plugin.json").write_text(json.dumps(
                {"name": "salesforce-development", "version": version}))
            (package / "scripts/codex_mcp_launcher.py").write_text(
                "import shutil, tempfile\nprint('trusted launcher ran')\n")
            marker = home / "untrusted-module-ran"
            for name in ("shutil", "tempfile"):
                (home / (name + ".py")).write_text(
                    "with open(" + repr(str(marker)) + ", 'w') as stream: stream.write('ran')\n"
                    "raise RuntimeError('project runtime module executed')\n")
            result = self.run_native(home, {"enabled": True, "plugin_root": str(package)})
            self.assertFalse(marker.exists(), result.stderr)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("trusted launcher ran", result.stdout)

    def test_deeply_nested_settings_do_not_break_disabled_handshake(self):
        for overrides in ({}, {"SF_HEADLESS_360_ENABLED": "0"}):
            with self.subTest(overrides=overrides), tempfile.TemporaryDirectory() as raw:
                result = self.run_native(Path(raw), "[" * 4000 + "0" + "]" * 4000, overrides)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual([], json.loads(result.stdout.splitlines()[1])["result"]["tools"])

    def test_non_object_package_manifest_fails_with_setup_error(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            package = home / "package"
            (package / ".codex-plugin").mkdir(parents=True)
            (package / "scripts").mkdir()
            (package / ".codex-plugin/plugin.json").write_text('[]')
            (package / "scripts/codex_mcp_launcher.py").write_text("raise RuntimeError('must not run')")
            result = self.run_native(home, {"enabled": True, "plugin_root": str(package)})
        self.assertEqual(2, result.returncode)
        self.assertIn("setup required", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_legacy_boolean_optin_reports_registration_required(self):
        with tempfile.TemporaryDirectory() as raw:
            result = self.run_native(Path(raw), {"enabled": True})
        self.assertEqual(2, result.returncode)
        self.assertIn("setup required", result.stderr)

    def test_saved_disable_completes_handshake_without_registered_package(self):
        with tempfile.TemporaryDirectory() as raw:
            result = self.run_native(Path(raw), {"enabled": False, "plugin_root": "/missing-package"})
        self.assertEqual(0, result.returncode, result.stderr)
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([], replies[1]["result"]["tools"])

    def test_inconsistent_package_versions_are_never_executed(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            package = home / "old-plugin"
            (package / ".codex-plugin").mkdir(parents=True)
            (package / "scripts").mkdir()
            (package / ".codex-plugin/plugin.json").write_text(
                '{"name":"salesforce-development","version":"0.0.0"}')
            (package / ".claude-plugin").mkdir()
            (package / ".claude-plugin/plugin.json").write_text(
                '{"name":"salesforce-development","version":"9.8.7"}')
            (package / "scripts/codex_mcp_launcher.py").write_text(
                'raise RuntimeError("Must never run stale package")')
            result = self.run_native(home, {"enabled": True, "plugin_root": str(package)})
        self.assertEqual(2, result.returncode)
        self.assertIn("setup required", result.stderr)
        self.assertNotIn("Must never run stale package", result.stderr)

    def test_version_bump_needs_no_bootstrap_argument_change(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            package = home / "updated-plugin"
            for directory in (".codex-plugin", ".claude-plugin", "scripts"):
                (package / directory).mkdir(parents=True)
            for directory in (".codex-plugin", ".claude-plugin"):
                (package / directory / "plugin.json").write_text(json.dumps(
                    {"name": "salesforce-development", "version": "9.8.7"}))
            (package / "scripts/codex_mcp_launcher.py").write_text("print('updated launcher ran')")
            result = self.run_native(home, {"enabled": True, "plugin_root": str(package)})
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("updated launcher ran", result.stdout)

    def test_disabled_handshake_handles_closed_host_pipe(self):
        manifest = json.loads((SCRIPTS.parent / ".codex-plugin/plugin.json").read_text())
        with tempfile.TemporaryDirectory() as raw:
            with subprocess.Popen(
                    [sys.executable, *manifest["mcpServers"]["salesforce-headless-360"]["args"]],
                    env={"HOME": raw, "PATH": "/missing-node"}, cwd=raw,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True) as process:
                process.stdout.close()
                process.stdin.write('{"jsonrpc":"2.0","id":1,"method":"ping"}\n')
                process.stdin.close()
                error = process.stderr.read()
                self.assertEqual(0, process.wait(timeout=10), error)
        self.assertNotIn("Traceback", error)

    def test_disabled_handshake_skips_blank_lines(self):
        with tempfile.TemporaryDirectory() as raw:
            result = self.run_native(Path(raw), requests='\n  \n{"jsonrpc":"2.0","id":7,"method":"ping"}\n\n')
        self.assertEqual(0, result.returncode, result.stderr)
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([{"jsonrpc": "2.0", "id": 7, "result": {}}], replies)
        self.assertIn("no auth or network request made", result.stderr)


if __name__ == "__main__":
    unittest.main()
