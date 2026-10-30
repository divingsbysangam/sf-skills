#!/bin/bash
# Verify the disabled H360 entry completes MCP initialization without starting the proxy.
set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LAUNCHER="$PLUGIN_ROOT/scripts/sf-headless-360-optin.js"
NODE_BIN="${NODE_BIN:-node}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cat > "$TMP/fake-proxy.js" <<'JS'
const fs = require('node:fs');
fs.writeFileSync(process.env.SF_H360_PROXY_STARTED, 'started');
process.stdin.pipe(process.stdout);
JS

cat > "$TMP/sticky-proxy.js" <<'JS'
const fs = require('node:fs');
fs.writeFileSync(process.env.SF_H360_PROXY_PID, String(process.pid));
process.on('SIGTERM', () => {});
process.stdin.resume();
JS

cat > "$TMP/exit-3-proxy.js" <<'JS'
const fs = require('node:fs');
fs.writeFileSync(process.env.SF_H360_PROXY_STARTED, 'ready');
process.stdin.once('data', () => setTimeout(() => process.exit(3), 25));
JS

DISABLED_OUT="$(printf '%s\n' \
  '{"jsonrpc":"2.0","id":99,"method":' \
  'null' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","id":3,"method":"resources/list"}' \
  '{"jsonrpc":"2.0","id":4,"method":"prompts/list"}' \
  | SF_HEADLESS_360_ENABLED=disabled SF_H360_PROXY_STARTED="$TMP/disabled-started" \
    "$NODE_BIN" "$LAUNCHER" "$TMP/fake-proxy.js")"
printf '%s\n' "$DISABLED_OUT" | grep -q '"name":"salesforce-headless-360 (disabled)"'
printf '%s\n' "$DISABLED_OUT" | grep -q '"capabilities":{"tools":{}}'
printf '%s\n' "$DISABLED_OUT" | grep -q '"tools":\[\]'
printf '%s\n' "$DISABLED_OUT" | grep -q '"id":null,"error":{"code":-32700,"message":"Parse error"}'
printf '%s\n' "$DISABLED_OUT" | grep -q '"id":null,"error":{"code":-32600,"message":"Invalid Request"}'
printf '%s\n' "$DISABLED_OUT" | grep -q '"id":3,"error":{"code":-32601'
printf '%s\n' "$DISABLED_OUT" | grep -q '"id":4,"error":{"code":-32601'
test ! -e "$TMP/disabled-started"

SF_HEADLESS_360_ENABLED=enabled SF_H360_PROXY_STARTED="$TMP/enabled-started" \
  "$NODE_BIN" "$LAUNCHER" "$TMP/fake-proxy.js" </dev/null >/dev/null 2>&1
test "$(cat "$TMP/enabled-started")" = started

"$NODE_BIN" - "$LAUNCHER" "$TMP/sticky-proxy.js" "$TMP/sticky-proxy.pid" <<'JS'
const fs = require('node:fs');
const { spawn } = require('node:child_process');
const [launcher, proxy, pidFile] = process.argv.slice(2);
const wrapper = spawn(process.execPath, [launcher, proxy], {
  env: { ...process.env, SF_HEADLESS_360_ENABLED: 'enabled', SF_H360_PROXY_PID: pidFile },
  stdio: ['pipe', 'ignore', 'ignore'],
});
const waitFor = async (predicate, label) => {
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  throw new Error(`Timed out waiting for ${label}`);
};
(async () => {
  await waitFor(() => fs.existsSync(pidFile), 'proxy startup');
  process.kill(wrapper.pid, 'SIGTERM');
  await waitFor(() => wrapper.exitCode !== null, 'wrapper exit after proxy ignores SIGTERM');
  if (wrapper.exitCode !== 137) throw new Error(`Expected SIGKILL fallback exit code 137, got ${wrapper.exitCode}`);
  wrapper.stdin.destroy();
})().catch((error) => {
  if (fs.existsSync(pidFile)) {
    try { process.kill(Number(fs.readFileSync(pidFile, 'utf8')), 'SIGKILL'); } catch {}
  }
  wrapper.kill('SIGKILL');
  console.error(error.message);
  process.exitCode = 1;
});
JS

"$NODE_BIN" - "$LAUNCHER" "$TMP/exit-3-proxy.js" "$TMP/exit-3-started" <<'JS'
const fs = require('node:fs');
const { spawn } = require('node:child_process');
const [launcher, proxy, readyFile] = process.argv.slice(2);
const wrapper = spawn(process.execPath, [launcher, proxy], {
  env: { ...process.env, SF_HEADLESS_360_ENABLED: 'enabled', SF_H360_PROXY_STARTED: readyFile },
  stdio: ['pipe', 'ignore', 'pipe'],
});
let stderr = '';
let flood;
wrapper.stderr.setEncoding('utf8').on('data', (chunk) => { stderr += chunk; });
wrapper.stdin.on('error', () => {});
const waitForReady = async () => {
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) {
    if (fs.existsSync(readyFile)) return;
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  throw new Error('Timed out waiting for proxy startup');
};
(async () => {
  await waitForReady();
  flood = setInterval(() => {
    if (!wrapper.stdin.destroyed) wrapper.stdin.write(Buffer.alloc(64 * 1024));
  }, 5);
  await new Promise((resolve, reject) => {
    wrapper.once('error', reject);
    wrapper.once('close', resolve);
  });
  clearInterval(flood);
  if (wrapper.exitCode !== 3) throw new Error(`Expected proxy exit code 3, got ${wrapper.exitCode}`);
  if (/EPIPE|Unhandled 'error'/.test(stderr)) throw new Error(`Unexpected launcher EPIPE error: ${stderr}`);
})().catch((error) => {
  clearInterval(flood);
  wrapper.kill('SIGKILL');
  console.error(error.message);
  process.exitCode = 1;
});
JS

"$NODE_BIN" - "$LAUNCHER" "$TMP/fake-proxy.js" <<'JS'
const { spawn } = require('node:child_process');
const [launcher, proxy] = process.argv.slice(2);
const runner = `process.execPath = '/missing/node'; process.argv = [process.argv[0], ${JSON.stringify(launcher)}, ${JSON.stringify(proxy)}]; require(${JSON.stringify(launcher)});`;
const wrapper = spawn(process.execPath, ['-e', runner], {
  env: { ...process.env, SF_HEADLESS_360_ENABLED: 'enabled' },
  stdio: ['pipe', 'ignore', 'pipe'],
});
let stderr = '';
wrapper.stderr.setEncoding('utf8').on('data', (chunk) => { stderr += chunk; });
wrapper.on('close', (code) => {
  if (code !== 1) throw new Error(`Expected spawn failure exit code 1, got ${code}`);
  if (!stderr.includes('could not start proxy')) throw new Error(`Missing spawn failure diagnostic: ${stderr}`);
});
setTimeout(() => {
  if (wrapper.exitCode === null) {
    wrapper.kill('SIGKILL');
    console.error('Launcher hung after a proxy spawn failure');
    process.exitCode = 1;
  }
}, 10000).unref();
JS

echo 'PASS (disabled handshake; unsupported methods; no proxy start; spawn failure; signal escalation; exit code; EPIPE handling)'
