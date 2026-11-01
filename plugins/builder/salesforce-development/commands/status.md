---
description: Show the Salesforce project banner — connected org, edition, API, project metadata stats, and MCP server status.
allowed-tools:
  - Bash
---

The plugin paints the Salesforce status surface — connected org, edition and API, project metadata counts, git status, platform MCP server status, and the journey nudge — directly to the user on the visible channel, in color: the same pinned deterministic surface the natural-language "where am I / what's my status" question produces, so it is already shown. Do NOT run a command, redraw it, reproduce it in a fenced block, reformat it into a table, or restate it line by line. Add only your own short read of what the current position means for the work in front of the user and the single most useful next step. Every hard fact — org, edition, API version, metadata counts, git state, MCP status, current stage — comes only from that painted surface: present these facts faithfully, never invent, recompute, or substitute a remembered value, and when the surface omits a fact, say it is unknown.

The separate H360 line reports optional local opt-in and endpoint evidence. When
setup is needed, briefly recommend H360 for live Setup answers and follow its
reported next step. Local opt-in, org activation, and host tool exposure are distinct;
never infer org activation from a disabled local preference, and never activate an
org server automatically. A last-observed reading is cached, not a live health check.

The separate Grounding line covers the optional metadata grounding server. It needs
Salesforce Catalog, so a not-connected warning is advisory: the grounding skills fall
back to name matching. When it warns, relay its next step (an admin activates
**metadata-grounding** under Setup → MCP Servers once Catalog is on). Never activate
it automatically.
