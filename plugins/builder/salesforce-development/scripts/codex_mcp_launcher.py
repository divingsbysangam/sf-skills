#!/usr/bin/env python3
"""Launch a bundled MCP server in one bounded project and explicit org."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile


SERVERS = {
    "salesforce-metadata-experts": ("scripts/sf-mcp-proxy.bundled.js", "metadata-experts"),
    "salesforce-metadata-grounding": ("scripts/sf-mcp-proxy.bundled.js", "metadata-grounding"),
    "salesforce-headless-360": (
        "scripts/sf-headless-360-optin.js",
        "scripts/sf-mcp-proxy.bundled.js",
        "headless-360",
    ),
    "salesforce-lsp": ("bin/sf-lsp-host.bundled.js",),
}


def _headless_360_enabled(environment: dict[str, str]) -> bool:
    """Explicit environment overrides the saved user opt-in; otherwise default off."""
    if "SF_HEADLESS_360_ENABLED" in environment:
        return environment["SF_HEADLESS_360_ENABLED"].strip().lower() in {
            "1", "true", "enabled", "on",
        }
    return _saved_headless_360_enabled()


def _headless_360_config_path() -> Path:
    return Path.home() / ".sf/salesforce-development/headless-360.json"


def _saved_headless_360_config() -> dict:
    try:
        with _headless_360_config_path().open(encoding="utf-8") as stream:
            text = stream.read(8193)
        if len(text) > 8192:
            return {}
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except (OSError, UnicodeError, ValueError, RecursionError):
        return {}


def _saved_headless_360_enabled() -> bool:
    return _saved_headless_360_config().get("enabled") is True


def _headless_360_launcher_registered() -> bool:
    """The saved registration must name this installed, version-consistent package."""
    package = Path(__file__).resolve().parent.parent
    if _saved_headless_360_config().get("plugin_root") != str(package):
        return False
    try:
        native = json.loads((package / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        claude = json.loads((package / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
        return (isinstance(native, dict) and isinstance(claude, dict)
                and native.get("name") == claude.get("name") == "salesforce-development"
                and isinstance(native.get("version"), str) and bool(native["version"])
                and native["version"] == claude.get("version")
                and (package / "scripts/codex_mcp_launcher.py").is_file())
    except (OSError, UnicodeError, ValueError, RecursionError):
        return False


def configure_headless_360(action: str) -> int:
    """Save only the local Codex preference. Never contact or configure an org."""
    if action not in {"enable", "disable", "status"}:
        print("Usage: codex_mcp_launcher.py --headless-360 enable|disable|status", file=sys.stderr)
        return 2
    path = _headless_360_config_path()
    if action != "status":
        temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({"enabled": action == "enable",
                           "plugin_root": str(Path(__file__).resolve().parent.parent)}, stream)
                stream.write("\n")
            temporary.replace(path)
        except OSError:
            print("codex_mcp_launcher: could not save the local H360 setting", file=sys.stderr)
            return 2
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    print(json.dumps({"enabled": _headless_360_enabled(dict(os.environ)),
                      "saved_enabled": _saved_headless_360_enabled(),
                      "launcher_registered": _headless_360_launcher_registered(),
                      "environment_override": "SF_HEADLESS_360_ENABLED" in os.environ,
                      "restart_required": action != "status"}))
    return 0


def resolve_node(environment: dict[str, str]) -> str | None:
    """Honor PATH first, then known user/system installs without running a shell."""
    original = environment.get("PATH", os.defpath)
    node = shutil.which("node", path=original)
    if node:
        return node
    home = Path.home()
    directories = (home / ".local/bin", home / ".volta/bin", home / ".asdf/shims",
                   home / ".local/share/mise/shims", Path("/opt/homebrew/bin"),
                   Path("/usr/local/bin"))
    search_path = os.pathsep.join([original, *(str(path) for path in directories if path.is_dir())])
    node = shutil.which("node", path=search_path)
    if node:
        # The proxy also launches CLI commands; retain the discovered bin directories.
        environment["PATH"] = search_path
    return node


def _resolved_dir(raw: str | None) -> Path | None:
    if not raw:
        return None
    try:
        candidate = Path(raw).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return candidate if candidate.is_dir() else None


def _find_ancestor_project(start: Path, *, plugin_root: Path,
                           boundary: Path | None = None) -> Path | None:
    for directory in (start, *start.parents):
        if boundary is not None and directory != boundary and boundary not in directory.parents:
            break
        if directory == plugin_root or plugin_root in directory.parents:
            break
        if (directory / "sfdx-project.json").is_file():
            return directory
    return None


def _find_nested_project(root: Path, *, plugin_root: Path, max_depth: int = 3) -> Path | None:
    """Accept one project only after a complete, bounded scan proves uniqueness."""
    frontier = [root]
    found = None
    scanned = 0
    for _ in range(max_depth + 1):
        next_frontier: list[Path] = []
        for directory in frontier:
            if directory == plugin_root or plugin_root in directory.parents:
                continue
            if (directory / "sfdx-project.json").is_file():
                if found is not None:
                    return None
                found = directory
                continue
            try:
                for entry in directory.iterdir():
                    scanned += 1
                    if scanned > 4096:
                        return None
                    if entry.is_dir() and not entry.is_symlink():
                        next_frontier.append(entry)
            except OSError:
                return None
        frontier = next_frontier
        if not frontier:
            break
    return found


def resolve_project_cwd(*, plugin_root: Path, environment: dict[str, str],
                        process_cwd: Path | None = None) -> Path | None:
    """Resolve one nearest project without escaping the Codex workspace boundary."""
    plugin_root = plugin_root.resolve()
    if "CODEX_WORKSPACE_ROOT" in environment:
        workspace_root = _resolved_dir(environment.get("CODEX_WORKSPACE_ROOT"))
        if workspace_root is None:
            return None
        for raw in (str(process_cwd) if process_cwd else None,
                    environment.get("PWD"), environment.get("INIT_CWD")):
            candidate = _resolved_dir(raw)
            if candidate is None:
                continue
            found = _find_ancestor_project(candidate, plugin_root=plugin_root,
                                           boundary=workspace_root)
            if found is not None:
                return found
        return (
            _find_ancestor_project(workspace_root, plugin_root=plugin_root,
                                   boundary=workspace_root)
            or _find_nested_project(workspace_root, plugin_root=plugin_root)
        )
    if process_cwd is not None:
        candidate = process_cwd.resolve()
        if candidate != plugin_root and plugin_root not in candidate.parents:
            # Native Codex inherits the selected thread cwd. A Dock-launched app's
            # PWD/INIT_CWD can be stale, so never use those to escape this context.
            return _find_ancestor_project(candidate, plugin_root=plugin_root)
    for key in ("INIT_CWD", "PWD"):
        candidate = _resolved_dir(environment.get(key))
        if candidate is None:
            continue
        found = _find_ancestor_project(candidate, plugin_root=plugin_root)
        if found is not None:
            return found
    return None


# Project-local CLI config files and the keys that name the default org, most
# authoritative first: the current CLI's `.sf/config.json` wins over the legacy file.
_PROJECT_ORG_CONFIG = (
    (".sf/config.json", "target-org"),
    (".sfdx/sfdx-config.json", "defaultusername"),
)


def _project_target_org(project: Path | None) -> str | None:
    if project is None:
        return None
    for relative, key in _PROJECT_ORG_CONFIG:
        try:
            data = json.loads((project / relative).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            continue
        value = data.get(key) if isinstance(data, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def resolve_target_org(environment: dict[str, str], project: Path | None = None) -> str | None:
    """Return exactly one org: the explicit environment value, else the project's.

    Explicit SF_TARGET_ORG/SFDX_TARGET_ORG values must agree. Without either, the
    selected project's own CLI config is used: `.sf/config.json` takes precedence over
    the legacy `.sfdx/sfdx-config.json` when both name an org. Global (home-directory)
    defaults are never consulted.
    """
    values = {
        value.strip()
        for key in ("SF_TARGET_ORG", "SFDX_TARGET_ORG")
        if (value := environment.get(key)) and value.strip()
    }
    if values:
        return next(iter(values)) if len(values) == 1 else None
    return _project_target_org(project)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) == 2 and args[0] == "--headless-360":
        return configure_headless_360(args[1])
    if len(args) != 1 or args[0] not in SERVERS:
        print("codex_mcp_launcher: unknown server", file=sys.stderr)
        return 2
    plugin_root = Path(__file__).resolve().parent.parent
    environment = dict(os.environ)
    server_name = args[0]
    server = SERVERS[server_name]
    enabled_h360 = server_name == "salesforce-headless-360" and _headless_360_enabled(environment)
    if server_name == "salesforce-headless-360":
        # The JS launcher retains its default-off contract, including legacy env opt-in.
        os.environ["SF_HEADLESS_360_ENABLED"] = "enabled" if enabled_h360 else "disabled"
    project = None
    if server_name != "salesforce-headless-360" or enabled_h360:
        project = resolve_project_cwd(plugin_root=plugin_root, environment=environment,
                                      process_cwd=Path.cwd())
        if project is None:
            print("codex_mcp_launcher: no Salesforce project in the bounded workspace. "
                  "Open a Codex chat in the Salesforce DX project.", file=sys.stderr)
            return 2
        target_org = resolve_target_org(environment, project)
        if target_org is None:
            print(
                "codex_mcp_launcher: no single target org. Run "
                "`sf config set target-org <alias>` in the project, or set SF_TARGET_ORG "
                "(or SFDX_TARGET_ORG, with the same value) in the environment that starts "
                "Codex.",
                file=sys.stderr,
            )
            return 2
        os.environ["CODEX_WORKSPACE_ROOT"] = str(project)
        os.environ["SF_TARGET_ORG"] = target_org
        os.environ["SFDX_TARGET_ORG"] = target_org
        os.chdir(project)
    executable = (plugin_root / server[0]).resolve()
    if not executable.is_file() or plugin_root not in executable.parents:
        print("codex_mcp_launcher: bundled server is missing", file=sys.stderr)
        return 2
    launch_args = [str(executable), *server[1:]]
    if server_name == "salesforce-headless-360":
        launch_args[1] = str((plugin_root / server[1]).resolve())
    node = resolve_node(environment)
    if node is None:
        print("codex_mcp_launcher: Node.js not found. Install Node.js on PATH or in a "
              "supported user/system bin directory; see the plugin README.", file=sys.stderr)
        return 2
    if "PATH" in environment:
        os.environ["PATH"] = environment["PATH"]
    try:
        os.execvp(node, [node, *launch_args])
        return 0
    except OSError as exc:
        print(f"codex_mcp_launcher: unable to start node: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
