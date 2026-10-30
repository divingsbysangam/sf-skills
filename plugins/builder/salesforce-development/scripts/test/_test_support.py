"""Shared helpers for the offline salesforce-development script tests."""
from __future__ import annotations

import importlib.util
import os
import re
import unittest
from pathlib import Path
from unittest import mock

_ANSI = re.compile(r"\x1b\[[0-9;]*m")

HOST_MARKER_KEYS = (
    "SF_AGENT_CLIENT", "CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_HOME",
    "CLAUDE_CODE_SESSION_ID", "CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CONFIG_DIR",
)


def isolate_host_markers():
    """Give a test module a neutral host; host-specific cases set their own markers.

    Restore the caller's environment when unittest finishes the module, including
    when setup fails. This also works when tests are run directly from either host.
    """
    patch = mock.patch.dict(os.environ, {
        key: value for key, value in os.environ.items() if key not in HOST_MARKER_KEYS
    }, clear=True)
    # Some older tests use patch.stopall(); keep this module fixture outside
    # that per-test registry so those cleanups cannot restore ambient markers.
    patch.__enter__()
    unittest.addModuleCleanup(patch.__exit__, None, None, None)


def load_module(path: Path, name: str):
    """Load a script module from an exact path under a test-specific name."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def strip_ansi(text: str) -> str:
    """Remove SGR sequences so visual goldens assert on visible text."""
    return _ANSI.sub("", text)
