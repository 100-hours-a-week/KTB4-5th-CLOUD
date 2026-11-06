"""계획 기본값과 환경 제한·관측 개인정보 경계를 검증합니다."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import run
import observe
from common import Kube
from observe import compact
from preflight import clock


class PreparationTests(unittest.TestCase):
    def test_default_command_does_not_query_or_execute(self):
        with patch.object(sys, 'argv', ['run.py']), patch('run.run') as execute, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(run.main(), 0)
        execute.assert_not_called()
        self.assertEqual(json.loads(out.getvalue())['action'], 'plan_only')

    def test_reject_non_stage_plan_before_any_execution(self):
        plan = run.load_plan()
        plan['baseUrl'] = 'https://dev.dameokja.com'
        with patch('pathlib.Path.read_text', return_value=json.dumps(plan)):
            with self.assertRaisesRegex(RuntimeError, 'stage'):
                run.load_plan()

    def test_reject_excessive_load_plan(self):
        plan = run.load_plan()
        plan['hpa']['stages'][0]['target'] = 1000
        with patch('pathlib.Path.read_text', return_value=json.dumps(plan)):
            with self.assertRaisesRegex(RuntimeError, '50'):
                run.load_plan()

    def test_pod_record_does_not_contain_environment_or_credentials(self):
        record = compact({'kind': 'Pod', 'metadata': {'name': 'be-blue-1'}, 'spec': {'nodeName': 'node-1', 'containers': [
            {'name': 'be', 'env': [{'name': 'PASSWORD', 'value': 'SECRET_VALUE'}], 'envFrom': [{'secretRef': {'name': 'be-secrets'}}]}]}})
        self.assertNotIn('SECRET_VALUE', json.dumps(record))
        self.assertNotIn('envFrom', json.dumps(record))
        self.assertEqual(record['spec']['nodeName'], 'node-1')

    def test_event_keeps_object_and_event_timestamps(self):
        event = {'kind': 'Event', 'metadata': {'name': 'scale'}, 'reason': 'SuccessfulRescale',
                 'eventTime': '2026-11-06T08:00:00Z', 'involvedObject': {'uid': 'pod-uid'}, 'count': 1}
        result = compact(event)
        self.assertEqual(result['eventTime'], event['eventTime'])
        self.assertEqual(result['involvedObject']['uid'], 'pod-uid')

    def test_unsynchronized_clock_blocks_execution(self):
        with patch('preflight.platform.system', return_value='Linux'), patch('preflight.subprocess.run') as command:
            command.return_value.returncode = 0
            command.return_value.stdout = 'no\n'
            with self.assertRaisesRegex(RuntimeError, 'NTP'):
                clock()

    def test_non_eks_context_is_rejected(self):
        args = type('Args', (), {'kubeconfig': 'local.yaml', 'server': None})()
        with patch('common.command_json', return_value={'clusters': [{'name': 'docker-desktop', 'cluster': {'server': 'https://localhost:6443'}}]}):
            with self.assertRaises(ValueError):
                Kube(args).validate()

    def test_windows_local_clock_is_not_accepted_as_ntp(self):
        with patch('preflight.platform.system', return_value='Windows'), patch('preflight.subprocess.run') as command:
            command.return_value.returncode = 0
            command.return_value.stdout = 'Leap Indicator: 0\nStratum: 1\nSource: Local CMOS Clock\n'
            with self.assertRaisesRegex(RuntimeError, 'NTP'):
                clock()

    def test_hpa_cannot_skip_successful_smoke(self):
        args = SimpleNamespace(kubeconfig='local.yaml', mode='hpa', smoke_manifest=None)
        with patch('run.inspect') as preflight:
            with self.assertRaisesRegex(RuntimeError, 'smoke'):
                run.run(args, run.load_plan())
        preflight.assert_not_called()

    def test_observer_records_initial_state_and_finishes_without_cloud_calls(self):
        cache = Path(__file__).resolve().parent / '.cache'
        cache.mkdir(exist_ok=True)
        def fake_get(*args):
            return {'items': [{'metadata': {'name': 'fixture'}, 'spec': {'nodeName': 'fixture-node'},
                               'status': {'conditions': []}}]}
        def fake_aws(args, service, *parts):
            return {'Account': '906462447513'} if service == 'sts' else []
        with tempfile.TemporaryDirectory(dir=cache) as directory:
            output = Path(directory) / 'timeline.jsonl'
            arguments = ['observe.py', '--kubeconfig', 'offline.yaml', '--output', str(output), '--seconds', '1']
            with patch.object(sys, 'argv', arguments), patch('observe.Kube.validate'), \
                    patch('observe.Kube.get', side_effect=fake_get), patch('observe.aws', side_effect=fake_aws):
                observe.main()
            records = [json.loads(line) for line in output.read_text(encoding='utf-8').splitlines()]
            self.assertEqual(records[-1]['source'], 'observer-finish')
            self.assertEqual(records[-1]['errors'], 0)
            pods = next(record for record in records if record['source'] == 'pods')
            self.assertEqual(pods['data'][0]['kind'], 'Pod')
            self.assertEqual(pods['data'][0]['spec']['nodeName'], 'fixture-node')
            self.assertIn('requestedAt', pods)


if __name__ == '__main__':
    unittest.main()
