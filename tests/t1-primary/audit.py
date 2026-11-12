"""실행기의 집계와 별개로 원시 시각·관측 완전성·소스 고정을 재검수한다."""
import collections
import datetime as dt
import gzip
import hashlib
import json
import math
from pathlib import Path

ROOT=Path(__file__).resolve().parent
PRIVATE=ROOT/'private'
OUT=ROOT/'results'/'2026-11-11'


def unix(value):
    return dt.datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()


def main():
    suite=json.loads((PRIVATE/'suite-status.json').read_text(encoding='utf-8'))
    audit={'source_hashes_match':{},'rounds':[]}
    for name,digest in suite['script_sha256'].items():
        audit['source_hashes_match'][name]=hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest
    for item in suite['rounds']:
        name=item['name'];a=item['analysis'];start=unix(a['window_start_utc']);end=start+1800
        observations=[json.loads(line) for line in (PRIVATE/name/'observations.jsonl').read_text(encoding='utf-8').splitlines()]
        inside=[r for r in observations if start<=unix(r['time'])<end and 'error' not in r]
        server_start,server_end=inside[0],inside[-1]
        interval_start,interval_end=unix(server_start['time']),unix(server_end['time'])
        client_interval=0
        def server_count(row):
            return sum(float(line.split()[-1]) for port in ['18081','18082'] for line in row.get(port,[])
                       if line.startswith('http_server_requests_seconds_count{') and
                       ('uri="/api/v1/ingredients/{ingredientId}"' in line or
                        'uri="/api/v1/refrigerators/{refrigeratorId}/ingredients"' in line))
        sends=completions=errors=drops=auth=0
        counts=collections.Counter();statuses=collections.Counter();apis=collections.Counter()
        latency_samples=collections.Counter();within_one_second=collections.Counter()
        window_sends=window_completions=0
        with gzip.open(PRIVATE/name/f'{name}.json.gz','rt',encoding='utf-8') as f:
            for line in f:
                obj=json.loads(line)
                if obj.get('type')!='Point':continue
                m,p=obj['metric'],obj['data'];t=unix(p['time']);tags=p.get('tags',{});value=p['value']
                if m=='business_completed' and interval_start<=t<interval_end:client_interval+=value
                if m=='dropped_iterations':drops+=value
                if m=='auth_error':auth+=value
                if m=='business_sent' and start<=t<end:window_sends+=value
                if m=='business_completed' and start<=t<end:window_completions+=value
                if tags.get('phase')!='measure':continue
                if m=='business_sent':
                    sends+=value;counts[int((t-start)//60)]+=value;apis[tags['api']]+=value
                elif m=='business_completed':completions+=value;statuses[tags['status']]+=value
                elif m=='business_error':errors+=value
                elif m=='business_latency':
                    latency_samples[tags['api']]+=1
                    within_one_second[tags['api']]+=int(value<=1000)
        gaps=[unix(b['time'])-unix(a['time']) for a,b in zip(observations,observations[1:])]
        observed_errors=[r['error'] for r in observations if 'error' in r]
        manifest=json.loads((PRIVATE/name/'manifest.json').read_text(encoding='utf-8'))
        artifact_hashes={filename:hashlib.sha256((PRIVATE/name/filename).read_bytes()).hexdigest()==digest
                         for filename,digest in manifest.get('artifact_sha256',{}).items()}
        record={'name':name,'request_start_cohort':sends,'cohort_completions':completions,
                'wall_window_started':window_sends,'wall_window_completed':window_completions,
                'status_counts':statuses,'api_counts':apis,'minute_counts':counts,
                'dropped_iterations':drops,'errors':errors,'auth_errors':auth,
                'max_observation_gap_seconds':max(gaps) if gaps else None,'observation_errors':observed_errors,
                'same_pods_every_sample':all(r.get('pods')==manifest['baseline'] for r in observations),
                'matches_primary_analysis':sends==a['sent'] and completions==a['completed'] and drops==a['dropped_iterations'] and errors==a['errors'],
                'independent_api_p95_pass':bool(latency_samples) and all(within_one_second[k]>=math.ceil(n*.95) for k,n in latency_samples.items()),
                'independent_api_p99_pass':bool(latency_samples) and all(within_one_second[k]>=math.ceil(n*.99) for k,n in latency_samples.items()),
                'latency_samples_match_requests':dict(latency_samples)==dict(apis),
                'artifact_hashes_match':artifact_hashes,
                'covers_measurement':unix(observations[0]['time'])<=start and unix(observations[-1]['time'])>=end-35}
        record['pass']=record['matches_primary_analysis'] and record['independent_api_p95_pass'] and record['latency_samples_match_requests'] and record['same_pods_every_sample'] and record['covers_measurement'] and not observed_errors and max(gaps)<60 and not drops and not errors and not auth and item['execution_exit']==0 and a['preliminary_pass']
        # 스크레이프 시차가 있으므로 보조 진단으로 보존하고 고정한 합격 기준은 바꾸지 않는다.
        server_delta=server_count(server_end)-server_count(server_start)
        record['server_crosscheck']={'client_completions':client_interval,'server_requests_delta':server_delta,
                                    'relative_difference':abs(server_delta-client_interval)/max(1,client_interval),
                                    'interval_seconds':interval_end-interval_start}
        record['pass']=record['pass'] and len(artifact_hashes)==4 and all(artifact_hashes.values())
        audit['rounds'].append(record)
    audit['all_three_pass']=len(audit['rounds'])==3 and all(r['pass'] for r in audit['rounds']) and all(audit['source_hashes_match'].values())
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(audit,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
