# Salesforce IT Service Agentforce Setup

A specialist plugin for the initial Agentforce for IT Service setup workflows: Agentforce Studio enablement and readiness, Employee and Fulfiller agent setup, ITSM Agentforce permission assignment, and agent runtime access. It does not cover complete ITSM setup.

Version: **0.1.0**.

## Install and prerequisites

Install from the Salesforce marketplace: **verify public availability before relying on this source**. After publication, the expected commands are:

```text
/plugin marketplace add forcedotcom/sf-skills
/plugin install service-itsm@salesforce
/reload-plugins
```

Verify that the refreshed host inventory includes the seven skills before continuing; if it does not, start a fresh session. This plugin does not automatically install related plugins or refresh an existing session.

Workflows require an authenticated Salesforce CLI (`sf`) and Node.js 18 or later. The org must be licensed and provisioned for Agentforce for IT Service, expose the required API v67 endpoints, and grant the acting user the permissions required by each operation. Local packaging and mocked fixtures do not establish that a particular org meets these prerequisites. Use each skill's read-only readiness/preflight steps and review its explicit write confirmations.

## Workflows and boundaries

- “Set up Agentforce for IT Service”: start with `service-itsm-agentic-setup-agentforce-coordinate`. It orders Studio setup/validation before Employee or Fulfiller setup and runtime access.
- Enable and validate the Agentforce for IT Service Studio features with the Studio configure and validate skills.
- Configure Employee or Fulfiller agents, then assign the required persona permissions and runtime access. These actions can modify an org and require explicit user gates in the canonical workflows.
- The coordinator's optional human-escalation choice references `service-agentforce-human-escalation-configure`, provided by the separate `service-engagement` plugin. Install that plugin, reload, and verify its skill inventory before selecting that branch. Related-skill references do not install dependencies automatically.

This plugin does not cover general Agentforce, Sales agents, Service Cloud channel agents, CMDB, Incident Management, Teams/Slack, employee portals, service catalogs, or the two pre-release ITSM orchestrators. Those capabilities require separately scoped plugins and, for some workflows, additional runtime integrations.

## Exact roster

- `service-itsm-agentic-setup-agentforce-coordinate`
- `service-itsm-agentic-setup-agentforce-studio-configure`
- `service-itsm-agentic-setup-agentforce-studio-validate`
- `service-itsm-agentic-setup-fulfiller-agent-configure`
- `service-itsm-agentic-setup-employee-agent-configure`
- `service-itsm-agentic-setup-itsm-agentforce-permset-assign`
- `service-itsm-agentic-setup-agent-runtime-access-assign`

## Validation

Edit plugin-owned content under `plugins/src/service-itsm/` and canonical skills under
`skills/`. The seven mirrored skills are assembled from the marketplace roster;
`plugins/builder/service-itsm/` is committed build output.

Run the repository gates in `plugins/DEVELOPING.md`, plus the release-tree check:

```bash
npm run build:plugins -- service-itsm
npm run build:plugins:check -- service-itsm
PYTHONDONTWRITEBYTECODE=1 python3 plugins/builder/service-itsm/scripts/verify-public-plugin-release.py --plugin-root plugins/builder/service-itsm --authoring-root skills
```

These checks establish packaging integrity, not live org execution or public availability.
