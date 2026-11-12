"""격리 stage 시험을 실행하고 관측 상실·환경 변경 시 중단한다."""
import argparse
import datetime as dt
import json
import subprocess
import time
import urllib.request
import hashlib
import gzip
import base64
import re
from pathlib import Path
from remote import aws

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
KUBE = ['kubectl', '--kubeconfig', str(REPO/'infra/v3/private/kubeconfig')]
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def command(args, timeout=20):
    p = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', timeout=timeout, cwd=REPO)
    if p.returncode:
        raise RuntimeError(p.stderr[-1500:])
    return p.stdout


def kubectl(*args):
    read_only = args and (args[0] in ['get','top'] or (args[0]=='exec' and (args[-1].startswith('cat /sys/fs/cgroup/cpu.stat') or 'sha256sum' in args)))
    attempts = 3 if read_only else 1
    for attempt in range(attempts):
        try:
            return command(KUBE + list(args))
        except (RuntimeError,subprocess.TimeoutExpired):
            if attempt+1==attempts:
                raise
            time.sleep(1)


def copy_from_pod(filename,local_file):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',filename):
        raise ValueError('원격 파일 이름 검증 실패')
    remote=f'/work/{filename}'
    digest=kubectl('exec','-n','stage','t1-runner','--','sha256sum',remote).split()[0]
    size=int(kubectl('exec','-n','stage','t1-runner','--','sh','-c',f'wc -c < {remote}').strip())
    partial=local_file.with_name(local_file.name+'.part')
    # 긴 스트림이 중간 종료되는 관리 터널에서도 청크별 재시도가 가능하게 한다.
    chunk=1024*1024
    with partial.open('wb') as output:
        for offset in range((size+chunk-1)//chunk):
            expected=min(chunk,size-offset*chunk)
            for attempt in range(3):
                try:
                    encoded=command(KUBE+['exec','-n','stage','t1-runner','--','sh','-c',
                        f'dd if={remote} bs={chunk} skip={offset} count=1 2>/dev/null | base64'],timeout=40)
                    value=base64.b64decode(encoded)
                    if len(value)!=expected:raise RuntimeError('청크 길이 불일치')
                    output.write(value)
                    break
                except Exception:
                    if attempt==2:raise
                    time.sleep(1)
    if partial.stat().st_size!=size or hashlib.sha256(partial.read_bytes()).hexdigest()!=digest:
        raise RuntimeError(f'{filename}: 원격·로컬 SHA256 불일치')
    if filename.endswith('.gz'):
        with gzip.open(partial,'rb') as stream:
            while stream.read(1024*1024):pass
    partial.replace(local_file)
    return digest


def pods():
    data = json.loads(kubectl('get','pods','-n','stage','-o','json'))
    return {p['metadata']['name']: {'uid': p['metadata']['uid'], 'node': p['spec'].get('nodeName'),
             'restarts': sum(c.get('restartCount',0) for c in p.get('status',{}).get('containerStatuses',[])),
             'ready': bool(p.get('status',{}).get('containerStatuses')) and all(c.get('ready',False) for c in p.get('status',{}).get('containerStatuses',[])),
             'images': [c['image'] for c in p['spec']['containers']],
             'resources': [c.get('resources',{}) for c in p['spec']['containers']]}
            for p in data['items']}


def database_sample(target):
    params = target/'db-read.json'
    params.write_text(json.dumps({'commands':['tail -n 1 /var/tmp/t1-issue329/db-observations.jsonl; df -Pk /var/lib/mysql']}),encoding='utf-8')
    sent = aws('ssm','send-command','--instance-ids','i-081d444e5e162a1c2',
               '--document-name','AWS-RunShellScript','--parameters',f'file://{params}')
    for _ in range(12):
        time.sleep(1)
        try:
            result = aws('ssm','get-command-invocation','--command-id',sent['Command']['CommandId'],
                         '--instance-id','i-081d444e5e162a1c2')
        except RuntimeError:
            continue
        if result['Status'] == 'Success':
            lines = result['StandardOutputContent'].splitlines()
            row = json.loads(lines[0])
            row['disk_free'] = lines[1:]
            age = (dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(row['time'])).total_seconds()
            memory = {line.split(':')[0]:int(line.split()[1]) for line in row['meminfo'].splitlines()}
            if age > 60 or row['mysql_exit'] != 0 or memory['MemAvailable'] < 131072:
                raise RuntimeError('DB 관측 단절 또는 메모리 임계값 초과')
            if int(lines[-1].split()[3]) < 1048576:
                raise RuntimeError('DB 디스크 여유 1GiB 미만')
            return row
        if result['Status'] not in ['Pending','InProgress','Delayed']:
            raise RuntimeError('DB 관측 조회 실패')
    raise TimeoutError('DB 관측 응답 제한 초과')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('name')
    parser.add_argument('--warmup', type=int, default=300)
    parser.add_argument('--seconds', type=int, default=1800)
    parser.add_argument('--scale', type=float, default=1)
    args = parser.parse_args()
    target = ROOT/'private'/args.name
    target.mkdir(exist_ok=False)
    baseline = pods()
    if 't1-runner' not in baseline or sum(name.startswith('be-blue-') for name in baseline) != 2 or not all(p['ready'] for p in baseline.values()):
        raise RuntimeError('BE 2개와 발생기의 준비 조건 미충족')
    manifest = {'name':args.name,'warmup':args.warmup,'seconds':args.seconds,'scale':args.scale,
                'start_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'baseline':baseline}
    (target/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    for filename in ['load.js','supervise.sh']:
        kubectl('cp','./'+(ROOT/filename).relative_to(REPO).as_posix(),f'stage/t1-runner:/work/{filename}')
    log = (target/'execution.log').open('w',encoding='utf-8')
    proc = subprocess.Popen(KUBE+['exec','-n','stage','t1-runner','--','sh','/work/supervise.sh',
                            args.name,str(args.warmup),str(args.seconds),str(args.scale)],stdout=log,stderr=log)
    failures = 0
    total_errors = 0
    started = time.monotonic()
    last_database = -60
    with (target/'observations.jsonl').open('w',encoding='utf-8') as out:
        while proc.poll() is None:
            sample = {'time':dt.datetime.now(dt.timezone.utc).isoformat(),'elapsed':time.monotonic()-started}
            try:
                current = pods()
                if current != baseline:
                    raise RuntimeError('Pod 배치·재시작·Ready 상태가 기준과 다름')
                sample['pods'] = current
                for port in [18081,18082]:
                    with HTTP.open(f'http://[::1]:{port}/actuator/prometheus',timeout=10) as r:
                        lines = r.read().decode().splitlines()
                    wanted = ('hikaricp_','process_cpu_usage','system_cpu_usage','jvm_memory_used_bytes','http_server_requests_seconds_count')
                    sample[str(port)] = [line for line in lines if line.startswith(wanted)]
                    if not any(line.startswith('hikaricp_connections_pending') for line in sample[str(port)]):
                        raise RuntimeError('연결 풀 지표 누락')
                sample['resources'] = kubectl('top','pods','-n','stage','--containers')
                if sample['elapsed'] - last_database >= 60:
                    sample['database'] = database_sample(target)
                    last_database = sample['elapsed']
                sample['generator'] = kubectl('exec','-n','stage','t1-runner','--','sh','-c',
                    'cat /sys/fs/cgroup/cpu.stat; cat /sys/fs/cgroup/memory.current; cat /proc/net/dev; date +%s > /work/heartbeat')
                failures = 0
            except Exception as error:
                failures += 1
                total_errors += 1
                sample['error'] = str(error)
                if failures >= 3:
                    try:
                        kubectl('exec','-n','stage','t1-runner','--','sh','-c','kill -INT "$(cat /work/load.pid)"')
                    except Exception:
                        pass
                    sample['aborted'] = True
            out.write(json.dumps(sample,ensure_ascii=False)+'\n')
            out.flush()
            print(f'{args.name}: {int(sample["elapsed"])}s, observation_errors={failures}',flush=True)
            if failures >= 3:
                break
            for _ in range(15):
                if proc.poll() is not None:
                    break
                time.sleep(1)
    try:
        proc.wait(timeout=110)
    except subprocess.TimeoutExpired:
        proc.terminate()
    log.close()
    manifest['end_utc'] = dt.datetime.now(dt.timezone.utc).isoformat()
    manifest['exit_code'] = proc.returncode
    manifest['observation_errors_at_end'] = failures
    manifest['observation_errors_total'] = total_errors
    for suffix in ['.json.gz','-summary.json','.log','.exit']:
        try:
            filename=f'{args.name}{suffix}'
            local_file=target/filename
            remote_hash=copy_from_pod(filename,local_file)
            manifest.setdefault('artifact_sha256',{})[filename]=remote_hash
        except Exception as error:
            manifest.setdefault('artifact_errors',[]).append(str(error))
    (target/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps(manifest,ensure_ascii=False),flush=True)
    raise SystemExit(0 if proc.returncode == 0 and total_errors == 0 and not manifest.get('artifact_errors') else 1)


if __name__ == '__main__':
    main()
