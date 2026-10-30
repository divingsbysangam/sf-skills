#!/usr/bin/env node
'use strict';

const { spawn } = require('node:child_process');
const readline = require('node:readline');
const os = require('node:os');

const SHUTDOWN_GRACE_MS = 1000;

function enabled(value) {
  return ['1', 'true', 'enabled', 'on'].includes(String(value || '').trim().toLowerCase());
}

function write(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

async function runDisabled() {
  process.stderr.write('[salesforce-headless-360] disabled by plugin configuration; no auth or network request made\n');
  const rl = readline.createInterface({ input: process.stdin });
  for await (const line of rl) {
    if (!line.trim()) continue;
    let request;
    try {
      request = JSON.parse(line);
    } catch {
      // JSON-RPC parse errors have a null id because the request id is unavailable.
      write({
        jsonrpc: '2.0',
        id: null,
        error: { code: -32700, message: 'Parse error' },
      });
      continue;
    }
    if (request === null || typeof request !== 'object' || Array.isArray(request)) {
      write({
        jsonrpc: '2.0',
        id: null,
        error: { code: -32600, message: 'Invalid Request' },
      });
      continue;
    }
    const { id, method } = request;
    if (id === undefined || id === null) continue;
    if (method === 'initialize') {
      write({
        jsonrpc: '2.0',
        id,
        result: {
          protocolVersion: request.params?.protocolVersion || '2025-06-18',
          capabilities: { tools: {} },
          serverInfo: { name: 'salesforce-headless-360 (disabled)', version: '1.0.0' },
        },
      });
    } else if (method === 'tools/list') {
      write({ jsonrpc: '2.0', id, result: { tools: [] } });
    } else if (method === 'ping') {
      write({ jsonrpc: '2.0', id, result: {} });
    } else {
      write({
        jsonrpc: '2.0',
        id,
        error: { code: -32601, message: 'Headless 360 is disabled by plugin configuration' },
      });
    }
  }
}

async function main() {
  const proxyEntry = process.argv[2];
  const proxyArgs = process.argv.slice(3);
  if (!proxyEntry) {
    process.stderr.write('Usage: sf-headless-360-optin <proxy-entry> <proxy-args...>\n');
    process.exitCode = 2;
    return;
  }
  if (!enabled(process.env.SF_HEADLESS_360_ENABLED)) {
    await runDisabled();
    return;
  }

  const child = spawn(process.execPath, [proxyEntry, ...proxyArgs], { stdio: ['pipe', 'pipe', 'pipe'] });
  process.stdin.pipe(child.stdin);
  child.stdout.pipe(process.stdout);
  child.stderr.pipe(process.stderr);
  let shutdownTimer;
  let finished = false;
  const finish = (code) => {
    if (finished) return;
    finished = true;
    clearTimeout(shutdownTimer);
    process.stdin.unpipe(child.stdin);
    process.stdin.pause();
    process.stdin.destroy();
    child.stdin.destroy();
    process.removeListener('SIGINT', forwardSignal);
    process.removeListener('SIGTERM', forwardSignal);
    process.exitCode = code;
  };
  const forwardSignal = (signal) => {
    process.stdin.unpipe(child.stdin);
    process.stdin.pause();
    child.kill(signal);
    clearTimeout(shutdownTimer);
    shutdownTimer = setTimeout(() => child.kill('SIGKILL'), SHUTDOWN_GRACE_MS);
  };
  process.on('SIGINT', forwardSignal);
  process.on('SIGTERM', forwardSignal);
  child.stdin.on('error', (error) => {
    if (error.code === 'EPIPE') {
      process.stdin.unpipe(child.stdin);
      process.stdin.pause();
      return;
    }
    process.stderr.write(`[salesforce-headless-360] proxy input failed: ${error.message}\n`);
  });
  process.stdout.on('error', (error) => {
    if (error.code === 'EPIPE') {
      forwardSignal('SIGTERM');
      return;
    }
    process.stderr.write(`[salesforce-headless-360] host output failed: ${error.message}\n`);
  });
  child.on('error', (error) => {
    process.stderr.write(`[salesforce-headless-360] could not start proxy: ${error.message}\n`);
    finish(1);
  });
  child.on('close', (code, signal) => {
    const signalExitCode = signal ? 128 + (os.constants.signals[signal] || 0) : 0;
    finish(code === null ? signalExitCode : code);
  });
}

main().catch((error) => {
  process.stderr.write(`[salesforce-headless-360] ${error.message || String(error)}\n`);
  process.exitCode = 1;
});
