"""배포·HTTP 요청 없이 Helm·Alloy·PromQL·k6 설정을 검증합니다."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import promql_parser
import yaml

from build_dashboard import dashboard
from run import load_plan

ROOT = Path(__file__).resolve().parents[2]


def command(*args, success=True):
    result = subprocess.run([str(arg) for arg in args], cwd=ROOT, capture_output=True,
                            text=True, encoding='utf-8', errors='replace', timeout=90)
    if success and result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)
    if not success and result.returncode == 0:
        raise AssertionError('거부해야 하는 설정이 통과했습니다.')
    return result.stdout


def render(release, chart, values):
    return list(yaml.safe_load_all(command('helm', 'template', release, chart, '-n', 'tool', '-f', values)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--alloy', required=True)
    parser.add_argument('--k6', required=True)
    parser.add_argument('--chart-dir', type=Path, default=ROOT / 'tests/stage/.cache')
    args = parser.parse_args()
    alloy_chart = args.chart_dir / 'alloy-1.15.0.tgz'
    ksm_chart = args.chart_dir / 'kube-state-metrics-9.0.0.tgz'
    plan = load_plan()
    assert (ROOT / 'infra/v3/cloudformation/observability.yaml').stat().st_size < 51200
    command(sys.executable, 'tests/stage/build_dashboard.py')
    panel_ids = [panel['id'] for panel in dashboard()['panels']]
    assert len(panel_ids) == len(set(panel_ids))
    parsed = 0
    for panel in dashboard()['panels']:
        if panel['type'] == 'logs':
            continue
        for target in panel['targets']:
            promql_parser.parse(target['expr'].replace('$cluster', 'dameokja-v3').replace('$namespace', 'stage'))
            parsed += 1
    with tempfile.TemporaryDirectory(prefix='stage-validate-') as temporary:
        folder = Path(temporary)
        for release, file in [('alloy', 'alloy.yaml'), ('alloy-events', 'alloy-events.yaml')]:
            values = ROOT / 'v3/platform/values' / file
            objects = render(release, alloy_chart, values)
            data = yaml.safe_load(values.read_text(encoding='utf-8'))
            config = folder / f'{release}.alloy'
            config.write_text(data['alloy']['configMap']['content'], encoding='utf-8')
            command(args.alloy, 'validate', config)
            controllers = [item for item in objects if item and item['kind'] in ['DaemonSet', 'Deployment']]
            assert len(controllers) == 1
            controller = controllers[0]
            if release == 'alloy':
                assert controller['kind'] == 'DaemonSet'
                assert any(t.get('value') == 'stage' for t in controller['spec']['template']['spec']['tolerations'])
            else:
                assert controller['kind'] == 'Deployment' and controller['spec']['replicas'] == 1
                rules = [rule for item in objects if item and item['kind'] in ['Role', 'ClusterRole'] for rule in item.get('rules', [])]
                assert rules and all(rule['resources'] == ['events'] for rule in rules)
        ksm = render('kube-state-metrics', ksm_chart, ROOT / 'v3/platform/values/kube-state-metrics.yaml')
        rules = [rule for item in ksm if item and item['kind'] == 'ClusterRole' for rule in item['rules']]
        assert any('horizontalpodautoscalers' in rule.get('resources', []) and 'watch' in rule['verbs'] for rule in rules)
        deploy = next(item for item in ksm if item and item['kind'] == 'Deployment')
        assert 'horizontalpodautoscalers' in ' '.join(deploy['spec']['template']['spec']['containers'][0]['args'])
        for env in ['dev', 'stage']:
            manifest = list(yaml.safe_load_all(command('helm', 'template', 'be', 'v3/charts/application', '-n', env,
                                                      '-f', f'v3/environments/{env}/be.yaml')))
            assert not any(item and item['kind'] == 'HorizontalPodAutoscaler' for item in manifest)
            config = next(item for item in manifest if item and item['kind'] == 'ConfigMap')
            if env == 'stage':
                assert config['data']['MANAGEMENT_ENDPOINTS_WEB_EXPOSURE_INCLUDE'] == 'health,prometheus'
                settings = json.loads(config['data']['SPRING_APPLICATION_JSON'])
                assert settings['management']['metrics']['distribution']['percentiles-histogram']['http.server.requests']
        fixture = yaml.safe_load((ROOT / 'v3/charts/platform/values.yaml').read_text(encoding='utf-8'))
        fixture.update(appNodeRoleName='dameokja-test', workerSecurityGroupId='sg-0123456789abcdef0',
                       gatewayAlbSecurityGroupId='sg-0123456789abcdef0')
        for env in fixture['environments'].values():
            env['podSecurityGroups'] = dict.fromkeys(env['podSecurityGroups'], 'sg-0123456789abcdef0')
        fixture_path = folder / 'platform.yaml'
        fixture_path.write_text(yaml.safe_dump(fixture), encoding='utf-8')
        platform = render('platform', ROOT / 'v3/charts/platform', fixture_path)
        role = next(item for item in platform if item and item['kind'] == 'Role' and item['metadata']['name'] == 'stage-power')
        rule = next(rule for rule in role['rules'] if 'horizontalpodautoscalers' in rule['resources'])
        assert rule['resourceNames'] == ['be-load-test'] and set(rule['verbs']) == {'get', 'delete'}
        hpa = yaml.safe_load((ROOT / 'tests/stage/be-hpa.yaml').read_text(encoding='utf-8'))
        assert hpa['metadata']['namespace'] == 'stage' and hpa['spec']['scaleTargetRef']['name'] == plan['deployment']
        assert hpa['spec']['minReplicas'] == 2 and hpa['spec']['maxReplicas'] == 6
        # 임시 가짜 계정은 inspect 전용이며 실제 네트워크 요청을 실행하지 않습니다.
        accounts = folder / 'accounts.json'
        accounts.write_text(json.dumps({'baseUrl': plan['baseUrl'], 'accounts': [
            {'loginId': f'validation-{i}', 'password': 'offline-only', 'refrigeratorId': str(i)} for i in range(50)]}), encoding='utf-8')
        for mode in ['smoke', 'hpa']:
            options = json.loads(command(args.k6, 'inspect', '--execution-requirements', '-e', f'MODE={mode}', '-e', 'RUN_ID=offline-validation',
                '-e', f'ACCOUNTS_FILE={accounts}', 'tests/stage/stage-read.js'))
            assert options['scenarios']['stage_read']['executor'] == ('per-vu-iterations' if mode == 'smoke' else 'ramping-arrival-rate')
        command(args.k6, 'inspect', '-e', 'BASE_URL=https://dev.dameokja.com', '-e', 'RUN_ID=offline-validation',
                '-e', f'ACCOUNTS_FILE={accounts}', 'tests/stage/stage-read.js', success=False)
    command(sys.executable, '-c', 'from cfnlint.runner import main; main()', '--regions', 'ap-northeast-2', '--', 'infra/v3/cloudformation/observability.yaml')
    print(f'검증 완료: Helm 5종, Alloy 2종, PromQL {parsed}개, k6 smoke/HPA 및 dev 차단, CloudFormation')


if __name__ == '__main__':
    main()
