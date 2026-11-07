"""Kubernetes·EC2·ALB 상태 변화를 UTC로 저장합니다. 자원을 변경하지 않습니다."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
import time

from preflight import ACCOUNT, Kube, aws, kube_args, now, query


def compact(item, fallback_kind=None):
    kind = item.get('kind') or fallback_kind
    metadata = item.get('metadata', {})
    result = {'kind': kind, 'metadata': {key: metadata[key] for key in
        ['name', 'namespace', 'uid', 'resourceVersion', 'creationTimestamp', 'deletionTimestamp', 'labels'] if key in metadata}}
    spec = item.get('spec', {})
    if kind == 'Pod':
        result['spec'] = {'nodeName': spec.get('nodeName'), 'containers': [
            {key: container.get(key) for key in ['name', 'image', 'resources']} for container in spec.get('containers', [])]}
    elif kind in ['Node', 'NodeClaim']:
        result['spec'] = {key: spec[key] for key in ['providerID', 'requirements', 'taints', 'nodeClassRef'] if key in spec}
    elif kind == 'HorizontalPodAutoscaler':
        result['spec'] = spec
    if 'status' in item:
        result['status'] = item['status']
    if kind == 'Event':
        for key in ['eventTime', 'firstTimestamp', 'lastTimestamp', 'count', 'type', 'reason', 'message',
                    'involvedObject', 'reportingComponent', 'reportingController', 'series', 'source']:
            if key in item:
                result[key] = item[key]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    kube_args(parser)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=1800)
    parser.add_argument('--interval', type=int, default=5)
    parser.add_argument('--target-group-arn')
    parser.add_argument('--prom-url')
    parser.add_argument('--stop-file', type=Path)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 7200 or not 2 <= args.interval <= 30:
        parser.error('seconds는 1~7200, interval은 2~30이어야 합니다.')
    if args.target_group_arn and not args.target_group_arn.startswith(
            f'arn:aws:elasticloadbalancing:ap-northeast-2:{ACCOUNT}:targetgroup/dameokja-v3-stage-be/'):
        parser.error('stage BE Target Group ARN만 허용합니다.')
    kube = Kube(args)
    kube.validate()
    if aws(args, 'sts', 'get-caller-identity')['Account'] != ACCOUNT:
        parser.error('AWS 계정이 다릅니다.')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # 기존 시험 기록을 덮어쓰지 않습니다.
    stream = args.output.open('x', encoding='utf-8', buffering=1)
    stop = threading.Event()
    lock = threading.Lock()
    errors = []

    def emit(source, **values):
        with lock:
            stream.write(json.dumps({'observedAt': now(), 'source': source, **values}, ensure_ascii=False) + '\n')

    def poll(source, action):
        previous = None
        last_heartbeat = 0
        while not stop.is_set():
            started = time.monotonic()
            requested = now()
            try:
                data = action()
                encoded = json.dumps(data, sort_keys=True)
                if encoded != previous or started - last_heartbeat >= 30:
                    emit(source, requestedAt=requested, data=data)
                    previous = encoded
                    last_heartbeat = started
            except Exception as error:
                emit(source, requestedAt=requested, error=str(error))
                errors.append(source)
            stop.wait(max(0, args.interval - (time.monotonic() - started)))

    def resources(*parts):
        kinds = {'pods': 'Pod', 'hpa': 'HorizontalPodAutoscaler', 'nodes': 'Node', 'nodeclaims': 'NodeClaim', 'events': 'Event'}
        return [compact(item, kinds[parts[0]]) for item in kube.get(*parts).get('items', [])]

    actions = {
        'pods': lambda: resources('pods', '-n', 'stage'),
        'hpa': lambda: resources('hpa', '-n', 'stage'),
        'nodes': lambda: resources('nodes', '-l', 'dameokja.com/environment=stage'),
        'nodeclaims': lambda: resources('nodeclaims', '-l', 'karpenter.sh/nodepool=stage-app'),
        'stage-events': lambda: resources('events', '-n', 'stage'),
        'cluster-node-events': lambda: resources('events', '-A', '--field-selector', 'involvedObject.kind=Node'),
        'cluster-nodeclaim-events': lambda: resources('events', '-A', '--field-selector', 'involvedObject.kind=NodeClaim'),
        'ec2': lambda: aws(args, 'ec2', 'describe-instances', '--filters', 'Name=tag:Name,Values=dameokja-v3-stage-app',
                           '--query', 'Reservations[].Instances[].{Id:InstanceId,LaunchTime:LaunchTime,State:State.Name,Ip:PrivateIpAddress}'),
    }
    if args.target_group_arn:
        actions['alb-target-health'] = lambda: aws(args, 'elbv2', 'describe-target-health', '--target-group-arn', args.target_group_arn)
    if args.prom_url:
        actions['pod-success-count'] = lambda: query(args.prom_url,
            'sum by(pod)(http_server_requests_seconds_count{cluster="dameokja-v3",namespace="stage",job="prometheus.scrape.be",uri!~"/actuator.*",status=~"2.."})')
    emit('observer-start', intervalSeconds=args.interval, targetHealthEnabled=bool(args.target_group_arn),
         podTrafficEnabled=bool(args.prom_url))
    pool = ThreadPoolExecutor(max_workers=len(actions))
    futures = [pool.submit(poll, source, action) for source, action in actions.items()]
    interrupted = False
    try:
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            if args.stop_file and args.stop_file.exists():
                break
            stop.wait(min(1, max(0, deadline - time.monotonic())))
    except KeyboardInterrupt:
        interrupted = True
    finally:
        stop.set()
        pool.shutdown(wait=True)
        for future in futures:
            future.result()
        emit('observer-finish', errors=len(errors), interrupted=interrupted)
        stream.close()
    if errors:
        raise SystemExit('일부 관측 조회가 실패했습니다. 기록 안의 error를 확인하세요.')


if __name__ == '__main__':
    main()
