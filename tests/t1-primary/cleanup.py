"""이 시험에서 만든 데이터·자원만 정리하고 stage 중지 상태를 복원한다."""
import datetime as dt
import base64
import gzip
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from remote import aws
from run import ROOT, REPO, KUBE, kubectl, command


def ssm_read(script):
    path=ROOT/'private/cleanup-command.json'
    path.write_text(json.dumps({'commands':[script],'executionTimeout':['120']}),encoding='utf-8')
    sent=aws('ssm','send-command','--instance-ids','i-081d444e5e162a1c2','--document-name','AWS-RunShellScript','--parameters',f'file://{path}')
    for _ in range(60):
        time.sleep(1)
        try:
            result=aws('ssm','get-command-invocation','--command-id',sent['Command']['CommandId'],'--instance-id','i-081d444e5e162a1c2')
        except RuntimeError:
            continue
        if result['Status']=='Success':return result['StandardOutputContent']
        if result['Status'] not in ['Pending','InProgress','Delayed']:raise RuntimeError(result['StandardErrorContent'])
    raise TimeoutError('DB 원시 관측 회수 제한 초과')


def capture_db():
    script='''set -eu
pid=$(cat /var/tmp/t1-issue329/observe.pid)
if [ -r /proc/$pid/cmdline ]; then
  case "$(tr '\\000' ' ' < /proc/$pid/cmdline)" in
    *t1-issue329/observe.py*) kill "$pid" ;;
    *) echo '관측 프로세스 식별 불일치' >&2; exit 46 ;;
  esac
fi
gzip -c /var/tmp/t1-issue329/db-observations.jsonl > /var/tmp/t1-issue329/db-observations.jsonl.gz
wc -c < /var/tmp/t1-issue329/db-observations.jsonl.gz
'''
    size=int(ssm_read(script).strip())
    target=ROOT/'private/db-observations.jsonl.gz'
    with target.open('wb') as out:
        for index in range((size+11999)//12000):
            value=ssm_read(f'dd if=/var/tmp/t1-issue329/db-observations.jsonl.gz bs=12000 skip={index} count=1 status=none | base64 -w0')
            out.write(base64.b64decode(value))
    assert target.stat().st_size==size
    with gzip.open(target,'rt') as stream:
        rows=sum(1 for line in stream if json.loads(line))
    return {'bytes':size,'samples':rows}


def data_cleanup_script():
    private=ROOT/'private'
    accounts=json.loads((private/'accounts.json').read_text())
    fixtures=json.loads((private/'fixtures.json').read_text())
    assert len(accounts)==len(fixtures)==50
    assert all(re.fullmatch('[a-z0-9]+',a['loginId']) for a in accounts)
    login_ids=','.join("'"+a['loginId']+"'" for a in accounts)
    fridge_ids=','.join(str(int(f['fridge'])) for f in fixtures)
    users=f'(SELECT user_id FROM users WHERE login_id IN ({login_ids}))'
    fridges=f'({fridge_ids})'
    own_user=f'user_id IN {users}'
    own_fridge=f'refrigerator_id IN {fridges}'
    conditions=[
        ('analysis_outbox',f'analysis_job_id IN (SELECT analysis_job_id FROM analysis_job_states WHERE {own_user})'),
        ('analysis_job_requests',f'analysis_job_id IN (SELECT analysis_job_id FROM analysis_job_states WHERE {own_user})'),
        ('analysis_dispatches',own_user),('analysis_job_states',own_user),('image_upload_metadata',own_user),
        ('cooking_outbox',f'cooking_record_id IN (SELECT cooking_record_id FROM cooking_records WHERE {own_fridge} OR {own_user})'),
        ('cooking_records',f'{own_fridge} OR {own_user}'),
        ('push_notifications',f'{own_fridge} OR {own_user}'),
        ('notification_recipients',f'{own_user} OR notification_id IN (SELECT notification_id FROM notifications WHERE {own_fridge})'),
        ('notifications',own_fridge),('recommendation_states',f'{own_fridge} OR {own_user}'),
        ('recommendation_daily_jobs',f'{own_fridge} OR {own_user}'),('recommendation_outbox',own_fridge),
        ('recommendation_lists',f'{own_fridge} OR {own_user}'),('ingredients',own_fridge),
        ('invite_codes',own_fridge),('membership_notification_progress',own_fridge),('membership_transmissions',own_fridge),
        ('expiration_checks',own_fridge),('refrigerator_members',own_fridge),('refrigerators',own_fridge),
        ('notification_preferences',own_user),('social_accounts',own_user),('user_devices',own_user),
        ('users',f'login_id IN ({login_ids})')]
    sql='START TRANSACTION;\n'+'\n'.join(f'DELETE FROM `{table}` WHERE {where};' for table,where in conditions)+'\nCOMMIT;\n'
    sql+=f'SELECT COUNT(*) AS remaining_test_users FROM users WHERE login_id IN ({login_ids});\n'
    sql+=f'SELECT COUNT(*) AS remaining_test_ingredients FROM ingredients WHERE {own_fridge};\n'
    guard=f"SELECT CONCAT((SELECT COUNT(*) FROM users WHERE login_id IN ({login_ids})),',',(SELECT COUNT(*) FROM refrigerator_members WHERE {own_fridge} AND user_id NOT IN {users}),',',(SELECT COUNT(*) FROM ingredients WHERE {own_fridge}));"
    script='''set -eu
MYSQL_PWD=$(aws ssm get-parameter --name /dameokja-v3-stage/MYSQL_ROOT_PASSWORD --with-decryption --region ap-northeast-2 --query Parameter.Value --output text)
export MYSQL_PWD
'''
    script+=f'guard=$(mysql -uroot --batch --skip-column-names app -e "{guard}")\n'
    script+='[ "$guard" = "50,0,50000" ] || { echo "정리 사전 조건 불일치: $guard"; exit 45; }\n'
    script+="mysql -uroot --batch app <<'SQL'\n"+sql+'SQL\nunset MYSQL_PWD\n'
    path=private/'cleanup-data.sh'
    path.write_text(script,encoding='utf-8')
    return path


def main():
    private=ROOT/'private'
    status={'started_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'steps':[],'errors':[]}
    def step(name,action):
        try:
            result=action()
            status['steps'].append({'name':name,'ok':True,'result':result})
        except Exception as error:
            status['errors'].append({'name':name,'error':str(error)})
        (private/'cleanup-status.json').write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8')
    # 원래 배포의 추가한 affinity만 제거하고 기존 설정은 보존한다.
    original=json.loads((private/'be-before.json').read_text(encoding='utf-8-sig'))
    patch={'spec':{'replicas':original['spec']['replicas'],'template':{'spec':{'affinity':original['spec']['template']['spec'].get('affinity')}}}}
    patch_path=private/'be-restore.json';patch_path.write_text(json.dumps(patch),encoding='utf-8')
    step('restore_be',lambda:kubectl('patch','deploy','be-blue','-n','stage','--type','merge','--patch-file',str(patch_path)))
    step('wait_be_stopped',lambda:command(KUBE+['wait','--for=delete','pod','-n','stage','-l','app.kubernetes.io/name=be','--timeout=90s'],timeout=100))
    step('capture_db_observations',capture_db)
    if not status['errors']:
        script=data_cleanup_script()
        step('remove_test_data',lambda:command([sys.executable,str(ROOT/'remote.py'),'i-081d444e5e162a1c2',str(script),'--output',str(private/'cleanup-data-result.json')],timeout=120))
    step('pause_kafka',lambda:kubectl('annotate','kafka','stage-kafka','-n','stage','strimzi.io/pause-reconciliation=true','--overwrite'))
    step('stop_kafka_operator',lambda:kubectl('scale','deployment','stage-kafka-entity-operator','-n','stage','--replicas=0'))
    step('stop_kafka_brokers',lambda:kubectl('delete','strimzipodset','stage-kafka-dual-role','-n','stage','--ignore-not-found','--wait=false'))
    step('stop_redis',lambda:kubectl('scale','statefulset','redis','-n','stage','--replicas=0'))
    step('remove_runner',lambda:kubectl('delete','pod','t1-runner','-n','stage','--ignore-not-found','--wait=false'))
    for kind,name in [('secret','t1-accounts'),('configmap','t1-scripts'),('networkpolicy','t1-runner')]:
        step('remove_'+kind,lambda kind=kind,name=name:kubectl('delete',kind,name,'-n','stage','--ignore-not-found'))
    original=json.loads((private/'nodepool-before.json').read_text(encoding='utf-8-sig'))
    restore=private/'nodepool-restore.json';restore.write_text(json.dumps({'spec':original['spec']}),encoding='utf-8')
    step('restore_nodepool',lambda:kubectl('patch','nodepool','stage-app','--type','merge','--patch-file',str(restore)))
    step('stop_stage_db',lambda:aws('ec2','stop-instances','--instance-ids','i-081d444e5e162a1c2'))
    # Kafka/PVC는 기존 자원이므로 삭제하지 않는다. 빈 시험 노드는 복원된 정책으로 정리된다.
    status['ended_utc']=dt.datetime.now(dt.timezone.utc).isoformat()
    (private/'cleanup-status.json').write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(status,ensure_ascii=False),flush=True)
    raise SystemExit(1 if status['errors'] else 0)


if __name__ == '__main__':
    main()
