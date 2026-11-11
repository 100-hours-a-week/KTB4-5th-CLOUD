# v3 dev 환경 구축 기록

## 2026-11-02: 0단계 보완과 1단계 템플릿 작성

작업 기준은 [구축 계획](v3%20dev%20환경%20구축%20계획.md)과 최신 로컬 v3 설계 문서입니다. Cloud 저장소의 현재 branch는 `feat/cloudwatch-monitoring`, HEAD는 `5f2067a`입니다. 기존 변경을 보존하고 신규 `infra/v3/`에만 기반 설정을 작성했습니다. FE·BE·AI 기능 코드와 인프라 파일은 변경하지 않았습니다.

### 확인 결과

| 확인 항목 | 결과와 영향 |
|---|---|
| 계정 | `906462447513`, 조회 principal `arn:aws:iam::906462447513:user/dameokja-aws-cli`입니다. 기본 프로필 대신 명시적 프로필을 사용했습니다. |
| dev DNS | v1: `43.200.71.145`, SetIdentifier `v1`, Weight 0, TTL 300입니다. v2: `15.164.208.228`, SetIdentifier `v2`, Weight 100, TTL 300입니다. 단순 A 레코드 교체가 아닌 weighted record set 변경으로 설계합니다. |
| 기존 Peering | `pcx-053681bb241b7a9ad`는 stage VPC `vpc-0145516a3a1360f36`와 기본 VPC `vpc-0dc397a47a708f727` 사이 active입니다. prod VPC와 dev DB 사이의 직접 연결이 아닙니다. |
| dev DB 경로 | 기본 VPC의 main Route Table `rtb-0b803993570ee77ab`에는 stage의 `10.10.32.0/20` 경로만 있습니다. 앞서 확인한 prod Private Route Table에도 기본 VPC행 경로가 없었습니다. EKS 재사용 VPC와 DB 사이 양방향 전용 경로가 필요합니다. |
| dev DB SG | `sg-039d80e4bb3d2aa4d`의 3306은 기존 dev 앱과 stage 마이그레이션용 SG들을 허용합니다. v3 BE Pod SG는 아직 없고 5432 수신 규칙도 조회 결과에 없었습니다. 기존 규칙은 수정하지 않았습니다. |
| dev DB SSM | `i-0111bde7fc68be69c`는 Ubuntu, SSM Online입니다. 현재 EC2 응답의 InstanceProfile은 null이었습니다. SSM Online만으로 관리 권한 방식과 엔진 실행을 단정하지 않습니다. |
| DB 엔진 | 원격 명령·접속을 수행하지 않았으므로 MySQL·PostgreSQL 실행, 버전, 데이터 경로는 미확인입니다. SG 3306 허용은 MySQL 실행 증거가 아닙니다. |
| EKS 1.35 add-on | AWS가 제공하는 기본 호환 버전을 `infra/v3/versions.yaml`에 기록했습니다. CNI·CoreDNS의 선택 설정 키와 타입을 AWS 스키마로 확인했습니다. |
| AL2023 AMI | SSM은 `ami-0a3227d9c98935bcb`, release `1.35.9-20261031`을 반환했습니다. 조회된 ID를 예시에 고정했으며 실제 EC2 기동은 수행하지 않았습니다. |
| NetworkPolicy | 최신 공식 문서는 ownerReferences가 있는 StatefulSet 등의 Pod를 적용 대상으로 설명합니다. StrimziPodSet의 실제 ownerReferences·PolicyEndpoint 생성은 설치 후 검증합니다. 거부 시험은 standalone Pod 대신 Deployment 또는 Job으로 실행합니다. |

### 작성한 기반 설정

- EKS는 private endpoint, API 인증, 명시적 관리자·EC2_LINUX Access Entry, 전용 Node 역할과 IRSA OIDC Provider를 사용합니다.
- CNI와 kube-proxy, 시스템 EC2, CoreDNS를 별도 스택으로 나누었습니다. CNI는 Pod ENI·NetworkPolicy와 standard 모드를 설정합니다.
- 시스템 EC2는 m7i.large·AL2023 한 대이며 nodeadm MIME UserData, 시스템 라벨·taint, IMDSv2·암호화 gp3를 적용했습니다. ASG와 신규 VPC는 없습니다.
- `infra/v3/README.md`에 적용 순서·입력 전달·접속·소유권·복원 제약을 정리하고 스택별 파라미터 예시를 작성했습니다.

### 검증과 남은 작업

CloudFormation 4개 파일은 cfn-lint에서 오류 없이 통과했습니다. 첫 lint에서 CloudFormation이 지원하지 않는 IPv4 Service CIDR GetAtt를 발견해 명시적 파라미터 Output으로 수정했습니다. 리전의 add-on 제공 여부·설정 키 확인은 실제 배포 검증과 구분합니다.

| 수행한 검증 | 결과 |
|---|---|
| cfn-lint, 서울 리전 스키마 | 4개 템플릿 통과했습니다. |
| AWS CloudFormation ValidateTemplate | 4개 템플릿 통과했습니다. IAM이 있는 스택의 CAPABILITY_IAM 요구를 확인했습니다. Windows 파일 읽기는 AWS_CLI_FILE_ENCODING=UTF-8로 지정했습니다. |
| AWS add-on JSON Schema | 조회한 CNI·CoreDNS 스키마를 cfn-lint의 StandardValidator로 검사해 설정 2개가 통과했습니다. 스키마 원본은 Git 제외 경로에 보관했습니다. |
| 파라미터 JSON | 4개 파일 파싱 성공, 각 템플릿의 파라미터 키와 정확히 일치했습니다. |
| 자원 종류 | 전체 템플릿에서 EC2 1개, 신규 VPC·NAT·ASG 0개임을 확인했습니다. |

다음 단계는 Controller의 release artifact·digest·호환성 확정, CSI와 전용 IAM, Karpenter와 관리 도구 설정입니다. DB 엔진 확인과 직접 Peering·양방향 경로 설계는 병행합니다. 관리자 VPN·SSM 경로 또는 public endpoint의 실제 `/32`는 적용 전에 확정해야 합니다. 현재 문서용 `/32`는 public 모드에서 거부하도록 구성했습니다.

AWS 자원 생성·갱신·삭제, Change Set 생성·실행, 원격 DB 명령, DNS 변경, 커밋·push·PR·Wiki 변경은 수행하지 않았습니다. EKS·Node Ready, Pod SG, Kafka·Redis 영속성, 앱 송수신과 작업 복구는 아직 검증하지 않았습니다.

## 후속 작업: 기반 배포 Workflow 연결

`.github/workflows/infra-v3-cfn.yml`과 `infra/v3/scripts/stack.py`를 추가했습니다. PR·main push는 검증만 수행하고 실제 적용은 main의 workflow_dispatch에서 plan과 apply를 나누어 실행합니다. 스택별 EKS Outputs를 자동 전달하므로 CA·endpoint·OIDC·역할·SG를 사용자가 복사할 필요가 없습니다.

기존 `dameokja-github-cfn-deploy`의 실제 신뢰 정책은 이 Cloud 저장소의 main 브랜치와 기존 stage·prod Environment를 허용했습니다. inline 정책은 `dameokja-*` 스택의 Change Set 작업과 `dameokja-cfn-execution` PassRole을 허용했습니다. 실행 역할에 AdministratorAccess가 연결된 사실을 확인했으며 역할과 GitHub 설정은 변경하지 않았습니다. 새 Environment의 OIDC 신뢰가 준비된 것으로 가정하지 않습니다.

배포 제어는 계정·리전·허용 스택 4개·main 수동 실행·명시적 활성화·동일 커밋의 Change Set을 확인합니다. plan에서는 실행하지 않고 apply에서는 새로운 Change Set을 만들지 않습니다. 삭제·교체 가능 변경은 차단하며 권한 오류를 신규 스택 부재로 오인하지 않습니다.

| 검증 | 결과 |
|---|---|
| 배포 제어 단위 테스트 | AWS 호출을 mock한 9개 테스트가 통과했습니다. plan/apply 분리, 다른 환경 대상 차단, 잘못된 변경 계획 차단, Outputs 전달을 확인했습니다. |
| 기반 템플릿 cfn-lint | 기존 4개 템플릿이 통과했습니다. Workflow도 같은 cfn-lint 1.58.0을 사용합니다. |
| Workflow 정적 확인 | YAML 파싱, PR·push·workflow_dispatch 트리거와 OIDC main 컨텍스트를 확인했습니다. actionlint와 GitHub 실제 실행은 수행하지 않았습니다. |

GitHub Variables의 실제 등록 여부와 Actions 실행은 미검증입니다. 설정할 값과 수동 실행 순서는 `infra/v3/README.md`에 정리했습니다. 이 상태에서도 merge만으로 EKS가 생성되지는 않습니다. 변수 활성화와 스택별 plan 검토·apply가 필요합니다. AWS Change Set 생성·실행, EKS·EC2 생성, 기존 환경 변경과 Git 작업은 수행하지 않았습니다.

## 2026-11-03: 클러스터 운영 기반 작성·검증

두 번째 PR의 예정 범위인 CSI·Controller IAM·Karpenter·관리 도구·dev Pod SG를 로컬에 작성했습니다. PR을 생성하거나 AWS에 적용한 단계는 아닙니다. 설치 순서와 검증 한계는 당시 로컬 작업본의 플랫폼 README(`v3/platform/README.md`)에 정리했습니다.

### 작성한 내용

- `controller-iam.yaml`: Karpenter 1.15.1의 공식 정책·Queue·이벤트 규칙을 기존 AppNodeRoleArn과 연결하고 EBS CSI·LBC 전용 IRSA를 추가했습니다. Node 역할의 CNI 권한을 복제하지 않고 기존 CNI 전용 IRSA를 유지합니다.
- `storage-addon.yaml`: EBS CSI·Metrics Server의 1 Replica 배치, 시스템 라벨·taint 대응과 명시적 자원을 구성했습니다. Metrics API의 Control Plane→10251을 추가했습니다.
- `dev-pod-sg.yaml`: 서비스별 SG 5개와 필요한 내부 수신 규칙을 작성했습니다. 기존 DB·ALB SG와 Peering은 변경하지 않았습니다.
- `v3/charts/platform/`: 고정 AMI의 EC2NodeClass·On-Demand NodePool, gp3 Retain StorageClass, dev Pod SG 연결과 서비스별 NetworkPolicy를 작성했습니다. Karpenter 상한은 26vCPU/104Gi이며 유휴 Node만 정리합니다.
- `v3/platform/`: Namespace, 관리 Chart values, 해시 lock, Outputs 기반 렌더링·검증 스크립트를 작성했습니다. GitHub 검증 Workflow는 이 렌더링을 가짜 Outputs로 재현합니다. 스택 실행 선택은 기존 4개에 신규 3개를 추가했습니다.

### 검증에서 수정한 문제

선택한 Argo Chart 10.11.6은 `applicationSet.enabled=false`를 지원하지 않아 기본 1 Replica가 남았습니다. 실제 렌더링으로 발견해 replicas 0으로 수정했습니다. Argo Redis 초기화 Job과 repo-server init container의 누락된 requests도 지정했습니다.

Strimzi Chart는 dev와 설치 Namespace tool을 자동으로 합쳐 감시하므로 실제 범위를 `dev,tool`로 기록했습니다. stage·prod는 포함하지 않습니다. Kafka 4.2.2가 `quay.io/strimzi/kafka:1.3.0-kafka-4.2.2`로 매핑되는 것도 확인했습니다. 자동 NetworkPolicy 생성을 꺼 사용자 정책과의 합집합으로 9092 허용 범위가 넓어지지 않도록 구성했습니다.

### 수행한 검증

| 검증 | 결과 |
|---|---|
| CloudFormation cfn-lint | 기반 4개와 신규 3개, 총 7개 템플릿이 통과했습니다. |
| AWS ValidateTemplate | 신규 Controller IAM·Pod SG·CSI/Metrics 템플릿이 통과했습니다. 최종 Metrics 수신 규칙을 추가한 버전도 다시 통과했습니다. |
| AWS add-on 설정 스키마 | EBS CSI와 Metrics Server의 설정이 선택한 버전의 AWS JSON Schema를 통과했습니다. |
| 파라미터 연결 | 7개 JSON의 파라미터 키가 해당 템플릿과 일치합니다. 기존 배포 제어 테스트 9개도 통과했습니다. |
| Chart artifact | 공식 색인의 archive SHA256과 다운로드를 대조했습니다. Karpenter·CRD는 OCI pull 후 archive SHA256을 lock에 기록했습니다. Gateway API 1.6.0도 고정 파일 해시를 기록했습니다. |
| Helm 렌더링 | 관리 Chart와 platform Chart가 렌더링됐습니다. tool의 활성 관리 Pod 8개에 시스템 선택자·toleration·requests가 있고 Karpenter IAM annotation이 연결되는지 검사했습니다. |
| Karpenter CRD | NodePool·EC2NodeClass가 고정 CRD의 OpenAPI 스키마를 통과했습니다. Kubernetes 서버의 CEL 검증과 Ready 판정은 아직 수행하지 않았습니다. |

관리 Chart 8개 Pod의 requests 합계는 CPU 650m, memory 1,408Mi입니다. 이 합계는 CoreDNS·CSI·Metrics Server·CNI·kube-proxy·Alloy·향후 Entity Operator를 제외하므로 전체 시스템 Node 예산으로 사용하지 않습니다. 실제 Allocatable과 설치된 모든 Pod·DaemonSet을 합산하는 검증이 남아 있습니다.

### 남은 조건과 다음 순서

이미지 태그별 digest·Node의 이미지 접근, 실제 Pod ENI·branch ENI 수용량, Kubernetes API·webhook 연결, Controller Ready는 미검증입니다. GitHub 실제 실행, Argo 관리 도메인, Sealed Secrets 키 백업·팀별 RBAC도 미적용입니다. ALB·관측 SG 규칙과 DB 접근 규칙은 해당 자원이 확인되는 후속 단계에서 추가합니다.

다음 구현 단위는 dev 데이터 계층입니다. Redis·Kafka CR과 7개 토픽을 작성하고, 기존 dev DB의 엔진·직접 VPC 연결·양방향 경로를 확인합니다. 이 단계에서도 기존 stage·prod 데이터나 서비스는 변경하지 않습니다.

현재까지 커밋·push·PR·merge, AWS Change Set 생성·실행, EKS·EC2 생성, 기존 환경 변경은 수행하지 않았습니다.

## 2026-11-03: dev 데이터 계층 작성·검증

세 번째 PR 예정 범위인 Redis·Kafka·업무 토픽과 외부 DB 연결 코드를 작성했습니다. 설치·검증 절차와 복구 한계는 당시 로컬 작업본의 데이터 계층 README(`v3/data/README.md`)에 정리했습니다.

### 작성한 내용

- dev 전용 `dev-data` Helm Chart에 Redis StatefulSet 1개, AOF everysec, maxmemory 512mb, gp3 10Gi PVC와 Retain 정책을 작성했습니다. ConfigMap 실제 내용의 checksum으로 설정 변경을 Pod에 반영합니다.
- Kafka 4.2.2의 broker/controller 겸용 KafkaNodePool 1개와 10Gi gp3, deleteClaim false, 내부 PLAINTEXT 9092를 작성했습니다. Topic Operator는 시스템 Node에, Kafka는 앱 Node에 배치합니다.
- 합의된 7개 KafkaTopic은 partitions/replicas 1, delete·retention 30일로 구성했습니다. Kafka Ready 이후 topics.enabled를 활성화하는 두 단계 설치로 구분했습니다.
- Redis 8.6.4-alpine과 Strimzi Kafka 1.3.0-kafka-4.2.2의 공식 레지스트리 index digest·linux/amd64 manifest를 확인하고 Chart에 digest를 고정했습니다. 실제 컨테이너 실행·Node pull은 수행하지 않았습니다.
- `dev-data-access.yaml`에 직접 Peering, 호스트 /32 정방향·앱 Subnet /20 반환 Route와 BE→MySQL·AI→PostgreSQL 추가 SG 규칙을 작성했습니다. Workflow의 스택 선택은 8개가 됐습니다.

### 기존 환경 조회와 설계 보완

DB EC2는 기존과 같은 VPC·주소·SG에서 running이었습니다. 양쪽 Route Table에는 아직 해당 직접 연결 경로가 없었습니다. SSM AWS:Application 인벤토리는 빈 배열을 반환했으므로 엔진 실행·부재를 판정하지 않았습니다. 원격 명령·DB 로그인·패키지 설치는 수행하지 않았습니다.

VPC 외부로 향하는 기본 CNI SNAT를 그대로 사용하면 DB SG에서 Pod SG를 식별한다는 설계와 어긋날 수 있어, CNI에 `AWS_VPC_K8S_CNI_EXCLUDE_SNAT_CIDRS=172.31.4.192/32`를 추가했습니다. 이는 앞선 network-addons 템플릿에 필요한 보완입니다. 실제 적용 시 해당 변경과 CNI ACTIVE를 먼저 확인하고 DB 연결 스택을 진행합니다. 전체 외부 SNAT 설정은 유지합니다.

배포 제어는 DB 연결의 plan·apply 전 CNI 예외, Route Table의 VPC와 동일 목적지 기존 Route를 조회합니다. 다른 소유의 Route는 자동 인수·덮어쓰기하지 않습니다. 기존 DB SG의 다른 규칙, 기본 Route, stage Peering은 코드의 수정 대상으로 포함하지 않았습니다.

### 수행한 검증과 남은 항목

| 검증 | 결과 |
|---|---|
| Helm lint·렌더링 | dev-data Chart가 통과했습니다. 기본 상태에는 토픽이 없고 활성화 후 7개가 생성됩니다. dev 이외 Namespace, 다른 Kafka 버전과 digest 없는 이미지 입력을 거부합니다. |
| Strimzi CRD | Kafka·KafkaNodePool·KafkaTopic 7개가 고정 1.3.0 CRD OpenAPI 스키마를 통과했습니다. 서버 CEL·실제 Broker 기동은 별도입니다. |
| 데이터 설정 계약 | Redis AOF·용량·보존, Kafka 단일 역할·자원·보존·토픽, 이미지 digest와 DB SG·SNAT 계약 검사가 통과했습니다. |
| CloudFormation | 전체 8개 cfn-lint와 신규 DB 연결·수정 CNI의 AWS ValidateTemplate 검증을 수행했습니다. |
| 배포 제어 | Route 충돌·소유권·VPC 검증을 포함한 10개 테스트가 통과했습니다. |
| 입력·CNI 설정 | 파라미터 JSON 8개의 키, Workflow YAML과 8개 스택 선택, 수정된 CNI 설정의 AWS JSON Schema 검증이 통과했습니다. |

DB 엔진·계정·백업·직접 연결의 실제 적용, Pod 원본 IP·SG 참조, Redis·Kafka 기동·송수신·PVC 재연결·메시지·offset 보존은 아직 검증하지 않았습니다. 10Gi로 30일 보관량이 충분하다고 판정하지 않습니다. 초기 Kafka·Redis 준비와 BE·AI 기능 연동의 완료는 구분합니다.

다음 범위는 FE·BE·AI API Chart와 CI/GitOps입니다. 기능 코드·Consumer 복구 구현이 필요하면 해당 팀에 알리고 직접 수정하지 않습니다. 현재도 커밋·push·PR·merge와 AWS·기존 환경 변경은 수행하지 않았습니다.

## 2026-11-03: 앱 배포 정의와 CI/GitOps 작성

공통 application Chart, 앱별 dev values, dev AppProject와 Application 3개를 작성했습니다. 운영 Service와 색상별 미리보기 Service, digest별 Deployment, 앱 Node 배치·자원·Probe·Secret·IRSA 참조를 구성했습니다. 모든 앱의 enabled와 runtimeContractVerified는 false로 두어 검증되지 않은 이미지가 실행되지 않게 했습니다. 자세한 계약은 당시 로컬 작업본의 앱 배포 README(`v3/apps/README.md`)에 기록했습니다.

### 확인한 기능 연동 차이

현재 로컬 BE build.gradle·application.yml에서는 Kafka·Redis 의존성과 설정을 확인하지 못했습니다. AI는 메모리 큐·로컬 Paddle OCR·Ollama를 사용하며 Kafka·RunPod API 계약을 확인하지 못했습니다. 따라서 현재 AI 이미지를 설계의 외부 추론 API 자원 예산으로 승인하지 않았습니다. 기능·Consumer 복구 코드는 수정하지 않았으며 팀 검증 대상으로 남깁니다.

AI Dockerfile에는 이미 pyproject에 선언된 db extra 설치만 추가했습니다. asyncpg를 이미지에 포함하기 위한 인프라 변경이며, 애플리케이션 코드·스키마 변경은 없습니다. FE·BE는 신규 v3 Workflow 외 기존 파일을 변경하지 않았습니다.

### CI/GitOps와 승격 경계

앱 저장소별 수동 v3-build Workflow와 Cloud 공통 이미지 Workflow를 작성했습니다. 기존 v1 자동 배포를 수정하지 않고 명시적 변수 활성화 후에만 빌드하도록 했습니다. 공통 Workflow는 검사·AMD64 빌드·GHCR 발행 후 선택적으로 Cloud의 후보 이미지 PR Workflow를 요청합니다. 실제 저장소 설정·토큰 등록이나 Workflow 실행은 하지 않았습니다.

Cloud 이미지 갱신은 비활성 색상의 digest만 변경하며 활성 색상·Replica·배포 활성화를 유지합니다. 실행 중 후보는 덮어쓰지 않습니다. BE·AI의 신구 Consumer 동시 실행은 별도 검증 입력이 없으면 Chart에서 차단합니다. 초기 Argo는 수동 sync이며 자동 prune·자동 승격은 없습니다.

이번 작업에서 PR을 만들었다는 뜻은 아닙니다. 후보 PR 자동화는 추후 V3_GITOPS_WRITES_ENABLED와 별도 쓰기 토큰을 설정하고 수동 요청한 때에만 작동합니다. 현재 실제 커밋·push·PR은 없습니다.

### 데이터 Probe 보완

설계 대조 중 Redis Probe의 Secret 인증·PONG 확인·기동 예산을 보완했습니다. cloud-secrets/REDIS_PASSWORD 참조, Startup 60회·주기 10초, 공통 timeout 3초와 설계의 Readiness·Liveness 횟수를 반영했습니다. 평문 비밀번호·실제 Secret은 생성하지 않았습니다. BE Redis 기능·설정 키와 같은 인증 값의 공급·회전은 연동 검증 대상으로 유지합니다.

### 정적 검증

- 앱 3개의 기본 미배포 상태와 테스트 이미지로 활성화한 렌더링, ClusterIP Service selector·색상·Replica를 검사했습니다.
- 후보 이미지 변경이 활성화·운영 색상을 바꾸지 않고, 실행 중 후보를 덮어쓰지 않는지 확인했습니다. BE·AI Consumer 동시 실행 거부도 확인했습니다.
- AppProject·Application이 고정 Argo CD CRD 스키마를 통과했습니다.
- 공통 이미지 빌드·후보 PR·앱별 호출자, 총 5개 CI Workflow YAML과 수동 dispatch·재사용 경로를 검사했습니다. actionlint·GitHub 실제 실행은 수행하지 않았습니다.

실제 FE·BE·AI 테스트·이미지 빌드, GHCR·GitHub 토큰·branch protection, Secret·S3 역할 공급, Argo sync·Pod 기동·Probe·Kafka·RunPod·DB 기능 시험은 아직 남아 있습니다. 단순 이미지 발행 성공도 서비스 연동 성공으로 간주하지 않습니다.

다음 인프라 범위는 Gateway·Internal ALB·CloudFront·도메인 연결입니다. 현재도 AWS 생성·갱신·삭제, 기존 환경 변경과 Git 작업은 수행하지 않았습니다.
