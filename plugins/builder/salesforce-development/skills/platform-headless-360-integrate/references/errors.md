# H360 error reference

Load when diagnosing a returned error. Report observed status/body and distinguish
transport errors from tool results; do not bypass authorization or managed policy.


| Symptom | Cause | What to do |
|---|---|---|
| `OAUTH_AUTHORIZATION_BLOCKED` during sign-in | The ECA is in a different org from the target, or the My Domain doesn't match | Create the ECA in the target org; check the `{mydomain}` segment and the login host |
| `401` on a manual OAuth connection | The ECA, scope, or token may be invalid or expired | Check `mcp_api` and `refresh_token`, allow for propagation, then re-authenticate in `/mcp`. For the bundled server, report the exact response and ask the user to verify the CLI login |
| A connection or `404` error | Could indicate activation, routing, or a missing operation | Report the observed transport error and confirm activation and environment routing with an admin |
| `404` on a `/headless/invoke/...` path | A `/services/data/vXX.0/` prefix was added, or the call didn't go through H360 | Send the path exactly as the recipe gives it, through H360 |
| `upstream HTTP 404` on every call after a long idle | The server's session went stale | Restart the server through the host (Claude Code: `/mcp`; Codex: start a new session), then retry |
| A returned `403` | The server rejected that operation; the tool result alone may not identify whether this is corpus access, user permission, or another policy | Report the exact status and body. Check the operation and org access with an admin; do not infer a cause or bypass the gate |
| `400 INVOCABLE_ACTION_ERROR` with "action type … isn't supported" | The org lacks a feature in the recipe's `preconditions` | Tell the user H360 can't read it in this org, and where in Setup to check; don't answer from documentation |
| `INVALID_ARGUMENT: 'url' and 'method' are required` | The dispatch call used the wrong argument shape | Send `{url, method, …}` |
| `INVALID_ARGUMENT: 'url' must not contain an inline query string` | The query string is in `url` | Move it to `queryParams` |
| `No operation or SOR found for id` | `describe` got an id that doesn't exist | Get a real id from `discover` |
| `400 ROUTE_NOT_FOUND` | The server did not resolve the requested route | Re-check the exact `url` against `describe` and the Step 2 version |
| `405 METHOD_NOT_ALLOWED` | The route doesn't accept that HTTP method | Use the method `describe` returns |
