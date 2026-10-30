#!/bin/bash
# Offline classification test for sf-deploy-gate (issue #259).
#
# Feeds {"result": <org>} fixtures (`sf org list` record or `sf org display --json`
# shape) into `sf-deploy-gate classify`
# and asserts the org bucket. No live org required — this is the documented
# regression guard for the trial-org-as-production mis-classification.
#
# Run: bash plugins/sfdx-deploy/test/classify.test.sh

set -uo pipefail

GATE="$(cd "$(dirname "$0")/.." && pwd)/sf-deploy-gate"
PASS=0
FAIL=0

# assert_bucket <expected> <description> <json-on-stdin>
assert_bucket() {
  local expected="$1" desc="$2" json="$3"
  local got
  got=$(printf '%s' "$json" | "$GATE" classify)
  if [ "$got" = "$expected" ]; then
    PASS=$((PASS + 1))
    printf '  ok   %-46s → %s\n' "$desc" "$got"
  else
    FAIL=$((FAIL + 1))
    printf '  FAIL %-46s → got "%s", expected "%s"\n' "$desc" "$got" "$expected"
  fi
}

echo "sf-deploy-gate classify — offline fixtures"

# --- The #259 regression: trial/dev orgs must NOT be production ---------------
# OrgFarm trial: isSandbox/isScratch null, host has no '--' marker. Pre-fix this
# fell through to "production" and over-blocked the deploy.
assert_bucket trial "OrgFarm trial (null flags, orgfarm host)" \
  '{"result":{"isSandbox":null,"isScratch":null,"instanceUrl":"https://orgfarm-abc123.develop.my.salesforce.com"}}'

assert_bucket trial "Developer Edition (.develop.my host)" \
  '{"result":{"isSandbox":false,"isScratch":false,"instanceUrl":"https://mydomain.develop.my.salesforce.com"}}'

assert_bucket trial "Demo org (.demo.my host, no expiration)" \
  '{"result":{"isSandbox":false,"isScratch":false,"orgEdition":"Enterprise Edition","instanceUrl":"https://acme.demo.my.salesforce.com"}}'

assert_bucket trial "Demo org host is matched case-insensitively" \
  '{"result":{"isSandbox":false,"isScratch":false,"instanceUrl":"https://ACME.DEMO.MY.SALESFORCE.COM"}}'

assert_bucket sandbox "Sandbox on demo partition wins over trial" \
  '{"result":{"isSandbox":true,"isScratch":false,"instanceUrl":"https://acme--sb.demo.my.salesforce.com"}}'

assert_bucket production "Look-alike My Domain name (acme-demo.my host)" \
  '{"result":{"isSandbox":false,"isScratch":false,"instanceUrl":"https://acme-demo.my.salesforce.com"}}'

assert_bucket production "My Domain named demo (demo.my host)" \
  '{"result":{"isSandbox":false,"isScratch":false,"instanceUrl":"https://demo.my.salesforce.com"}}'

assert_bucket trial "pc-rnd internal dev host" \
  '{"result":{"isSandbox":null,"isScratch":null,"instanceUrl":"https://na1.pc-rnd.salesforce.com"}}'

assert_bucket trial "trial via trialExpirationDate field" \
  '{"result":{"isSandbox":false,"isScratch":false,"instanceUrl":"https://example.my.salesforce.com","trialExpirationDate":"2099-09-01T00:00:00.000+0000"}}'

# #356: the CLI spells it trailExpirationDate (sic) — the SDO/demo-org case.
assert_bucket trial "SDO via trailExpirationDate (CLI spelling)" \
  '{"result":{"isSandbox":false,"isScratch":false,"orgEdition":"Enterprise Edition","instanceUrl":"https://co1778596511055.my.salesforce.com","trailExpirationDate":"2027-06-18T19:21:38.000+0000"}}'

# --- Genuine production must still be gated -----------------------------------
assert_bucket production "Enterprise prod (my.salesforce.com)" \
  '{"result":{"isSandbox":false,"isScratch":false,"instanceUrl":"https://acme.my.salesforce.com"}}'

assert_bucket production "Classic prod (login host)" \
  '{"result":{"isSandbox":false,"isScratch":false,"instanceUrl":"https://na1.salesforce.com"}}'

# --- Existing non-prod buckets preserved --------------------------------------
assert_bucket sandbox "Sandbox via isSandbox flag" \
  '{"result":{"isSandbox":true,"isScratch":false,"instanceUrl":"https://acme--dev.sandbox.my.salesforce.com"}}'

assert_bucket sandbox "Sandbox via -- host marker (flag null)" \
  '{"result":{"isSandbox":null,"isScratch":null,"instanceUrl":"https://acme--uat.my.salesforce.com"}}'

assert_bucket sandbox "Classic sandbox (test.salesforce.com)" \
  '{"result":{"isSandbox":true,"instanceUrl":"https://test.salesforce.com"}}'

assert_bucket scratch "Scratch org" \
  '{"result":{"isSandbox":false,"isScratch":true,"instanceUrl":"https://random-scratch.scratch.my.salesforce.com"}}'

# Dev Hub is usually enabled in the production org, so isDevHub alone is not non-prod.
assert_bucket production "DevHub with no sandbox/scratch/trial signal is production" \
  '{"result":{"isSandbox":false,"isScratch":false,"isDevHub":true,"instanceUrl":"https://acme.my.salesforce.com"}}'

assert_bucket trial "DevHub trial (future expiration) stays trial" \
  '{"result":{"isSandbox":false,"isScratch":false,"isDevHub":true,"instanceUrl":"https://acme.my.salesforce.com","trailExpirationDate":"2099-01-01T00:00:00.000+0000"}}'

# The expiration date is cached at login; a converted trial keeps a stale past date.
assert_bucket production "past trial expiration (converted trial) is production" \
  '{"result":{"isSandbox":false,"isScratch":false,"instanceUrl":"https://acme.my.salesforce.com","trailExpirationDate":"2020-01-01T00:00:00.000+0000"}}'

assert_bucket production "unparseable trial expiration is production" \
  '{"result":{"isSandbox":false,"isScratch":false,"instanceUrl":"https://acme.my.salesforce.com","trailExpirationDate":"soon"}}'

# --- Degenerate input ---------------------------------------------------------
assert_bucket unknown "empty / unparseable result" \
  '{}'

echo ""
echo "  $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
