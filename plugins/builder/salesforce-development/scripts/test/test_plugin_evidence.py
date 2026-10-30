"""Capability evidence regressions through scorer and real proposal consumers.

PRECISION_OTHER_MARKETPLACE optionally adds the other PR's entry to the catalog
for combined validation; default execution validates this PR independently.
"""
import copy
import io
import json
import os
import sys
import tempfile
import unittest
import uuid
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from _test_support import load_module

SCRIPTS = Path(__file__).resolve().parent.parent
ROOT = SCRIPTS.parents[3]
TARGETS = {'education-cloud', 'service-omni', 'service-itsm', 'consumer-goods'}
NEGATIVES = [
    'Write a course syllabus', 'Manage a campus network',
    'Set up an employee training course catalog',
    'Migrate our online course catalog to Shopify',
    'Configure admissions FAQs and campus tours in WordPress',
    'Build an Education Cloud admissions FAQ and campus tour website',
    'Create a generic Agentforce service agent',
    'Create an Agentforce agent for Education Cloud admissions',
    'Configure Kubernetes queue routing', 'Configure RabbitMQ queue routing',
    'Configure skills-based routing in Zendesk',
    'show user presence status in Slack',
    'set up supervisor settings for a Linux process',
    'Set up an employee recruitment agent for hiring',
    'Create a course catalog for our university',
    'Configure university admissions',
    'Omni Studio settings', 'Configure Omni Studio settings',
    'Create a Salesforce queue for Leads', 'Route traffic to my website',
    'Configure Salesforce voice call transcripts',
    'Configure Salesforce storage capacity alerts',
    'Increase Salesforce API capacity', 'Check Salesforce data storage capacity',
    'Configure voice routing in Amazon Connect', 'Set up Twilio voice routing',
    'Route voice traffic through Twilio',
    'Create a campus hierarchy for field service territories',
    'Plan the institutional knowledge article hierarchy',
    "Show me the academic calendar for my kid's school",
    'Route voice calls', 'Please route voice calls',
    'Can you route voice calls?', 'Configure voice call routing',
]
# Review regressions belong to this plugin; neighboring plugins may own the request.
ITSM_NEGATIVES = [
    'Test my Agentforce agent and fix it',
    'Deploy the Agentforce agent, then publish it',
    'Set up the service console and configure it',
    'Set up Microsoft Teams for IT Service',
    'Configure CMDB for IT Service',
    'Configure incident management for IT Service',
    'Set up IT Service incident SLA',
    'Enable Swarming for ITSM',
    'Enable Agentforce Studio',
    'Configure Agentforce Studio agent builder for sales',
    'Configure Agentforce Employee Agent for HR questions',
    'Configure Agentforce runtime for Service Cloud voice',
    'Set up Agentforce for Employee Service',
    'Set up Agentforce runtime access for TPE users',
    'Configure incident permissions for ITSM',
    'Assign ITSM permissions for Teams',
    'Configure ITSM employee portal access',
    'Set up a service agent in Agentforce and test it',
    'Fix our Agentforce service agent in Service Cloud; it is broken',
    'Configure employee records for IT Service',
    'Configure runtime monitoring for IT Service',
    'Set up Agentforce and test it. Service Cloud needs a generic agent',
    'Configure Agentforce and test it; service agents need deployment',
    'Configure Agentforce and test it! Service Cloud needs a generic agent',
    'Configure Agentforce and test it? Service agents need deployment',
]
POSITIVES = {
    'consumer-goods': [
        'Set up TPE',
        'Set up CG Cloud',
        'Deploy the Consumer Goods TPM liability accrual data kit and schedule its accrual chain',
        'Set up the TPE data kit',
        'Set up the TPE data kit and Trade Promotion Effectiveness dashboards',
        'Configure custom KPI measures for our TPE dashboards',
        'Set up the RTR export of TPM promotion measures to Data Cloud for TPE',
        'Configure Consumer Goods sync management and assign users to a sync configuration',
        'Configure CG Cloud sync',
        'Clone the TPE dashboards in Tableau Next for consumer goods',
        'Set up trade promotion effectiveness',
        'Set up trade promotion effectiveness analytics for our CPG company',
        'Enable trade promotion management in my org',
        'Create custom KPIs for TPE dashboards',
        'Install the accruals data kit for trade promotions',
        'Set up real time reporting export from Consumer Goods Cloud to Data Cloud',
        'Deploy the CG Cloud trade promotion effectiveness data kit',
    ],
    'education-cloud': [
        'Enable Education Cloud recruitment and admissions domains',
        'Generate an academic calendar with semester terms and sessions',
        'Generate academic sessions and registration windows',
        'Migrate our course catalog into Education Cloud',
        'Configure a multi-campus hierarchy',
        'Migrate our course catalog into Education Cloud LearningCourse records',
        'Configure a multi-campus institutional hierarchy with Business Profiles',
        'Set up the Education Cloud Student Recruitment Agent for admissions and campus tours',
    ],
    'service-omni': [
        'Route VoiceCall records', 'Please route VoiceCall records',
        'Can you route VoiceCall records?', 'Configure Salesforce voice call routing',
        'Configure Omni voice call routing', 'Configure Salesforce work sharing',
        'Configure Salesforce agent capacity', 'Configure Salesforce agents capacity',
        # Agent capacity is capability evidence even without a vendor cue.
        'Configure generic queue routing and agent capacity',
        'Configure Salesforce presence statuses', 'Set up Salesforce routing for chats',
        'Configure Case queue routing', 'Deploy a routing flow for VoiceCall',
        'I need to route Cases to service queues', 'I need to route VoiceCall to a queue',
        'Configure Salesforce queues and agent capacity',
        'Configure skills-based routing for Cases',
        'Configure Salesforce queue routing and agent capacity',
        'Deploy Salesforce presence statuses',
    ],
    'service-itsm': [
        'Set up Agentforce for IT Service',
        'Enable IT Service Agentforce Studio',
        'Configure Employee agent for IT Service',
        'Configure Fulfiller agent for IT Service',
        'Assign ITSM Agentforce permissions',
        'Assign IT Service Agentforce runtime access',
        'Set up AGENTFORCE for IT SERVICE',
        'Configure employee agent for IT-Service',
        'Configure Employee agents for IT Service',
        'Assign runtime access to IT Service agents',
        'Configure ITSM Employee agents',
        'Assign ITSM runtime access to agents',
    ],
}


class PluginEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_module(SCRIPTS / 'plugin_catalog.py', 'precision_catalog')
        cls.context = load_module(SCRIPTS / 'sf_context.py', 'precision_context')
        cls.data = cls.catalog.build_catalog(ROOT, SCRIPTS.parent)
        other = os.environ.get('PRECISION_OTHER_MARKETPLACE')
        if other:
            marketplace = json.loads((ROOT / '.claude-plugin/marketplace.json').read_text())
            existing = {p['name'] for p in marketplace['plugins']}
            marketplace['plugins'] += [p for p in json.loads(Path(other).read_text())['plugins']
                                       if p['name'] in TARGETS and p['name'] not in existing]
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                (root / '.claude-plugin').mkdir()
                (root / '.claude-plugin/marketplace.json').write_text(json.dumps(marketplace))
                (root / 'config.yml').write_text((ROOT / 'config.yml').read_text())
                cls.data = cls.catalog.build_catalog(root, SCRIPTS.parent)
        cls.present = TARGETS & {p['name'] for p in cls.data['plugins']}

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        td = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.project = Path(td)
        (self.project / 'sfdx-project.json').write_text('{"packageDirectories":[]}')
        config = self.project / 'config'
        config.mkdir()
        (config / 'settings.json').write_text('{"enabledPlugins":{"salesforce-development@salesforce":true}}')
        previous = Path.cwd()
        os.chdir(self.project)
        self.addCleanup(os.chdir, previous)
        self.stack.enter_context(patch.dict(os.environ, {
            'CLAUDE_CONFIG_DIR': str(config), 'SF_PLUGIN_MATCH_SENSITIVITY': 'standard',
            'SF_HOOK_UI_MODE': 'full', 'SF_TELEMETRY_DISABLED': '1',
        }))
        c = self.context
        for name, value in [
            ('_load_plugin_catalog_module', self.catalog),
            ('_plugin_display_name', 'salesforce-development'),
            ('_plugin_match_sensitivity', 'standard'),
            ('_load_plugin_flow', None),
            ('_enabled_plugin_names', set()),
        ]:
            self.stack.enter_context(patch.object(c, name, return_value=value))
        self.stack.enter_context(patch.object(c, '_load_plugin_proposals', side_effect=lambda _: {}))
        self.stack.enter_context(patch.object(c, '_entered_this_session', return_value=True))
        self.stack.enter_context(patch.object(c, '_welcomed_this_session', return_value=True))
        for name in ('_save_plugin_proposals', '_open_plugin_flow', '_fire_plugin_telemetry_event'):
            self.stack.enter_context(patch.object(c, name))
        self.stack.enter_context(patch.object(self.catalog, 'load_catalog', return_value=self.data))
        self.stack.enter_context(patch.object(c, '_PROMPT_RUNTIME_DIR', self.project / 'runtime'))

    def score(self, prompt, anchors=True):
        corpus = {**self.data, 'plugins': [p for p in self.data['plugins']
                                         if p['name'] != 'salesforce-development']}
        return self.catalog.score_prompt_against_catalog(prompt, corpus, require_anchor_terms=anchors)

    def surfaced(self, prompt, surface):
        c = self.context
        out = io.StringIO()
        session = 'precision-' + uuid.uuid4().hex
        with redirect_stdout(out):
            if surface == 'user-prompt':
                payload = {'hook_event_name': 'UserPromptSubmit', 'session_id': session,
                           'prompt_id': 'p1', 'prompt': prompt}
                with patch.object(c.sys, 'stdin', io.StringIO(json.dumps(payload))):
                    self.assertEqual(0, c.cmd_prompt_dispatch())
            elif surface == 'discovery-command':
                self.assertEqual(0, c.cmd_plugin_match(['--json', '--session-id', session, prompt]))
            elif surface == 'bypass-gate':
                payload = {'session_id': session, 'tool_name': 'Write',
                           'tool_input': {'file_path': 'example.txt', 'content': 'example'}}
                with patch.object(c.sys, 'stdin', io.StringIO(json.dumps(payload))), \
                     patch.object(c, '_skills_first_match', return_value=None), \
                     patch.object(c, '_prompt_text', return_value=prompt), \
                     patch.object(c, '_recorded_catalog_prompt_eligible', return_value=True):
                    self.assertEqual(0, c.cmd_skills_first_advisory())
            else:
                # SessionStart has file signals rather than free-form user prompts.
                # Feed a controlled query through the actual slot consumer.
                with patch.object(c, '_detect_plugin_signals', return_value=[('agentforce', 'controlled evidence', prompt)]):
                    model, paint = c._session_start_plugin_slot(session, 'startup', self.project)
                out.write(model + paint)
        return out.getvalue()

    def test_negatives_have_neither_high_nor_medium_candidates_and_never_surface(self):
        for prompt in NEGATIVES:
            for anchors in (True, False):
                with self.subTest(prompt=prompt, anchors=anchors):
                    self.assertFalse(TARGETS & {m.plugin['name'] for m in self.score(prompt, anchors)})
            for surface in ('user-prompt', 'session-start', 'discovery-command', 'bypass-gate'):
                with self.subTest(prompt=prompt, surface=surface):
                    output = self.surfaced(prompt, surface)
                    for name in TARGETS:
                        self.assertNotIn(name, output)

    def test_consumer_goods_scope_regressions_across_all_real_surfaces(self):
        name = 'consumer-goods'
        self.assertIn(name, {plugin['name'] for plugin in self.data['plugins']})
        positives = POSITIVES[name].copy()
        negatives = [
            'Set up CG',
            'What is CG?',
            'Set up trade promotion for B2B Commerce',
            'Configure CG rendering for my graphics project',
            'Set up CG lighting in Blender',
            'Configure consumer account management',
            'Build consumer goods inventory management',
            'Configure promotion management for my Shopify store',
            'Set up promotion effectiveness analytics for WooCommerce',
            'Calculate accounting liability accruals',
            'Configure liability accruals in SAP',
            'Add a TPM promotion BO API workflow step',
            'Configure the Consumer Goods TPM promotion BO API to copy promotions into the target org',
            'Configure generic ecommerce promotions for my online store',
            'Build a generic Data Cloud ingestion pipeline',
            'Build a consumer data ingestion pipeline',
            'Export consumer data to a CSV',
            'Build a consumer analytics dashboard',
            'Configure Consumer Goods Data Cloud ingestion',
            'Create Data Cloud audience segments',
            'Deploy a Data Cloud code extension',
            'Author a Tableau Next dashboard from scratch',
            'Fix generic mobile and offline sync for my field app',
            'Create a Consumer Goods visit activity definition',
            'Configure Consumer Goods visit activity account scoping',
            'Deploy ecommerce promotion management for my B2C storefront',
            'Configure real time reporting for a router RTR',
            'Install a Data Cloud data kit',
            'Configure KPIs for a Tableau Next dashboard',
            'Deploy Salesforce metadata to my org',
            'Set up an Agentforce agent',
        ]
        row = next(plugin for plugin in self.data['plugins'] if plugin['name'] == name)
        positives += row['match']['examplePrompts']
        surfaces = ('user-prompt', 'session-start', 'discovery-command', 'bypass-gate')
        for prompt in positives:
            with self.subTest(kind='positive-score', prompt=prompt):
                self.assertTrue(self.context._plugin_prompt_requests_action(prompt))
                self.assertIn(name, {match.plugin['name'] for match in self.score(prompt)
                                     if match.band == 'high'})
            for surface in surfaces:
                with self.subTest(kind='positive-surface', prompt=prompt, surface=surface):
                    self.assertIn(name, self.surfaced(prompt, surface))
        for prompt in negatives:
            for anchors in (True, False):
                with self.subTest(kind='negative-score', prompt=prompt, anchors=anchors):
                    self.assertNotIn(name, {match.plugin['name']
                                            for match in self.score(prompt, anchors)})
            for surface in surfaces:
                with self.subTest(kind='negative-surface', prompt=prompt, surface=surface):
                    self.assertNotIn(name, self.surfaced(prompt, surface))

    def test_consumer_goods_bare_product_information_and_commerce_neighbor(self):
        for prompt in ['What is TPE?', 'Explain TPE', 'What is CG Cloud?',
                       'Tell me about CG Cloud']:
            with self.subTest(prompt=prompt):
                self.assertFalse(self.context._plugin_prompt_requests_action(prompt))
                self.assertNotIn('consumer-goods', self.surfaced(prompt, 'user-prompt'))
                self.assertIn('consumer-goods', self.surfaced(prompt, 'discovery-command'))
        prompt = 'Set up trade promotion for B2B Commerce'
        for surface in ('user-prompt', 'session-start', 'discovery-command', 'bypass-gate'):
            with self.subTest(surface=surface):
                output = self.surfaced(prompt, surface)
                self.assertNotIn('consumer-goods', output)
                self.assertIn('commerce-b2b', output)

    def test_clone_requests_keep_information_and_homographs_out_of_prompt_surface(self):
        for prompt in [
            'Please clone the TPE dashboards in Tableau Next for consumer goods',
            'Can you clone the TPE dashboards in Tableau Next for consumer goods?',
        ]:
            with self.subTest(prompt=prompt):
                self.assertTrue(self.context._plugin_prompt_requests_action(prompt))
                self.assertIn('consumer-goods', self.surfaced(prompt, 'user-prompt'))
        for prompt in [
            'Explain how to clone the TPE dashboards for consumer goods',
            'What is a clone of the TPE dashboards for consumer goods?',
            'Clone settings for the TPE dashboards are incorrect',
            'Clone failed in Tableau Next for consumer goods TPE dashboards',
        ]:
            with self.subTest(prompt=prompt):
                self.assertFalse(self.context._plugin_prompt_requests_action(prompt))
                self.assertNotIn('consumer-goods', self.surfaced(prompt, 'user-prompt'))

    def test_itsm_unrelated_requests_never_match_or_surface(self):
        if 'service-itsm' not in self.present:
            self.skipTest('IT Service plugin is not in this catalog')
        for prompt in ITSM_NEGATIVES:
            for anchors in (True, False):
                with self.subTest(prompt=prompt, anchors=anchors):
                    self.assertNotIn('service-itsm', {m.plugin['name'] for m in self.score(prompt, anchors)})
            for surface in ('user-prompt', 'session-start', 'discovery-command', 'bypass-gate'):
                with self.subTest(prompt=prompt, surface=surface):
                    self.assertNotIn('service-itsm', self.surfaced(prompt, surface))

    def test_supported_and_curated_requests_pass_action_gate_and_surface(self):
        for name in self.present:
            row = next(p for p in self.data['plugins'] if p['name'] == name)
            for prompt in POSITIVES[name] + row['match']['examplePrompts']:
                with self.subTest(name=name, prompt=prompt):
                    self.assertTrue(self.context._plugin_prompt_requests_action(prompt))
                    self.assertIn(name, {m.plugin['name'] for m in self.score(prompt) if m.band == 'high'})
                for surface in ('user-prompt', 'session-start', 'discovery-command', 'bypass-gate'):
                    with self.subTest(name=name, prompt=prompt, surface=surface):
                        self.assertIn(name, self.surfaced(prompt, surface))

    def test_route_requests_preserve_neighboring_plugin_recommendations(self):
        for name, prompt in [
            ('service-engagement', 'Route Omni-Channel work items to agents'),
            ('experience-lwc', 'route traffic to Experience Cloud LWC site'),
        ]:
            with self.subTest(name=name, prompt=prompt):
                self.assertTrue(self.context._plugin_prompt_requests_action(prompt))
                self.assertIn(name, self.surfaced(prompt, 'user-prompt'))
        for prompt in ['Route settings for Experience Cloud LWC are incorrect',
                       'Route failed for Omni-Channel work items']:
            with self.subTest(prompt=prompt):
                self.assertFalse(self.context._plugin_prompt_requests_action(prompt))
                output = self.surfaced(prompt, 'user-prompt')
                for name in ('service-engagement', 'experience-lwc', 'service-omni'):
                    self.assertNotIn(name, output)

    def test_route_observations_do_not_proactively_surface(self):
        for prompt in ['Route settings for voice calls are incorrect',
                       'Route failed for voice calls']:
            with self.subTest(prompt=prompt):
                self.assertFalse(self.context._plugin_prompt_requests_action(prompt))
                self.assertNotIn('service-omni', self.surfaced(prompt, 'user-prompt'))

    def test_itsm_neighbors_keep_their_supported_recommendations(self):
        for name, prompt in [
            ('agentforce-adlc', 'Test my Agentforce agent and fix it'),
            ('agentforce-adlc', 'Deploy the Agentforce agent, then publish it'),
            ('service-engagement', 'Route Omni-Channel work items to agents'),
        ]:
            for surface in ('user-prompt', 'session-start', 'discovery-command', 'bypass-gate'):
                with self.subTest(plugin=name, prompt=prompt, surface=surface):
                    self.assertIn(name, self.surfaced(prompt, surface))

    def test_information_only_prompt_scores_but_does_not_proactively_surface(self):
        for name in self.present:
            topic = ('an academic calendar with semester terms?' if name == 'education-cloud'
                     else 'TPE?' if name == 'consumer-goods'
                     else 'Agentforce for IT Service setup?' if name == 'service-itsm'
                     else 'Omni queue routing and presence?')
            prompt = 'What is ' + topic
            self.assertIn(name, {m.plugin['name'] for m in self.score(prompt)})
            self.assertFalse(self.context._plugin_prompt_requests_action(prompt))
            self.assertNotIn(name, self.surfaced(prompt, 'user-prompt'))
            self.assertIn(name, self.surfaced(prompt, 'discovery-command'))

    def test_installed_plugins_suppressed_on_every_surface(self):
        with patch.object(self.context, '_enabled_plugin_names', return_value=self.present):
            for name in self.present:
                for surface in ('user-prompt', 'session-start', 'discovery-command', 'bypass-gate'):
                    self.assertNotIn(name, self.surfaced(POSITIVES[name][0], surface))

    def test_existing_curated_routes_and_session_start_signals(self):
        for name, prompt in [
            ('experience-lwc', 'Build an LWC datatable with wire service and Jest tests'),
            ('experience-react', 'Scaffold a Salesforce React UI bundle with Tailwind and shadcn'),
            ('experience-cms', 'Find an existing Salesforce CMS media asset'),
            ('agentforce-adlc', 'Create a generic Agentforce service agent'),
            ('dx-devops', 'Configure a DevOps Center test pipeline'),
            ('service-engagement', 'Set up a Salesforce Digital Engagement messaging channel'),
            ('commerce-b2b', 'Set up a B2B commerce catalog'),
        ]:
            for surface in ('user-prompt', 'discovery-command', 'bypass-gate'):
                with self.subTest(name=name, surface=surface):
                    out = self.surfaced(prompt, surface)
                    self.assertIn(name, out)
                    for target in TARGETS:
                        self.assertNotIn(target, out)
        for path in ['force-app/main/default/lwc/example/example.js-meta.xml',
                     'ui/example.tsx', 'agents/example.agent',
                     'force-app/main/default/managedContentTypes/example.xml']:
            file = self.project / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text('fixture')
        detected = self.context._detect_plugin_signals(self.project)
        self.assertEqual({'lwc','react','agentforce','cms'}, {s[0] for s in detected})
        for _, _, query in detected:
            self.assertFalse(TARGETS & {m.plugin['name'] for m in self.score(query)})
        expected = {'experience-lwc','experience-react','agentforce-adlc','experience-cms'}
        for _, _, query in detected:
            self.assertTrue(expected & {m.plugin['name'] for m in self.score(query) if m.band == 'high'})
        model, paint = self.context._session_start_plugin_slot('precision-signals','startup',self.project)
        for name in expected:
            self.assertIn(name, model + paint)
        for name in TARGETS:
            self.assertNotIn(name, model + paint)

    def test_messaging_setup_and_combined_routing_ownership(self):
        if 'service-omni' not in self.present:
            self.skipTest('Omni is not present in this independent catalog')
        setup = 'Set up a Salesforce Digital Engagement messaging channel'
        combined = setup + ' and configure Omni queue routing with presence statuses'
        for surface in ('user-prompt','session-start','discovery-command','bypass-gate'):
            self.assertNotIn('service-omni', self.surfaced(setup, surface))
            self.assertIn('service-engagement', self.surfaced(setup, surface))
            output = self.surfaced(combined, surface)
            self.assertIn('service-engagement', output)
            self.assertIn('service-omni', output)

    def test_all_curated_routes_keep_the_existing_discovery_confidence_bar(self):
        for row in self.data['plugins']:
            if row['name'] == 'salesforce-development':
                continue
            for prompt in row['match']['examplePrompts']:
                with self.subTest(plugin=row['name'], prompt=prompt):
                    self.assertIn(row['name'], {m.plugin['name'] for m in self.score(prompt, False)
                                               if m.band == 'high'})

    def test_optional_policy_preserves_scores_and_legacy_anchor_bypass(self):
        for row in self.data['plugins']:
            if row['name'] == 'consumer-goods':
                self.assertIs(row['match'].get('enforceAnchorsOnAllSurfaces'), True)
            elif row['name'] not in self.present:
                self.assertNotIn('enforceAnchorsOnAllSurfaces', row['match'])
        policies = {row['name'] for row in self.data['plugins']
                    if row['match'].get('enforceAnchorsOnAllSurfaces')}
        for name in self.present | policies:
            before = copy.deepcopy(self.data)
            for row in before['plugins']:
                if row['name'] == name:
                    row['match'].pop('enforceAnchorsOnAllSurfaces', None)
            target = next(row for row in self.data['plugins'] if row['name'] == name)
            prompt = POSITIVES[name][0] if name in POSITIVES else target['match']['examplePrompts'][0]
            old = self.catalog.score_prompt_against_catalog(prompt, before, require_anchor_terms=False)
            new = self.catalog.score_prompt_against_catalog(prompt, self.data, require_anchor_terms=False)
            # Changing one policy must preserve all scores on its supported request.
            self.assertEqual(
                {m.plugin['name']: (m.score, m.band) for m in old},
                {m.plugin['name']: (m.score, m.band) for m in new})
        row = copy.deepcopy(self.data['plugins'][0])
        row['match'] = {'description':'Unique task vocabulary', 'keywords':['unique'],
                        'examplePrompts':['Create a unique task'], 'anchorTerms':['absent']}
        catalog = {**self.data, 'plugins':[row]}
        self.assertFalse(self.catalog.score_prompt_against_catalog('unique task vocabulary',catalog))
        self.assertTrue(self.catalog.score_prompt_against_catalog('unique task vocabulary',catalog,require_anchor_terms=False))

    def test_policy_validation_build_and_load(self):
        for bad in [None, 0, 1, 'true', 'false', [], {}, ['omni']]:
            with self.subTest(value=bad):
                data = copy.deepcopy(self.data)
                data['plugins'][0]['match']['enforceAnchorsOnAllSurfaces'] = bad
                with self.assertRaises(self.catalog.PluginCatalogError):
                    self.catalog._validate_catalog(data, 'fixture')
                with tempfile.TemporaryDirectory() as td:
                    root = Path(td)
                    (root / '.claude-plugin').mkdir()
                    entry = {'name': 'example', 'source': './plugins/example',
                             'description': 'Example capability', 'keywords': ['example'],
                             'metadata': {'match': {'examplePrompts': ['Create an example'],
                                                    'anchorTerms': ['example'],
                                                    'enforceAnchorsOnAllSurfaces': bad}}}
                    (root / '.claude-plugin/marketplace.json').write_text(
                        json.dumps({'name': 'example', 'plugins': [entry]}))
                    (root / 'config.yml').write_text('internalPlugins: []')
                    with self.assertRaises(self.catalog.PluginCatalogError):
                        self.catalog.build_catalog(root, root)
        # Enforcing an absent gate is a configuration error, not silent opt-in.
        data = copy.deepcopy(self.data)
        data['plugins'][0]['match'].pop('anchorTerms', None)
        data['plugins'][0]['match'].pop('anchorCompanions', None)
        data['plugins'][0]['match']['enforceAnchorsOnAllSurfaces'] = True
        with self.assertRaisesRegex(self.catalog.PluginCatalogError, 'requires anchorTerms'):
            self.catalog._validate_catalog(data, 'fixture')
        data['plugins'][0]['match']['enforceAnchorsOnAllSurfaces'] = False
        self.catalog._validate_catalog(data, 'fixture')

    def test_replaced_evidence_field_cannot_be_silently_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / '.claude-plugin').mkdir()
            entry = {'name': 'example', 'source': './plugins/example',
                     'description': 'Example capability', 'keywords': ['example'],
                     'metadata': {'match': {'examplePrompts': ['Create an example'],
                                            'requiredEvidence': [['example']]}}}
            (root / '.claude-plugin/marketplace.json').write_text(
                json.dumps({'name': 'example', 'plugins': [entry]}))
            (root / 'config.yml').write_text('internalPlugins: []')
            with self.assertRaisesRegex(self.catalog.PluginCatalogError, 'uses replaced'):
                self.catalog.build_catalog(root, root)



if __name__ == '__main__':
    unittest.main()
