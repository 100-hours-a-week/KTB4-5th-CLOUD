# v3 EKS 기반과 수동 배포

기존 prod VPC·Private Subnet·NAT를 재사용하는 EKS 기반입니다. 클러스터·IAM·SG·시스템 EC2·CNI·kube-proxy·CoreDNS와 Controller IAM·EBS CSI·Metrics Server를 포함합니다. Helm으로 설치하는 관리 도구·앱 Node 공급·DB·Redis·Kafka·앱 배포는 후속 변경입니다.

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

public endpoint가 필요하면 EnablePublicEndpoint=true와 실제 관리자 공인 IPv4 /32를 명시합니다. 기본 문서용 192.0.2.1/32는 public 모드에서 거부합니다. 전체 인터넷 CIDR은 허용하지 않습니다.

Service CIDR 192.168.0.0/16이 VPC·VPN·관리자 경로와 겹치지 않는지 확인합니다. AMI는 versions.yaml의 확인 시점 ID로 고정하며 recommended를 실행 때마다 추종하지 않습니다.

## GitHub 설정과 실행

Repository Variables의 `AWS_CFN_DEPLOY_ROLE_ARN`(dameokja-github-cfn-deploy), `AWS_CFN_EXEC_ROLE_ARN`(dameokja-cfn-execution)을 v2와 같이 사용합니다. 기존 배포 역할의 main OIDC 신뢰를 재사용하며 GitHub Environment 승인은 두지 않습니다.

Actions의 "v3 EKS 기반 CloudFormation"을 main에서 실행합니다. 스택을 고르고 plan으로 실행해 Summary의 변경 내용(특히 Replacement)을 확인한 뒤, 같은 스택을 apply로 실행합니다. apply는 새 change set을 만들지 않고 가장 최근 plan의 change set을 그대로 실행합니다. 다음 스택은 앞 스택이 끝난 뒤 실행합니다. Export를 다른 스택이 쓰고 있으면 EKS 스택의 해당 Output을 바꾸거나 지울 수 없습니다.

## 검증과 한계

cfn-lint만 확인했습니다. GitHub 실제 실행·EKS 생성·Node 등록·DNS·Pod ENI 기능 검증은 아직 수행하지 않았습니다. CloudFormation 성공이 Node Ready를 보장하지 않으므로 관리자 경로에서 실제 상태를 확인합니다.

단일 시스템 Node 장애 시 DNS·후속 관리 도구 중단을 수용하며 복구·교체는 수동입니다. Node 루트 디스크는 종료 시 삭제하므로 업무 데이터·키의 유일한 사본을 보관하지 않습니다. add-on의 PreserveOnDelete=true는 스택 삭제 후 add-on을 남기므로 재생성 시 충돌과 잔여 자원을 확인합니다.
