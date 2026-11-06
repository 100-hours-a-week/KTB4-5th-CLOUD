"""CloudFormation 안의 대시보드를 생성하거나 생성 결과가 최신인지 검사합니다."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / 'infra/v3/cloudformation/observability.yaml'
START = '          # stage 부하·확장 관측. namespace로 dev와 분리하고 UTC로 표시합니다.\n'
END = '          # 설정이 바뀐 경우에만 컨테이너를 다시 만듭니다.\n'


def dashboard():
    scope = 'cluster="$cluster",namespace="$namespace"'
    http = f'{scope},job="prometheus.scrape.be",uri!~"/actuator.*"'
    cpu = f'sum by(pod) (rate(container_cpu_usage_seconds_total{{{scope},container!="",container!="POD"}}[1m]))'
    memory = f'sum by(pod) (container_memory_working_set_bytes{{{scope},container!="",container!="POD"}})'
    req_cpu = f'sum by(pod) (kube_pod_container_resource_requests{{{scope},resource="cpu"}})'
    req_mem = f'sum by(pod) (kube_pod_container_resource_requests{{{scope},resource="memory"}})'
    node_filter = f'max by(node) (kube_pod_info{{{scope}}})'
    def node_metric(expr):
        return f'label_replace({expr},"node","$1","instance","(.+)") * on(node) group_left() {node_filter}'
    node_cpu = node_metric('sum by(instance) (rate(container_cpu_usage_seconds_total{cluster="$cluster",id="/"}[1m]))')
    node_mem = node_metric('container_memory_working_set_bytes{cluster="$cluster",id="/"}')
    panels = []
    def add(title, expressions, unit='short', kind='timeseries', description=''):
        index = len(panels)
        panels.append({
            'id': index + 1, 'title': title, 'type': kind, 'description': description,
            'gridPos': {'x': (index % 2) * 12, 'y': (index // 2) * 8, 'w': 12, 'h': 8},
            'datasource': {'type': 'prometheus', 'uid': '${prom}'},
            'fieldConfig': {'defaults': {'unit': unit}, 'overrides': []},
            'targets': [{'refId': chr(65 + i), 'expr': expr, 'legendFormat': legend}
                        for i, (legend, expr) in enumerate(expressions)],
        })
    add('BE 요청량·5xx', [('전체', f'sum(rate(http_server_requests_seconds_count{{{http}}}[1m]))'),
                             ('5xx', f'sum(rate(http_server_requests_seconds_count{{{http},status=~"5.."}}[1m]))')], 'reqps')
    add('BE 5xx 비율', [('5xx / 전체', f'(sum(rate(http_server_requests_seconds_count{{{http},status=~"5.."}}[1m])) or vector(0)) / clamp_min(sum(rate(http_server_requests_seconds_count{{{http}}}[1m])), 0.000001)')], 'percentunit',
        description='서버에 도달한 요청 기준입니다. ALB·네트워크 실패는 k6 결과와 함께 확인합니다.')
    add('BE 평균·p95·p99 응답시간', [('평균', f'sum(rate(http_server_requests_seconds_sum{{{http}}}[1m])) / clamp_min(sum(rate(http_server_requests_seconds_count{{{http}}}[1m])),0.000001)'),
        ('p95', f'histogram_quantile(0.95,sum by(le)(rate(http_server_requests_seconds_bucket{{{http}}}[1m])))'),
        ('p99', f'histogram_quantile(0.99,sum by(le)(rate(http_server_requests_seconds_bucket{{{http}}}[1m])))')], 's')
    add('Pod별 성공 요청률', [('{{pod}}', f'sum by(pod)(rate(http_server_requests_seconds_count{{{http},status=~"2.."}}[1m]))')], 'reqps',
        description='새 Pod의 트래픽 처리를 15초 수집 간격으로 확인합니다. 최초 성공 요청의 정확한 시각은 아닙니다.')
    add('Pod CPU', [('{{pod}}', cpu)], 'cores')
    add('Pod 메모리 working set', [('{{pod}}', memory)], 'bytes')
    add('Pod CPU / requests', [('{{pod}}', f'{cpu} / ({req_cpu} > 0)')], 'percentunit')
    add('Pod 메모리 / requests', [('{{pod}}', f'{memory} / ({req_mem} > 0)')], 'percentunit')
    add('Node CPU', [('{{node}} cores', node_cpu)], 'cores',
        description='이 환경의 Pod가 배치된 Node입니다. 전체 Node 사용량이므로 공유 Node의 다른 환경 사용량도 포함합니다.')
    add('Node 메모리 working set', [('{{node}}', node_mem)], 'bytes')
    add('Node CPU / 전체 Pod requests', [('{{node}}', f'({node_cpu}) / on(node) (sum by(node)(kube_pod_container_resource_requests{{cluster="$cluster",resource="cpu"}}) > 0)')], 'percentunit')
    add('Node 메모리 / 전체 Pod requests', [('{{node}}', f'({node_mem}) / on(node) (sum by(node)(kube_pod_container_resource_requests{{cluster="$cluster",resource="memory"}}) > 0)')], 'percentunit')
    hpa = f'{scope},horizontalpodautoscaler="be-load-test"'
    add('HPA current·desired·max', [('current', f'kube_horizontalpodautoscaler_status_current_replicas{{{hpa}}}'),
        ('desired', f'kube_horizontalpodautoscaler_status_desired_replicas{{{hpa}}}'),
        ('max', f'kube_horizontalpodautoscaler_spec_max_replicas{{{hpa}}}')])
    add('HPA 조건', [('{{condition}}', f'kube_horizontalpodautoscaler_status_condition{{{hpa},status="true"}}')])
    add('Pod Pending·Ready', [('Pending', f'sum(kube_pod_status_phase{{{scope},phase="Pending"}})'),
        ('Ready', f'sum(kube_pod_status_ready{{{scope},condition="true"}})')])
    add('Pod 생성·스케줄링·Ready 시각', [('{{pod}} 생성', f'kube_pod_created{{{scope}}} * 1000'),
        ('{{pod}} Scheduled', f'kube_pod_status_scheduled_time{{{scope}}} * 1000'),
        ('{{pod}} Ready', f'kube_pod_status_ready_time{{{scope}}} * 1000')], 'dateTimeAsIso')
    for title, expression in [('환경 Pod 로그', '{cluster="$cluster",namespace="$namespace",source!="kubernetes-events"}'),
                              ('환경 Kubernetes 이벤트', '{cluster="$cluster",namespace="$namespace",source="kubernetes-events"} | json')]:
        index = len(panels)
        panels.append({'id': index + 1, 'title': title, 'type': 'logs',
            'gridPos': {'x': 0, 'y': (index // 2) * 8 + (index % 2) * 8, 'w': 24, 'h': 8},
            'datasource': {'type': 'loki', 'uid': '${loki}'}, 'targets': [{'refId': 'A', 'expr': expression}]})
    return {'uid': 'dameokja-v3-stage-load-test', 'title': 'v3 stage 부하·확장 관측', 'schemaVersion': 41,
        'timezone': 'utc', 'refresh': '5s', 'time': {'from': 'now-30m', 'to': 'now'},
        'templating': {'list': [
            {'name': 'prom', 'type': 'datasource', 'query': 'prometheus'},
            {'name': 'loki', 'type': 'datasource', 'query': 'loki'},
            {'name': 'cluster', 'type': 'constant', 'query': 'dameokja-v3', 'hide': 2},
            {'name': 'namespace', 'type': 'custom', 'query': 'stage,dev', 'current': {'text': 'stage', 'value': 'stage'}}]},
        'panels': panels}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    source = TEMPLATE.read_text(encoding='utf-8')
    encoded = json.dumps(dashboard(), ensure_ascii=False, indent=2)
    block = START + '          - |\n            cat > /opt/observability.new/dashboards/stage-load-test.json <<\'EOF\'\n'
    block += ''.join('            ' + line + '\n' for line in encoded.splitlines()) + '            EOF\n'
    before = source[:source.index(START)] if START in source else source[:source.index(END)]
    # 기존 dev 화면도 환경 필터를 고정해 stage 메트릭과 섞이지 않게 합니다.
    before = before.replace(r'job=\"prometheus.scrape.be\",uri',
                            r'job=\"prometheus.scrape.be\",namespace=\"dev\",cluster=\"dameokja-v3\",uri')
    result = before + block + source[source.index(END):]
    if args.write:
        TEMPLATE.write_text(result, encoding='utf-8', newline='\n')
    elif result != source:
        raise SystemExit('대시보드 생성 결과가 다릅니다: build_dashboard.py --write 실행 필요')
    print('대시보드 생성 결과 검증 완료')


if __name__ == '__main__':
    main()
