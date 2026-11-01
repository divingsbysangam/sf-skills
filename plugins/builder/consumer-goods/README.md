# Salesforce Consumer Goods Cloud

Six guided workflows for Consumer Goods Cloud Trade Promotion Management (TPM): liability
accruals, Trade Promotion Effectiveness (TPE) data kits, promotion-measure export to Data Cloud,
TPE analytics dashboards and optional custom KPI measures, and supported Consumer Goods sync
configuration and assignments.

## Installation

After this plugin is available in the Salesforce public marketplace, install and activate it:

```text
/plugin marketplace add forcedotcom/sf-skills
/plugin install consumer-goods@salesforce
/reload-plugins
```

Confirm the six skills in the refreshed host inventory before starting a task:

| Skill | Capability |
| --- | --- |
| `consumer-goods-accruals-datakit-deploy` | Deploy TPM Liability Accruals data kit and schedule its export/calculation chain |
| `consumer-goods-tpe-datakit-deploy` | Deploy the TPM data kit components used by TPE dashboards |
| `consumer-goods-rtr-datacloud-export-configure` | Configure the TPM RTR promotion-measure export to Data Cloud for TPE |
| `consumer-goods-tpe-dashboard-configure` | Set up TPE analytics, dashboard prerequisites and the base dashboards |
| `consumer-goods-tpe-dashboard-custom-kpi-configure` | Add customer-selected KPI measures to cloned TPE dashboards |
| `consumer-goods-sync-management-configure` | Install supported sync baselines and manage sync configuration and user assignments |

Version 0.1.0 is pending public publication. Marketplace registration alone does not establish
availability. This plugin adds no agents, commands, hooks or MCP servers.

## Prerequisites

Use an authenticated Salesforce org with the managed Consumer Goods Cloud / TPM package at the
version and namespace required by the selected skill. The operator needs the TPM administrator
permissions and API access required for that workflow. These workflows write org configuration;
each skill requires the user to confirm its target org and requested writes before acting.

Requirements differ by workflow. Accrual and TPE kit deployment can require a licensed Data Cloud
org, TPM data connector and CRM Home connection, packaged resources, sales-org configuration, and
appropriate data and object permissions. TPE dashboard setup also depends on supported TPM and
Data Cloud versions, Tableau Next / analytics provisioning, assigned administrator permissions,
and off-core tenant pairing. Tenant pairing and some connector or permission checks require manual
confirmation in Setup and cannot be inferred from local packaging. RTR export setup may require
an explicit export/sync step; a configured stream is not evidence that its data has been synchronized.
Sync management requires the supported managed package and its global REST endpoints. Server-side
configuration or assignment counts do not establish that mobile devices have synchronized.

Read the selected skill's preflight before making changes; a prerequisite failure means stop or
report the blocked step, not claim that it succeeded. Dry-run, pending, partial and blocked results
must remain distinct from completed org work.

## Related capability availability

Sibling flat skills `consumer-goods-visit-activity-*` and `consumer-goods-promotion-bo-api-deploy`
exist outside this plugin. Check the active host inventory for those workflows; installing
this plugin does not add them.

The accrual skill references `platform-dataspace-access-configure` for Data Space access. That
related skill is not bundled in this plugin or in the `salesforce-development` plugin. Check the
active host inventory before delegating to it. If it is unavailable, do not imply that installing
Consumer Goods made it available; arrange the Data Space prerequisite through an installed owning
workflow or the org administrator.

## Discovery scope

Recommendations cover TPM liability accruals (including its TPM summary Lightning component), TPE
data kit and dashboard setup, TPE custom KPI configuration, RTR promotion-measure export to Data
Cloud, and the supported Consumer Goods sync management endpoints and assignments. The promotion
BO API workflow is excluded. This plugin does not provide generic ecommerce promotion setup,
general Data Cloud development or ingestion, segmentation, code extensions, Tableau dashboard
authoring, general Lightning component development, generic mobile/offline sync, Salesforce
deployment, or Agentforce setup.

Salesforce Development can recommend this plugin for matching tasks. Installation requires user
acceptance, followed by reload and a refreshed inventory. An installed Consumer Goods plugin is
excluded from installation recommendations. Consumer Goods anchors apply to proactive suggestions,
explicit discovery and bypass advice; unqualified neighboring requests should not select this
plugin. Setup requests such as "Set up TPE" and "Set up CG Cloud" can recommend this plugin;
select the relevant data kit, dashboard, accrual, export or sync workflow after installation.
