# v3 EKS 기반과 수동 배포

기존 prod VPC·Private Subnet·NAT를 재사용하는 EKS 기반입니다. 클러스터·IAM·SG·시스템 EC2·CNI·kube-proxy·CoreDNS와 Controller IAM·EBS CSI·Metrics Server를 포함합니다. 관리 도구와 앱 NodePool은 아래 "관리 도구 설치"의 Helm으로 설치합니다. DB·Redis·Kafka·앱 배포는 후속 변경입니다.

PR은 cfn-lint만 실행합니다. AWS 자원은 main에서 수동 Workflow를 plan으로 실행해 변경 내용을 확인한 뒤, 같은 스택을 apply로 실행할 때 생성됩니다.

## 구성과 적용 순서

| 순서 | 스택 | 템플릿 | 확인할 상태 |
|---|---|---|---|
| 1 | dameokja-v3-eks | cloudformation/eks.yaml | 클러스터 ACTIVE·Access Entry·OIDC |
| 2 | dameokja-v3-network-addons | cloudformation/network-addons.yaml | CNI·kube-proxy·IRSA |
| 3 | dameokja-v3-system-node | cloudformation/system-node.yaml | 시스템 Node 1대 Ready |
| 4 | dameokja-v3-dns-addon | cloudformation/dns-addon.yaml | CoreDNS 1 Replica Ready·DNS |
| 5 | dameokja-v3-controller-iam | cloudformation/controller-iam.yaml | Karpenter·EBS CSI IRSA 역할 |
| 6 | dameokja-v3-storage-addon | cloudformation/storage-addon.yaml | EBS CSI·Metrics Server 1 Replica Ready |
| 7 | dameokja-v3-dev-pod-sg | cloudformation/dev-pod-sg.yaml | dev FE·BE·AI·Redis·Kafka Pod SG |
| 8 | dameokja-v3-dev-data-access | cloudformation/dev-data-access.yaml | 기본 VPC Peering·경로, dev DB SG의 BE→3306·AI→5432, dev DB EC2 PostgreSQL 설치 |
| 9 | dameokja-v3-dev-app-iam | cloudformation/dev-app-iam.yaml | dev BE·AI IRSA 역할(S3 image/ 접두사), 팀 그룹 dameokja-iam의 dev SSM(`/dameokja-v3-dev*`) 관리 정책 |
| 10 | dameokja-v3-observability | cloudformation/observability.yaml | 관측 EC2(Prometheus·Loki·Grafana), Association 성공 |
| 11 | dameokja-v3-stage-pod-sg | cloudformation/stage-pod-sg.yaml | stage FE·BE·AI·Redis·Kafka Pod SG, Kafka 브로커 사이 9090·9091, Gateway ALB→stage FE·BE(dev-pod-sg의 ALB SG Export를 먼저 apply) |
| 12 | dameokja-v3-stage-db | cloudformation/stage-db.yaml | stage DB EC2(t3.medium) MySQL·PostgreSQL, 두 Association 성공(stage-pod-sg 뒤, 비밀번호는 prod SSM `/dameokja-v2-prod/SPRING_DATASOURCE_PASSWORD`·`/dameokja-v2-prod-ai/POSTGRES_PASSWORD`와 같은 값) |
| 13 | dameokja-v3-stage-app-iam | cloudformation/stage-app-iam.yaml | stage BE·AI IRSA 역할(S3 image/stage/ 접두사), 팀 그룹 dameokja-iam의 stage SSM(`/dameokja-v3-stage*`) 관리 정책 |
| 14 | dameokja-v3-stage-power | cloudformation/stage-power.yaml | stage 켜기·끄기용 GitHub OIDC 역할·kubectl 역할(Access Entry, 그룹 dameokja:stage-power) |
| 15 | dameokja-v3-grafana-edge | cloudformation/grafana-edge.yaml | us-east-1. Grafana 인증서·CloudFront·VPC Origin(관측 EC2 3000)·grafana.dameokja.com 레코드(observability 뒤) |

EKS는 서울 1.35, 시스템 Node는 self-managed m7i.large·AL2023 1대이며 ASG를 생성하지 않습니다. AL2023 nodeadm MIME UserData, IMDSv2, 암호화 gp3를 사용합니다. 시스템 라벨은 dameokja.com/node-role=system, taint는 dameokja.com/system=true:NoSchedule입니다. CoreDNS는 해당 라벨·toleration으로 배치합니다.

기본 self-managed add-on 생성을 끄고 고정 버전 managed add-on을 별도 스택으로 관리합니다. Node가 없는 상태에서 CoreDNS의 가용성을 기다리는 문제를 피하기 위해 CoreDNS를 마지막에 설치합니다. CNI·kube-proxy의 Node 없는 상태와 실제 add-on 생성 동작은 실환경에서 확인해야 합니다.

클러스터 인증은 API Access Entry를 사용합니다. 생성자의 암묵적 관리자 권한은 끄고 지정한 관리자 principal과 EC2_LINUX Node 역할을 등록합니다. CNI는 전용 IRSA를 사용하고 Node 역할에 CNI 정책을 중복 부여하지 않습니다. 앱 Node 역할은 후속 Karpenter용으로 미리 준비하지만 이번 변경으로 앱 EC2를 공급하지는 않습니다.

## 입력과 접근 경로

params/*.json은 Workflow가 그대로 쓰는 실제 입력입니다. 여기에는 조회한 실제 계정·재사용 VPC·Subnet과 고정 AMI를 넣었습니다. 실행 전 대상과 소유권을 다시 대조합니다. 클러스터 endpoint·CA·OIDC·역할·SG처럼 EKS 스택이 만드는 값은 파라미터로 넣지 않고 `dameokja-v3-eks-*` Export를 ImportValue로 가져옵니다. CA는 Export 길이 제한(1024자)을 넘어 SSM Parameter `/dameokja-v3-eks/cluster-ca`로 전달하고, system-node 스택은 `AWS::SSM::Parameter::Value<String>` 파라미터로 읽습니다. kubeconfig는 Git 제외 경로 private/에 보관합니다.

API endpoint는 기본 private입니다. 기존 prod Worker를 관리 호스트로 임의 사용하지 않고, 3단계 이후 시스템 Node를 거치는 SSM 포트 포워딩으로 접속합니다. Access Entry에 등록한 관리자 principal(dameokja-aws-cli)로 실행합니다.

```bash
ENDPOINT=$(aws eks describe-cluster --name dameokja-v3 --query cluster.endpoint --output text | sed 's#https://##')
NODE=$(aws cloudformation describe-stacks --stack-name dameokja-v3-system-node --query "Stacks[0].Outputs[?OutputKey=='SystemNodeInstanceId'].OutputValue" --output text)
aws ssm start-session --target "$NODE" --document-name AWS-StartPortForwardingSessionToRemoteHost --parameters "host=$ENDPOINT,portNumber=443,localPortNumber=8443"
# 다른 터미널에서
aws eks update-kubeconfig --name dameokja-v3 --kubeconfig infra/v3/private/kubeconfig --alias dameokja-v3
kubectl --kubeconfig infra/v3/private/kubeconfig config set-cluster "$(aws eks describe-cluster --name dameokja-v3 --query cluster.arn --output text)" --server https://127.0.0.1:8443 --tls-server-name "$ENDPOINT"
```

시스템 Node 생성 전(1~2단계)에는 kubectl 접속이 필요하지 않습니다.

Service CIDR 192.168.0.0/16이 VPC·VPN·관리자 경로와 겹치지 않는지 확인합니다. AMI는 versions.yaml의 확인 시점 ID로 고정하며 recommended를 실행 때마다 추종하지 않습니다.

## GitHub 설정과 실행

Repository Variables의 `AWS_CFN_DEPLOY_ROLE_ARN`(dameokja-github-cfn-deploy), `AWS_CFN_EXEC_ROLE_ARN`(dameokja-cfn-execution)을 v2와 같이 사용합니다. 기존 배포 역할의 main OIDC 신뢰를 재사용하며 GitHub Environment 승인은 두지 않습니다.

Actions의 "v3 EKS 기반 CloudFormation"을 main에서 실행합니다. 스택을 고르고 plan으로 실행해 Summary의 변경 내용(특히 Replacement)을 확인한 뒤, 같은 스택을 apply로 실행합니다. apply는 새 change set을 만들지 않고 가장 최근 plan의 change set을 그대로 실행합니다. 다음 스택은 앞 스택이 끝난 뒤 실행합니다. Export를 다른 스택이 쓰고 있으면 EKS 스택의 해당 Output을 바꾸거나 지울 수 없습니다.

## 관리 도구 설치

CloudFormation 6개 스택 뒤에 관리자 PC에서 포트 포워딩을 연 상태로 실행합니다. 모두 tool Namespace, 시스템 Node(라벨·toleration)에 1 Replica로 둡니다. values는 `v3/platform/values/`, 앱 NodePool·StorageClass는 `v3/charts/platform/`에 있습니다. 이후 Argo CD로 인계하며 Helm과 이중 운영하지 않습니다.

| 도구 | Chart | 버전 |
|---|---|---|
| Karpenter(CRD 포함) | oci://public.ecr.aws/karpenter/karpenter-crd, karpenter | 1.15.1 |
| Strimzi(dev 감시) | oci://quay.io/strimzi-helm/strimzi-kafka-operator | 1.3.0 |
| Sealed Secrets | https://bitnami.github.io/sealed-secrets sealed-secrets | 2.22.0 |
| Argo CD | https://argoproj.github.io/argo-helm argo-cd | 10.11.6 |
| Gateway API CRD(standard) | https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.6.0/standard-install.yaml | v1.6.0 |
| AWS Load Balancer Controller(CRD 포함) | https://aws.github.io/eks-charts aws-load-balancer-controller | 3.6.2 |
| kube-state-metrics | https://prometheus-community.github.io/helm-charts kube-state-metrics | 9.0.0 |
| Grafana Alloy(DaemonSet) | https://grafana.github.io/helm-charts alloy | 1.15.0 |

```bash
K="--kubeconfig infra/v3/private/kubeconfig"  # v3 전용 kubeconfig라 context 지정이 필요 없습니다
out() { aws cloudformation describe-stacks --stack-name "$1" --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text; }
kubectl $K apply -f v3/platform/namespaces.yaml
helm $K upgrade --install karpenter-crd oci://public.ecr.aws/karpenter/karpenter-crd --version 1.15.1 -n tool --wait
helm $K upgrade --install karpenter oci://public.ecr.aws/karpenter/karpenter --version 1.15.1 -n tool --skip-crds --wait \
  -f v3/platform/values/karpenter.yaml \
  --set 'serviceAccount.annotations.eks\.amazonaws\.com/role-arn'=$(out dameokja-v3-controller-iam KarpenterControllerRoleArn)
helm $K upgrade --install strimzi oci://quay.io/strimzi-helm/strimzi-kafka-operator --version 1.3.0 -n tool --wait -f v3/platform/values/strimzi.yaml
helm $K upgrade --install sealed-secrets sealed-secrets --repo https://bitnami.github.io/sealed-secrets --version 2.22.0 -n tool --wait -f v3/platform/values/sealed-secrets.yaml
helm $K upgrade --install argocd argo-cd --repo https://argoproj.github.io/argo-helm --version 10.11.6 -n tool --wait -f v3/platform/values/argo-cd.yaml
kubectl $K apply --server-side -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.6.0/standard-install.yaml
helm $K upgrade --install aws-load-balancer-controller aws-load-balancer-controller --repo https://aws.github.io/eks-charts --version 3.6.2 -n tool --wait \
  -f v3/platform/values/load-balancer-controller.yaml \
  --set 'serviceAccount.annotations.eks\.amazonaws\.com/role-arn'=$(out dameokja-v3-controller-iam LoadBalancerControllerRoleArn)
# 관측(observability 스택 뒤): 로그·메트릭을 관측 EC2(10.20.32.213)로 보냅니다.
helm $K upgrade --install kube-state-metrics kube-state-metrics --repo https://prometheus-community.github.io/helm-charts --version 9.0.0 -n tool --wait -f v3/platform/values/kube-state-metrics.yaml
helm $K upgrade --install alloy alloy --repo https://grafana.github.io/helm-charts --version 1.15.0 -n tool --wait -f v3/platform/values/alloy.yaml
helm $K upgrade --install platform v3/charts/platform -n tool --wait \
  --set gatewayAlbSecurityGroupId=$(out dameokja-v3-dev-pod-sg GatewayAlbSecurityGroupId) \
  --set appNodeRoleName=$(out dameokja-v3-eks AppNodeRoleName) \
  --set workerSecurityGroupId=$(out dameokja-v3-eks WorkerSecurityGroupId) \
  --set environments.dev.podSecurityGroups.fe=$(out dameokja-v3-dev-pod-sg FePodSecurityGroupId) \
  --set environments.dev.podSecurityGroups.be=$(out dameokja-v3-dev-pod-sg BePodSecurityGroupId) \
  --set environments.dev.podSecurityGroups.ai-api=$(out dameokja-v3-dev-pod-sg AiPodSecurityGroupId) \
  --set environments.dev.podSecurityGroups.redis=$(out dameokja-v3-dev-pod-sg RedisPodSecurityGroupId) \
  --set environments.dev.podSecurityGroups.kafka=$(out dameokja-v3-dev-pod-sg KafkaPodSecurityGroupId) \
  --set environments.stage.podSecurityGroups.fe=$(out dameokja-v3-stage-pod-sg FePodSecurityGroupId) \
  --set environments.stage.podSecurityGroups.be=$(out dameokja-v3-stage-pod-sg BePodSecurityGroupId) \
  --set environments.stage.podSecurityGroups.ai-api=$(out dameokja-v3-stage-pod-sg AiPodSecurityGroupId) \
  --set environments.stage.podSecurityGroups.redis=$(out dameokja-v3-stage-pod-sg RedisPodSecurityGroupId) \
  --set environments.stage.podSecurityGroups.kafka=$(out dameokja-v3-stage-pod-sg KafkaPodSecurityGroupId)
```

stage-app NodePool은 dev-app과 같은 사양이지만 `dameokja.com/environment=stage` 라벨·NoSchedule taint가 있어 stage Pod(같은 nodeSelector·toleration)만 받습니다. stage Pod를 0개로 줄이면 5분 뒤 stage Node도 0대가 됩니다. stage Kafka는 브로커 3개라 NetworkPolicy·Pod SG에서 브로커끼리 9090·9091을 엽니다.

NodePool은 Pending Pod가 있을 때만 앱 Node(m7i.large·2b·On-Demand)를 만들고, 빈 Node는 5분 뒤 정리합니다. redis-gp3·kafka-gp3는 Retain이라 PVC를 지워도 EBS가 남습니다. platform 차트는 dev Pod에 Pod SG를 붙이고 dev를 기본 차단한 뒤 DNS·Redis·Kafka·DB만 허용합니다. platform 차트는 tool에 공용 Gateway(internal ALB, HTTP 80)도 만듭니다. LBC는 ALB만 만들고 SG는 dev-pod-sg의 Gateway ALB SG를 그대로 씁니다(SG 생성·Pod SG 규칙 변경 권한 없음). Gateway에는 `dameokja.com/gateway-access: 'true'` 라벨이 있는 Namespace의 HTTPRoute만 붙습니다.

## dev 데이터 계층

dev-pod-sg·dev-data-access 스택과 platform 차트 뒤에 설치합니다. dev-data-access는 CNI의 DB 호스트 SNAT 예외(network-addons)가 먼저 적용되어 있어야 BE Pod SG로 DB 접근을 제한할 수 있습니다. Redis 비밀번호는 4단계에서 SealedSecret으로 바꾸기 전까지 Git 밖에서 만듭니다.

```bash
kubectl $K -n dev create secret generic cloud-secrets --from-literal=REDIS_PASSWORD="$(openssl rand -hex 24)"
helm $K upgrade --install dev-data v3/charts/dev-data -n dev --wait --timeout 10m
kubectl $K -n dev wait kafka/dev-kafka --for=condition=Ready --timeout=600s
helm $K upgrade dev-data v3/charts/dev-data -n dev --set topics.enabled=true --wait
kubectl $K -n dev wait kafkatopic --all --for=condition=Ready --timeout=300s
```

| 대상 | 구성 | 주소 |
|---|---|---|
| Redis | StatefulSet 1, AOF everysec, maxmemory 512mb, redis-gp3 10Gi | redis.dev.svc.cluster.local:6379 |
| Kafka | Strimzi Kafka 4.2.2 broker·controller 1 Pod, kafka-gp3 10Gi, 내부 PLAINTEXT | dev-kafka-kafka-bootstrap.dev.svc.cluster.local:9092 |
| 토픽 | dev.dameokja.* 7개, partitions·replicas 1, 30일 보존 | |
| PostgreSQL | dev DB EC2에 Ubuntu 기본 패키지(18), DB·계정 receipt_ai, pg_trgm | 172.31.4.192:5432 |

Redis·Kafka는 앱 Node, Topic Operator는 시스템 Node에 둡니다. PVC는 Retain이고 Kafka·Topic CR은 Argo가 지우지 않습니다. `helm uninstall dev-data`는 업무 토픽을 지우므로 정리 수단으로 쓰지 않습니다. 단일 Pod라 재시작·EBS 재연결 동안 중단됩니다.

dev DB EC2에는 인스턴스 역할과 aws CLI가 없어 Association이 SSM의 비밀번호를 읽지 못합니다. receipt_ai는 비밀번호 없이 만들어지므로 Association 성공 뒤 한 번 직접 설정합니다. 같은 값을 v3 AI의 DATABASE_URL에 넣습니다.

```bash
aws ssm start-session --target i-0111bde7fc68be69c
sudo -u postgres psql -c '\password receipt_ai'
```

MySQL의 BE 계정도 v3 앱 Subnet(10.20.32.0/255.255.240.0) host로 같은 권한·비밀번호를 한 번 추가합니다. 없으면 BE가 1130(Host is not allowed)으로 시작하지 못합니다.

## stage 데이터 계층

stage-pod-sg·stage-db 스택과 platform 차트(stage-app NodePool·stage NetworkPolicy), Strimzi의 stage 감시 뒤에 설치합니다. Kafka 브로커 3개가 서로 다른 stage Node에 놓이므로 stage Node(m7i.large) 3대가 새로 생깁니다.

```bash
kubectl $K -n stage create secret generic cloud-secrets --from-literal=REDIS_PASSWORD="$(openssl rand -hex 24)"
helm $K upgrade --install stage-data v3/charts/stage-data -n stage --wait --timeout 15m
kubectl $K -n stage wait kafka/stage-kafka --for=condition=Ready --timeout=900s
helm $K upgrade stage-data v3/charts/stage-data -n stage --set topics.enabled=true --wait
kubectl $K -n stage wait kafkatopic --all --for=condition=Ready --timeout=300s
```

| 대상 | 구성 | 주소 |
|---|---|---|
| Redis | StatefulSet 1, AOF everysec, maxmemory 512mb, redis-gp3 10Gi | redis.stage.svc.cluster.local:6379 |
| Kafka | Strimzi Kafka 4.2.2 broker·controller 3 Pod(Node마다 1), kafka-gp3 10Gi씩, 복제 3·min.insync 2 | stage-kafka-kafka-bootstrap.stage.svc.cluster.local:9092 |
| 토픽 | stage.dameokja.* 9개, partitions 3·replicas 3, 30일 보존 | |
| MySQL·PostgreSQL | stage DB EC2(stage-db 스택) | 10.20.38.0:3306, 10.20.38.0:5432 |

브로커 1개가 내려가도 쓰기(acks=all)와 읽기는 계속됩니다. 단일 AZ(2b)라 AZ 장애는 대비하지 않습니다.

## dev 앱 배포

FE·BE·AI는 `v3/charts/application` 하나로 배포하고 환경 값은 `v3/environments/dev/<앱>.yaml`에 둡니다. 이미지는 v2 dev가 쓰던 digest이며 태그는 받지 않습니다. 색(blue·green)마다 Deployment를 두고 Service `<앱>`이 activeColor를 가리킵니다. FE(/)·BE(/api, Swagger)는 HTTPRoute로 tool Gateway에 붙고, Target Group은 Pod IP 대상입니다.

| 앱 | 주소 | Secret | IAM |
|---|---|---|---|
| fe | fe.dev.svc.cluster.local:3000 | fe-secrets | 없음 |
| be | be.dev.svc.cluster.local:8080 | be-secrets, cloud-secrets(REDIS_PASSWORD) | dameokja-v3-dev-be |
| ai-api | ai-api.dev.svc.cluster.local:8000 | ai-secrets | dameokja-v3-dev-ai-api |

Secret 값은 dev SSM(`/dameokja-v3-dev`, `-ai`, `-fe`)과 Secrets Manager `dameokja-v2-dev/ghcr`에서 옮깁니다. 팀원은 IAM 그룹 dameokja-iam(정책 `dameokja-v3-dev-ssm-team`)으로 이 SSM 경로 값을 바꿀 수 있습니다. 클러스터 주소가 바뀌는 키(AI_BASE_URL, SPRING_DATA_REDIS_*, CALLBACK_URL, LOG_DIR)는 Secret에서 빼고 values의 env(ConfigMap)로 정합니다. AI의 DATABASE_URL은 호스트만 dev DB EC2로 바꿉니다. 관리자 PC Git Bash에서 kubeseal 0.41.2(Controller와 같은 버전)를 설치한 뒤 실행하고, 생성된 `secrets/*.yaml`만 커밋합니다. 인증서는 `v3/platform/sealed-secrets-cert.pem`(2036년 만료)입니다.

```bash
AWS_PROFILE=dameokja-aws-cli v3/environments/dev/seal-secrets.sh
```

dev SSM 경로는 v2 이름(`/dameokja-v2-dev*`)에서 옮겼습니다. dev-app-iam 스택을 배포한 뒤 처음 한 번 관리자가 값을 복사합니다(stage와 같은 방식, 값은 출력하지 않고 이미 있는 이름은 덮어쓰지 않습니다). Secret 값은 그대로라 다시 만들지 않아도 됩니다. 확인한 뒤 v2 경로 값은 지웁니다(스크립트가 마지막에 명령을 보여 줍니다).

```bash
AWS_PROFILE=dameokja-aws-cli v3/environments/dev/copy-ssm-from-v2.sh           # 복사할 이름만 출력
AWS_PROFILE=dameokja-aws-cli v3/environments/dev/copy-ssm-from-v2.sh --apply   # 복사
```

Argo CD 정의는 `v3/argocd/`에 있고 dev-apps 프로젝트는 dev Namespace의 Deployment·Service·ConfigMap·ServiceAccount·SealedSecret만 허용합니다. 3단계에서 직접 만든 cloud-secrets는 SealedSecret이 넘겨받도록 표시한 뒤 동기화합니다. 동기화는 수동이며 secrets를 먼저 합니다.

```bash
kubectl $K -n dev annotate secret cloud-secrets sealedsecrets.bitnami.com/managed=true
kubectl $K apply -f v3/argocd/dev-apps-project.yaml -f v3/argocd/dev-applications.yaml
# argocd CLI 없이 Application에 sync 작업을 넣습니다.
sync() { kubectl $K -n tool patch application "$1" --type merge -p '{"operation":{"sync":{}}}'; }
sync dev-secrets; for a in be ai-api fe; do sync dev-$a; done
```

NetworkPolicy는 FE→BE, BE↔AI(요청·콜백), BE→Redis·Kafka·MySQL, AI→Kafka·PostgreSQL, 외부 443(사설 대역 제외)만 엽니다. Pod SG도 같은 방향만 허용합니다(AiToBe 추가).

## stage 앱 배포

dev와 같은 `v3/charts/application`을 쓰고 값은 `v3/environments/stage/<앱>.yaml`입니다. FE·BE는 2개, AI는 분석 결과를 메모리에 두므로 v2 stage처럼 1개입니다. 모두 stage-app Node(라벨·taint)에만 놓입니다. 이미지는 dev에서 확인한 digest를 수동으로 올립니다.

Secret은 stage SSM(`/dameokja-v3-stage`, `-ai`, `-fe`)에서 옮기고, DB 계정·비밀번호는 prod SSM과 같은 값, DB 주소는 stage DB EC2입니다. stage SSM에 없는 SENTRY_DSN·TAVILY_API_KEY는 dev 값(`/dameokja-v3-dev*`)을 씁니다. 팀원은 IAM 그룹 dameokja-iam(정책 `dameokja-v3-stage-ssm-team`)으로 이 경로 값을 바꿀 수 있습니다. 값을 바꾼 뒤에는 관리자가 아래 스크립트로 Secret을 다시 만들어 커밋합니다.

stage SSM 경로는 v2 이름(`/dameokja-v2-stage*`)에서 옮겼습니다. 처음 한 번 관리자가 값을 복사합니다(값은 출력하지 않고, 이미 있는 이름은 덮어쓰지 않습니다).

```bash
AWS_PROFILE=dameokja-aws-cli v3/environments/stage/copy-ssm-from-v2.sh           # 복사할 이름만 출력
AWS_PROFILE=dameokja-aws-cli v3/environments/stage/copy-ssm-from-v2.sh --apply   # 복사
```

v3 stage Secret을 다시 만들어 동작을 확인한 뒤 v2 경로 값은 지웁니다(스크립트가 마지막에 명령을 보여 줍니다).

```bash
AWS_PROFILE=dameokja-aws-cli v3/environments/stage/seal-secrets.sh
kubectl $K -n stage annotate secret cloud-secrets sealedsecrets.bitnami.com/managed=true
kubectl $K apply -f v3/argocd/stage-apps-project.yaml -f v3/argocd/stage-applications.yaml
sync() { kubectl $K -n tool patch application "$1" --type merge -p '{"operation":{"sync":{}}}'; }
sync stage-secrets; for a in be ai-api fe; do sync stage-$a; done
```

BE는 `ddl-auto=validate`라 빈 DB에서 시작하지 못합니다. 앱 동기화 전에 dev DB의 BE 스키마(데이터 제외)와 AI 스키마·기준 데이터를 stage DB에 한 번 넣습니다(v2 stage 데이터는 옮기지 않음, #266).

## stage 켜기·끄기

Actions의 "v3 stage 켜기·끄기"를 main에서 start 또는 stop으로 실행합니다. 러너가 SSM으로 시스템 Node에서 `v3/environments/stage/power.sh`를 실행하고, 그 스크립트는 kubectl 역할(stage Namespace의 `stage-power` Role만)로 앱·Redis·Kafka를 조정합니다. DB EC2는 Workflow가 시작·중지합니다.

- stop: 앱 0 → Kafka 조정 일시정지(`strimzi.io/pause-reconciliation`)·StrimziPodSet 삭제 → Redis 0 → DB 중지. PVC는 남고, 빈 stage Node는 Karpenter가 5분 뒤 지웁니다.
- start: DB 시작 → Redis 1 → Kafka 조정 재개 → Argo stage 앱 sync(꺼진 동안 반영된 이미지) → 앱 FE 2·BE 2·AI 1.
- 꺼진 동안 Argo의 stage 앱은 OutOfSync로 보입니다(수동 sync라 되돌리지 않음). 켜진 상태에서만 sync합니다.

## stage 이미지와 prod 승인

FE·BE·AI main CI는 이미지를 빌드한 뒤 "v3 stage 이미지 반영"(`v3-stage-image.yml`)을 실행합니다. 이 Workflow가 `v3/environments/stage/<앱>.yaml`의 활성 색상 이미지를 main에 커밋하고, 실행 이름은 `v3 stage <저장소> <실행ID>.<재시도> <이미지>`입니다. `prod-approval.yml`은 같은 이름의 성공 실행이 있어야 prod 배포를 진행합니다. stage가 꺼져 있으면 다음 켜기 때 그 이미지로 뜹니다.

## dev 외부 진입점

CloudFront(dev.dameokja.com, TLS) → VPC Origin → internal Gateway ALB(HTTP 80) → FE·BE Pod 순서입니다. ALB는 LBC가 만들고, edge 스택은 그 ARN·DNS 이름만 params로 받습니다. ALB SG는 CloudFront origin-facing prefix list만 받으므로 VPC 밖에서 ALB로 직접 들어올 수 없습니다. 캐시는 끄고 Host·쿠키·Authorization·쿼리를 모두 넘깁니다.

1. edge-cert(us-east-1) plan → apply. ACM이 검증 CNAME을 만들고 ISSUED가 되면 끝납니다.
2. Output CertificateArn과 ALB ARN·DNS 이름으로 params/edge.json을 만들고 edge plan → apply(DnsWeight 0이라 사용자 트래픽은 아직 v2입니다).
3. CloudFront 도메인으로 Host를 유지해 사전 시험합니다(`curl --connect-to dev.dameokja.com:443:<배포>.cloudfront.net:443 https://dev.dameokja.com/...`). 로그인 redirect, Secure Cookie, 업로드, SSE를 확인합니다.
4. 전환: edge의 DnsWeight 100, v2 dev 스택의 DevDomainV2Record Weight 0. 복원은 두 값을 되돌립니다(TTL 300). prod·stage 레코드는 바꾸지 않습니다.

## dev 관측

관측 EC2(t3.medium, 시스템 Node와 같은 사설 Subnet)에 Prometheus·Loki·Grafana를 docker compose로 띄웁니다. 클러스터 안 Alloy가 로그를 Loki(3100)로, 메트릭을 Prometheus remote-write(9090)로 보내므로 EC2에는 클러스터 자격 증명이 없습니다. 보존은 메트릭 15일·로그 7일입니다. 데이터는 별도 EBS(gp3 30GiB, Retain)의 /data에 둡니다.

1. observability plan → apply. 설정은 State Manager Association(dameokja-v3-observability)이 적용하고, 템플릿의 설정을 바꾸면 다시 실행됩니다.
2. 클러스터 쪽은 "관리 도구 설치"의 kube-state-metrics·Alloy Helm 명령으로 설치합니다. Alloy는 Node마다 1개로, 자기 Node의 Pod 로그(/var/log/pods)를 Loki로, 자기 Node의 kubelet·cAdvisor와 같은 Node의 kube-state-metrics·dev BE(/actuator/prometheus) 메트릭을 Prometheus remote-write로 보냅니다. 송신 주소는 Output ObservabilityPrivateIp(10.20.32.213)이고 `v3/platform/values/alloy.yaml`에 적혀 있습니다. 로그 읽기 위치는 Node의 /var/lib/alloy(hostPath)에 남습니다.
   - dev BE는 NetworkPolicy가 tool의 Alloy에서 8080만 추가로 허용합니다(platform 차트). Alloy는 Pod SG 없이 Node SG로 나가므로 관측 SG와 BE Pod SG(BeProbe)를 그대로 통과합니다.
3. Grafana는 https://grafana.dameokja.com (CloudFront → VPC Origin → 관측 EC2 3000)으로 접속합니다. 관측 SG는 CloudFront Prefix List만 3000으로 들여보냅니다.
   - 로그인은 GitHub만 됩니다(비밀번호 로그인·API Basic 인증 끔). observability.yaml의 `GF_AUTH_GITHUB_ROLE_ATTRIBUTE_PATH`에 있는 GitHub 아이디만 첫 로그인 때 자동 가입되고, 목록에 없는 아이디는 거절됩니다(`ROLE_ATTRIBUTE_STRICT`). 역할(GrafanaAdmin·Editor)은 로그인할 때마다 목록대로 맞춰지므로 화면에서 바꾸지 않습니다.
   - GitHub 앱(Callback `https://grafana.dameokja.com/login/github`)의 값은 SSM `/dameokja-v3-observability/grafana-github-client-id`(String)·`grafana-github-client-secret`(SecureString)에 넣고 observability를 apply합니다. 값이 없으면 Association이 실패합니다. GitHub App이면 Account permissions → Email addresses: Read-only를 주고 Make public(Any account)으로 둡니다. 안 그러면 이메일 403, 다른 사용자는 GitHub 404가 납니다.
   - 사용자 추가·삭제: 위 목록의 GitHub 아이디를 고쳐 PR → observability apply. 목록에서 뺀 사람은 다음 로그인부터 거절되며, 이미 있는 계정은 Administration → Users에서 지웁니다.
   - 비밀번호 로그인이 꺼져 있어 첫 관리자는 이 변경 전에 SSM 포트 포워딩으로 만들어 둡니다. 잠겼을 때는 SSM으로 EC2에 들어가 `docker exec $(docker ps -qf name=grafana) grafana cli admin reset-admin-password <새 비밀번호>` 뒤 임시로 로그인 폼을 켭니다.

```bash
aws ssm start-session --target <ObservabilityInstanceId> --document-name AWS-StartPortForwardingSession --parameters "portNumber=3000,localPortNumber=3000" --profile dameokja-aws-cli --region ap-northeast-2
```

4. 대시보드 "v3 dev 개요"(uid dameokja-v3-dev)는 Association이 파일로 넣습니다. Node Ready·CPU·메모리, Pending Pod, Pod 재시작, BE 요청률·5xx·응답 시간(평균·최대), dev 로그를 봅니다. 화면에서 고칠 수 없으니 `observability.yaml`을 고쳐 다시 apply합니다. BE가 histogram bucket을 내보내지 않아 p95는 없습니다.

스택을 지워도 데이터 EBS는 남습니다. 다시 쓰지 않으면 Output DataVolumeId의 볼륨을 직접 지웁니다.

## 검증과 한계

cfn-lint만 확인했습니다. GitHub 실제 실행·EKS 생성·Node 등록·DNS·Pod ENI 기능 검증은 아직 수행하지 않았습니다. CloudFormation 성공이 Node Ready를 보장하지 않으므로 관리자 경로에서 실제 상태를 확인합니다.

단일 시스템 Node 장애 시 DNS·후속 관리 도구 중단을 수용하며 복구·교체는 수동입니다. Node 루트 디스크는 종료 시 삭제하므로 업무 데이터·키의 유일한 사본을 보관하지 않습니다. add-on의 PreserveOnDelete=true는 스택 삭제 후 add-on을 남기므로 재생성 시 충돌과 잔여 자원을 확인합니다.
