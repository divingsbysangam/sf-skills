import json
import os
from pathlib import Path
import runpy
import sys

# Native compatibility manifests do not expand plugin-root placeholders.
# Setup registers the installed package explicitly; never scan or guess a cache.
try:
    with (Path.home() / '.sf/salesforce-development/headless-360.json').open(encoding='utf-8') as stream:
        raw = stream.read(8193)
    config = json.loads(raw) if len(raw) <= 8192 else {}
    if not isinstance(config, dict):
        config = {}
except (OSError, UnicodeError, ValueError, RecursionError):
    config = {}
value = os.environ.get('SF_HEADLESS_360_ENABLED')
enabled = value.strip().lower() in ('1', 'true', 'enabled', 'on') if value is not None else config.get('enabled') is True
root = config.get('plugin_root')
entry = None
if enabled and isinstance(root, str) and Path(root).is_absolute():
    try:
        package = Path(root).resolve(strict=True)
        manifest = json.loads((package / '.codex-plugin/plugin.json').read_text(encoding='utf-8'))
        claude = json.loads((package / '.claude-plugin/plugin.json').read_text(encoding='utf-8'))
        launcher = (package / 'scripts/codex_mcp_launcher.py').resolve(strict=True)
        if (isinstance(manifest, dict) and manifest.get('name') == 'salesforce-development'
                and isinstance(claude, dict) and claude.get('name') == manifest['name']
                and isinstance(manifest.get('version'), str) and bool(manifest['version'])
                and manifest['version'] == claude.get('version')
                and package in launcher.parents and launcher.is_file()):
            entry = launcher
    except (OSError, UnicodeError, ValueError, RecursionError):
        pass
if entry is not None:
    sys.argv = [str(entry), sys.argv[1]]
    runpy.run_path(str(entry), run_name='__main__')
    raise SystemExit(0)
if enabled:
    sys.stderr.write('Headless 360 setup required: ask Codex to enable Headless 360 again to register this installed plugin version.\n')
    raise SystemExit(2)


def reply(value):
    try:
        print(json.dumps(value), flush=True)
    except BrokenPipeError:
        # The host closed its pipe; avoid an extra traceback during shutdown.
        os._exit(0)

sys.stderr.write('Headless 360 disabled; no auth or network request made.\n')
# Default-off still completes an empty MCP handshake without auth or network work.
for line in sys.stdin:
    if not line.strip():
        continue
    try:
        request = json.loads(line)
    except (ValueError, RecursionError):
        reply({'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Parse error'}})
        continue
    if not isinstance(request, dict):
        reply({'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'Invalid Request'}})
        continue
    if request.get('id') is None:
        continue
    method = request.get('method')
    if method == 'initialize':
        params = request.get('params')
        params = params if isinstance(params, dict) else {}
        result = {'protocolVersion': params.get('protocolVersion', '2025-06-18'),
                  'capabilities': {'tools': {}}, 'serverInfo': {'name': 'salesforce-headless-360 (disabled)', 'version': '1.0.0'}}
    elif method == 'tools/list':
        result = {'tools': []}
    elif method == 'ping':
        result = {}
    else:
        reply({'jsonrpc': '2.0', 'id': request['id'], 'error': {'code': -32601, 'message': 'Headless 360 is disabled'}})
        continue
    reply({'jsonrpc': '2.0', 'id': request['id'], 'result': result})
