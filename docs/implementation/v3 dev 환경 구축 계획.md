# v3 dev 환경 구축 계획

> 작성일: 2026-11-02, Asia/Seoul
>
> 현재 단계는 계획 작성 후 EKS 기반 템플릿 작성·정적 검증까지 진행했습니다. 상세 상태는 [구축 기록](v3%20dev%20환경%20구축%20기록.md)을 참조합니다. AWS에서는 조회·템플릿 검증 API만 실행했으며 자원 생성, 기존 환경 변경, 커밋, push, PR, Wiki 변경은 수행하지 않았습니다.

## 1. 범위와 실행 원칙

최신 로컬 [인프라 설계](../design/v3%20인프라%20설계.md), [의사결정 근거](../design/v3%20인프라%20설계%20의사결정%20근거.md), [고도화 계획](../design/v3%20인프라%20고도화%20계획.md), [설계 배경](../design/v3%20인프라%20설계%20전%20배경.md)을 기준으로 dev를 구축합니다. 이 문서는 설계 설명 대신 실행 순서와 완료 기준을 관리합니다. 기존 implementation 디렉터리에는 비용 산정 문서는 있으나 같은 목적의 dev 구축 계획은 없었습니다.

- 서울 EKS 1개와 dev, stage, prod, tool Namespace를 준비하되 실제 업무 워크로드는 dev에만 배포합니다. stage, prod의 DB, Kafka, 앱 전환은 제외합니다.
- 기존 prod VPC, Subnet, NAT를 참조하고 신규 AWS 자원은 별도 CloudFormation 스택으로 관리합니다. 기존 `dameokja-dev`, `dameokja-prod`, `dameokja-stage` 스택에 v3 템플릿을 덮어쓰지 않습니다.
- 시스템 Node는 AL2023 기반 self-managed `m7i.large` EC2 1대이며 ASG를 생성하지 않습니다. 앱 Node는 직접 운영 Karpenter가 공급합니다.
- FE·BE 저장소는 Dockerfile, CI/CD 등 인프라 파일만 변경합니다. Kafka 처리, Probe 엔드포인트, 메시지 계약 등 기능 코드가 필요하면 해당 팀에 알리고 인프라 준비와 연동 완료를 구분합니다.
- 현재 여러 저장소에 미커밋 변경이 있습니다. 기존 파일을 초기화하거나 정리하지 않고 작업 직전 diff를 다시 확인합니다. 코드·설정의 추가 주석은 한국어로 작성합니다.
- 아래 배포·쓰기·장애 시험 명령은 다음 구현 단계의 절차입니다. 이번 계획 작성에서 실행한 명령과 혼동하지 않습니다.

## 2. 확인한 현재 상태와 재사용 대상

AWS CLI의 기본 프로필은 자격 증명이 없었습니다. 등록된 `dameokja-aws-cli` 프로필은 네트워크 접근 허용 후 STS와 조회 API에 성공했습니다. 아래는 2026-11-02 조회 시점의 상태이며 구현 시작 시 다시 조회합니다. EC2 실행 상태가 애플리케이션 정상 동작이나 DB 엔진 실행을 증명하지는 않습니다.

| 대상 | 확인 결과 | 구축 시 처리 |
|---|---|---|
| AWS 계정·리전 | 계정 `906462447513`, 서울 | 실행 전 STS로 계정을 다시 대조합니다. |
| EKS | 서울 `list-clusters` 결과 빈 목록 | 신규 클러스터 이름은 `dameokja-v3`를 초기값으로 사용합니다. |
| prod VPC | `vpc-04fc5393afa0ce9fd`, `10.20.0.0/16`, 기존 prod 스택 소유 | 소유권을 옮기지 않고 파라미터로 참조합니다. |
| Private Subnet | `subnet-0778204ce3b43756e` / 2b / `10.20.32.0/20`, `subnet-0f86136fb72318e11` / 2c / `10.20.48.0/20` | EKS와 Internal ALB는 두 AZ를 사용하고 Worker와 PVC는 우선 2b에 배치합니다. 조회 시 가용 IPv4는 각각 4,080개, 4,091개였습니다. |
| 외부 송신 | 두 Private Subnet의 기본 경로가 `nat-0d2622e4d400a2265`를 참조하며 active | NAT 상태, EIP, 처리량과 연결 오류를 추가 확인하고 기존 경로를 재사용합니다. |
| Public Subnet·IGW | 2b `subnet-04ba6eac801038452`, 2c `subnet-0c5b797aea024a561`, 기본 경로 `igw-064b3baead4065189` | VPC Origin의 IGW 선행 조건을 충족하는지 연결 상태까지 확인합니다. |
| 기존 dev | `dameokja-dev` 스택 UPDATE_COMPLETE, `i-035563477d331b380`와 AI `i-0572ef1adecaf0855` running | 기존 서비스는 유지합니다. 로컬 `infra/v2/dev.yaml`만으로 현재 스택 전체를 재현할 수 있다고 가정하지 않습니다. |
| 기존 dev DB 후보 | 기본 VPC `vpc-0dc397a47a708f727`의 `i-0111bde7fc68be69c`, `172.31.4.192`, 이름 `dameokja-v2-dev-DB`, running | MySQL·PostgreSQL 실제 실행, 데이터·계정, SG, 저장 장치와 소유자를 먼저 확인합니다. EKS VPC와 다릅니다. |
| 기존 prod·stage | prod DB·Redis·앱 EC2 running, stage DB·Redis EC2 stopped | dev 검증을 위해 기동·변경·삭제하지 않습니다. prod DB `10.20.33.212`를 dev 연결 대상으로 사용하지 않습니다. |
| Route 53 | 공개 Zone `dameokja.com.`, ID `Z08470472LJSW357027J9` | 도메인과 Zone을 재사용합니다. |
| dev DNS | `dev.dameokja.com` A 레코드가 `43.200.71.145`, `15.164.208.228`로 각각 조회됨 | SetIdentifier, Weight, Failover, TTL, HealthCheckId를 포함해 원본 전체를 다시 확인합니다. 단순 A 레코드 하나로 간주하지 않습니다. |
| CloudFront·인증서 | 조회된 배포는 `pappus.cloud` 1개, us-east-1 인증서도 pappus용만 확인됨 | 다른 서비스 배포는 건드리지 않습니다. dameokja 공유 배포와 인증서는 신규 구성 대상으로 계획합니다. SAN 전체는 추가 조회합니다. |
| 관리용 도메인 | Argo CD용 기존 레코드는 이번 A/AAAA/CNAME 목록에서 확인되지 않음 | `argocd.dameokja.com`을 초기 후보로 두고 충돌 확인 후 별도 Alias와 인증서 SAN을 구성합니다. |

### 2.1 로컬 재사용 범위

| 기존 파일·디렉터리 | 활용 방법 |
|---|---|
| `infra/v2/bootstrap/github-oidc.yaml` | 기존 OIDC Provider와 역할 구조를 참고합니다. 역할의 실제 권한·신뢰 정책을 조회한 뒤 v3 범위로 한정하며 Provider를 중복 생성하지 않습니다. |
| `.github/workflows/infra-v2-cfn.yml` | lint와 Change Set 검토 흐름을 재사용합니다. 스택 이름과 경로가 v2에 고정되어 있으므로 별도 v3 Workflow를 만듭니다. |
| `infra/v2/dev.yaml`, `infra/v2/params/dev.json` | 기존 dev 정의 참고용입니다. 파라미터의 VPC/Subnet ID는 자리표시자이므로 실제 재사용 값으로 복사하지 않습니다. |
| FE·BE·AI의 Dockerfile, `.github/workflows/v1-cicd.yml` | 기존 빌드·검증·GHCR 발행 부분을 재사용하고 SSH 배포와 v3 GitOps 배포를 분리합니다. 로컬 Workflow는 미추적 작업물일 수 있으므로 diff를 보존합니다. |
| `infra/monitoring/`, `docs/observability/` | SLI/SLO와 기존 대시보드·알림 지표를 참조합니다. 기존 CloudWatch 수집을 PLG가 이미 있는 것으로 간주하지 않습니다. |
| `v2/stage/k6/` | 기능별 검증 항목과 fixture 구조를 참고합니다. stage 실행 보호 장치를 해제해 dev에 그대로 사용하지 않고 v3 dev 전용 실행 설정을 만듭니다. |

Helm은 현재 PATH에서 확인되지 않았습니다. AWS CLI, kubectl, cfn-lint는 확인했습니다. kubectl 설치만으로 클러스터 접근이 가능한 것은 아닙니다.

## 3. 먼저 해소할 실제 선행 조건

1. **dev DB의 VPC가 다릅니다.** 기존 dev DB의 엔진·데이터 확인 후, 재사용 가능하면 두 VPC 사이 기존 연결과 양방향 경로, DNS, SG 참조 지원 조건을 확인합니다. 이번 prod VPC의 Private Route Table 조회에는 기본 VPC행 경로가 없었습니다. 연결이 없다면 dev 전용 CIDR 경로와 Peering을 별도 Change Set으로 검토합니다. 이전·신규 DB가 필요하다면 그 작업만 분리하며 prod DB를 임시로 공유하지 않습니다. 이 문제는 EKS 기반 구축을 막지 않지만 앱의 DB 연동 완료를 막습니다.
2. **실제 애플리케이션의 Kafka 지원을 확인해야 합니다.** 현재 로컬 AI 앱과 BE resource 설정에 대한 제한된 검색에서는 Kafka 설정을 확인하지 못했습니다. 미구현을 확정한 결과는 아닙니다. 배포할 커밋·이미지의 환경변수, 의존성, Producer/Consumer, Consumer Group, offset 커밋과 복구 지원을 BE·AI 팀과 대조합니다. 환경변수 이름이나 메시지 스키마를 임의로 만들지 않습니다.
3. **공유 진입점은 신규 구성부터 검증합니다.** 기존 prod ALB를 Controller가 자동 인수한다고 가정하지 않습니다. 신규 Internal ALB를 만들고 신규 공유 CloudFront에 dev만 연결합니다. 기존 도메인 전환은 모든 검증 후 별도 단계로 수행합니다.
4. **관리 접속 경로를 확보합니다.** EKS private endpoint 접근을 위해 기존 SSM 관리 호스트나 승인된 VPN·터널 경로를 확인합니다. 없으면 제한된 관리자 CIDR의 public endpoint를 초기 접근 수단으로 구성하고 private 접근을 검증합니다. GitHub hosted runner가 private EKS API에 직접 접근하는 흐름은 만들지 않습니다.

stage/prod Kafka의 Broker 수, 배치와 고도화 정책은 dev 구축의 선행 조건으로 두지 않습니다.

## 4. 생성할 파일과 관리 경계

다음은 단계별 생성 경로이며 실제 작성 상태는 구축 기록에서 관리합니다. 경로 기준은 Cloud 저장소 루트입니다. 초기 bootstrap 의존성에 따라 CNI·kube-proxy와 CoreDNS는 각각 `network-addons.yaml`, `dns-addon.yaml`로 분리했습니다.

| 경로 | 내용 |
|---|---|
| `infra/v3/README.md`, `infra/v3/versions.yaml` | 스택 적용 순서, 검증한 버전·Chart·이미지 digest·CRD 출처를 기록합니다. |
| `infra/v3/cloudformation/eks.yaml` | 클러스터, Access Entry, IAM, 클러스터·Node·Pod SG를 관리합니다. |
| `infra/v3/cloudformation/system-node.yaml` | AL2023 EKS 최적화 AMI, nodeadm, InstanceProfile, EC2 1대, 라벨·taint를 관리합니다. |
| `infra/v3/cloudformation/controller-iam.yaml` | CNI, EBS CSI, LBC, Karpenter의 전용 역할과 필요한 Queue·이벤트 연동을 관리합니다. |
| `infra/v3/cloudformation/dev-data-access.yaml` | dev DB SG 추가 규칙과 필요시 Peering·전용 경로를 관리합니다. 기존 SG 전체를 재정의하지 않습니다. |
| `infra/v3/cloudformation/observability.yaml`, `infra/v3/observability/` | 외부 관측 EC2·볼륨·SG와 PLG 설정을 관리합니다. |
| `infra/v3/cloudformation/acm-us-east-1.yaml`, `edge.yaml` | 인증서와 CloudFront·VPC Origin·OAC·DNS를 분리합니다. ALB ARN은 Controller 생성 후 입력합니다. |
| `infra/v3/params/*.example.json` | 비밀값 없는 스택별 입력 예시를 둡니다. 실제 입력의 Git 보관 여부를 분류합니다. |
| `v3/bootstrap/`, `v3/platform/crds/`, `v3/platform/values/` | 최초 설치 절차, 고정 버전 CRD, 관리 Chart의 values를 둡니다. |
| `v3/platform/manifests/` | Namespace·RBAC·StorageClass·SecurityGroupPolicy·NetworkPolicy·Karpenter·Gateway 공통 CR을 둡니다. |
| `v3/charts/application/`, `v3/charts/dev-data/` | Blue/Green 앱 공통 Chart, Redis와 Strimzi CR·토픽 Chart를 둡니다. |
| `v3/environments/dev/`, `v3/argocd/` | 앱별 dev values, strict SealedSecret, AppProject·Application과 동기화 순서를 둡니다. |
| `v3/tests/`, `docs/implementation/v3 dev 환경 구축 기록.md` | dev 검증 스크립트, 비밀값 없는 실행 결과와 단계별 진행 상태를 기록합니다. |
| `.github/workflows/infra-v3-cfn.yml` | v3 경로만 검증하며 초기에는 자동 적용을 끕니다. |

CloudFormation은 AWS 기반 자원을, Karpenter는 앱 EC2를, LBC는 ALB·Target Group을, CSI는 PVC의 EBS를, Strimzi는 Kafka 하위 리소스를 관리합니다. 같은 자원을 두 도구가 동시에 소유하지 않습니다. 기존 스택 소유 VPC와 SG에 대한 변경은 소유 스택과 별도 추가 규칙의 경계를 확인합니다.

## 5. 단계별 구축 절차

### 0단계. 인벤토리 확정과 버전 고정

**선행 조건:** 대상 계정의 읽기 권한과 위 로컬 문서입니다.

1. Cloud·FE·BE·AI의 branch, HEAD, status를 기록하고 기존 변경을 보존합니다. AWS 스택의 실제 template·parameters·resources, ECS 서비스와 desired/running 수를 조회해 로컬 정의와 비교합니다.
2. VPC·Subnet·Route Table·NAT·IGW·NACL·SG·ENI·EBS와 기존 DB 엔진을 확인합니다. Subnet discovery tag 추가가 기존 Controller 선택에 영향을 주지 않게 신규 클러스터 이름과 명시적 Subnet ID를 사용합니다.
3. AWS 조회에서 EKS 1.34~1.37은 STANDARD_SUPPORT로 확인했습니다. 초기 후보는 **EKS 1.35**로 두고, AL2023 AMI·CNI·CSI·CoreDNS·kube-proxy·Karpenter·LBC·Metrics Server·Strimzi의 교집합을 확인하여 `versions.yaml`에 고정합니다. 부합하지 않는 항목이 있으면 같은 표준 지원 범위의 버전으로 조정합니다.
4. Strimzi 공식 호환표는 **Operator 1.3.0 / Kafka 4.2.2 / 시험 Kubernetes 1.32~1.37** 조합을 명시합니다. 이를 후보로 삼고 release artifact의 이미지 매핑·CR API·digest까지 확인합니다. Kafka 4.2.2라는 이름만 붙인 임의 이미지는 사용하지 않습니다.
5. LBC의 ALB Gateway API 지원 release와 해당 release가 요구하는 Standard·전용 CRD를 함께 고정합니다. 인터넷의 `main` 매니페스트를 버전 고정 없이 적용하지 않습니다. CNI의 NetworkPolicy와 Pod SG 병행 조건, m7i.large의 trunk/branch ENI 수용량도 확인합니다.
6. GHCR private package 읽기 권한과 Argo Git 읽기 자격 증명, CI의 배포 저장소 쓰기 자격 증명, DNS 변경 권한을 준비합니다. 기존 GITHUB_TOKEN이 다른 저장소 쓰기를 자동 허용한다고 가정하지 않습니다.

**검증 명령 예시:** 아래 조회는 PowerShell에서 실행하며 민감한 원본 출력은 Git에 보관하지 않습니다.

```powershell
$env:AWS_PROFILE = 'dameokja-aws-cli'
$env:AWS_REGION = 'ap-northeast-2'
aws sts get-caller-identity
aws cloudformation describe-stacks
aws cloudformation get-template --stack-name dameokja-dev
aws cloudformation list-stack-resources --stack-name dameokja-prod
aws eks list-clusters
aws eks describe-cluster-versions
aws ec2 describe-subnets --filters Name=vpc-id,Values=vpc-04fc5393afa0ce9fd
aws ec2 describe-route-tables --filters Name=vpc-id,Values=vpc-04fc5393afa0ce9fd
aws route53 list-resource-record-sets --hosted-zone-id Z08470472LJSW357027J9
aws acm list-certificates --region us-east-1
aws cloudfront list-distributions
```

**완료 기준:** 비밀값을 제외한 인벤토리, 버전표, 실제 dev DB 대상, 관리자 접속 경로와 기존 DNS 복원 자료가 준비됩니다. AWS 자원 상태를 추정으로 채우지 않습니다.

### 1단계. EKS·IAM·시스템 Node 기반

**선행 조건:** 네트워크 인벤토리와 버전 교집합입니다. DB 이전 완료는 필요하지 않습니다.

1. 기존 VPC와 두 Private Subnet을 입력받는 별도 스택을 작성합니다. EKS Access Entry, 클러스터 서비스 역할과 Node 역할, 시스템·앱·Pod SG를 구성합니다. Secret 저장에는 EKS 기본 암호화를 사용합니다.
2. self-managed 시스템 Node의 EKS 최적화 AL2023 AMI ID를 고정하고 nodeadm에 endpoint·CA·service CIDR 등 필요한 값을 공급합니다. ECS AMI나 AL2 bootstrap을 재사용하지 않습니다.
3. `m7i.large` EC2 1대에 시스템 라벨과 NoSchedule taint를 적용합니다. 최초 CoreDNS, CNI, CSI, 관리 Pod의 배치와 toleration을 미리 준비해 bootstrap 교착을 피합니다. SSM·DNS·NAT를 통한 이미지와 AWS API 접근을 확인합니다.
4. 스택별 cfn-lint 후 Change Set의 추가·수정·교체·삭제를 검토합니다. 기존 VPC·NAT·DB·서비스의 교체나 삭제가 포함되면 진행하지 않습니다. 실제 생성은 후속 구축 작업에서 실행합니다.

**검증:** `cfn-lint --regions ap-northeast-2 -- infra/v3/cloudformation/eks.yaml infra/v3/cloudformation/system-node.yaml`, `aws eks describe-cluster --name dameokja-v3`, `kubectl get nodes -o wide`, `kubectl describe node <시스템-Node>`를 사용합니다. kubeconfig는 별도 파일에 만들고 명령마다 대상 context를 확인합니다.

**완료 기준:** 클러스터 ACTIVE, 시스템 Node Ready 1대, 시스템 라벨·taint 일치, 관리자 접속과 CoreDNS가 정상입니다. ASG는 생성되지 않습니다.

### 2단계. CNI·CSI·Pod SG·Karpenter·관리 도구

**선행 조건:** 시스템 Node Ready와 Controller IAM입니다.

1. VPC CNI add-on의 Pod ENI와 NetworkPolicy를 활성화하고 Pod SG enforcement는 standard로 설정합니다. Node 역할에 필요한 VPC Resource Controller 연동을 준비하고 BE·AI·Kafka 등의 SecurityGroupPolicy를 설치합니다. Probe·DNS와 Node SNAT의 실제 적용 SG를 검증합니다. 선택한 CNI 버전의 NetworkPolicy 적용 대상에 Redis StatefulSet과 StrimziPodSet이 포함되는지도 확인합니다. 지원되지 않는 대상은 NetworkPolicy가 적용됐다고 보고하지 않고 Pod SG의 차단 효과를 별도 검증하며 설계와의 차이를 기록합니다.
2. EBS CSI add-on과 전용 IAM을 설치합니다. `redis-gp3`, `kafka-gp3`는 gp3, WaitForFirstConsumer, 확장 허용, Retain으로 구성합니다. Node의 AZ와 EBS의 AZ가 일치하도록 합니다.
3. Karpenter CRD → Controller → Ready 확인 → EC2NodeClass → NodePool 순서로 적용합니다. Controller 1 Replica를 시스템 Node에 고정합니다. 앱 Node는 m7i.large·AL2023·2b·초기 On-Demand로 시작하며 `WhenEmpty`, `5m`, 자발적 교체 예산 1대를 적용합니다. 설계의 물리 한도 CPU 26, memory 104Gi는 상시 대수가 아니며 dev 평시 2대 공급을 예상합니다. 시험 중 불필요한 대량 확장은 요청량을 제한하여 방지합니다.
4. Namespace·RBAC와 기본 관리 도구를 설치합니다. Chart별 기본 Replica·requests를 확인하고 Karpenter, Argo CD, Sealed Secrets Controller, Strimzi Cluster Operator를 tool·시스템 Node에 배치합니다. LBC, Metrics Server와 필요한 exporter도 시스템 예산에 합산합니다. Strimzi는 dev를 감시하도록 제한하고 필요한 cluster-scoped 권한은 별도 확인합니다.
5. CRD는 Controller보다 먼저 Established를 확인합니다. Gateway API·LBC CRD는 LBC보다 먼저, Strimzi CRD는 Operator보다 먼저, SealedSecret CRD는 SealedSecret보다 먼저 적용합니다. CRD 설치와 하위 CR 배포는 별도 Argo Application으로 분리하고 건강 상태를 확인합니다. 서로 다른 Application의 sync wave만으로 준비 완료가 보장된다고 가정하지 않습니다.
6. Argo CD와 Sealed Secrets의 최초 설치는 bootstrap으로 수행합니다. 이후 같은 release·리소스를 GitOps 관리로 인계하고 수동 Helm 갱신과 이중 운영하지 않습니다. 초기 Argo 접속은 port-forward로 검증하며 Git에 아직 올라가지 않은 로컬 디렉터리는 동기화할 수 없음을 기록합니다. 이번에는 Git 발행을 수행하지 않습니다.
7. Sealed Secrets 공개 인증서로 이름·Namespace 결합 strict 암호화를 수행합니다. Controller 개인키 백업은 암호화된 별도 보관소에 저장하고 Git에 넣지 않습니다. GHCR pull Secret, 앱 Secret, Argo Git 인증을 먼저 공급합니다. Secret create는 resourceNames로 제한되지 않으므로 생성 검증과 Git 리뷰를 적용하고 Deployment 수정 권한의 Secret 참조 한계도 명시합니다.

**검증:** `kubectl get pods -n kube-system -o wide`, `kubectl get pods -n tool -o wide`, `kubectl get crd`, `kubectl get nodepools,ec2nodeclasses,nodeclaims`, `kubectl get securitygrouppolicies -A`, `kubectl get nodes -o custom-columns=NAME:.metadata.name,CPU:.status.allocatable.cpu,MEMORY:.status.allocatable.memory`를 사용합니다. 신규 dev 시험 Pod를 통해 DNS·이미지 pull·PVC 공급을 확인하고 필요 Node가 Karpenter로 공급되는지 확인합니다.

**완료 기준:** 관리 Controller Ready, dev 앱 Node 공급 성공, CNI와 Pod SG 병행 성공, CSI 볼륨 공급 성공, 시스템 Node와 앱 Node의 requests가 실제 Allocatable 이내입니다. private Node에서는 GHCR뿐 아니라 quay.io, registry.k8s.io, ECR/S3 등 선택한 이미지의 실제 registry·blob endpoint에 NAT로 접근할 수 있어야 합니다.

### 3단계. dev Redis·Kafka·외부 DB 연결

**선행 조건:** CSI, dev 앱 NodePool, Sealed Secrets와 Strimzi Operator Ready입니다.

1. Redis StatefulSet 1개와 `redis-headless`, `redis` Service를 배포합니다. CPU 250m/500m, memory 512Mi/1Gi, maxmemory 512Mi, AOF everysec, `/data` PVC 10Gi를 적용합니다. PVC whenDeleted·whenScaled는 Retain이며 VPA는 설치하지 않습니다.
2. `KafkaNodePool` 1 Replica의 역할을 broker·controller로 설정하고 Kafka 4.2.2 CR을 적용합니다. CPU request 500m·limit 2, memory request/limit 2Gi, gp3 10Gi, deleteClaim false를 설정합니다. Strimzi가 생성하는 Entity/Topic Operator Pod의 위치·requests도 확인해 시스템 Node 예산에 포함합니다.
3. Kafka 내부 listener 9092는 PLAINTEXT로 두고 외부 listener는 생성하지 않습니다. bootstrap은 초기 이름 `dev-kafka` 기준 `dev-kafka-kafka-bootstrap.dev.svc.cluster.local:9092`이며 실제 생성 Service로 확정합니다. auto.create.topics.enable false, offsets.topic.replication.factor 1, min.insync.replicas 1을 적용하고 트랜잭션 사용 여부에 맞는 내부 토픽 복제 조건도 확인합니다.
4. Kafka Ready 후 아래 KafkaTopic 7개를 생성합니다. 모두 partitions 1, replicas 1, cleanup.policy delete, retention.ms 2592000000입니다. CR 이름 제한이 있으면 Kubernetes metadata.name과 Kafka의 실제 topicName을 구분합니다.

| Kafka topicName |
|---|
| `dev.dameokja.refrigerator.events.v1` |
| `dev.dameokja.push.dispatch.v1` |
| `dev.dameokja.analysis.requested.v1` |
| `dev.dameokja.analysis.completed.v1` |
| `dev.dameokja.analysis.feedback.v1` |
| `dev.dameokja.recommendation.requested.v1` |
| `dev.dameokja.recommendation.completed.v1` |

5. NetworkPolicy와 Pod SG는 dev BE·AI의 9092만 허용합니다. bootstrap 뒤 advertised broker 주소로도 접속되는지 확인합니다. Strimzi 조정·Topic Operator·broker/controller 내부 통신은 설치 버전이 요구하는 라벨·포트에 맞게 허용하며 생성된 정책과 사용자 정책의 합집합으로 접근이 확대되지 않는지 확인합니다.
6. 기존 dev DB를 재사용할 수 있으면 별도 네트워크 연결을 구성하고 BE Pod SG→MySQL 3306, AI Pod SG→PostgreSQL 5432만 허용합니다. 엔진별 계정·데이터 경로·연결 풀을 구분합니다. 엔진 추가가 필요하면 기존 MySQL의 메모리·디스크 여유와 백업을 확인한 뒤 dev 전용 변경으로 분리합니다.

**검증:** `kubectl get statefulset,svc,pvc -n dev`, `kubectl get kafka,kafkanodepool,kafkatopic -n dev`, `kubectl wait kafka/dev-kafka -n dev --for=condition=Ready --timeout=600s`, `kubectl get pv`를 실행합니다. 허용된 dev BE·AI 시험 Pod에서 Kafka CLI의 topics describe·console producer/consumer, Redis PING·CONFIG GET appendonly·appendfsync, DB의 `SELECT 1`을 수행합니다. Kafka CLI는 고정 버전 이미지의 실제 경로와 bootstrap을 사용하며 업무 메시지는 팀과 합의한 시험 데이터만 발행합니다.

**완료 기준:** 7개 토픽과 정책 일치, Redis AOF 설정 일치, PVC Bound·Retain, 올바른 환경의 DB에만 연결됩니다. Kafka 10Gi가 30일치 저장을 보장한다는 판정은 하지 않습니다. 디스크 사용량·증가율을 기록합니다.

### 4단계. FE·BE·AI API의 내부 배포와 기능 연동

**선행 조건:** 데이터 계층과 실제 이미지·Secret·환경변수 계약입니다.

1. FE 3000, BE 8080, AI API 8000의 개별 Service와 blue·green Deployment를 공통 Chart로 작성합니다. 초기에는 blue만 각 1 Replica로 가동하고 green은 0으로 둡니다. activeColor와 이미지 digest를 values로 관리합니다.
2. FE 250m/512Mi, BE 500m/1Gi, AI 250m/512Mi requests와 설계 limits를 적용합니다. dev FE·BE는 1 Replica를 유지하며 AI HPA도 초기에는 사용하지 않습니다. FE의 NEXT_PUBLIC_API_BASE_URL은 브라우저가 접근할 주소로 빌드하며 ClusterIP 주소를 번들에 넣지 않습니다.
3. FE의 설계 Probe와 실제 이미지 경로를 대조합니다. BE Actuator readiness/liveness는 환경 설정으로 활성화 가능한지 확인하고, AI의 `/health/live`, 인증된 `/health/ready`는 해당 배포 이미지에서 확인합니다. 없는 엔드포인트는 인프라 코드에서 가정하지 않고 팀에 요청합니다.
4. DNS·BE→AI·BE→Redis·각 DB·Kafka·RunPod HTTPS와 필요한 S3/외부 API 송신을 허용합니다. Namespace 기본 ingress/egress 거부를 적용하되 DNS, Probe, LBC health check, Controller webhook, 관측 예외를 설계 통신표대로 명시합니다.
5. dev 전용 fixture로 로그인·재고·알림·AI 요청/결과를 확인합니다. Producer 발행 성공, 결과 저장 후 offset 커밋, Pod 재시작 시 미완료 작업 재처리와 중복 결과 반영 방지는 BE·AI 팀의 연동 검증으로 기록합니다. 기능 미구현이면 플랫폼 배포 완료와 서비스 연동 대기를 분리합니다.

**검증:** `helm lint v3/charts/application`, `helm template <release> v3/charts/application -f <dev-values>`, `kubectl rollout status deployment/<앱>-blue -n dev`, `kubectl get endpointslices -n dev`와 VPC 내부 HTTP 요청을 사용합니다. 렌더링 결과의 Secret 평문을 출력·보관하지 않습니다.

**완료 기준:** 각 앱 Ready, Service endpoint 일치, 내부 송수신 정상, RunPod 연결 정상입니다. 지원되지 않는 Kafka 기능과 복구 동작은 담당 팀·증거·남은 항목을 남깁니다.

### 5단계. Gateway·Internal ALB·CloudFront·도메인

**선행 조건:** 내부 앱 검증과 LBC 준비입니다. ACM DNS 검증은 0단계 이후 미리 진행할 수 있습니다.

1. GatewayClass `gateway.k8s.aws/alb`, tool의 공용 HTTP Gateway와 LBC LoadBalancerConfiguration을 적용합니다. ALB scheme은 internal, 두 Private Subnet을 명시하며 초기 target은 Pod IP로 구성합니다. dev HTTPRoute의 parentRef는 tool Gateway를 가리키고 allowedRoutes는 명시적으로 라벨링한 Namespace로 제한합니다. AI는 BE 내부 연결만 사용합니다.
2. ALB Active·healthy target을 확인하고 ARN을 edge 스택 입력으로 전달합니다. CloudFormation이 Controller의 ALB를 새로 만들거나 수정하지 않게 합니다.
3. us-east-1에서 서비스와 관리 도메인의 ACM 인증서를 DNS 검증합니다. CloudFront→VPC Origin은 HTTP Only, ALB listener→Pod도 HTTP로 구성합니다. VPC Origin 생성 전 ALB가 Active여야 하므로 edge 스택을 EKS 스택과 분리합니다.
4. ALB SG는 최초 CloudFront origin-facing prefix list를 허용하고 VPC Origin 생성 후 서비스 관리 SG 참조로 좁힐 수 있도록 두 단계로 구성합니다. AWS가 만든 `CloudFront-VPCOrigins-Service-SG` 자체를 수정하지 않습니다.
5. Origin에 Host를 전달해 환경별 HTTPRoute가 일치하도록 합니다. API와 인증·세션 응답은 캐시하지 않고 쿠키·Authorization·쿼리 전달을 검증합니다. 초기 FE도 캐시를 끈 상태로 시작하며 캐시 도입 시 Host를 키에 포함합니다. S3 이미지 origin은 OAC와 해당 배포 ARN으로 제한한 Bucket Policy를 적용합니다.
6. Argo CD는 별도 관리 도메인과 로컬 계정·비밀번호·RBAC를 적용합니다. 같은 L7 진입점에서는 CLI의 gRPC-web을 검증하고 필요하면 port-forward를 유지합니다. 익명 접근을 끄고 API·관리 응답 캐시도 끕니다.
7. CloudFront 배포 완료 후 `curl --connect-to` 등으로 실제 Host·TLS를 유지한 사전 시험을 수행합니다. SSE, 업로드, 로그인 redirect·Secure Cookie, 오류 응답을 확인한 뒤 기존 dev DNS의 라우팅 정책 전체를 보존하며 전환합니다. prod와 stage DNS는 변경하지 않습니다.

**검증:** `kubectl get gatewayclass`, `kubectl get gateway -n tool`, `kubectl get httproute -n dev`, `aws elbv2 describe-target-health --target-group-arn <ARN>`, `aws cloudfront get-distribution --id <ID>`, `Resolve-DnsName dev.dameokja.com`, `curl.exe -I https://dev.dameokja.com`을 사용합니다. Gateway/Route의 Accepted·Programmed·ResolvedRefs와 실제 응답을 모두 확인합니다.

**완료 기준:** 사용자 HTTPS 인증서 정상, Host별 라우팅 정상, CloudFront를 통한 dev 접근 정상, ALB 직접 외부 접근 차단, API 캐시 없음, Argo 로그인·권한 확인과 기존 DNS 복원 절차 확보입니다.

### 6단계. CI·GitOps·HTTP Blue/Green 연결

**선행 조건:** Chart 검증과 원격 배포 저장소에 반영된 기준 리비전입니다. 로컬 작업만 허용된 현재 단계에서는 연결 준비까지만 가능합니다.

1. 표준 hosted runner `ubuntu-24.04`에서 기존 빌드·테스트·GHCR 발행을 재사용하고 m7i.large에 맞는 linux/amd64 이미지를 digest로 고정합니다. PR 검증은 배포하지 않으며 기존 v1 SSH 배포가 v3 변경으로 같이 실행되지 않게 명시적 전환 스위치를 둡니다.
2. 배포 Git은 Cloud 저장소의 v3 경로를 초기 위치로 사용합니다. CI가 자기 앱의 dev 이미지 값만 갱신하도록 GitHub App 또는 최소 권한 토큰, 동시 변경 직렬화와 재시도 정책을 구성합니다. 신규 커밋·push와 자동화 활성화는 후속 구현 범위입니다.
3. Argo AppProject의 source·destination·허용 리소스를 제한합니다. CRD/플랫폼, 데이터, Secret, 앱 순서를 분리하고 Kafka·Redis PVC에 자동 prune을 적용하지 않습니다. 최초에는 수동 sync로 검증한 후 앱 자동 sync를 활성화합니다.
4. 신버전 green 기동→Readiness와 내부 기능 검증→운영 Service selector 전환→ALB target healthy·연결 종료 대기→외부 검증→관찰 후 구버전 축소 순서로 수행합니다. 실패 시 이전 digest·activeColor를 Git에서 복원합니다.
5. HTTP selector 전환은 Kafka Consumer 전환이 아닙니다. BE·AI의 신구 Pod는 HTTP를 받기 전에도 소비할 수 있습니다. Kafka 연동 초기에는 해당 팀이 동시 소비 안전성을 검증하기 전 BE·AI의 자동 Blue/Green 승격을 활성화하지 않습니다. Consumer 전환 자동화·메시지 버전 호환·업무 롤백은 고도화로 남깁니다.

**검증:** 이미지 manifest의 amd64·digest, `argocd app diff <앱>`, `argocd app get <앱>`, `kubectl get svc <앱> -n dev -o yaml`과 이전 버전 복귀 시험을 확인합니다. Secret diff는 공개 로그에 남기지 않습니다.

**완료 기준:** 앱별 변경이 다른 앱을 재배포하지 않고 Git 리비전·이미지 digest·활성 색상을 추적할 수 있습니다. 실패한 신버전이 운영 HTTP로 승격되지 않으며 이전 정상 버전 복귀를 확인합니다.

### 7단계. 관측 연결과 최종 인수 시험

**선행 조건:** 기본 워크로드 실행입니다. 관측 EC2 준비와 Alloy 설치는 2단계 이후 병행해 앞 단계의 실패 분석에 사용합니다.

1. 기존 PLG EC2 존재를 추가 확인하고 재사용할 서버가 없으면 외부 EC2를 별도 스택으로 구성합니다. Prometheus·Loki 데이터 볼륨과 복구 절차를 마련하고 Grafana 계정·접근 경로를 제한합니다.
2. Alloy DaemonSet은 시스템 taint toleration을 포함해 Node당 1개를 실행합니다. Node 로그와 읽기 위치를 hostPath 등 지속 경로에 보관하고 Loki 송신을 허용합니다. Node 자체 손실 시 위치·미전송 로그 손실 가능성을 기록합니다.
3. 외부 Prometheus는 VPC 내부 API discovery와 최소 권한 ServiceAccount로 수집합니다. metrics-server는 HPA용이고 Prometheus 수집 대체재가 아닙니다. 필요한 kube-state-metrics·Node·앱·Kafka exporter의 배치·인증·SG·NetworkPolicy를 명시하며 불필요한 NodePort를 공개하지 않습니다.
4. Node Allocatable, Pod requests·실사용량, OOM·재시작, ENI/IP, Pending, EBS 사용량, Redis AOF, Kafka disk·lag, HTTP 오류·지연, NAT 오류와 Controller 로그를 수집합니다.

| 시험 | 수행과 합격 기준 |
|---|---|
| 배치·용량 | 평시 FE1·BE1·AI1·Redis1·Kafka1 requests CPU 1.75 vCPU·memory 4.5Gi, memory limits 7Gi를 대조합니다. 앱 Node당 계획 예산 1.5vCPU/7Gi로 앱 2대+시스템 1대를 예상하되 실제 Allocatable에서 DaemonSet을 뺀 값과 Pod ENI를 검증합니다. Node 예약분을 중복 차감하지 않습니다. |
| 관리 도구 | 시스템 m7i.large 한 대에 Argo의 모든 구성요소, Karpenter, Sealed Secrets, Strimzi·Entity Operator, LBC, 공통 Pod가 들어가는지 합산합니다. 초과하면 Replica·초기 requests를 검증하며 조정하고 여전히 불가능하면 사양 제약을 보고합니다. |
| 송수신 | 7개 업무 토픽의 발행·소비와 BE↔AI 결과를 시험합니다. 파티션·복제 1, retention과 consumer group·offset을 조회합니다. 기능 송수신은 메시지 계약을 갖춘 앱에서 검증합니다. |
| Kafka 영속성 | 시험 메시지·cluster ID·committed offset·PVC UID를 기록하고 dev Kafka Pod만 재생성합니다. 같은 PVC에서 동일 식별자·메시지·offset이 유지되는지 확인합니다. 클러스터 삭제 복구는 별도 절차로 PVC와 cluster ID 매핑을 보존합니다. |
| Redis 영속성 | TTL 없는 시험 키를 쓰고 AOF 반영을 기다린 뒤 Pod 재생성 후 값을 확인합니다. everysec의 장애 직전 유실 가능성을 인정하며 키는 시험 후 정리합니다. |
| 접근 제한 | dev BE·AI는 Kafka 허용, dev FE·라벨 없는 dev 시험 Pod·다른 Namespace는 거부합니다. 임시 stage 시험 Pod만 사용하며 실제 stage 업무 자원은 만들지 않습니다. DNS·Probe·Strimzi 조정·관측 예외는 정상이어야 합니다. |
| 외부 DB | BE→dev MySQL, AI→dev PostgreSQL 허용과 포트·서비스가 다른 접근 거부를 확인합니다. prod DB로 향하는 dev 접속은 거부되어야 합니다. |
| 작업 복구 | 처리 도중 dev BE·AI Pod를 재시작하고 미완료 작업 재처리·offset 커밋·중복 반영 방지를 팀과 검증합니다. 컨테이너 Ready만으로 성공 처리하지 않습니다. |
| 배포·관측 | 신버전 실패 시 이전 HTTP 유지, 정상 전환과 복귀를 확인합니다. Alloy 재시작 후 로그 수집 재개와 Prometheus target 상태, Grafana 조회를 확인합니다. |
| Node 장애 | 먼저 dev 전용 NodeClaim과 배치 Pod를 식별합니다. 시스템·기존 ECS·DB Node를 건드리지 않고 앱 Node 교체 및 같은 AZ EBS 재연결을 시험합니다. 작업 재처리와 복구 시간을 기록합니다. |

**검증:** `kubectl top nodes`, `kubectl top pods -A`, `kubectl get events -A --sort-by=.lastTimestamp`, `kubectl get ds -A`, `kubectl get pvc -n dev`, Kafka consumer-groups describe와 Grafana의 동일 시간대 지표·로그를 사용합니다. Metrics Server가 준비된 뒤에만 top 결과를 판정합니다.

**완료 기준:** 위 시험의 실행 리비전·대상·시간·결과·미해결 항목을 구축 기록에 남깁니다. Kafka 기능 미구현이나 작업 복구 실패가 있으면 인프라 준비 완료와 서비스 연동 미완료를 명시하고 전체 dev 완료로 표시하지 않습니다.

## 6. 기존 환경 영향과 복원 기준

| 변경 | 기존 환경 영향 | 보호·복원 방법 |
|---|---|---|
| VPC·NAT 공유 | 신규 Pod IP·ENI·NAT 부하와 비용이 늘어납니다. | 기존 Route의 목적지와 기본 경로를 교체하지 않고 변경 전후 사용량을 비교합니다. |
| dev DB 재사용 | 기존 dev 서비스와 DB 자원·스키마를 공유할 수 있습니다. | 전용 계정과 시험 fixture를 쓰며 기존 앱과 중복 Batch·Consumer 실행 여부를 확인합니다. DDL·데이터 이전은 별도 팀 작업으로 분리합니다. |
| Pod SG·Peering | 잘못된 규칙이 기존 DB 접근이나 타 환경 격리에 영향을 줄 수 있습니다. | 전용 추가 규칙과 CIDR 경로만 적용하고 기존 규칙 삭제는 피합니다. 역방향 경로·SG 변경을 별도로 검토합니다. |
| DNS 전환 | 현재 v2 dev 사용자 진입점이 바뀝니다. | 전체 record set·라우팅 정책을 저장하고 이전 레코드로 복구합니다. DNS 복귀는 이미 발생한 DB 쓰기·메시지 소비를 되돌리지 않습니다. |
| CI·GitOps 활성화 | 기존 SSH CD와 중복 배포할 수 있습니다. | v3 명시적 활성화와 기존 CD 전환 순서를 정하고 변경 전 Workflow를 보존합니다. |
| Controller·CR 삭제 | ALB나 Node가 정리되고 PVC·EBS는 남을 수 있습니다. | stateful CR과 PVC의 자동 prune을 끄고 의존성 역순으로 정리합니다. Retain 자원의 잔여 비용을 별도 기록합니다. |

실패한 단계에서는 다음 단계로 넘기지 않고 직전 완료 지점으로 복원합니다. CloudFormation rollback과 앱 Git revert는 각각의 관리 대상에만 적용합니다. 시스템 Node 수동 교체 절차에는 같은 AMI·nodeadm·Access Entry·라벨·taint 복원과 Sealed Secrets 키 복구를 포함합니다.

## 7. 첫 실행 단위와 진행 기록

기반·플랫폼·데이터와 앱 Chart, Argo Application·CI/GitOps 정의의 정적 검증까지 진행했습니다. 실제 AWS·Kubernetes 설치와 GitHub 실행은 하지 않았습니다. 다음 인프라 작업은 **Gateway·Internal ALB·CloudFront·도메인 연결**입니다. 로컬 BE·AI의 Kafka·RunPod·복구 기능과 실제 배포 선행 조건은 구축 기록을 참조합니다.

| 단계 | 현재 상태 | 다음 결과물 |
|---|---|---|
| 0 | DNS weighted 정책·Peering·AMI·기본 add-on 확인, DB 엔진·Controller digest 등 남음 | 인벤토리·버전표 보완 |
| 1 | 기반 템플릿 4개 작성·정적 검증 완료, AWS 미적용 | 실제 생성 전 선행 조건과 Change Set 검토 |
| 2 | CSI·Controller IAM·Karpenter·Pod SG·관리 Chart 작성·렌더링, AWS·Kubernetes 미적용 | 이미지 digest와 실환경 배치·권한·연결 검증 |
| 3 | Redis·Kafka·7개 토픽 Chart와 DB 연결 스택 작성·정적 검증, AWS·Kubernetes 미적용 | DB 엔진·실제 송수신·영속성·접근 제한 검증 |
| 4 | 공통 앱 Chart·dev 설정·Argo·수동 CI/GitOps 작성·정적 검증, 앱 비활성 | Kafka·RunPod·S3 권한·Secret·실제 이미지와 서비스 연동 검증 |
| 5~6 | CI/GitOps 정의 작성, edge 미착수 | edge 템플릿·DNS 전환안·실제 GitHub·Argo 동작 검증 |
| 7 | 미착수 | 관측·영속성·접근 제한·작업 복구 결과 |

## 8. 확인한 공식 자료와 추가 버전 검증

다음은 2026-11-02 웹 조회에서 확인한 공식 자료입니다. Strimzi 호환표의 확인 결과를 제외한 세부 설치 옵션·이미지 digest는 0단계에서 선택한 release의 자료로 다시 고정합니다.

- [Strimzi 지원 버전표](https://strimzi.io/downloads/): Operator 1.3.0의 Kafka 4.2.2 지원과 Kubernetes 시험 범위를 확인했습니다.
- [Kafka 4.2.2 발표](https://kafka.apache.org/blog/2026/09/29/apache-kafka-4.2.2-release-announcement/): 요청 버전의 공식 발표를 확인했습니다.
- [LBC Gateway API 안내](https://kubernetes-sigs.github.io/aws-load-balancer-controller/v2.15/guide/gateway/gateway/)와 [공식 release 목록](https://github.com/kubernetes-sigs/aws-load-balancer-controller/releases): Standard CRD와 LBC 전용 CRD가 필요함을 확인했습니다. v2.15를 설치 확정한 것은 아닙니다.
- [CloudFront VPC Origin 요건](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-vpc-origins.html): IGW, 사설 주소 여유, Active origin과 서비스 관리 SG 생성 순서를 확인했습니다. Argo CLI의 native gRPC 제약도 관리 접속 시험에 반영합니다.

현재까지의 검증은 문서 정적 검토와 위 기반 CloudFormation·설정의 검증입니다. AWS 배포, Helm 렌더링, Kubernetes 적용, 부하·복구 시험은 아직 수행하지 않았습니다.
