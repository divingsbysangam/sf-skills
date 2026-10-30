#!/usr/bin/env python3
"""Patch routing and primary-session context without org access or tool execution."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import codex_edit_advisory as edits
import codex_hooks as hooks


class CodexEditContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / 'project with spaces'
        self.project.mkdir()
        (self.project / 'sfdx-project.json').write_text('{}')
        old = Path.cwd()
        self.addCleanup(os.chdir, old)
        os.chdir(self.project)
        patch = mock.patch.dict(os.environ, {'DO_NOT_TRACK': 'true',
            'SF_DISABLE_TELEMETRY': 'true', 'SF_AGENT_CLIENT': 'codex',
            'SF_PLUGIN_UI_MODE': 'off', 'SFDX_AUTO_DEPLOY': '1'})
        patch.start()
        self.addCleanup(patch.stop)

    def payload(self, patch):
        return {'cwd': str(self.project), 'tool_name': 'apply_patch',
                'tool_input': {'command': patch}, 'session_id': 'edit-contract'}

    def invoke(self, patch):
        with mock.patch.object(subprocess, 'run', side_effect=AssertionError('No subprocess')):
            return hooks.dispatch('PreToolUse', self.payload(patch))

    def text(self, output):
        self.assertNotEqual('deny', output.get('hookSpecificOutput', {}).get('permissionDecision'))
        return output.get('hookSpecificOutput', {}).get('additionalContext', '')

    def test_multi_file_dedup_and_primary_flow_skill(self):
        patch = ('*** Begin Patch\n*** Add File: force-app/One.cls\n+class One {}\n'
                 '*** Add File: force-app/Two.cls\n+class Two {}\n'
                 '*** Add File: force-app/OneTest.cls\n+class OneTest {}\n'
                 '*** Add File: force-app/flows/X.flow-meta.xml\n+<Flow/>\n*** End Patch')
        text = self.text(self.invoke(patch))
        self.assertEqual(1, text.count('platform-apex-generate'))
        self.assertIn('platform-apex-test-generate', text)
        self.assertIn('automation-flow-generate', text)
        self.assertIn('Automatic deployment after edits is disabled', text)
        self.assertFalse((self.project / 'force-app').exists())

    def test_rename_checks_destination_and_original_but_delete_is_not_authoring(self):
        patch = ('*** Begin Patch\n*** Update File: A.cls\n*** Move to: notes.txt\n@@\n-x\n+y\n'
                 '*** Update File: old.txt\n*** Move to: BTest.cls\n@@\n-x\n+y\n'
                 '*** Delete File: Deleted.cls\n*** End Patch')
        text = self.text(self.invoke(patch))
        self.assertIn('platform-apex-generate', text)
        self.assertIn('platform-apex-test-generate', text)
        self.assertEqual({'continue': True}, self.invoke('*** Begin Patch\n*** Delete File: A.cls\n*** End Patch'))

    def test_manifest_add_warns_without_dispatch_enforcement(self):
        text = self.text(self.invoke('*** Begin Patch\n*** Add File: manifest/package.xml\n+<Package/>\n*** End Patch'))
        self.assertIn('platform-manifest-generate', text)
        self.assertIn('does not block', text)

    def test_manifest_content_and_generic_metadata_keep_shared_advice(self):
        patch='*** Begin Patch\n*** Add File: manifest/pkg.xml\n+<Package xmlns="http://soap.sforce.com/2006/04/metadata"/>\n*** End Patch'
        self.assertIn('platform-manifest-generate',self.text(self.invoke(patch)))
        patch='*** Begin Patch\n*** Add File: X.businessProcess-meta.xml\n+<BusinessProcess/>\n*** End Patch'
        self.assertIn('installed Salesforce metadata skills',self.text(self.invoke(patch)))

    def test_content_that_looks_like_patch_header_does_not_route_a_phantom_file(self):
        patch = ('*** Begin Patch\n*** Add File: notes.txt\n'
                 '+*** Add File: A.cls\n+*** Update File: X.flow-meta.xml\n*** End Patch')
        self.assertEqual({'continue': True}, self.invoke(patch))

    def test_ownerless_metadata_sidecars_and_regular_files_stay_silent(self):
        for name in ['A.cls-meta.xml', 'A.trigger-meta.xml', 'X.settings-meta.xml',
                     'X.labels-meta.xml', 'X.reportFolder-meta.xml', 'README.md']:
            with self.subTest(name=name):
                self.assertEqual({'continue': True}, self.invoke(
                    '*** Begin Patch\n*** Add File: '+name+'\n+x\n*** End Patch'))

    def test_field_object_permission_routing(self):
        for name, skill in [('objects/X__c/X__c.object-meta.xml','platform-custom-object-generate'),
                            ('objects/X__c/fields/F__c.field-meta.xml','platform-custom-field-generate'),
                            ('X.permissionset-meta.xml','platform-permission-set-generate')]:
            with self.subTest(name=name):
                self.assertIn(skill,self.text(self.invoke('*** Begin Patch\n*** Add File: '+name+'\n+x\n*** End Patch')))

    def test_whole_patch_is_checked_before_any_advice(self):
        for name in ['../A.cls', str(Path(self.temp.name) / 'outside.cls')]:
            patch='*** Begin Patch\n*** Add File: Valid.cls\n+x\n*** Add File: '+name+'\n+y\n*** End Patch'
            text=self.text(self.invoke(patch))
            self.assertIn('unavailable',text)
            self.assertNotIn('platform-apex-generate',text)
            self.assertNotIn(name,text)

    def test_symlink_and_rename_escape_stay_advisory_only(self):
        (self.project/'escape').symlink_to(Path(self.temp.name),target_is_directory=True)
        for patch in ['*** Begin Patch\n*** Add File: escape/A.cls\n+x\n*** End Patch',
                      '*** Begin Patch\n*** Update File: A.cls\n*** Move to: ../B.cls\n@@\n-x\n+y\n*** End Patch']:
            self.assertIn('unavailable',self.text(self.invoke(patch)))

    def test_malformed_and_oversized_patches_do_not_raise_or_block(self):
        for patch in [None,'bad','*** Begin Patch\n*** Update File: A.cls\n*** End Patch',
                      '*** Begin Patch\n*** Delete File: A.cls\n+bad\n*** End Patch',
                      '*** Begin Patch\n*** Add File: A.cls\n+'+'x'*1_000_000+'\n*** End Patch']:
            with self.subTest(patch_type=type(patch).__name__):
                self.assertIn('unavailable',self.text(self.invoke(patch)))

    def test_eof_marker_and_following_hunk_contract(self):
        patch='*** Begin Patch\n*** Update File: A.cls\n@@\n-x\n+y\n*** End of File\n@@ class X\n x\n+z\n*** End Patch'
        self.assertIn('platform-apex-generate',self.text(self.invoke(patch)))

    def test_non_project_and_uninstalled_skill_do_not_invent_advice(self):
        (self.project/'sfdx-project.json').unlink()
        self.assertEqual({'continue':True},self.invoke('*** Begin Patch\n*** Add File: A.cls\n+x\n*** End Patch'))
        with mock.patch.object(edits.telemetry,'_in_sf_project',return_value=True), \
             mock.patch.object(edits,'ROOT',Path(self.temp.name)/'uninstalled'):
            self.assertEqual({'continue':True},self.invoke('*** Begin Patch\n*** Add File: A.cls\n+x\n*** End Patch'))

    def test_shell_advice_does_not_require_or_manufacture_dispatch_ledger(self):
        for command in ['sf data query --query "SELECT Id FROM Account"',
                        'sf apex run test --class-names X', 'sf project retrieve start']:
            output=edits.pre_shell_advice(dict(self.payload(''),tool_name='Bash',tool_input={'command':command}))
            self.assertIn('skills-first advisory',self.text(output))
        for command in ['echo sf data query', 'rg "sf apex run test" .',
                        'sf version --json', 'sf org display --json']:
            self.assertEqual({'continue':True},edits.pre_shell_advice(
                dict(self.payload(''),tool_name='Bash',tool_input={'command':command})))

    def test_primary_context_survives_ui_off_and_telemetry_optout(self):
        result=hooks.dispatch('SessionStart',self.payload(''))
        text=result['hookSpecificOutput']['additionalContext']
        for term in ['--json','direct Salesforce APIs','owning skill and target org',
                     'platform-metadata-deploy','platform-destructive-deploy','code analysis',
                     'governor-limit','Automatic deployment','dispatch ledger']:
            self.assertIn(term,text)
        self.assertNotIn('Telemetry Usage',result.get('systemMessage',''))
        (self.project/'sfdx-project.json').unlink()
        self.assertNotIn('hookSpecificOutput',hooks.dispatch('SessionStart',self.payload('')))


if __name__=='__main__':
    unittest.main()
