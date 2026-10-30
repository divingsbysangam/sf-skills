#!/usr/bin/env python3
"""Focused regression tests for the native Codex compatibility projection."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from _test_support import isolate_host_markers


def setUpModule():
    isolate_host_markers()


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PLUGIN_ROOT.parents[2]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


launcher = load_module("codex_mcp_launcher", PLUGIN_ROOT / "scripts/codex_mcp_launcher.py")
sf_context = load_module("sf_context_codex_compat", PLUGIN_ROOT / "scripts/sf_context.py")
artifact = load_module("verify_codex_artifact", PLUGIN_ROOT / "scripts/verify_codex_artifact.py")


class ManifestTests(unittest.TestCase):
    def test_manifest_matches_primary_version_and_exact_server_set(self):
        codex = json.loads((PLUGIN_ROOT / ".codex-plugin/plugin.json").read_text())
        claude = json.loads((PLUGIN_ROOT / ".claude-plugin/plugin.json").read_text())
        self.assertEqual(claude["version"], codex["version"])
        self.assertEqual("./skills/", codex["skills"])
        self.assertEqual(
            {"salesforce-metadata-experts", "salesforce-metadata-grounding", "salesforce-headless-360", "salesforce-lsp"},
            set(codex["mcpServers"]),
        )
        for name, server in codex["mcpServers"].items():
            self.assertEqual("python3", server["command"])
            if name == "salesforce-headless-360":
                self.assertEqual(["-I", "-c"], server["args"][:2])
                self.assertEqual([name], server["args"][3:])
                self.assertNotIn("cwd", server)
            else:
                self.assertEqual(["scripts/codex_mcp_launcher.py", name], server["args"])
        h360 = codex["mcpServers"]["salesforce-headless-360"]
        self.assertIn("SF_HEADLESS_360_ENABLED", h360["env_vars"])
        self.assertFalse(
            (PLUGIN_ROOT / "hooks/hooks.json").exists(),
            "a conventional second hook manifest would also be auto-loaded by Claude",
        )
        self.assertEqual("./hooks/codex-hooks.json", codex["hooks"])
        codex_hooks = json.loads(
            (PLUGIN_ROOT / "hooks/codex-hooks.json").read_text(encoding="utf-8")
        )
        prompt_hooks = codex_hooks["hooks"]["UserPromptSubmit"]
        self.assertEqual(1, len(prompt_hooks))
        handler = prompt_hooks[0]["hooks"][0]
        self.assertEqual("command", handler["type"])
        self.assertIn("SF_AGENT_CLIENT=codex", handler["command"])
        self.assertIn("${PLUGIN_ROOT}", handler["command"])
        self.assertIn("sf-context prompt-dispatch", handler["command"])

    def test_marketplace_points_to_this_plugin(self):
        marketplace = json.loads((REPO_ROOT / ".claude-plugin/marketplace.json").read_text())
        matches = [
            row for row in marketplace["plugins"]
            if row.get("name") == "salesforce-development"
        ]
        self.assertEqual(1, len(matches))
        self.assertEqual(PLUGIN_ROOT, (REPO_ROOT / matches[0]["source"]).resolve())

    def test_source_tree_is_a_complete_installable_artifact(self):
        result = artifact.verify(PLUGIN_ROOT)
        self.assertGreater(result["skills"], 0)

    def test_artifact_rejects_a_missing_runtime_target(self):
        with tempfile.TemporaryDirectory() as raw:
            copied = Path(raw) / "plugin"
            for relative in artifact.REQUIRED_FILES:
                target = copied / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(PLUGIN_ROOT / relative, target)
            (copied / "skills/example").mkdir(parents=True)
            (copied / "scripts/codex_mcp_launcher.py").unlink()
            with self.assertRaisesRegex(ValueError, "codex_mcp_launcher.py"):
                artifact.verify(copied)


class LauncherTests(unittest.TestCase):
    def test_thread_cwd_wins_over_stale_desktop_environment(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plugin = root / "plugin"
            active = root / "active"
            stale = root / "stale"
            for path in (plugin, active, stale):
                path.mkdir()
            for path in (active, stale):
                (path / "sfdx-project.json").write_text("{}")
            env = {"PWD": str(stale), "INIT_CWD": str(stale)}
            self.assertEqual(active.resolve(), launcher.resolve_project_cwd(
                plugin_root=plugin, environment=env, process_cwd=active))
            (active / "sfdx-project.json").unlink()
            self.assertIsNone(launcher.resolve_project_cwd(
                plugin_root=plugin, environment=env, process_cwd=active))
            self.assertIsNone(launcher.resolve_project_cwd(
                plugin_root=plugin,
                environment={**env, "CODEX_WORKSPACE_ROOT": str(active)},
                process_cwd=stale))

    def test_disabled_h360_launcher_does_not_require_a_project_or_org(self):
        with mock.patch.dict(launcher.os.environ, {}, clear=True), \
                mock.patch.object(launcher, "_saved_headless_360_enabled", return_value=False), \
                mock.patch.object(launcher, "resolve_node", return_value="/test/bin/node"), \
                mock.patch.object(launcher.os, "execvp") as execvp:
            self.assertEqual(0, launcher.main(["salesforce-headless-360"]))
        args = execvp.call_args.args[1]
        self.assertEqual("/test/bin/node", args[0])
        self.assertTrue(args[1].endswith("sf-headless-360-optin.js"))
        self.assertTrue(args[2].endswith("sf-mcp-proxy.bundled.js"))
        self.assertEqual("headless-360", args[3])

    def test_target_org_requires_one_explicit_value(self):
        self.assertIsNone(launcher.resolve_target_org({}))
        self.assertIsNone(launcher.resolve_target_org({
            "SF_TARGET_ORG": "one", "SFDX_TARGET_ORG": "two",
        }))
        self.assertEqual("one", launcher.resolve_target_org({"SF_TARGET_ORG": " one "}))
        self.assertEqual("one", launcher.resolve_target_org({
            "SF_TARGET_ORG": "one", "SFDX_TARGET_ORG": "one",
        }))

    def test_target_org_falls_back_to_one_project_config_value(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            self.assertIsNone(launcher.resolve_target_org({}, project))
            (project / ".sf").mkdir()
            (project / ".sf/config.json").write_text(json.dumps({"target-org": "dev"}))
            self.assertEqual("dev", launcher.resolve_target_org({}, project))
            self.assertEqual("env", launcher.resolve_target_org({"SF_TARGET_ORG": "env"}, project))
            self.assertIsNone(launcher.resolve_target_org(
                {"SF_TARGET_ORG": "a", "SFDX_TARGET_ORG": "b"}, project))
            (project / ".sfdx").mkdir()
            (project / ".sfdx/sfdx-config.json").write_text(
                json.dumps({"defaultusername": "other"}))
            # The current CLI's config wins over the legacy file when they differ.
            self.assertEqual("dev", launcher.resolve_target_org({}, project))
            (project / ".sf/config.json").unlink()
            self.assertEqual("other", launcher.resolve_target_org({}, project))
            (project / ".sfdx/sfdx-config.json").write_text(
                json.dumps({"defaultusername": "dev"}))
            self.assertEqual("dev", launcher.resolve_target_org({}, project))

    def test_missing_target_org_message_says_how_to_set_it(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            (project / "sfdx-project.json").write_text("{}")
            with mock.patch.dict(launcher.os.environ, {"CODEX_WORKSPACE_ROOT": str(project)},
                                 clear=True), \
                    mock.patch("sys.stderr", new_callable=__import__("io").StringIO) as stderr:
                self.assertEqual(2, launcher.main(["salesforce-metadata-experts"]))
            self.assertIn("sf config set target-org", stderr.getvalue())
            self.assertIn("SF_TARGET_ORG", stderr.getvalue())

    def test_workspace_is_an_authority_boundary(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plugin = root / "plugin"
            workspace = root / "workspace"
            unrelated = root / "unrelated"
            plugin.mkdir()
            workspace.mkdir()
            unrelated.mkdir()
            (unrelated / "sfdx-project.json").write_text("{}")
            found = launcher.resolve_project_cwd(
                plugin_root=plugin,
                environment={
                    "CODEX_WORKSPACE_ROOT": str(workspace),
                    "INIT_CWD": str(unrelated),
                },
            )
            self.assertIsNone(found)

    def test_invalid_workspace_boundary_does_not_fall_back_to_pwd(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plugin = root / "plugin"
            project = root / "project"
            plugin.mkdir()
            project.mkdir()
            (project / "sfdx-project.json").write_text("{}")
            self.assertIsNone(launcher.resolve_project_cwd(
                plugin_root=plugin,
                environment={
                    "CODEX_WORKSPACE_ROOT": str(root / "missing"),
                    "PWD": str(project),
                },
            ))

    def test_nearest_nested_project_is_bounded(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plugin = root / "plugin"
            workspace = root / "workspace"
            project = workspace / "a" / "project"
            plugin.mkdir()
            project.mkdir(parents=True)
            (project / "sfdx-project.json").write_text("{}")
            self.assertEqual(
                project.resolve(),
                launcher.resolve_project_cwd(
                    plugin_root=plugin,
                    environment={"CODEX_WORKSPACE_ROOT": str(workspace)},
                ),
            )

    def test_workspace_prefers_active_project_and_rejects_ambiguity(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plugin = root / "plugin"
            plugin.mkdir()
            workspace = root / "workspace"
            for name in ("a", "z"):
                project = workspace / name
                project.mkdir(parents=True)
                (project / "sfdx-project.json").write_text("{}")
            active = workspace / "z" / "force-app"
            active.mkdir()
            env = {"CODEX_WORKSPACE_ROOT": str(workspace), "PWD": str(active)}
            self.assertEqual((workspace / "z").resolve(), launcher.resolve_project_cwd(
                plugin_root=plugin, environment=env))
            env.pop("PWD")
            self.assertIsNone(launcher.resolve_project_cwd(plugin_root=plugin, environment=env))

    def test_workspace_cannot_select_ancestor_or_bundled_project(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "sfdx-project.json").write_text("{}")
            workspace = root / "workspace"
            plugin = workspace / "plugin"
            plugin.mkdir(parents=True)
            (plugin / "sfdx-project.json").write_text("{}")
            self.assertIsNone(launcher.resolve_project_cwd(plugin_root=plugin,
                environment={"CODEX_WORKSPACE_ROOT": str(workspace), "PWD": str(root)}))

    def test_main_passes_project_and_explicit_org_to_node(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            (project / "sfdx-project.json").write_text("{}")
            env = {
                "CODEX_WORKSPACE_ROOT": str(project),
                "SF_TARGET_ORG": "scratch",
            }
            with mock.patch.dict(launcher.os.environ, env, clear=True), mock.patch.object(
                launcher.os, "chdir"
            ) as chdir, mock.patch.object(launcher, "resolve_node", return_value="/test/bin/node"), \
                    mock.patch.object(launcher.os, "execvp", side_effect=OSError("stop")) as execvp:
                self.assertEqual(2, launcher.main(["salesforce-metadata-experts"]))
                self.assertEqual("scratch", launcher.os.environ["SFDX_TARGET_ORG"])
            chdir.assert_called_once_with(project.resolve())
            self.assertEqual("/test/bin/node", execvp.call_args.args[0])



class CodexRecommendationBoundaryTests(unittest.TestCase):
    NAMES = ("agentforce-adlc", "experience-cms", "service-engagement", "experience-react")
    DENIED = frozenset({"agentforce-adlc", "experience-cms", "service-engagement"})

    def setUp(self):
        denied = mock.patch.object(sf_context, "_CODEX_UNVERIFIED_WORKFLOW_PLUGINS", self.DENIED)
        denied.start()
        self.addCleanup(denied.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for attribute in ("_PLUGIN_PROPOSAL_DIR", "_PLUGIN_FLOW_DIR",
                          "_PLUGIN_INSTALL_PENDING_DIR", "_PLUGIN_LAST_OFFER_DIR"):
            patch = mock.patch.object(sf_context, attribute, root / attribute)
            patch.start()
            self.addCleanup(patch.stop)

    def test_codex_prompt_dispatch_forces_plain_renderer_output(self):
        def orientation(*, payload, prompt_context):
            self.assertEqual("1", os.environ.get("NO_COLOR"))
            return 0

        with mock.patch.dict(os.environ, {"SF_AGENT_CLIENT": "codex", "NO_COLOR": ""}), \
                mock.patch.object(sf_context, "_prompt_context", return_value=None), \
                mock.patch.object(sf_context, "_prune_prompt_runtime"), \
                mock.patch.object(sf_context, "cmd_orientation_paint", side_effect=orientation), \
                mock.patch.object(sf_context.sys, "stdin", io.StringIO("{}")):
            self.assertEqual(0, sf_context.cmd_prompt_dispatch())

    def catalog(self):
        import types
        plugins = [{"name": name, "match": {"description": name}} for name in self.NAMES]
        scorer = mock.Mock(return_value=[
            types.SimpleNamespace(plugin=p, band="high", score=5.0) for p in plugins
        ])
        return types.SimpleNamespace(load_catalog=lambda root: {"plugins": plugins},
                                     score_prompt_against_catalog=scorer)

    def test_all_proposal_surfaces_exclude_unverified_codex_plugins_after_scoring(self):
        for client in ("codex", "claude"):
            for surface in sf_context._PLUGIN_PROPOSAL_SURFACES:
                with self.subTest(client=client, surface=surface):
                    catalog = self.catalog()
                    with mock.patch.object(sf_context, "_load_plugin_catalog_module", return_value=catalog), \
                         mock.patch.object(sf_context, "_enabled_plugin_names", return_value=set()), \
                         mock.patch.object(sf_context, "_plugin_display_name", return_value="salesforce-development"), \
                         mock.patch.object(sf_context, "_plugin_match_sensitivity", return_value="standard"), \
                         mock.patch.object(sf_context, "_load_plugin_proposals", return_value={}), \
                         mock.patch.object(sf_context, "_save_plugin_proposals"):
                        matches = sf_context._plugin_catalog_match("build a workflow", "", surface=surface, client=client)
                    expected = ["experience-react"] if client == "codex" else list(self.NAMES)
                    self.assertEqual(expected, [m["name"] for m in matches])
                    scored_names = [p["name"] for p in catalog.score_prompt_against_catalog.call_args.args[1]["plugins"]]
                    self.assertEqual(list(self.NAMES), scored_names)

    def test_overview_filters_available_codex_plugins_but_preserves_installed_inventory(self):
        for client in ("codex", "claude"):
            with self.subTest(client=client), mock.patch.dict("os.environ", {"SF_AGENT_CLIENT": client}), \
                 mock.patch.object(sf_context, "_load_plugin_catalog_module", return_value=self.catalog()), \
                 mock.patch.object(sf_context, "_enabled_plugin_names", return_value={"agentforce-adlc"}), \
                 mock.patch.object(sf_context, "_plugin_display_name", return_value="salesforce-development"), \
                 mock.patch.object(sf_context, "_installed_skill_count", return_value=1), \
                 mock.patch.object(sf_context, "_banner_provenance", return_value={}):
                facts = sf_context._capability_overview_facts()
            self.assertIn("agentforce-adlc", [p["name"] for p in facts["installed"]])
            expected = ["experience-react"] if client == "codex" else list(self.NAMES[1:])
            self.assertEqual(expected, [p["name"] for p in facts["available"]])

    def test_refused_persisted_proposal_does_not_block_next_eligible_discovery(self):
        import io
        session = "codex:thread"
        for name in self.NAMES[:3]:
            with self.subTest(name=name):
                sf_context._save_plugin_proposals(session, {
                    name: {"confidence": "high", "surface": "user-prompt"},
                    "experience-react": {"confidence": "high", "surface": "user-prompt"},
                })
                sf_context._open_plugin_flow(session, [name], "user-prompt")
                sf_context._select_plugin_flow(session, name, "selected")
                entry = {"source": f"./plugins/builder/{name}", "origin": "local"}
                parsed = {"name": name, "confirm": None, "decline": False,
                          "accept_proposed": True, "session_id": "thread", "client": "codex"}
                with mock.patch.object(sf_context, "_plugin_install_args", return_value=parsed), \
                     mock.patch.object(sf_context, "_plugin_install_lookup", return_value=sf_context.PluginInstallLookupResult(entry, "ok")), \
                     mock.patch.object(sf_context, "_perform_plugin_install") as install, \
                     mock.patch.object(sf_context, "_fire_plugin_install_result"), \
                     mock.patch("sys.stderr", new_callable=io.StringIO):
                    self.assertEqual(2, sf_context.cmd_plugin_install([]))
                install.assert_not_called()
                self.assertIsNone(sf_context._load_plugin_flow(session))
                self.assertEqual({"experience-react"}, set(sf_context._load_plugin_proposals(session)))
                with mock.patch.object(sf_context, "_load_plugin_catalog_module", return_value=self.catalog()), \
                     mock.patch.object(sf_context, "_enabled_plugin_names", return_value=set()), \
                     mock.patch.object(sf_context, "_plugin_display_name", return_value="salesforce-development"), \
                     mock.patch.object(sf_context, "_plugin_match_sensitivity", return_value="standard"), \
                     mock.patch.object(sf_context, "_fire_plugin_telemetry_event"), \
                     mock.patch("sys.stdout", new_callable=io.StringIO) as output:
                    self.assertEqual(0, sf_context.cmd_plugin_match(["--client", "codex", "--session-id", "thread", "--json", "build a workflow"]))
                self.assertEqual(["experience-react"], [m["name"] for m in json.loads(output.getvalue())["matches"]])
                sf_context._clear_plugin_flow(session)

    def test_refusal_preserves_unrelated_selected_flow(self):
        import io
        session = "codex:thread"
        sf_context._save_plugin_proposals(session, {
            name: {"confidence": "high", "surface": "user-prompt"}
            for name in ("agentforce-adlc", "experience-react")
        })
        sf_context._open_plugin_flow(session, ["agentforce-adlc", "experience-react"], "user-prompt")
        sf_context._select_plugin_flow(session, "experience-react", "selected")
        entry = {"source": "./plugins/builder/agentforce-adlc", "origin": "local"}
        with mock.patch.object(sf_context, "_plugin_install_lookup", return_value=sf_context.PluginInstallLookupResult(entry, "ok")), \
             mock.patch.object(sf_context, "_fire_plugin_install_result"), \
             mock.patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(2, sf_context.cmd_plugin_install(["agentforce-adlc", "--accept-proposed", "--client", "codex", "--session-id", "thread"]))
        flow = sf_context._load_plugin_flow(session)
        self.assertEqual(["experience-react"], flow["candidates"])
        self.assertEqual("experience-react", flow["selected"])
        self.assertEqual("selected", flow["state"])

    def test_stale_codex_proposal_is_refused_but_explicit_confirmed_install_remains(self):
        import io
        for name in self.NAMES[:3]:
            entry = {"source": f"./plugins/builder/{name}", "origin": "local"}
            nonce = sf_context._plugin_install_nonce(name, entry)
            for proposed in (True, False):
                parsed = {"name": name, "confirm": None if proposed else nonce,
                          "decline": False, "accept_proposed": proposed,
                          "session_id": "thread", "client": "codex"}
                with self.subTest(name=name, proposed=proposed), \
                     mock.patch.object(sf_context, "_plugin_install_args", return_value=parsed), \
                     mock.patch.object(sf_context, "_plugin_install_lookup", return_value=sf_context.PluginInstallLookupResult(entry, "ok")), \
                     mock.patch.object(sf_context, "_consume_plugin_install_pending", return_value=True), \
                     mock.patch.object(sf_context, "_clear_plugin_install_pending"), \
                     mock.patch.object(sf_context, "_fire_plugin_install_result"), \
                     mock.patch.object(sf_context, "_perform_plugin_install", return_value=0) as install, \
                     mock.patch("sys.stderr", new_callable=io.StringIO) as error:
                    self.assertEqual(2 if proposed else 0, sf_context.cmd_plugin_install([]))
                    if proposed:
                        install.assert_not_called()
                        self.assertIn("end-to-end Codex workflows are not yet verified", error.getvalue())
                    else:
                        install.assert_called_once_with(name, entry, "codex:thread", "codex")


_AMBIENT_HOST_KEYS = (
    "SF_AGENT_CLIENT", "CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_HOME",
    "CLAUDE_CODE_SESSION_ID", "CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CONFIG_DIR",
)


class CodexHostInferenceStateTests(unittest.TestCase):
    """W-24599110: Codex is recognised from its own env markers alone (no
    SF_AGENT_CLIENT), and plugins enabled only in Claude's settings.json are
    not treated as installed there. Ambient host markers are cleared so the
    result is identical inside a Claude Code session and in CI."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / "home"
        (self.home / ".claude").mkdir(parents=True)
        (self.home / ".claude" / "settings.json").write_text(
            json.dumps({"enabledPlugins": {"experience-react@salesforce": True}}),
            encoding="utf-8")
        self.codex_home = self.root / "codex-home"
        self.codex_home.mkdir()
        env = {k: v for k, v in os.environ.items() if k not in _AMBIENT_HOST_KEYS}
        env["HOME"] = str(self.home)
        env["CODEX_HOME"] = str(self.codex_home)
        for patch in (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(sf_context, "_plugin_root",
                              return_value=str(self.root / "worktree-plugin")),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def _no_codex_cli(self):
        return mock.patch.object(
            sf_context.subprocess, "run",
            return_value=mock.Mock(returncode=0, stdout='{"installed": []}'))

    def test_codex_detected_from_thread_id_without_sf_agent_client(self):
        for marker in ("CODEX_THREAD_ID", "CODEX_SESSION_ID"):
            with self.subTest(marker=marker), mock.patch.dict(os.environ, {marker: "t1"}):
                self.assertNotIn("SF_AGENT_CLIENT", os.environ)
                self.assertEqual("codex", sf_context._inferred_plugin_host())
                self.assertEqual("codex", sf_context._plugin_client())
                self.assertEqual("codex", sf_context._plugin_state_host())

    def test_codex_detected_from_plugin_root_under_codex_plugin_cache(self):
        cached = self.codex_home / "plugins" / "cache" / "mkt" / "salesforce-development"
        cached.mkdir(parents=True)
        with mock.patch.object(sf_context, "_plugin_root", return_value=str(cached)):
            self.assertEqual("codex", sf_context._inferred_plugin_host())
            self.assertEqual("codex", sf_context._plugin_state_host())

    def test_claude_only_enabled_plugin_is_not_installed_under_codex_markers(self):
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "t1"}), self._no_codex_cli():
            enabled = sf_context._enabled_plugin_names()
        self.assertNotIn("experience-react", enabled or set())

    def test_codex_own_config_is_the_install_source_under_codex_markers(self):
        if sf_context.tomllib is None:
            self.skipTest("tomllib unavailable")
        (self.codex_home / "config.toml").write_text(
            '[plugins."service-engagement@mkt"]\nenabled = true\n', encoding="utf-8")
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "t1"}):
            self.assertEqual({"service-engagement"}, sf_context._enabled_plugin_names())

    def test_codex_marker_beats_a_stale_claude_client_claim(self):
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "t1", "SF_AGENT_CLIENT": "claude"}):
            self.assertEqual("unknown", sf_context._plugin_state_host())
            self.assertIsNone(sf_context._enabled_plugin_names())

    def test_claude_default_without_markers_still_reads_claude_settings(self):
        os.environ.pop("CODEX_HOME")
        self.assertEqual("claude", sf_context._plugin_state_host())
        self.assertEqual({"experience-react"}, sf_context._enabled_plugin_names())

    def test_conflicting_markers_do_not_read_claude_settings(self):
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "t1", "CLAUDECODE": "1"}), \
                mock.patch("pathlib.Path.read_text", side_effect=AssertionError("read settings")):
            self.assertEqual("unknown", sf_context._plugin_state_host())
            self.assertIsNone(sf_context._enabled_plugin_names())


class RealCatalogHostTests(unittest.TestCase):
    """Exercise shipped catalog scoring before host eligibility is applied."""

    def test_all_catalog_prompts_preserve_claude_and_codex_eligibility(self):
        catalog = json.loads((PLUGIN_ROOT / "catalog/plugins.json").read_text(encoding="utf-8"))
        plugins = [row for row in catalog["plugins"] if row["name"] != "salesforce-development"]
        names = {row["name"] for row in plugins}
        excluded = set()
        self.assertEqual(excluded, sf_context._CODEX_UNVERIFIED_WORKFLOW_PLUGINS)
        self.assertTrue({"agentforce-adlc", "experience-cms", "service-engagement"} <= names)
        self.assertEqual(names, {name for name in names
                                if sf_context._plugin_recommendation_supported(name, "claude")})
        self.assertEqual(names - excluded, {name for name in names
                                           if sf_context._plugin_recommendation_supported(name, "codex")})
        with mock.patch.object(sf_context, "_plugin_display_name", return_value="salesforce-development"), \
                mock.patch.object(sf_context, "_load_plugin_proposals", side_effect=lambda _: {}), \
                mock.patch.object(sf_context, "_save_plugin_proposals"):
            for plugin in plugins:
                self.assertTrue(plugin["match"]["examplePrompts"], plugin["name"])
                for prompt in plugin["match"]["examplePrompts"]:
                    for sensitivity in ("standard", "strict", "off"):
                        for surface in sorted(sf_context._PLUGIN_PROPOSAL_SURFACES):
                            for inventory, enabled in (
                                ("uninstalled", set()), ("subject-installed", {plugin["name"]}),
                                ("all-installed", names), ("unknown", None),
                            ):
                                with self.subTest(plugin=plugin["name"], prompt=prompt,
                                                  sensitivity=sensitivity, surface=surface,
                                                  inventory=inventory), \
                                        mock.patch.object(sf_context, "_plugin_match_sensitivity", return_value=sensitivity), \
                                        mock.patch.object(sf_context, "_enabled_plugin_names", return_value=enabled):
                                    claude = sf_context._plugin_catalog_match(prompt, "", surface=surface, client="claude")
                                    codex = sf_context._plugin_catalog_match(prompt, "", surface=surface, client="codex")
                                    self.assertEqual([row for row in claude if row["name"] not in excluded], codex)
                                    if inventory == "subject-installed":
                                        self.assertNotIn(plugin["name"], [row["name"] for row in claude + codex])
                                    if inventory == "all-installed" or sensitivity == "off":
                                        self.assertEqual([], claude)
                                        self.assertEqual([], codex)
                                    if sensitivity == "standard" and surface == "discovery-command" and inventory == "uninstalled":
                                        self.assertIn(plugin["name"], [row["name"] for row in claude])
                                        self.assertEqual(plugin["name"] not in excluded,
                                                         plugin["name"] in [row["name"] for row in codex])


    def test_real_prompt_dispatch_offers_eligible_plugins_for_each_host(self):
        cases = (
            ("author an .agent definition", "agentforce-adlc"),
            ("search Salesforce CMS for an existing media asset", "experience-cms"),
            ("set up a Salesforce Digital Engagement messaging channel", "service-engagement"),
            ("build me an LWC datatable", "experience-lwc"),
            ("build a Salesforce iOS app with Mobile SDK", "mobile-development"),
        )
        runtime_dirs = (
            "_PROMPT_RUNTIME_DIR", "_WELCOME_MARKER_DIR", "_PLUGIN_PROPOSAL_DIR",
            "_PLUGIN_FLOW_DIR", "_PLUGIN_INSTALL_PENDING_DIR", "_PLUGIN_LAST_OFFER_DIR",
            "_DRIVE_MARKER_DIR",
        )
        for client in ("claude", "codex"):
            for index, (prompt, target) in enumerate(cases):
                with self.subTest(client=client, target=target), \
                        tempfile.TemporaryDirectory() as raw, contextlib.ExitStack() as stack:
                    root = Path(raw).resolve()
                    project = root / "project"
                    project.mkdir()
                    (project / "sfdx-project.json").write_text("{}")
                    home = root / "home"
                    home.mkdir()
                    stack.callback(os.chdir, Path.cwd())
                    os.chdir(project)
                    for name in runtime_dirs:
                        stack.enter_context(mock.patch.object(sf_context, name, root / name))
                    stack.enter_context(mock.patch.dict(os.environ, {
                        "HOME": str(home), "SF_AGENT_CLIENT": client,
                        "DO_NOT_TRACK": "true", "SF_DISABLE_TELEMETRY": "true",
                    }, clear=True))
                    for name, value in (("_enabled_plugin_names", {"salesforce-development"}),
                                        ("_plugin_match_sensitivity", "standard"), ("_ui_mode", "off")):
                        stack.enter_context(mock.patch.object(sf_context, name, return_value=value))
                    cli = stack.enter_context(mock.patch.object(
                        sf_context.subprocess, "run", side_effect=AssertionError("real subprocess")))
                    session = "dispatch-" + str(index)
                    payload = {"cwd": str(project), "prompt": prompt, "session_id": session,
                               "prompt_id": "p1", "hook_event_name": "UserPromptSubmit"}
                    stack.enter_context(mock.patch.object(sf_context.sys, "stdin", io.StringIO(json.dumps(payload))))
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        self.assertEqual(0, sf_context.cmd_prompt_dispatch())
                    reply = json.loads(output.getvalue())
                    cli.assert_not_called()
                    host_session = sf_context._plugin_session_id(session, client)
                    flow = sf_context._load_plugin_flow(host_session)
                    candidates = flow["candidates"] if flow else []
                    expected = client == "claude" or target not in sf_context._CODEX_UNVERIFIED_WORKFLOW_PLUGINS
                    self.assertEqual(expected, target in candidates, reply)
                    if expected:
                        self.assertTrue(reply.get("systemMessage"), reply)
                    if client == "codex":
                        excluded = sf_context._CODEX_UNVERIFIED_WORKFLOW_PLUGINS
                        self.assertFalse(excluded.intersection(candidates))
                        self.assertFalse(excluded.intersection(sf_context._load_plugin_proposals(host_session)))


class BuilderPluginManifestParityTests(unittest.TestCase):
    """Every builder plugin must be installable from Codex."""

    # Plugins that ship only commands; they select ./codex-commands/ instead of ./skills/.
    COMMANDS_ONLY = frozenset({"salesforce-test-drive"})

    def test_every_builder_plugin_has_a_matching_codex_manifest(self):
        builder = PLUGIN_ROOT.parent
        checked = 0
        for claude_path in sorted(builder.glob("*/.claude-plugin/plugin.json")):
            plugin_dir = claude_path.parent.parent
            with self.subTest(plugin=plugin_dir.name):
                codex_path = plugin_dir / ".codex-plugin" / "plugin.json"
                self.assertTrue(codex_path.is_file(), "missing .codex-plugin/plugin.json")
                claude = json.loads(claude_path.read_text(encoding="utf-8"))
                codex = json.loads(codex_path.read_text(encoding="utf-8"))
                self.assertEqual(claude["name"], codex["name"])
                self.assertEqual(claude["version"], codex["version"])
                skills_dir = plugin_dir / "skills"
                if plugin_dir.name in self.COMMANDS_ONLY:
                    self.assertFalse(skills_dir.is_dir(), "now ships skills; select ./skills/")
                    self.assertEqual("./codex-commands/", codex.get("commands"))
                    self.assertTrue(any((plugin_dir / "codex-commands").glob("*.md")))
                else:
                    self.assertEqual("./skills/", codex.get("skills"))
                    self.assertTrue(skills_dir.is_dir())
                checked += 1
        self.assertGreater(checked, 10)


class DynamicLoadingTests(unittest.TestCase):
    def test_codex_install_subprocess_keeps_isolated_home(self):
        runner = mock.Mock(return_value=mock.Mock(returncode=0, stdout="", stderr=""))
        ok, _ = sf_context._run_plugin_install_step(
            ["codex", "plugin", "add", "agentforce-adlc@salesforce", "--json"],
            env={
                "PATH": "/usr/bin",
                "CODEX_HOME": "/tmp/isolated-codex",
                "UNRELATED_SECRET": "do-not-forward",
            },
            runner=runner,
        )
        self.assertTrue(ok)
        self.assertEqual(
            {"PATH": "/usr/bin", "CODEX_HOME": "/tmp/isolated-codex"},
            runner.call_args.kwargs["env"],
        )

    @unittest.skipIf(sf_context.tomllib is None, "TOML parser unavailable; CLI fallback is tested separately")
    def test_codex_inventory_reads_enabled_plugins_from_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir) / "config.toml"
            config.write_text(
                "[plugins.'enabled@market']\nenabled = true\n"
                "[plugins.'disabled@market']\nenabled = false\n",
                encoding="utf-8",
            )
            runner = mock.Mock()
            self.assertEqual(
                {"enabled"},
                sf_context._enabled_plugin_names(
                    "codex", runner=runner, codex_config=config,
                ),
            )
        runner.assert_not_called()

    def test_codex_inventory_uses_cli_when_tomllib_is_unavailable(self):
        result = mock.Mock(returncode=0, stdout=json.dumps({"installed": [
            {"name": "enabled", "installed": True, "enabled": True},
        ]}))
        runner = mock.Mock(return_value=result)
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.object(sf_context, "tomllib", None):
            config = Path(temp_dir) / "config.toml"
            config.write_text("[plugins.'enabled@market']\nenabled = true\n", encoding="utf-8")
            self.assertEqual({"enabled"}, sf_context._enabled_plugin_names(
                "codex", runner=runner, codex_config=config,
            ))
        runner.assert_called_once()

    def test_codex_inventory_falls_back_to_cli_for_missing_config(self):
        result = mock.Mock(
            returncode=0,
            stdout=json.dumps({"installed": [
                {"name": "enabled", "installed": True, "enabled": True},
                {"name": "disabled", "installed": True, "enabled": False},
            ]}),
        )
        runner = mock.Mock(return_value=result)
        self.assertEqual(
            {"enabled"},
            sf_context._enabled_plugin_names(
                "codex", runner=runner,
                codex_config=Path("/missing/codex/config.toml"),
            ),
        )
        runner.assert_called_once_with(
            ["codex", "plugin", "list", "--json"],
            capture_output=True, text=True, timeout=3, check=False,
        )

    def test_codex_inventory_falls_back_when_toml_parser_is_unavailable(self):
        result = mock.Mock(returncode=0, stdout=json.dumps({"installed": [
            {"name": "enabled", "installed": True, "enabled": True},
            {"name": "disabled", "installed": True, "enabled": False},
        ]}))
        with tempfile.TemporaryDirectory() as raw, mock.patch.object(sf_context, "tomllib", None):
            config = Path(raw) / "config.toml"
            config.write_text("[plugins.'enabled@market']\nenabled = true\n", encoding="utf-8")
            runner = mock.Mock(return_value=result)
            self.assertEqual({"enabled"}, sf_context._enabled_plugin_names(
                "codex", runner=runner, codex_config=config))
            runner.assert_called_once_with(
                ["codex", "plugin", "list", "--json"],
                capture_output=True, text=True, timeout=3, check=False)

    def test_codex_plugin_match_uses_codex_inventory_and_session_namespace(self):
        with mock.patch.object(
            sf_context, "_plugin_session_id", return_value="codex:thread-1",
        ) as session_id, mock.patch.object(
            sf_context, "_load_plugin_flow", return_value=None,
        ), mock.patch.object(
            sf_context, "_plugin_catalog_match", return_value=[],
        ) as catalog_match, mock.patch(
            "sys.stdout", new_callable=__import__("io").StringIO,
        ):
            self.assertEqual(0, sf_context.cmd_plugin_match([
                "--client", "codex", "build", "an", "agent",
            ]))
        session_id.assert_called_once_with("", "codex")
        catalog_match.assert_called_once_with(
            "build an agent", "codex:thread-1",
            surface="discovery-command", client="codex",
        )

    def test_plugin_match_rejects_an_unknown_client(self):
        with mock.patch("sys.stderr", new_callable=__import__("io").StringIO) as stderr:
            self.assertEqual(2, sf_context.cmd_plugin_match([
                "--client", "other", "build", "an", "agent",
            ]))
        self.assertIn("invalid client", stderr.getvalue())

    def test_client_state_is_namespaced_without_changing_claude(self):
        with mock.patch.dict(sf_context.os.environ, {}, clear=True):
            self.assertEqual("session", sf_context._plugin_session_id("session", "claude"))
            self.assertEqual("codex:session", sf_context._plugin_session_id("session", "codex"))
            self.assertEqual(
                "session with spaces",
                sf_context._plugin_session_id("session with spaces", "claude"),
            )

    def test_environment_session_id_is_validated(self):
        with mock.patch.dict(
            sf_context.os.environ,
            {"CLAUDE_CODE_SESSION_ID": "session with spaces"},
            clear=True,
        ):
            self.assertEqual("", sf_context._plugin_session_id(client="claude"))
        with mock.patch.dict(
            sf_context.os.environ,
            {"CODEX_SESSION_ID": "session with spaces"},
            clear=True,
        ):
            self.assertEqual("", sf_context._plugin_session_id(client="codex"))

    def test_codex_session_prefers_thread_then_session(self):
        both = {"CODEX_THREAD_ID": "thread-2", "CODEX_SESSION_ID": "root-1"}
        with mock.patch.dict(sf_context.os.environ, both, clear=True):
            self.assertEqual("codex:thread-2", sf_context._plugin_session_id(client="codex"))
            self.assertEqual("codex:event", sf_context._plugin_session_id("event", "codex"))
        with mock.patch.dict(sf_context.os.environ, {"CODEX_SESSION_ID": "root-1"}, clear=True):
            self.assertEqual("codex:root-1", sf_context._plugin_session_id(client="codex"))
        invalid_thread = {"CODEX_THREAD_ID": "bad id", "CODEX_SESSION_ID": "root-1"}
        with mock.patch.dict(sf_context.os.environ, invalid_thread, clear=True):
            self.assertEqual("", sf_context._plugin_session_id(client="codex"))

    def test_codex_hook_uses_absolute_payload_cwd_without_changing_claude(self):
        with mock.patch.object(sf_context.os, "chdir") as chdir:
            sf_context._activate_host_working_directory({"cwd": "/workspace"}, "codex")
            chdir.assert_called_once_with(Path("/workspace"))
            chdir.reset_mock()
            sf_context._activate_host_working_directory({"cwd": "/workspace"}, "claude")
            sf_context._activate_host_working_directory({"cwd": "relative"}, "codex")
            chdir.assert_not_called()

    def test_codex_preview_has_no_reload_plugins_guidance(self):
        rendered = sf_context._render_plugin_install_dry_run(
            "experience-react",
            {"source": "./plugins/builder/experience-react"},
            "a" * 64,
            "codex",
        )
        self.assertIn("--client codex", rendered)
        self.assertIn("start a new Codex session", rendered)
        self.assertNotIn("/reload-plugins", rendered)

    def test_codex_route_notes_use_client_specific_command_and_handoff(self):
        install = sf_context._plugin_install_route_note(
            "experience-react", client="codex",
        )
        confirm = sf_context._plugin_confirm_route_note(
            "experience-react", "a" * 64, client="codex",
        )
        self.assertIn("--accept-proposed --client codex", install)
        self.assertIn("--confirm " + "a" * 64 + " --client codex", confirm)
        self.assertNotIn("/reload-plugins", confirm)

    def test_codex_selected_proposal_correlates_through_install(self):
        name = "experience-react"
        session = "codex:thread-1"
        entry = {"source": f"./plugins/builder/{name}", "origin": "local"}
        with mock.patch.object(
            sf_context, "_plugin_install_args", return_value={
                "name": name, "confirm": None, "decline": False,
                "accept_proposed": True, "session_id": "thread-1", "client": "codex",
            },
        ), mock.patch.object(
            sf_context, "_plugin_install_lookup",
            return_value=sf_context.PluginInstallLookupResult(entry, "ok"),
        ), mock.patch.object(
            sf_context, "_selected_plugin_proposal", return_value={"surface": "user-prompt"},
        ) as selected, mock.patch.object(
            sf_context, "_perform_plugin_install", return_value=0,
        ) as perform:
            self.assertEqual(0, sf_context.cmd_plugin_install([]))
        selected.assert_called_once_with(name, session)
        perform.assert_called_once_with(name, entry, session, "codex")

    def test_confirmation_requires_matching_session_pending_marker(self):
        entry = {"source": {"url": "https://example.invalid/plugin"}, "origin": "external"}
        nonce = sf_context._plugin_install_nonce("external-plugin", entry)
        parsed = {
            "name": "external-plugin", "confirm": nonce, "decline": False,
            "accept_proposed": False, "session_id": "other", "client": "codex",
        }
        with mock.patch.object(sf_context, "_plugin_install_args", return_value=parsed), mock.patch.object(
            sf_context, "_plugin_install_lookup",
            return_value=sf_context.PluginInstallLookupResult(entry, "ok"),
        ), mock.patch.object(sf_context, "_consume_plugin_install_pending", return_value=False), mock.patch.object(
            sf_context, "_perform_plugin_install"
        ) as perform, mock.patch.object(sf_context, "_clear_plugin_install_pending"), mock.patch.object(
            sf_context, "_select_plugin_flow"
        ), mock.patch.object(sf_context, "_fire_plugin_install_result"), mock.patch(
            "sys.stderr", new_callable=__import__("io").StringIO
        ):
            self.assertEqual(3, sf_context.cmd_plugin_install([]))
        perform.assert_not_called()

    def test_confirmation_aborts_when_atomic_pending_claim_is_lost(self):
        entry = {"source": {"url": "https://example.invalid/plugin"}, "origin": "external"}
        name = "external-plugin"
        nonce = sf_context._plugin_install_nonce(name, entry)
        parsed = {
            "name": name, "confirm": nonce, "decline": False,
            "accept_proposed": False, "session_id": "thread", "client": "codex",
        }
        with mock.patch.object(sf_context, "_plugin_install_args", return_value=parsed), mock.patch.object(
            sf_context, "_plugin_install_lookup",
            return_value=sf_context.PluginInstallLookupResult(entry, "ok"),
        ), mock.patch.object(
            sf_context, "_consume_plugin_install_pending", return_value=False,
        ) as consume, mock.patch.object(
            sf_context, "_perform_plugin_install"
        ) as perform, mock.patch.object(sf_context, "_clear_plugin_install_pending"), mock.patch.object(
            sf_context, "_select_plugin_flow"
        ), mock.patch.object(sf_context, "_fire_plugin_install_result"), mock.patch(
            "sys.stderr", new_callable=__import__("io").StringIO
        ):
            self.assertEqual(3, sf_context.cmd_plugin_install([]))
        consume.assert_called_once_with(
            "codex:thread", name, nonce, require_user_confirmation=True,
        )
        perform.assert_not_called()

    def _codex_confirm(self, name, entry, nonce, session):
        parsed = {
            "name": name, "confirm": nonce, "decline": False,
            "accept_proposed": False, "session_id": session, "client": "codex",
        }
        with mock.patch.object(sf_context, "_plugin_install_args", return_value=parsed), mock.patch.object(
            sf_context, "_plugin_install_lookup",
            return_value=sf_context.PluginInstallLookupResult(entry, "ok"),
        ), mock.patch.object(
            sf_context, "_perform_plugin_install", return_value=0,
        ) as perform, mock.patch.object(sf_context, "_select_plugin_flow"), mock.patch.object(
            sf_context, "_fire_plugin_install_result"
        ), mock.patch("sys.stderr", new_callable=__import__("io").StringIO) as stderr:
            code = sf_context.cmd_plugin_install([])
        return code, perform.called, stderr.getvalue()

    def test_codex_confirm_requires_the_users_own_reply_after_the_preview(self):
        name = "external-plugin"
        entry = {"source": {"url": "https://example.invalid/plugin"}, "origin": "external"}
        nonce = sf_context._plugin_install_nonce(name, entry)
        session = "codex:thread"
        with tempfile.TemporaryDirectory() as raw, mock.patch.object(
            sf_context, "_PLUGIN_INSTALL_PENDING_DIR", Path(raw) / "pending",
        ):
            # A model-run dry run followed directly by --confirm is refused.
            self.assertTrue(sf_context._save_plugin_install_pending(session, name, nonce))
            code, performed, err = self._codex_confirm(name, entry, nonce, "thread")
            self.assertEqual((3, False), (code, performed))
            self.assertIn("ask the user to confirm", err)

            # The user's own confirmation reply, recorded by UserPromptSubmit, authorizes it once.
            self.assertTrue(sf_context._save_plugin_install_pending(session, name, nonce))
            self.assertTrue(sf_context._confirm_plugin_install_pending(session, name, nonce))
            self.assertEqual((0, True), self._codex_confirm(name, entry, nonce, "thread")[:2])
            self.assertEqual((3, False), self._codex_confirm(name, entry, nonce, "thread")[:2])

            # A confirmation recorded before a newer preview does not carry over.
            self.assertTrue(sf_context._save_plugin_install_pending(session, name, nonce))
            self.assertTrue(sf_context._confirm_plugin_install_pending(session, name, nonce))
            self.assertTrue(sf_context._save_plugin_install_pending(session, name, nonce))
            self.assertEqual((3, False), self._codex_confirm(name, entry, nonce, "thread")[:2])

            # Another session's confirmation is not this session's.
            self.assertTrue(sf_context._save_plugin_install_pending("codex:other", name, nonce))
            self.assertTrue(sf_context._confirm_plugin_install_pending("codex:other", name, nonce))
            self.assertTrue(sf_context._save_plugin_install_pending(session, name, nonce))
            self.assertEqual((3, False), self._codex_confirm(name, entry, nonce, "thread")[:2])

    def test_codex_prompt_confirmation_stamps_the_pending_preview(self):
        name = "external-plugin"
        entry = {"source": {"url": "https://example.invalid/plugin"}, "origin": "external"}
        nonce = sf_context._plugin_install_nonce(name, entry)
        with tempfile.TemporaryDirectory() as raw, mock.patch.object(
            sf_context, "_PLUGIN_INSTALL_PENDING_DIR", Path(raw) / "pending",
        ), mock.patch.dict(sf_context.os.environ, {"SF_AGENT_CLIENT": "codex"}, clear=False), \
                mock.patch.object(sf_context, "_select_plugin_flow"), \
                mock.patch.object(sf_context, "_record_prompt_text"), \
                mock.patch("sys.stdout", new_callable=__import__("io").StringIO) as stdout:
            self.assertTrue(sf_context._save_plugin_install_pending("codex:thread", name, nonce))
            self.assertEqual(0, sf_context.cmd_orientation_paint(
                {"prompt": "yes", "session_id": "thread"}, None,
            ))
            self.assertIn("--confirm " + nonce + " --client codex", stdout.getvalue())
            self.assertEqual((0, True), self._codex_confirm(name, entry, nonce, "thread")[:2])

    def test_codex_prompt_hook_finishes_inside_one_budget_with_a_slow_cli(self):
        import subprocess
        import sys
        import time
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project = root / "project"
            (project / ".sf").mkdir(parents=True)
            (project / "sfdx-project.json").write_text('{"packageDirectories":[{"path":"force-app"}]}')
            (project / ".sf/config.json").write_text('{"target-org":"dev"}')
            bin_dir = root / "bin"
            bin_dir.mkdir()
            for tool in ("sf", "codex", "git", "node"):
                fake = bin_dir / tool
                fake.write_text("#!/bin/sh\nexec sleep 20\n")
                fake.chmod(0o755)
            env = {
                "PATH": str(bin_dir) + ":/usr/bin:/bin",
                "HOME": str(root / "home"),
                "TMPDIR": str(root / "tmp"),
                "SF_AGENT_CLIENT": "codex",
                "SFDX_DISABLE_TELEMETRY": "true",
            }
            (root / "tmp").mkdir()
            payload = {"hook_event_name": "UserPromptSubmit", "session_id": "thread",
                       "cwd": str(project), "prompt": "what is my salesforce status?"}
            started = time.monotonic()
            result = subprocess.run(
                [sys.executable, str(PLUGIN_ROOT / "scripts/sf_context.py"), "prompt-dispatch"],
                input=json.dumps(payload), capture_output=True, text=True, cwd=project,
                env=env, timeout=30, start_new_session=True,
            )
            elapsed = time.monotonic() - started
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertLess(elapsed, 9.5, result.stdout)

    def test_install_argv_and_handoff_are_client_specific(self):
        entry = {"source": "./plugins/builder/experience-react", "origin": "local"}
        with mock.patch.object(sf_context, "_ensure_salesforce_marketplace_registered"), mock.patch.object(
            sf_context, "_run_plugin_install_step", return_value=(True, {})
        ) as run, mock.patch.object(sf_context, "_select_plugin_flow"), mock.patch.object(
            sf_context, "_plugin_install_fire_installed"
        ), mock.patch.object(sf_context, "_plugin_install_fire_loaded"), mock.patch.object(
            sf_context, "_clear_plugin_install_pending"
        ), mock.patch.object(sf_context, "_load_plugin_flow", return_value=None), mock.patch.object(
            sf_context, "_fire_plugin_install_result"
        ), mock.patch("sys.stdout", new_callable=__import__("io").StringIO) as stdout:
            self.assertEqual(0, sf_context._perform_plugin_install(
                "experience-react", entry, "codex:s", "codex"
            ))
        self.assertEqual(
            ["codex", "plugin", "add", "experience-react@salesforce", "--json"],
            run.call_args.args[0],
        )
        self.assertIn("Start a new Codex session", stdout.getvalue())
        self.assertNotIn("/reload-plugins", stdout.getvalue())


class NaturalLanguageInstallRoutingTests(unittest.TestCase):
    def test_the_plugin_phrasing_is_an_install_acceptance(self):
        for prompt in (
            "I want to install the experience-react plugin",
            "install experience-react plugin",
            "please install the experience-react",
        ):
            self.assertTrue(
                sf_context._install_verb_governs_name(prompt, "experience-react"), prompt)
        self.assertFalse(sf_context._install_verb_governs_name(
            "add experience-react to .gitignore", "experience-react"))
        self.assertTrue(sf_context._decline_verb_governs_name(
            "I don't want to install the experience-react plugin", "experience-react"))

    def test_lets_phrasing_is_an_action_request(self):
        for prompt in (
            "Let's apply branding to a scratch-org Experience site",
            "Let's build an Experience Cloud site with React",
        ):
            self.assertTrue(sf_context._plugin_prompt_requests_action(prompt), prompt)
        for prompt in ("Let's talk about Apex", "let's see what happens"):
            self.assertFalse(sf_context._plugin_prompt_requests_action(prompt), prompt)

    def test_recommendations_adapter_escalates_preference_writes(self):
        # off/on/set write ~/.sf, outside the workspace-write sandbox (W-24599110).
        body = (PLUGIN_ROOT / "codex-commands" / "plugin-recommendations.md").read_text(
            encoding="utf-8")
        self.assertIn('sandbox_permissions="require_escalated"', body)
        self.assertIn("status only reads and needs no escalation", body)

    def test_codex_route_notes_request_escalation_only_on_codex(self):
        codex = sf_context._plugin_install_route_note("experience-react", client="codex")
        self.assertIn("require_escalated", codex)
        self.assertNotIn("require_escalated", sf_context._plugin_install_route_note(
            "experience-react", client="claude"))
        confirm = sf_context._plugin_confirm_route_note("experience-react", "n0nce", "codex")
        self.assertIn("require_escalated", confirm)
        self.assertIn("--confirm n0nce --client codex with Bash", confirm)
        self.assertNotIn("require_escalated", sf_context._plugin_confirm_route_note(
            "experience-react", "n0nce", "claude"))


class TestDriveClientAwareSurfaceTests(unittest.TestCase):
    """On Codex the test-drive CTA and resume nudge must not name Claude-only commands."""

    CLAUDE_ONLY = ("/salesforce-test-drive:start", "/reload-plugins")

    def _env(self, client):
        return mock.patch.dict(os.environ, {"SF_AGENT_CLIENT": client}, clear=False)

    def test_overview_cta_on_codex_has_no_slash_command(self):
        installed = {"version": "1.0", "available": [],
                     "installed": [{"name": "salesforce-development", "skills": 3},
                                   {"name": "salesforce-test-drive"}]}
        with self._env("codex"):
            plain = "\n".join(sf_context._render_capability_overview_lines(installed, color=False))
        self.assertIn("Salesforce Test Drive start workflow", plain)
        for token in self.CLAUDE_ONLY:
            self.assertNotIn(token, plain)

    def test_overview_cta_on_claude_is_unchanged(self):
        installed = {"version": "1.0", "available": [],
                     "installed": [{"name": "salesforce-test-drive"}]}
        with self._env("claude"):
            plain = "\n".join(sf_context._render_capability_overview_lines(installed, color=False))
        self.assertIn("Run /salesforce-test-drive:start", plain)

    def test_resume_nudge_on_codex_names_no_claude_commands(self):
        with self._env("codex"):
            model, visible = sf_context._prompt_test_drive_resume_surface("service-help-agent")
        for text in (model, visible):
            self.assertIn("service-help-agent", text)
            self.assertIn("new Codex session", text)
            for token in self.CLAUDE_ONLY:
                self.assertNotIn(token, text)
        self.assertRegex(model, r"(?i)do not run it")
        self.assertTrue(all(sf_context._terminal_cell_width(line) <= 80
                            for line in visible.split("\n")))

    def test_resume_nudge_on_claude_is_unchanged(self):
        with self._env("claude"):
            model, visible = sf_context._prompt_test_drive_resume_surface("service-help-agent")
        self.assertIn("/salesforce-test-drive:start service-help-agent", visible)
        self.assertIn("/reload-plugins", model)


class HostCommandTextTests(unittest.TestCase):
    """Codex projects commands as skills, so `/salesforce-development:*` must not reach it."""

    SLASH = "/salesforce-development:"

    def _env(self, client):
        return mock.patch.dict(os.environ, {"SF_AGENT_CLIENT": client}, clear=False)

    def test_rewrites_bare_wrapped_and_overview_references_on_codex(self):
        cases = {
            "run /salesforce-development:discover.":
                "run the Salesforce Development discover workflow.",
            "or run /salesforce-development:discover overview.":
                "or run the Salesforce Development discover workflow for an overview.",
            "(or the /salesforce-development:setup command) to":
                "(or the Salesforce Development setup workflow) to",
            "run `/salesforce-development:feedback` to rate":
                "run the Salesforce Development feedback workflow to rate",
        }
        with self._env("codex"):
            for text, expected in cases.items():
                self.assertEqual(expected, sf_context._host_text(text))

    def test_claude_text_is_untouched(self):
        text = "run /salesforce-development:discover overview or `/salesforce-development:setup`"
        with self._env("claude"):
            self.assertEqual(text, sf_context._host_text(text))

    def test_emit_rewrites_every_model_and_visible_field_on_codex(self):
        text = "run /salesforce-development:status"
        for kwargs in ({}, {"system_message": text}, {"decision": "deny", "reason": text}):
            with self.subTest(kwargs=kwargs), self._env("codex"), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                sf_context.emit("SessionStart", text, **kwargs)
            self.assertNotIn(self.SLASH, out.getvalue())
            self.assertIn("Salesforce Development status workflow", out.getvalue())

    def test_discovery_pointer_and_org_hints_fit_the_frame_on_codex(self):
        with self._env("codex"):
            pointer = sf_context._host_text(sf_context.DISCOVERY_POINTER)
            hints = [sf_context._clip(sf_context._host_text(f"none set — {self.SLASH}login"), 73)]
            wayfinding = [line for line in sf_context._wayfinding_footer(color=False)]
        self.assertNotIn(self.SLASH, pointer)
        self.assertLessEqual(sf_context._terminal_cell_width(pointer), 80)
        self.assertFalse(any(self.SLASH in h for h in hints))
        self.assertFalse(any(self.SLASH in line for line in wayfinding))

    def test_install_command_names_the_plugin_on_each_host(self):
        with self._env("codex"):
            self.assertEqual("ask Codex to install the foo plugin (guarded plugin-install flow)",
                             sf_context._plugin_install_command("foo"))
        with self._env("claude"):
            self.assertEqual("/salesforce-development:plugin-install foo",
                             sf_context._plugin_install_command("foo"))

    def test_plugin_recommendations_command_output_has_no_slash_form_on_codex(self):
        with self._env("codex"), mock.patch.object(sf_context, "_clear_plugin_match_override",
                                                   return_value=True), \
                mock.patch.object(sf_context, "_fire_plugin_recommendation_configured"), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            sf_context.cmd_plugin_match_config(["on"])
        self.assertNotIn(self.SLASH, out.getvalue())
        self.assertIn("ask Codex to run the Salesforce Development plugin-recommendations workflow",
                      out.getvalue())


if __name__ == "__main__":
    unittest.main()
