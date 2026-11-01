"""Offline regression proof using the real Codex proxy-denial response shape."""
import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from _test_support import load_module

sfx = load_module(Path(__file__).resolve().parent.parent / "sf_context.py", "network_policy_context")
DENIAL = json.dumps({"status": "blocked", "host": "example.org", "reason": "not_allowed",
                     "decision": "deny", "source": "baseline_policy", "protocol": "http",
                     "port": 80, "message": "Domain not in allowlist."})


class NetworkPolicyTests(unittest.TestCase):
    def setUp(self):
        for patch in (mock.patch.object(sfx, "_plugin_client", return_value="codex"),
                      mock.patch.object(sfx, "_HOOK_DEADLINE", None)):
            patch.start()
            self.addCleanup(patch.stop)

    def probe(self, stdout="", stderr="", returncode=1, slug="metadata-experts"):
        with mock.patch.object(sfx, "build_command", return_value=["node", "fake-probe"]), \
                mock.patch.object(sfx.subprocess, "run", return_value=subprocess.CompletedProcess(
                    ["node"], returncode, stdout, stderr)):
            return sfx._probe_server_raw(slug)

    def test_real_proxy_json_denial_with_zero_process_exit_is_not_endpoint_health(self):
        data = self.probe(stdout=DENIAL, returncode=0)
        self.assertEqual("policy-blocked", data["state"])
        self.assertIn("Domain not in allowlist", data["detail"])
        self.assertNotIn("example.org", data["detail"])

    def test_https_connect_denial_preserves_marker_from_stderr(self):
        data = self.probe(stderr="HTTP/1.1 403 Forbidden\nx-proxy-error: blocked-by-allowlist\n")
        self.assertEqual("policy-blocked", data["state"])
        self.assertIn("blocked-by-allowlist", data["detail"])

    def test_philippe_error_is_recognized_without_echoing_hostname_or_other_output(self):
        data = self.probe(stderr='secret-token=DO-NOT-ECHO\nNetwork access to "example.org" was blocked: '
                                'domain is not on the allowlist for the current sandbox mode.')
        self.assertEqual("policy-blocked", data["state"])
        self.assertNotIn("DO-NOT-ECHO", str(data))
        self.assertNotIn("example.org", str(data))

    def test_wrapped_probe_error_is_detected(self):
        data = self.probe(stdout=json.dumps({"state": "auth", "httpStatus": 403,
                            "detail": "x-proxy-error: blocked-by-allowlist"}), returncode=0)
        self.assertEqual("policy-blocked", data["state"])

    def test_actual_bundled_probe_http_prefixed_detail_is_detected(self):
        wrapped = {"slug": "metadata-experts", "state": "auth", "httpStatus": 403,
                   "detail": "HTTP 403 " + DENIAL[:200], "org": "fixture"}
        data = self.probe(stdout=json.dumps(wrapped), returncode=0)
        self.assertEqual("policy-blocked", data["state"])

    def test_truncated_bundled_probe_detail_keeps_complete_policy_fields(self):
        body = json.loads(DENIAL)
        body["host"] = "long-example-host-for-a-salesforce-test-org.example.org"
        detail = "HTTP 403 " + json.dumps(body, separators=(",", ":"))[:200]
        wrapped = {"state": "auth", "httpStatus": 403, "detail": detail}
        data = self.probe(stdout=json.dumps(wrapped), returncode=0)
        self.assertEqual("policy-blocked", data["state"])

    def test_wrapped_partial_or_unrelated_denials_remain_auth(self):
        for body in ({"message": "Domain not in allowlist."},
                     {"status": "blocked", "decision": "deny", "reason": "not_allowed", "source": "salesforce"}):
            with self.subTest(body=body):
                wrapped = {"state": "auth", "detail": "HTTP 403 " + json.dumps(body)}
                self.assertEqual("auth", self.probe(stdout=json.dumps(wrapped), returncode=0)["state"])

    def test_generic_failures_are_not_diagnosed_as_codex_policy(self):
        for text in ("403 Forbidden", "ETIMEDOUT", "EPERM: operation not permitted",
                     "Domain not in allowlist.", "failed to fetch"):
            with self.subTest(text=text):
                self.assertIsNone(self.probe(stderr=text))
        data = self.probe(stdout=json.dumps({"state": "auth", "httpStatus": 403,
                                            "detail": "INSUFFICIENT_ACCESS"}), returncode=0)
        self.assertEqual("auth", data["state"])

    def test_timeout_retains_explicit_denial_but_generic_timeout_stays_unknown(self):
        for stderr, expected in (("x-proxy-error: blocked-by-allowlist", "policy-blocked"), ("", None)):
            with self.subTest(stderr=stderr), \
                    mock.patch.object(sfx, "build_command", return_value=["node"]), \
                    mock.patch.object(sfx.subprocess, "run", side_effect=subprocess.TimeoutExpired(
                        ["node"], 1, stderr=stderr.encode())):
                data = sfx._probe_server_raw("metadata-experts")
            self.assertEqual(expected, data["state"] if data else None)

    def test_json_denial_evidence_is_retained_in_row_without_host(self):
        with mock.patch.object(sfx, "_probe_server_raw", return_value={
                "state": "policy-blocked", "detail": "Domain not in allowlist."}):
            row = sfx._probe_server("metadata-experts")
        self.assertIn("Evidence: Domain not in allowlist.", row["message"])

    def test_unknown_probe_detail_is_not_echoed_as_policy_evidence(self):
        row = sfx._render_mcp_state_row("metadata-experts", "policy-blocked", "secret=DO-NOT-ECHO")
        self.assertNotIn("DO-NOT-ECHO", str(row))

    def test_live_success_is_not_overridden_by_a_stderr_warning(self):
        data = self.probe(stdout='{"state":"ok"}',
                          stderr="x-proxy-error: blocked-by-allowlist", returncode=0)
        self.assertEqual("ok", data["state"])

    def test_codex_specific_diagnosis_is_not_applied_to_claude(self):
        with mock.patch.object(sfx, "_plugin_client", return_value="claude"):
            self.assertIsNone(self.probe(stderr="x-proxy-error: blocked-by-allowlist"))

    def test_required_readiness_row_explains_check_scope_and_recovery(self):
        with mock.patch.object(sfx, "_probe_server_raw", return_value={
                "state": "policy-blocked", "detail": "x-proxy-error: blocked-by-allowlist"}):
            row = sfx._probe_server("metadata-experts")
        self.assertEqual("warn", row["status"])
        self.assertIn("Status check blocked by Codex network policy", row["message"])
        self.assertIn("endpoint health unverified", row["message"])
        self.assertIn("administrator", row["message"])
        self.assertIn("MCP connection separately", row["message"])
        self.assertIn("blocked-by-allowlist", row["message"])

    def test_required_summary_does_not_fall_back_to_cached_success(self):
        with mock.patch.object(sfx, "_probe_server_raw", return_value={"state": "policy-blocked"}), \
                mock.patch.object(sfx, "_sidecar_state_for_org", return_value="ok") as cached:
            summary = sfx._live_mcp_summary("current")
        cached.assert_not_called()
        self.assertIn("blocked by Codex network policy", summary)
        self.assertEqual(("⚠ check blocked", "warn"), sfx._mcp_indicator(summary))

    def test_status_band_includes_policy_recovery_for_required_servers(self):
        summary = sfx._summarize_mcp_states({"metadata-experts": "policy-blocked"})
        content = sfx._environment_content({"alias": "current"}, summary,
                                          h360_row={"status": "info", "message": "disabled locally"})
        text = "\n".join("".join(part[0] for part in line) for line in content)
        self.assertIn("administrator", text)
        self.assertIn("endpoint health unverified", text)
        self.assertNotIn("✓ connected", text)

    def test_optional_h360_does_not_reuse_cached_success_after_denial(self):
        with mock.patch.object(sfx, "_h360_local_optin", return_value=True), \
                mock.patch.object(sfx, "_codex_h360_launcher") as launcher, \
                mock.patch.object(sfx, "_probe_server_raw", return_value={
                    "state": "policy-blocked", "detail": "x-proxy-error: blocked-by-allowlist"}), \
                mock.patch.object(sfx, "_read_health_sidecar", return_value={
                    "org": "current", "state": "ok"}) as cached:
            launcher.return_value._headless_360_launcher_registered.return_value = True
            row = sfx._h360_status_row(active_org="current", probe=True)
        cached.assert_not_called()
        self.assertTrue(row["optional"])
        self.assertEqual("warn", row["status"])
        self.assertIn("endpoint health unverified", row["message"])
        self.assertNotIn("endpoint reachable", row["message"])

    def test_optional_grounding_does_not_reuse_cached_success_after_denial(self):
        with mock.patch.object(sfx, "_HOOK_DEADLINE", None), \
                mock.patch.object(sfx, "_probe_server_raw", return_value={
                    "state": "policy-blocked", "detail": "x-proxy-error: blocked-by-allowlist"}), \
                mock.patch.object(sfx, "_read_health_sidecar", return_value={
                    "org": "current", "state": "ok"}) as cached:
            row = sfx._grounding_status_row(active_org="current", probe=True)
        cached.assert_not_called()
        self.assertTrue(row["optional"])
        self.assertEqual("warn", row["status"])
        self.assertIn("endpoint health unverified", row["message"])
        self.assertNotIn("endpoint reachable", row["message"])


if __name__ == "__main__":
    unittest.main()
