# v3 EKS 기반과 수동 배포

기존 prod VPC·Private Subnet·NAT를 재사용하는 EKS 기반입니다. 이 변경에는 클러스터·IAM·SG·시스템 EC2·CNI·kube-proxy·CoreDNS만 포함합니다. 관리 Controller·앱 Node 공급·DB·Redis·Kafka·앱 배포는 후속 변경입니다.

PR과 main merge는 검증만 실행합니다. AWS 자원은 main의 수동 Workflow에서 plan으로 변경 내용을 확인한 뒤, 해당 ARN을 입력한 apply를 별도로 실행할 때 생성됩니다. 이 코드의 작성·검증 과정에서는 AWS 자원을 생성하거나 기존 환경을 변경하지 않았습니다.

## 구성과 적용 순서

| 순서 | 스택 | 템플릿 | 확인할 상태 |
|---|---|---|---|
| 1 | dameokja-v3-eks | cloudformation/eks.yaml | 클러스터 ACTIVE·Access Entry·OIDC |
| 2 | dameokja-v3-network-addons | cloudformation/network-addons.yaml | CNI·kube-proxy·IRSA |
| 3 | dameokja-v3-system-node | cloudformation/system-node.yaml | 시스템 Node 1대 Ready |
| 4 | dameokja-v3-dns-addon | cloudformation/dns-addon.yaml | CoreDNS 1 Replica Ready·DNS |

EKS는 서울 1.35, 시스템 Node는 self-managed m7i.large·AL2023 1대이며 ASG를 생성하지 않습니다. AL2023 nodeadm MIME UserData, IMDSv2, 암호화 gp3를 사용합니다. 시스템 라벨은 dameokja.com/node-role=system, taint는 dameokja.com/system=true:NoSchedule입니다. CoreDNS는 해당 라벨·toleration으로 배치합니다.

기본 self-managed add-on 생성을 끄고 고정 버전 managed add-on을 별도 스택으로 관리합니다. Node가 없는 상태에서 CoreDNS의 가용성을 기다리는 문제를 피하기 위해 CoreDNS를 마지막에 설치합니다. CNI·kube-proxy의 Node 없는 상태와 실제 add-on 생성 동작은 실환경에서 확인해야 합니다.

클러스터 인증은 API Access Entry를 사용합니다. 생성자의 암묵적 관리자 권한은 끄고 지정한 관리자 principal과 EC2_LINUX Node 역할을 등록합니다. CNI는 전용 IRSA를 사용하고 Node 역할에 CNI 정책을 중복 부여하지 않습니다. 앱 Node 역할은 후속 Karpenter용으로 미리 준비하지만 이번 변경으로 앱 EC2를 공급하지는 않습니다.

## 입력과 접근 경로

params/*.json은 Workflow가 그대로 쓰는 실제 입력입니다. 여기에는 조회한 실제 계정·재사용 VPC·Subnet과 고정 AMI를 넣었습니다. 실행 전 대상과 소유권을 다시 대조합니다. REPLACE_FROM_EKS_STACK_OUTPUT은 다음 스택 실행 시 EKS Outputs로 자동 치환합니다. kubeconfig는 Git 제외 경로 private/에 보관합니다.

API endpoint는 기본 private입니다. 기존 prod Worker를 관리 호스트로 임의 사용하지 않고, 3단계 이후 시스템 Node를 거치는 SSM 포트 포워딩으로 접속합니다. Access Entry에 등록한 관리자 principal(dameokja-aws-cli)로 실행합니다.

```bash
ENDPOINT=$(aws eks describe-cluster --name dameokja-v3 --query cluster.endpoint --output text | sed 's#https://##')
NODE=$(aws cloudformation describe-stacks --stack-name dameokja-v3-system-node --query "Stacks[0].Outputs[?OutputKey=='SystemNodeInstanceId'].OutputValue" --output text)
aws ssm start-session --target "$NODE" --document-name AWS-StartPortForwardingSessionToRemoteHost --parameters "host=$ENDPOINT,portNumber=443,localPortNumber=8443"
# 다른 터미널에서
aws eks update-kubeconfig --name dameokja-v3 --kubeconfig infra/v3/private/kubeconfig
kubectl --kubeconfig infra/v3/private/kubeconfig config set-cluster "$(aws eks describe-cluster --name dameokja-v3 --query cluster.arn --output text)" --server https://127.0.0.1:8443 --tls-server-name "$ENDPOINT"
```

시스템 Node 생성 전(1~2단계)에는 kubectl 접속이 필요하지 않습니다.

public endpoint가 필요하면 EnablePublicEndpoint=true와 실제 관리자 공인 IPv4 /32를 명시합니다. 기본 문서용 192.0.2.1/32는 public 모드에서 거부합니다. 전체 인터넷 CIDR은 허용하지 않습니다.

Service CIDR 192.168.0.0/16이 VPC·VPN·관리자 경로와 겹치지 않는지 확인합니다. AMI는 versions.yaml의 확인 시점 ID로 고정하며 recommended를 실행 때마다 추종하지 않습니다. API endpoint·CA·OIDC·역할·SG는 다음 스택에 Outputs로 전달합니다.

## GitHub 설정과 실행

Repository Variables에 다음 값을 확인합니다. 이번 코드 추가가 변수 등록을 수행하지는 않습니다.

| 변수 | 값 |
|---|---|
| V3_INFRA_ENABLED | 수동 AWS 작업을 활성화할 때 true |
| AWS_CFN_DEPLOY_ROLE_ARN | arn:aws:iam::906462447513:role/dameokja-github-cfn-deploy |
| AWS_CFN_EXEC_ROLE_ARN | arn:aws:iam::906462447513:role/dameokja-cfn-execution |

기존 배포 역할의 main OIDC 신뢰와 실행 역할 PassRole을 재사용합니다. 확인 당시 실행 역할에는 AdministratorAccess가 연결되어 있었습니다. 이 PR에서 기존 역할·신뢰 정책을 변경하거나 새 GitHub Environment를 추가하지 않습니다. Environment를 추가하면 OIDC sub를 다시 검토해야 합니다.

Actions의 v3 dev 기반 CloudFormation을 main에서 실행합니다. 각 스택의 plan 결과 Summary에서 자원 변경과 Change Set ARN을 확인하고, 동일 스택의 apply에 ARN을 넣습니다. 다음 스택은 앞 단계 완료 후 실행합니다. 삭제·확정 또는 조건부 교체, 다른 계정·스택·main 커밋의 변경 계획은 차단합니다. main이 바뀌면 plan을 다시 만듭니다.

plan은 자원을 적용하지 않지만 신규 스택의 REVIEW_IN_PROGRESS 기록은 만들 수 있습니다. GitHub reviewer 승인이 자동 구성된 것은 아니며 사용자가 변경 내용을 검토한 뒤 apply를 수동 실행하는 절차입니다. 첫 적용과 이후 갱신 모두 기존 dameokja-dev·stage·prod 스택에 덮어쓰지 않습니다.

## 검증과 한계

```powershell
python -m pip install cfn-lint==1.58.0
cfn-lint --regions ap-northeast-2 -- infra/v3/cloudformation/*.yaml
python -m unittest discover -s infra/v3/tests -v
```

로컬 lint·배포 제어 테스트와 AWS ValidateTemplate은 수행했습니다. GitHub 실제 실행·EKS 생성·Node 등록·DNS·Pod ENI 기능 검증은 아직 수행하지 않았습니다. CloudFormation 성공이 Node Ready를 보장하지 않으므로 관리자 경로에서 실제 상태를 확인합니다. hosted runner가 private Kubernetes API에 직접 접속하는 흐름은 없습니다.

단일 시스템 Node 장애 시 DNS·후속 관리 도구 중단을 수용하며 복구·교체는 수동입니다. Node 루트 디스크는 종료 시 삭제하므로 업무 데이터·키의 유일한 사본을 보관하지 않습니다. add-on의 PreserveOnDelete=true는 스택 삭제 후 add-on을 남기므로 재생성 시 충돌과 잔여 자원을 확인합니다.
