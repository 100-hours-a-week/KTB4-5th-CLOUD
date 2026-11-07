"""기본은 계획 출력이며 --execute에서만 준비 상태 검사 후 stage 부하를 실행합니다."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid

from preflight import api, inspect, kube_args, now, require

ROOT = Path(__file__).resolve().parent


def seconds(duration):
    match = re.fullmatch(r'(\d+)(s|m)', duration)
    if not match:
        raise ValueError('duration은 양의 정수 초 또는 분이어야 합니다.')
    return int(match[1]) * (60 if match[2] == 'm' else 1)


def load_plan():
    plan = json.loads((ROOT / 'plan.json').read_text(encoding='utf-8'))
    require(plan['baseUrl'] == 'https://stage.dameokja.com' and plan['namespace'] == 'stage'
            and plan['deployment'] == 'be-blue', 'stage 대상이 변경되었습니다.')
    hpa = plan['hpa']
    require(1 <= plan['smoke']['vus'] <= 2 and plan['smoke']['iterations'] == 1, 'smoke는 최대 2 VU·각 1회입니다.')
    require(1 <= hpa['preAllocatedVUs'] <= hpa['maxVUs'] <= 50, 'HPA VU 범위는 1~50입니다.')
    require(1 <= hpa['startRate'] <= 50 and hpa['stages'], 'HPA 시작률·단계가 잘못되었습니다.')
    require(all(1 <= stage['target'] <= 50 and seconds(stage['duration']) > 0 for stage in hpa['stages']),
            '각 단계는 최대 50 iterations/s여야 합니다.')
    require(sum(seconds(stage['duration']) for stage in hpa['stages']) <= 3600, 'HPA 시험은 최대 1시간입니다.')
    require(0 < plan['thresholds']['errorRate'] <= plan['thresholds']['abortErrorRate'] <= 0.05,
            '오류율 기준이 잘못되었습니다.')
    return plan


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def export_metrics(url, start, end, path):
    output = {'start': start, 'end': end, 'stepSeconds': 15, 'queries': []}
    queries = json.loads((ROOT / 'metric-queries.json').read_text(encoding='utf-8'))
    for name, expression in queries.items():
        item = {'name': name, 'expr': expression}
        try:
            item['data'] = api(url, '/api/v1/query_range', {'query': expression, 'start': start, 'end': end, 'step': 15})
        except Exception as error:
            item['error'] = str(error)
        output['queries'].append(item)
    save(path, output)
    return not any('error' in item for item in output['queries'])


def wait_observer(process, path, required_sources):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('관측 수집기가 준비되기 전에 종료되었습니다. observer.log를 확인하세요.')
        if path.exists():
            records = []
            for line in path.read_text(encoding='utf-8').splitlines():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            require(not any('error' in record for record in records), '관측 수집기에 조회 오류가 있습니다.')
            if required_sources <= {record['source'] for record in records}:
                return
        time.sleep(0.5)
    raise RuntimeError('관측 수집기의 초기 조회가 60초 안에 완료되지 않았습니다.')


def run(args, plan):
    require(args.kubeconfig, '--kubeconfig가 필요합니다.')
    if args.mode == 'hpa':
        require(args.smoke_manifest and args.smoke_manifest.is_file(), 'HPA 시험 전에 --smoke-manifest로 성공한 smoke 결과를 지정하세요.')
        smoke = json.loads(args.smoke_manifest.read_text(encoding='utf-8'))
        require(smoke.get('mode') == 'smoke' and smoke.get('status') == 'completed' and smoke.get('baseUrl') == plan['baseUrl'],
                '성공한 stage smoke manifest가 아닙니다.')
        age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(smoke['finishedAt'])
        require(0 <= age.total_seconds() <= 3600, 'smoke 성공 후 1시간 이내에 HPA 시험을 시작하세요.')
    checks = inspect(args, plan)
    if not args.execute:
        print(json.dumps(checks, ensure_ascii=False, indent=2))
        return 0
    run_id = args.run_id or f'stage-{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-{args.mode}-{uuid.uuid4().hex[:6]}'
    require(re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', run_id), 'RUN_ID 형식이 잘못되었습니다.')
    output = args.results.resolve() / run_id
    output.mkdir(parents=True, exist_ok=False)
    save(output / 'preflight.json', checks)
    save(output / 'plan.json', plan)
    manifest = {'run_id': run_id, 'mode': args.mode, 'baseUrl': plan['baseUrl'], 'startedAt': now(), 'status': 'starting',
                'testScriptSha256': hashlib.sha256((ROOT / 'stage-read.js').read_bytes()).hexdigest(),
                'planSha256': hashlib.sha256((ROOT / 'plan.json').read_bytes()).hexdigest()}
    save(output / 'manifest.json', manifest)
    observer = None
    load = None
    start = time.time()
    observer_log = (output / 'observer.log').open('w', encoding='utf-8')
    load_log = (output / 'k6.log').open('w', encoding='utf-8')
    stop_file = output / 'observer.stop'
    try:
        observe_cmd = [sys.executable, str(ROOT / 'observe.py'), '--kubeconfig', str(Path(args.kubeconfig).resolve()),
            '--profile', args.profile, '--output', str(output / 'timeline.jsonl'), '--seconds', '7200',
            '--prom-url', args.prom_url, '--stop-file', str(stop_file)]
        if args.server:
            observe_cmd += ['--server', args.server]
        if args.target_group_arn:
            observe_cmd += ['--target-group-arn', args.target_group_arn]
        observer = subprocess.Popen(observe_cmd, stdout=observer_log, stderr=subprocess.STDOUT)
        required_sources = {'pods', 'hpa', 'nodes', 'nodeclaims', 'stage-events', 'cluster-node-events',
                            'cluster-nodeclaim-events', 'ec2', 'pod-success-count'}
        if args.target_group_arn:
            required_sources.add('alb-target-health')
        wait_observer(observer, output / 'timeline.jsonl', required_sources)
        environment = dict(os.environ, MODE=args.mode, RUN_ID=run_id, BASE_URL=plan['baseUrl'],
                           ACCOUNTS_FILE=str(args.accounts.resolve()), SUMMARY_FILE=str(output / 'summary.json'))
        manifest.update(status='running', k6StartedAt=now())
        save(output / 'manifest.json', manifest)
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
        # 계정 값은 명령 인자나 로그에 넣지 않고 k6가 읽는 파일 경로만 넘깁니다.
        load = subprocess.Popen([args.k6, 'run', '--out', f'json={output / "samples.jsonl"}', str(ROOT / 'stage-read.js')],
            cwd=ROOT, env=environment, stdout=load_log, stderr=subprocess.STDOUT, creationflags=flags)
        while load.poll() is None:
            require(observer.poll() is None, '관측 수집기가 부하 중 종료되었습니다.')
            # 조회 실패가 생기면 데이터가 불완전하므로 부하를 계속하지 않습니다.
            timeline = (output / 'timeline.jsonl').read_text(encoding='utf-8')
            require(not any('error' in json.loads(line) for line in timeline.splitlines(keepends=True) if line.endswith('\n')),
                    '관측 조회 오류가 발생했습니다. 부하를 중단합니다.')
            time.sleep(1)
        manifest.update(k6ExitCode=load.returncode, k6FinishedAt=now())
        # 종료 후에도 두 번 이상의 scrape를 남겨 마지막 요청까지 보존합니다.
        time.sleep(35)
        manifest['status'] = 'completed' if load.returncode == 0 else 'thresholds_or_execution_failed'
    except (Exception, KeyboardInterrupt) as error:
        manifest.update(status='interrupted_or_failed', error=str(error) or '사용자 중단')
        if load and load.poll() is None:
            load.send_signal(signal.CTRL_BREAK_EVENT if os.name == 'nt' else signal.SIGINT)
            try:
                load.wait(timeout=20)
            except subprocess.TimeoutExpired:
                load.kill()
                load.wait(timeout=10)
    finally:
        stop_file.touch()
        if observer:
            try:
                observer.wait(timeout=40)
            except subprocess.TimeoutExpired:
                observer.kill()
                observer.wait(timeout=10)
            manifest['observerExitCode'] = observer.returncode
            if observer.returncode != 0:
                manifest['status'] = 'observation_incomplete'
        try:
            if not export_metrics(args.prom_url, start, time.time(), output / 'prometheus.json'):
                manifest['status'] = 'export_incomplete'
        except Exception as error:
            manifest.update(status='export_incomplete', exportError=str(error))
        manifest['finishedAt'] = now()
        save(output / 'manifest.json', manifest)
        observer_log.close()
        load_log.close()
    print(json.dumps({'results': str(output), **manifest}, ensure_ascii=False, indent=2))
    return 0 if manifest['status'] == 'completed' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    kube_args(parser, required=False)
    parser.add_argument('--mode', choices=['smoke', 'hpa'], default='smoke')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--execute', action='store_true')
    action.add_argument('--preflight', action='store_true')
    parser.add_argument('--accounts', type=Path, default=ROOT / 'private/accounts.json')
    parser.add_argument('--k6', default='k6')
    parser.add_argument('--prom-url')
    parser.add_argument('--loki-url')
    parser.add_argument('--target-group-arn')
    parser.add_argument('--results', type=Path, default=ROOT / 'results')
    parser.add_argument('--run-id')
    parser.add_argument('--smoke-manifest', type=Path)
    args = parser.parse_args()
    try:
        plan = load_plan()
        if not args.execute and not args.preflight:
            print(json.dumps({'action': 'plan_only', 'mode': args.mode, 'plan': plan,
                'message': '기동·부하·AWS/Kubernetes 조회를 실행하지 않았습니다. --preflight는 읽기 점검, --execute만 부하 실행입니다.'}, ensure_ascii=False, indent=2))
            return 0
        return run(args, plan)
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
