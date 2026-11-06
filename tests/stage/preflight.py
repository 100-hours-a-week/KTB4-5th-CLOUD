"""부하를 보내지 않고 stage 관측·HPA·시각 동기화 준비 상태를 검사합니다."""
import json
import locale
from pathlib import Path
import platform
import re
import shutil
import subprocess

from common import ACCOUNT, Kube, api, aws, now, query


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def clock():
    if platform.system() == 'Windows':
        command = ['w32tm', '/query', '/status']
    else:
        command = ['timedatectl', 'show', '-p', 'NTPSynchronized', '--value']
    result = subprocess.run(command, capture_output=True, text=True,
                            encoding=locale.getencoding(), errors='replace', timeout=10)
    if platform.system() == 'Windows':
        stratum = re.search(r'(?:Stratum|계층)\s*:\s*(\d+)', result.stdout)
        leap = re.search(r'(?:Leap Indicator|윤초 표시기)\s*:\s*(\d+)', result.stdout)
        local_clock = any(source in result.stdout for source in ['Local CMOS Clock', 'Free-running System Clock', '로컬 CMOS 시계'])
        synced = result.returncode == 0 and stratum and leap and int(leap[1]) != 3 and not local_clock and 1 <= int(stratum[1]) <= 15
    else:
        synced = result.returncode == 0 and result.stdout.strip() == 'yes'
    require(synced, '부하 발생 호스트의 NTP 동기화 확인 실패. 동기화·조회 권한을 확인하세요.')
    return {'checkedAt': now(), 'host': platform.node(), 'command': command, 'output': result.stdout.strip()}


def ready(pod):
    return any(condition['type'] == 'Ready' and condition['status'] == 'True'
               for condition in pod.get('status', {}).get('conditions', []))


def inspect(args, plan):
    require(shutil.which(args.k6), 'k6 실행 파일이 없습니다. --k6 경로를 지정하세요.')
    version = subprocess.run([args.k6, 'version'], capture_output=True, text=True, timeout=10)
    require(version.returncode == 0 and 'v2.3.0' in version.stdout, '검증한 k6 v2.3.0을 사용하세요.')
    require(args.prom_url and args.loki_url, '--prom-url과 --loki-url이 필요합니다.')
    require(args.accounts.is_file(), 'stage 전용 계정 JSON이 필요합니다. 예제에 실제 값을 넣어 private/에 저장하세요.')
    data = json.loads(args.accounts.read_text(encoding='utf-8-sig'))
    count = plan['smoke']['vus'] if args.mode == 'smoke' else plan['hpa']['maxVUs']
    accounts = data.get('accounts', [])
    require(data.get('baseUrl') == plan['baseUrl'] and len(accounts) >= count, f'stage 전용 계정 {count}개 이상 필요')
    require(len({a.get('loginId') for a in accounts}) == len(accounts), '계정 loginId가 중복됩니다.')
    require(all(all(isinstance(a.get(key), str) and a[key] and 'REPLACE' not in a[key]
                    for key in ['loginId', 'password', 'refrigeratorId']) for a in accounts), '테스트 계정 필수 값이 잘못되었습니다.')
    result = {'checkedAt': now(), 'clock': clock(), 'k6Version': version.stdout.strip()}
    require(aws(args, 'sts', 'get-caller-identity')['Account'] == ACCOUNT, 'AWS 계정이 다릅니다.')
    kube = Kube(args)
    kube.validate()
    deployment = kube.get('deploy', 'be-blue', '-n', 'stage')
    replicas = deployment['spec'].get('replicas', 0)
    require(replicas >= 2 and deployment['status'].get('readyReplicas', 0) == replicas, 'stage BE가 Ready 2개 이상이어야 합니다.')
    service = kube.get('svc', 'be', '-n', 'stage')
    require(service['spec']['selector'].get('dameokja.com/color') == 'blue', 'HPA 대상 be-blue와 활성 Service 색상이 다릅니다.')
    application = kube.get('application', 'stage-be', '-n', 'tool')
    require(application.get('spec', {}).get('syncPolicy', {}).get('automated') is None,
            '시험 중 replica 충돌 방지를 위해 stage-be 자동 sync가 꺼져 있어야 합니다.')
    require(application.get('status', {}).get('operationState', {}).get('phase') != 'Running', 'Argo sync가 진행 중입니다.')
    pods = kube.get('pods', '-n', 'stage')['items']
    require(pods and all(ready(pod) for pod in pods), 'stage에 Ready가 아닌 Pod가 있습니다.')
    nodes = kube.get('nodes', '-l', 'dameokja.com/environment=stage')['items']
    require(nodes, 'stage Node가 없습니다.')
    alloys = kube.get('pods', '-n', 'tool', '-l', 'app.kubernetes.io/name=alloy')['items']
    for node in nodes:
        require(any(pod['spec'].get('nodeName') == node['metadata']['name'] and ready(pod) for pod in alloys),
                'stage Node에 Ready Alloy가 없습니다.')
    events = kube.get('deploy', 'alloy-events', '-n', 'tool')
    require(events['spec'].get('replicas') == 1 and events['status'].get('readyReplicas') == 1, '이벤트 수집기가 Ready 1개여야 합니다.')
    ksm = kube.get('deploy', 'kube-state-metrics', '-n', 'tool')
    require('horizontalpodautoscalers' in ' '.join(ksm['spec']['template']['spec']['containers'][0].get('args', [])),
            'KSM HPA collector가 없습니다.')
    metric_api = kube.get('apiservice', 'v1beta1.metrics.k8s.io')
    require(any(c['type'] == 'Available' and c['status'] == 'True' for c in metric_api['status']['conditions']),
            'metrics-server API가 Available이 아닙니다.')
    be_scope = 'cluster="dameokja-v3",namespace="stage",job="prometheus.scrape.be"'
    up = query(args.prom_url, f'up{{{be_scope}}}')
    require(len(up) == replicas and all(float(s['value'][1]) == 1 for s in up), 'stage BE scrape 타깃이 빠졌거나 up!=1입니다.')
    cadence = query(args.prom_url, f'count_over_time(up{{{be_scope}}}[1m])')
    require(len(cadence) == replicas and all(float(s['value'][1]) >= 3 for s in cadence), '15초 scrape 안정화가 필요합니다(최소 1분 대기).')
    for node in nodes:
        name = node['metadata']['name']
        for job in ['cadvisor', 'kubelet']:
            samples = query(args.prom_url, f'up{{cluster="dameokja-v3",job="prometheus.scrape.{job}",instance="{name}"}}')
            require(len(samples) == 1 and float(samples[0]['value'][1]) == 1, f'stage {job} 수집 실패: {name}')
    for metric in ['kube_pod_container_resource_requests', 'container_cpu_usage_seconds_total',
                   'container_memory_working_set_bytes', 'http_server_requests_seconds_bucket']:
        require(query(args.prom_url, f'count({metric}{{cluster="dameokja-v3",namespace="stage"}})'), f'{metric} stage 시계열이 없습니다.')
    logs = api(args.loki_url, '/loki/api/v1/query', {'query': 'sum(count_over_time({cluster="dameokja-v3",namespace="stage",source!="kubernetes-events"}[5m]))'})
    require(logs['result'] and float(logs['result'][0]['value'][1]) > 0, '최근 5분 stage Pod 로그가 없습니다.')
    if args.mode == 'hpa':
        hpa = kube.get('hpa', 'be-load-test', '-n', 'stage')
        require(replicas == 2 and hpa.get('status', {}).get('currentReplicas') == 2
                and hpa.get('status', {}).get('desiredReplicas') == 2,
                'HPA 시험 전 current/desired/Ready replica가 모두 기준값 2로 안정화되어야 합니다.')
        spec = hpa['spec']
        require(spec['scaleTargetRef']['name'] == 'be-blue' and spec['minReplicas'] == 2 and spec['maxReplicas'] == 6,
                '시험 HPA 대상·min/max가 준비 설정과 다릅니다.')
        require(spec['metrics'] == [{'type': 'Resource', 'resource': {'name': 'cpu', 'target': {'type': 'Utilization', 'averageUtilization': 60}}}],
                '시험 HPA CPU 목표가 60%가 아닙니다.')
        conditions = {c['type']: c['status'] for c in hpa.get('status', {}).get('conditions', [])}
        require(conditions.get('AbleToScale') == 'True' and conditions.get('ScalingActive') == 'True', 'HPA가 CPU 입력을 계산할 수 없습니다.')
        for metric in ['status_current_replicas', 'status_desired_replicas', 'status_condition']:
            require(query(args.prom_url, f'kube_horizontalpodautoscaler_{metric}{{cluster="dameokja-v3",namespace="stage",horizontalpodautoscaler="be-load-test"}}'),
                    'KSM HPA 시계열이 없습니다.')
        require(args.target_group_arn and args.target_group_arn.startswith(
            f'arn:aws:elasticloadbalancing:ap-northeast-2:{ACCOUNT}:targetgroup/dameokja-v3-stage-be/'), 'stage BE Target Group ARN이 필요합니다.')
        health = aws(args, 'elbv2', 'describe-target-health', '--target-group-arn', args.target_group_arn)['TargetHealthDescriptions']
        require(health and all(item['TargetHealth']['State'] == 'healthy' for item in health), 'BE ALB Target에 healthy가 아닌 항목이 있습니다.')
    result['beReadyReplicas'] = replicas
    result['stageNodes'] = [node['metadata']['name'] for node in nodes]
    return result
