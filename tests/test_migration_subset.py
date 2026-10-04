import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import migration_ci as ci
from test_migration_contract import fixture


class MigrationSubsetTests(unittest.TestCase):
    def test_blank_preserves_full_roster_and_valid_subset_uses_roster_order(self):
        regions=['canada','russia','germany']
        self.assertEqual(ci.select_regions(regions,''),regions)
        self.assertEqual(ci.select_regions(regions,'["russia"]'),['russia'])
        self.assertEqual(ci.select_regions(regions,'["germany","canada"]'),['canada','germany'])

    def test_unknown_duplicate_empty_and_injected_subset_fail_closed(self):
        for raw in ('[]','null','{}','true','"russia"','[1]','[["russia"]]',
                    '["russia","russia"]','["unknown"]','["$(touch /tmp/injected)"]',
                    '["russia\\nregions=evil"]','["Russia"]',' ','not-json'):
            with self.subTest(raw=raw),self.assertRaises(ValueError):ci.select_regions(['russia'],raw)

    def test_prepare_validates_complete_supply_before_selecting_subset(self):
        release,ready=fixture()
        def fetch(url,token=None,**kwargs):
            if '/releases/tags/' in url:return json.dumps(release).encode()
            if '/assets?' in url:return json.dumps(release['assets']).encode()
            return ready
        with tempfile.TemporaryDirectory() as folder, patch.object(ci,'fetch',side_effect=fetch), \
                patch('migration_contract.subprocess.check_output',return_value='a'*40+'\n'):
            output=io.StringIO()
            with redirect_stdout(output):
                chosen=ci.prepare(release['tag_name'],Path(folder),'gap3','a'*40,'["central-america"]')
            self.assertEqual(chosen,['central-america'])
            event=json.loads(output.getvalue())
            self.assertEqual((event['validated_regions'],event['selected_regions'],event['scope']),(3,1,'subset'))
            contract=json.loads((Path(folder)/'source-contract.json').read_text())
            self.assertEqual(contract,ci.source_contract(ci.load_profile('gap3'),'a'*40))
            release['assets']=[a for a in release['assets'] if not a['name'].startswith('tiles-grytviken-window')]
            with patch.object(ci,'select_regions',wraps=ci.select_regions) as selector:
                with self.assertRaises(ValueError):
                    ci.prepare(release['tag_name'],Path(folder),'gap3','a'*40,'["central-america"]')
                selector.assert_not_called()

    def test_workflow_full_group_preserved_recovery_isolated_and_inputs_are_env_only(self):
        workflow=(ROOT/'.github/workflows/migrate-release-r2.yml').read_text()
        # Exercise the actual expression for legacy callers and both explicit lanes.
        line=next(line.strip() for line in workflow.splitlines() if line.strip().startswith('group:'))
        expression=line.removeprefix('group: ${{ ').removesuffix(' }}').replace('&&','and').replace('||','or')
        primary='migrate-release-tiles-to-r2';recovery=primary+'-recovery'
        for lane in (None,'','auto','primary','recovery'):
            for subset in ('','["russia"]'):
                wanted=primary if lane=='primary' or lane!='recovery' and not subset else recovery
                actual=eval(expression,{'__builtins__':{}},{'inputs':SimpleNamespace(queue_lane=lane,region_subset=subset)})
                self.assertEqual(actual,wanted,(lane,subset))
        self.assertIn('options: [auto, primary, recovery]',workflow)
        self.assertIn('case "$QUEUE_LANE" in auto|primary|recovery)',workflow)
        self.assertIn('max-parallel: 4',workflow)
        self.assertIn('cancel-in-progress: false',workflow)
        self.assertIn('REGION_SUBSET: ${{ inputs.region_subset }}',workflow)
        self.assertEqual(workflow.count('--region-subset "$REGION_SUBSET" prepare'),2)
        self.assertIn('region: ${{ fromJSON(needs.pilot.outputs.regions) }}',workflow)
        self.assertIn('timeout-minutes: 180',workflow)
        self.assertIn('Verify twenty actual tiles in private R2',workflow)
        self.assertIn('if: inputs.mode == \'full\'',workflow)
        self.assertNotIn('inputs.region_subset', '\n'.join(line for line in workflow.splitlines() if 'python3 ' in line))


if __name__=='__main__':unittest.main()
