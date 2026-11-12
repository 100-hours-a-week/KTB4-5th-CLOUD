"""회차별 초기화와 35분 시험을 순차 실행하고 실패 회차에서 멈춘다."""
import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from analyze import analyze
from remote import aws
from run import ROOT, REPO, KUBE, kubectl


def credit_snapshot():
    now=dt.datetime.now(dt.timezone.utc)
    result={}
    for metric in ['CPUUtilization','CPUCreditBalance','CPUSurplusCreditBalance','CPUSurplusCreditsCharged']:
        result[metric]=aws('cloudwatch','get-metric-statistics','--namespace','AWS/EC2','--metric-name',metric,
            '--dimensions','Name=InstanceId,Value=i-081d444e5e162a1c2','--start-time',(now-dt.timedelta(minutes=10)).isoformat(),
            '--end-time',now.isoformat(),'--period','300','--statistics','Average','Maximum','Minimum')
    return result


def main():
    root=ROOT/'private'
    status={'started_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'rounds':[],
            'script_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ['load.js','prepare.js','run.py','analyze.py']}}
    if '--resume' in sys.argv:
        prior=json.loads((root/'suite-status.json').read_text(encoding='utf-8'))
        if prior['script_sha256']!=status['script_sha256']:
            raise RuntimeError('재개 전 고정 소스 해시 불일치')
        status=prior
        status.setdefault('resumed_utc',[]).append(dt.datetime.now(dt.timezone.utc).isoformat())
        status.pop('ended_utc',None)
        status.pop('error',None)
    def save():
        (root/'suite-status.json').write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8')
    save()
    try:
        kubectl('cp','./tests/t1-primary/prepare.js','stage/t1-runner:/work/prepare-final.js')
        for index in range(len(status['rounds'])+1,4):
            name=f'round-{index}'
            status['phase']=f'{name}: reset'
            save()
            with (root/f'{name}-reset.log').open('w',encoding='utf-8') as log:
                reset=subprocess.run(KUBE+['exec','-n','stage','t1-runner','--','k6','run','--quiet','-e','MODE=reset','/work/prepare-final.js'],stdout=log,stderr=log)
            if reset.returncode:
                raise RuntimeError(f'{name}: 데이터 초기화 검증 실패')
            before=credit_snapshot()
            (root/f'{name}-credits-before.json').write_text(json.dumps(before,indent=2),encoding='utf-8')
            status['phase']=f'{name}: running'
            save()
            print(status['phase'],flush=True)
            run=subprocess.run([sys.executable,str(ROOT/'run.py'),name])
            raw=root/name/f'{name}.json.gz'
            if not raw.exists():
                raise RuntimeError(f'{name}: 원시 데이터 없음')
            result=analyze(raw)
            (root/name/'analysis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            after=credit_snapshot()
            (root/f'{name}-credits-after.json').write_text(json.dumps(after,indent=2),encoding='utf-8')
            status['rounds'].append({'name':name,'execution_exit':run.returncode,'analysis':result})
            save()
            if run.returncode or not result['preliminary_pass']:
                raise RuntimeError(f'{name}: 실행 또는 판정 기준 미충족. 실패 회차 보존 후 중단')
        status['phase']='measurements_complete_pending_review'
    except Exception as error:
        status['phase']='stopped'
        status['error']=str(error)
        print(str(error),flush=True)
    finally:
        status['ended_utc']=dt.datetime.now(dt.timezone.utc).isoformat()
        save()
        print('SUITE '+status['phase'],flush=True)


if __name__ == '__main__':
    main()
