# Manual H360 registration

Load this only when the user is connecting without the bundled plugin proxy.
Org activation is still required; see the main skill. Walk the user through
configuration changes rather than making them automatically.

1. **For manual registration only**, create an External Client App (ECA) in
   the same org (also a one-time admin task):
   - OAuth scopes: `mcp_api` and `refresh_token`.
   - Turn on **Issue JSON Web Token (JWT)-based access tokens for named users**,
     and require PKCE.
   - Callback URL for Claude Code: `http://localhost:<port>/callback`. Pick a
     fixed port; the same port goes into the next step.
   - A new ECA can take up to 30 minutes to start working.
2. **Register the server** with the URL for the org's environment (only when
   connecting manually):

   | Org | Server URL |
   |---|---|
   | Production | `https://api.salesforce.com/platform/mcp/v1/platform/headless-360` |
   | Sandbox or scratch org | `https://api.salesforce.com/platform/mcp/v1/sandbox/platform/headless-360` |
   | Org that blocks login through login.salesforce.com and test.salesforce.com | `https://api.salesforce.com/platform/mcp/v1/d/{mydomain}/{develop\|scratch}/platform/headless-360` |

   `{mydomain}` is the part before `.my.salesforce.com` in the org's login host.

   ```bash
   claude mcp add --transport http --client-id <ECA consumer key> --callback-port <port> headless-360 <server URL>
   ```

   Add `--client-secret` only if the ECA requires a secret. The CLI prompts for
   it, so the user runs that command in their own terminal. Never ask for the
   secret in chat.
3. **Sign in to a manual registration.** Restart Claude Code, open `/mcp`, and
   authenticate `headless-360` in the browser. Then return to Step 1 of the main skill. The
   bundled Codex server uses the CLI org login and does not use this OAuth flow.

Other MCP clients need the same server URL, client ID, and a callback URL
registered on the ECA. Public reference:
https://developer.salesforce.com/docs/platform/hosted-mcp-servers/references/reference/headless-360-mcp.html
