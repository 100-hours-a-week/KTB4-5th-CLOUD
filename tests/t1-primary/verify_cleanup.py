"""정리 후 실제 상태를 읽어 원래 stage 중지 상태와 운영 배포 보존을 확인한다."""
import datetime as dt
import json
from run import ROOT,kubectl
from remote import aws


def main():
    private=ROOT/'private'
    def read(kind,name=None,namespace=None):
        args=['get',kind]
        if name:args.append(name)
        if namespace:args+=['-n',namespace]
        return json.loads(kubectl(*args,'-o','json'))
    def original(name):return json.loads((private/name).read_text(encoding='utf-8-sig'))
    be=read('deploy','be-blue','stage')
    before=original('be-before.json')
    nodepool=read('nodepool','stage-app')
    prod=read('deploy',namespace='prod')
    prod_specs=lambda data:{p['metadata']['name']:p['spec'] for p in data['items']}
    pods=read('pods',namespace='stage')
    nodes=read('nodes')
    db=aws('ec2','describe-instances','--instance-ids','i-081d444e5e162a1c2')['Reservations'][0]['Instances'][0]
    pvc=read('pvc',namespace='stage')
    status={'verified_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'checks':{
        'be_replicas_restored':be['spec']['replicas']==before['spec']['replicas'],
        'be_affinity_restored':be['spec']['template']['spec'].get('affinity')==before['spec']['template']['spec'].get('affinity'),
        'nodepool_spec_restored':nodepool['spec']==original('nodepool-before.json')['spec'],
        'prod_deployment_specs_preserved':prod_specs(prod)==prod_specs(original('prod-baseline.json')),
        'stage_pods_zero':not pods['items'],
        'stage_nodes_zero':not any(n['metadata']['labels'].get('dameokja.com/environment')=='stage' for n in nodes['items']),
        'database_stopped':db['State']['Name']=='stopped',
        'redis_zero':read('statefulset','redis','stage')['spec']['replicas']==0,
        'kafka_paused':read('kafka','stage-kafka','stage')['metadata']['annotations'].get('strimzi.io/pause-reconciliation')=='true',
        'kafka_operator_zero':read('deploy','stage-kafka-entity-operator','stage')['spec']['replicas']==0,
        'kafka_podsets_zero':not read('strimzipodset',namespace='stage')['items'],
    },'remaining_stage_pvcs':[p['metadata']['name'] for p in pvc['items']],
       'remaining_stage_pods':[p['metadata']['name'] for p in pods['items']]}
    status['all_checked_pass']=all(status['checks'].values())
    out=ROOT/'results/2026-11-11';out.mkdir(parents=True,exist_ok=True)
    (out/'cleanup-verification.json').write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(status,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
