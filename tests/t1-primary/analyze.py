"""k6 원시 표본을 요청 시작·완료 시각별로 집계한다."""
import argparse
import collections
import datetime as dt
import gzip
import json
import math
from pathlib import Path


def percentile(values, q):
    if not values:
        return None
    values = sorted(values)
    return values[max(0, math.ceil(len(values) * q) - 1)]


def analyze(path, duration=1800, target_rps=95):
    latency = collections.defaultdict(list)
    statuses = collections.Counter()
    sent_times, completed_times = [], []
    dropped = errors = integrity = auth_errors = 0
    phase_counts = collections.Counter()
    window_starts = set()
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            obj = json.loads(line)
            if obj.get('type') != 'Point':
                continue
            metric, point = obj['metric'], obj['data']
            tags, value = point.get('tags', {}), point['value']
            if 'window_start' in tags:
                window_starts.add(float(tags['window_start']) / 1000)
            if metric == 'dropped_iterations':
                dropped += value
            if metric == 'auth_error':
                auth_errors += value
            if metric == 'integrity_error':
                integrity += value
            if tags.get('phase') != 'measure':
                if metric == 'business_sent':
                    phase_counts[tags.get('phase')] += value
                continue
            timestamp = dt.datetime.fromisoformat(point['time'].replace('Z', '+00:00')).timestamp()
            if metric == 'business_sent':
                sent_times.append(timestamp)
            elif metric == 'business_completed':
                completed_times.append(timestamp)
                statuses[tags.get('status')] += value
            elif metric == 'business_latency':
                latency[tags['api']].append(value)
            elif metric == 'business_error':
                errors += value
    # 실제 측정 경계는 실행 manifest의 UTC 시각으로 재검수한다. 첫 전송 기준 창은 보조 집계다.
    window_start = min(window_starts) if window_starts else (min(sent_times) if sent_times else 0)
    windows = collections.Counter(int((t - window_start)/60) for t in sent_times)
    api = {name: {'count': len(values), 'p95_ms': percentile(values, .95),
                  'p99_ms': percentile(values, .99), 'max_ms': max(values)} for name, values in latency.items()}
    rate = len(sent_times)/duration
    checks = {'rate': abs(rate-target_rps) <= target_rps*.01, 'dropped_zero': dropped == 0,
              'errors_zero': errors == 0 and auth_errors == 0,
              'integrity': integrity == 0, 'api_p95': bool(api) and all(x['p95_ms'] <= 1000 for x in api.values()),
              'completed': len(sent_times) == len(completed_times),
              'minute_rate': duration % 60 == 0 and all(abs(windows.get(i,0)/60-target_rps) <= target_rps*.01 for i in range(duration//60))}
    expected = {'list':855/22,'list-next':855/22,'write-pick':95/22,'detail':95/22,'update':95/22,'verify':95/22}
    checks['api_mix'] = set(api) == set(expected) and all(abs(api[name]['count']-rate*duration*target_rps/95) <= max(2,rate*duration*target_rps/95*.01) for name,rate in expected.items())
    return {'source': str(path), 'duration_seconds': duration, 'sent': len(sent_times),
            'completed': len(completed_times), 'rps': rate, 'target_rps':target_rps, 'statuses': statuses,
            'window_start_utc':dt.datetime.fromtimestamp(window_start,dt.timezone.utc).isoformat(),
            'window_start_spread_ms':(max(window_starts)-min(window_starts))*1000 if window_starts else None,
            'completion_count_within_window':sum(window_start <= t < window_start+duration for t in completed_times),
            'dropped_iterations': dropped, 'errors': errors, 'auth_errors': auth_errors,
            'integrity_errors': integrity, 'api': api, 'minute_counts': windows,
            'other_phase_requests': phase_counts, 'checks': checks,
            'preliminary_pass': all(checks.values()),
            'note': '환경 불변·관측 완전성·정확한 시간 경계·요청별 누락 대조 후에만 최종 판정한다.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('raw', type=Path)
    parser.add_argument('--seconds', type=int, default=1800)
    parser.add_argument('--target-rps', type=float, default=95)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.raw, args.seconds, args.target_rps)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
