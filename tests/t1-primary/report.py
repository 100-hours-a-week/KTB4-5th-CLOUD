"""완료 회차의 원시 자료를 검수 가능한 보고서와 정적 그래프로 묶는다."""
import collections
import datetime as dt
import gzip
import hashlib
import json
import os
import re
import shutil
import statistics
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent
PRIVATE=ROOT/'private'
OUT=ROOT/'results'/'2026-11-11'
os.environ['MPLCONFIGDIR']=str(PRIVATE/'matplotlib-cache')
sys.path.insert(0,str(PRIVATE/'pythonlibs'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from analyze import percentile


def timestamp(value):
    return dt.datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()


def copy_raw(source,target):
    # 요청 경로의 식별자·커서가 결과에 불필요하므로 URL 태그를 제거한다.
    with gzip.open(source,'rt',encoding='utf-8') as src,gzip.open(target,'wt',encoding='utf-8') as dst:
        for line in src:
            row=json.loads(line)
            if row.get('type')=='Point':
                row['data'].get('tags',{}).pop('url',None)
            dst.write(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n')


def copy_evidence(source,target):
    # k6 요약의 setup_data에는 사전 인증 쿠키가 포함되므로 공유 결과에서 제외한다.
    if source.name.endswith('-summary.json'):
        value=json.loads(source.read_text(encoding='utf-8'))
        value.pop('setup_data',None)
        target.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    else:
        shutil.copyfile(source,target)


def observations(path,window_start):
    rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    output={'time':[],'be0':[],'be1':[],'runner':[],'pending':[],'db_time':[],'db_cpu':[],'db_memory':[],'db_io':[]}
    previous_db=None
    previous_io=None
    for row in rows:
        minute=(timestamp(row['time'])-window_start)/60
        output['time'].append(minute)
        cpus=[];runner=0
        for line in row.get('resources','').splitlines():
            parts=line.split()
            if len(parts)<4 or not parts[2].endswith('m'):
                continue
            if parts[0].startswith('be-blue-'):cpus.append(float(parts[2][:-1]))
            if parts[0]=='t1-runner':runner=float(parts[2][:-1])
        output['be0'].append(cpus[0] if cpus else float('nan'))
        output['be1'].append(cpus[1] if len(cpus)>1 else float('nan'))
        output['runner'].append(runner)
        pending=0
        for port in ['18081','18082']:
            for metric in row.get(port,[]):
                if metric.startswith('hikaricp_connections_pending{'):pending+=float(metric.split()[-1])
        output['pending'].append(pending)
        if 'database' in row:
            db=row['database']
            cpu=list(map(int,db['stat'].splitlines()[0].split()[1:9]))
            mem={line.split(':')[0]:int(line.split()[1]) for line in db['meminfo'].splitlines()}
            io_total=0
            for disk in db['diskstats'].splitlines():
                fields=disk.split()
                if len(fields)>9 and re.fullmatch(r'nvme\d+n\d+|xvd[a-z]+',fields[2]):
                    io_total+=(int(fields[5])+int(fields[9]))*512
            db_timestamp=timestamp(db['time'])
            if previous_db:
                delta=[a-b for a,b in zip(cpu,previous_db)]
                output['db_time'].append((timestamp(db['time'])-window_start)/60)
                output['db_cpu'].append(100*(sum(delta)-delta[3]-delta[4])/sum(delta) if sum(delta)>0 else 0)
                output['db_memory'].append(mem['MemAvailable']/1024)
                output['db_io'].append(max(0,io_total-previous_io[1])/max(1,db_timestamp-previous_io[0])/1024/1024)
            previous_db=cpu
            previous_io=(db_timestamp,io_total)
    return output


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    suite=json.loads((PRIVATE/'suite-status.json').read_text(encoding='utf-8'))
    pods=json.loads((PRIVATE/'pods-baseline.json').read_text(encoding='utf-8-sig'))['items']
    nodes=json.loads((PRIVATE/'nodes-baseline.json').read_text(encoding='utf-8-sig'))['items']
    environment={'pods':[{'name':p['metadata']['name'],'node':p['spec'].get('nodeName'),
                          'containers':[{'name':c['name'],'image':c['image'],'resources':c.get('resources')} for c in p['spec']['containers']],
                          'image_ids':[c.get('imageID') for c in p['status'].get('containerStatuses',[])]} for p in pods],
                 'nodes':[{'name':n['metadata']['name'],'instance_type':n['metadata']['labels'].get('node.kubernetes.io/instance-type'),
                           'zone':n['metadata']['labels'].get('topology.kubernetes.io/zone'),'capacity':n['status']['capacity'],
                           'allocatable':n['status']['allocatable'],'runtime':n['status']['nodeInfo']} for n in nodes if n['metadata']['labels'].get('dameokja.com/environment')=='stage'],
                 'database':{'instance_id':'i-081d444e5e162a1c2','instance_type':'t3.medium','address':'10.20.38.0',
                             'engine':'MySQL 8.4.13','app_database':'app','read_replica_used':False},
                 'collection_note':'Pod·노드 원본은 재실행 준비 시점의 스냅샷이며 각 본시험 manifest의 실제 배치와 함께 확인한다.'}
    (OUT/'environment.json').write_text(json.dumps(environment,ensure_ascii=False,indent=2),encoding='utf-8')
    shutil.copyfile(PRIVATE/'db-metadata.json',OUT/'database-metadata.json')
    rounds=suite['rounds']
    if not rounds:raise RuntimeError('완료된 본시험 회차가 없습니다.')
    metadata=[]
    colors=['#2864b4','#d57920','#218c69']
    fig,axes=plt.subplots(4,2,figsize=(13,14),constrained_layout=True)
    for i,item in enumerate(rounds):
        name=item['name'];source=PRIVATE/name;target=OUT/name;target.mkdir(exist_ok=True)
        a=json.loads((source/'analysis.json').read_text(encoding='utf-8'))
        start=timestamp(a['window_start_utc'])
        raw=source/f'{name}.json.gz'
        minute_latencies=collections.defaultdict(list)
        auth=collections.Counter()
        with gzip.open(raw,'rt',encoding='utf-8') as stream:
            for line in stream:
                obj=json.loads(line)
                if obj.get('type')!='Point':continue
                tags=obj['data'].get('tags',{})
                if obj['metric']=='business_latency' and tags.get('phase')=='measure':
                    minute=int((timestamp(obj['data']['time'])-start)/60)
                    minute_latencies[minute].append(obj['data']['value'])
                if obj['metric']=='http_reqs' and tags.get('kind')=='auth':
                    auth[tags.get('name','unknown')]+=obj['data']['value']
        data=observations(source/'observations.jsonl',start)
        x=sorted(map(int,a['minute_counts']))
        axes[0,0].plot(x,[a['minute_counts'][str(t)]/60 for t in x],label=name,color=colors[i])
        x=sorted(t for t in minute_latencies if 0<=t<30)
        axes[0,1].plot(x,[percentile(minute_latencies[t],.95) for t in x],label=name,color=colors[i])
        axes[1,0].plot(data['time'],data['be0'],label=f'{name} BE-1',color=colors[i])
        axes[1,0].plot(data['time'],data['be1'],label=f'{name} BE-2',color=colors[i],linestyle='--',alpha=.6)
        axes[1,1].plot(data['time'],data['runner'],label=name,color=colors[i])
        axes[2,0].plot(data['db_time'],data['db_cpu'],label=name,color=colors[i])
        axes[2,1].plot(data['time'],data['pending'],label=name,color=colors[i])
        axes[3,0].plot(data['db_time'],data['db_memory'],label=name,color=colors[i])
        axes[3,1].plot(data['db_time'],data['db_io'],label=name,color=colors[i])
        for file in ['analysis.json','manifest.json','observations.jsonl',f'{name}.log',f'{name}.exit']:
            if (source/file).exists():shutil.copyfile(source/file,target/file)
        copy_raw(raw,target/raw.name)
        credits={}
        for phase in ['before','after']:
            file=f'{name}-credits-{phase}.json'
            shutil.copyfile(PRIVATE/file,target/file)
            credit_data=json.loads((PRIVATE/file).read_text(encoding='utf-8'))
            credits[phase]={metric:max(value['Datapoints'],key=lambda row:row['Timestamp']) if value['Datapoints'] else None
                            for metric,value in credit_data.items()}
        metadata.append({'name':name,'auth_requests':auth,'max_be_cpu_millicores':max(data['be0']+data['be1']),
                         'credits_latest':credits,
                         'max_runner_cpu_millicores':max(data['runner']),'max_pool_pending':max(data['pending']),
                         'max_db_cpu_percent':max(data['db_cpu']) if data['db_cpu'] else None,
                         'min_db_available_memory_mib':min(data['db_memory']) if data['db_memory'] else None})
    titles=['Business requests / second (1-minute window)','Business latency p95 / ms (1-minute window)',
            'BE CPU / millicores','Generator CPU / millicores','DB CPU / percent (sample interval)','BE connection pool pending / total',
            'DB available memory / MiB','DB disk read + write / MiB per second']
    for axis,title in zip(axes.flat,titles):
        axis.set_title(title);axis.set_xlabel('Minutes from measurement start');axis.grid(alpha=.2);axis.legend(fontsize=7)
    axes[0,0].axhline(95,color='black',linestyle=':',linewidth=1)
    axes[0,0].axhspan(94.05,95.95,color='green',alpha=.08)
    axes[0,1].axhline(1000,color='red',linestyle=':',linewidth=1)
    fig.suptitle('T1 / v3 stage / Primary-only / 2026-11-11 to 12',fontsize=16)
    fig.savefig(OUT/'resource-summary.png',dpi=160)
    plt.close(fig)
    (OUT/'resource-summary.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    if (PRIVATE/'db-observations.jsonl.gz').exists():shutil.copyfile(PRIVATE/'db-observations.jsonl.gz',OUT/'db-observations.jsonl.gz')
    failures=OUT/'preflight';failures.mkdir(exist_ok=True)
    for filename in ['prepare-create.log','prepare-resume.log','prepare-one.log','probe-auth.log','probe-auth-corrected.log']:
        if (PRIVATE/filename).exists():shutil.copyfile(PRIVATE/filename,failures/filename)
    invalid=failures/'invalid-round-1';invalid.mkdir(exist_ok=True)
    for filename in ['manifest.json','observations.jsonl','round-1-summary.json','round-1.log']:
        if (PRIVATE/'invalid-round-1'/filename).exists():copy_evidence(PRIVATE/'invalid-round-1'/filename,invalid/filename)
    if (PRIVATE/'invalid-round-1/round-1.json.gz').exists():
        shutil.copyfile(PRIVATE/'invalid-round-1/round-1.json.gz',invalid/'truncated.json.gz.part')
    shutil.copyfile(PRIVATE/'transfer-verification.json',OUT/'transfer-verification.json')
    interrupted=PRIVATE/'interrupted-round-2'
    if interrupted.exists():
        target=failures/'interrupted-round-2';target.mkdir(exist_ok=True)
        for filename in ['manifest.json','observations.jsonl','round-2-summary.json','round-2.log','round-2.exit']:
            if (interrupted/filename).exists():copy_evidence(interrupted/filename,target/filename)
        if (interrupted/'round-2.json.gz').exists():copy_raw(interrupted/'round-2.json.gz',target/'round-2.json.gz')
    for name in ['smoke-low-02','smoke-95','smoke-95-preauth','smoke-95-preauth-02','smoke-95-headroom','smoke-storage']:
        source=PRIVATE/name
        target=failures/name;target.mkdir(exist_ok=True)
        for filename in ['analysis.json','manifest.json',f'{name}.log']:
            if (source/filename).exists():shutil.copyfile(source/filename,target/filename)
        if (source/f'{name}.json.gz').exists():copy_raw(source/f'{name}.json.gz',target/f'{name}.json.gz')
    (OUT/'suite-status.json').write_text(json.dumps(suite,ensure_ascii=False,indent=2),encoding='utf-8')
    if (PRIVATE/'cleanup-status.json').exists():shutil.copyfile(PRIVATE/'cleanup-status.json',OUT/'cleanup-status.json')
    lines=['# T1 검증 결과 — v3 stage Primary 단독','',
           '이 보고서는 [이슈 #329](https://github.com/100-hours-a-week/KTB4-5th-CLOUD/issues/329)의 실행 결과다. 구체적인 시나리오와 판정 기준은 [시험 명세](../../README.md)에 고정했다.','',
           '| 회차 | 업무 전송 | 시작 요청의 완료 | 실제 RPS | 미전송 iteration | HTTP 오류 | 정합성 오류 | 최대 API p95 | 판정 |',
           '|---|---:|---:|---:|---:|---:|---:|---:|---|']
    for item in rounds:
        a=item['analysis'];passed=a['preliminary_pass'] and item['execution_exit']==0
        lines.append(f"| {item['name']} | {a['sent']:,} | {a['completed']:,} | {a['rps']:.3f} | {int(a['dropped_iterations'])} | {int(a['errors'])} | {int(a['integrity_errors'])} | {max(v['p95_ms'] for v in a['api'].values()):.0f}ms | {'기준 충족' if passed else '미충족'} |")
    lines+=['','## API별 지연','','| 회차 | API | 표본 수 | p95 | p99 | 최대 |','|---|---|---:|---:|---:|---:|']
    for item in rounds:
        for api,values in item['analysis']['api'].items():
            lines.append(f"| {item['name']} | {api} | {values['count']:,} | {values['p95_ms']:.0f}ms | {values['p99_ms']:.0f}ms | {values['max_ms']:.0f}ms |")
    lines+=['','## 회차 간 분포','','| API | p95 중앙값 | p95 범위 | p99 중앙값 | p99 범위 |','|---|---:|---:|---:|---:|']
    for api in rounds[0]['analysis']['api']:
        p95=[r['analysis']['api'][api]['p95_ms'] for r in rounds if api in r['analysis']['api']]
        p99=[r['analysis']['api'][api]['p99_ms'] for r in rounds if api in r['analysis']['api']]
        lines.append(f'| {api} | {statistics.median(p95):.0f}ms | {min(p95):.0f}~{max(p95):.0f}ms | {statistics.median(p99):.0f}ms | {min(p99):.0f}~{max(p99):.0f}ms |')
    lines+=['','## 자원 관측','','| 회차 | BE 최대 CPU | 발생기 최대 CPU | DB 최대 CPU | DB 최소 가용 메모리 | 풀 대기 최대 | DB 크레딧 전→후 |',
            '|---|---:|---:|---:|---:|---:|---:|']
    for row in metadata:
        credit=row['credits_latest']
        values=[credit[p]['CPUCreditBalance']['Average'] if credit[p]['CPUCreditBalance'] else float('nan') for p in ['before','after']]
        lines.append(f"| {row['name']} | {row['max_be_cpu_millicores']:.0f}m | {row['max_runner_cpu_millicores']:.0f}m | {row['max_db_cpu_percent']:.1f}% | {row['min_db_available_memory_mib']:.0f}MiB | {row['max_pool_pending']:.0f} | {values[0]:.1f} → {values[1]:.1f} |")
    lines+=['','크레딧은 회차 전후 조회에서 가장 최근 CloudWatch 표본이며 측정 경계와 정확히 같은 시각은 아니다. 자원 최댓값은 관측 표본 기준으로 예열도 포함한다.','',
            '![자원 관측](resource-summary.png)','',
            'BE·발생기는 약 15~30초 간격, DB는 약 1분 간격으로 원시 상태를 수집했다. DB CPU는 연속 /proc/stat 표본의 차이로 계산한다. HTTP 지연은 클라이언트 요청 시작부터 응답 완료까지이며 기존 stage 외부 경로를 포함한다.','',
            '1회차 2026-11-11 18:00:02 KST 표본에서는 두 BE의 풀 active가 각각 10개, pending이 31개와 24개로 일시 증가했다. 전체 p95 기준은 충족했지만 개별 요청의 최대 지연은 1초를 넘었다. 관측 간격 사이의 정확한 대기 지속 시간과 원인은 이 자료만으로 확정할 수 없다. 따라서 연결 풀 병목이 전혀 없었다고 해석하지 않는다.','',
            '## 결과의 범위','',
            '- v3 stage, BE 2개, EC2 MySQL Primary 한 대, 전용 냉장고 50개×재료 1,000개에 대한 결과다. 기존 stage 데이터를 포함한 전체 재료는 65,018개였다.',
            '- 읽기:쓰기 **세션** 9:1, 실제 HTTP GET:PATCH 21:1이다. 목록·수정 경로만 검증했고 등록·소비·이미지 분석·모든 사용자 트래픽을 대표하지 않는다.',
            '- DB CPU 크레딧은 회차 전후 파일에 보존한다. 회차를 재실행해도 CPU 크레딧과 캐시가 물리적으로 완전히 초기화되지는 않는다.',
            '- BE는 Kafka와 노드를 공유하고 기존 SQL 로그 설정을 유지했다. 과거 v2 성능이나 Replica·HA의 일반적 불필요로 확대 해석하지 않는다.',
            '- 원시 요청 자료의 URL 태그와 실패 회차 요약의 인증 setup_data를 제거했다. 요청별 시각·상태·API·지연·phase·측정 경계는 보존했다. 인증 정보는 결과 폴더에 포함하지 않는다. manifest의 artifact_sha256은 비식별화 전 원격 원본의 해시이며, 공개 결과 파일의 해시는 sha256.json에 기록한다.','',
            '## 사전 검증에서 보존한 실패','',
            '첫 데이터 생성은 50개 동시 실행 중 500 오류가 발생했다. 순차 생성으로 전환해 50,000개를 확인했다. 초기 95 RPS 발생기는 로그인 지연 및 VU 부족으로 미전송이 발생해 무효 처리했다. 사전 인증과 읽기 100/쓰기 50 VU를 준비한 예비 시험은 94.992 RPS, 미전송·오류·정합성 불일치 0으로 통과했다. 인증 갱신 사전 검증도 올바른 CSRF 갱신 후 10/10 성공했다. 이 실패들을 본시험 성공으로 집계하지 않는다.','',
            '첫 35분 본시험도 k6 자체 오류·미전송은 0이었지만 원시 파일의 20초 복사 제한으로 gzip이 잘렸고 관리 조회의 일시 오류 1회가 기록됐다. 이 회차는 세 회차에 포함하지 않는다. 원본 보관용 PVC와 청크별 전송·해시 검증을 추가하고 32MiB 파일로 검증한 뒤 세 회차를 다시 실행했다.','',
            '유효 1회차 완료 후 2026-11-11 18:40 KST경 로컬 관리 프로세스가 종료됐다. 당시 진행 중이던 2회차는 heartbeat 중단으로 18:42경 강제 종료되어 제외했다. 로컬 정리 감시도 함께 종료돼 stage DB·노드가 밤새 기동 상태로 남았다. 2026-11-12 관리 연결·발생기·DB 관측을 복구한 뒤 남은 두 회차를 같은 고정 소스로 재실행했다. 회차 사이의 긴 간격과 DB 크레딧·캐시 상태 차이를 결과 해석에 포함해야 한다.','',
            '## 종료 상태','',f"자동 실행 상태: `{suite['phase']}`. 정리 결과와 독립적인 최종 검수는 `cleanup-status.json` 및 후속 검수 기록으로 확인한다."]
    (OUT/'결과.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    hashes={str(p.relative_to(OUT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.rglob('*') if p.is_file() and p.name!='sha256.json'}
    (OUT/'sha256.json').write_text(json.dumps(hashes,ensure_ascii=False,indent=2),encoding='utf-8')
    print(OUT)


if __name__ == '__main__':main()
