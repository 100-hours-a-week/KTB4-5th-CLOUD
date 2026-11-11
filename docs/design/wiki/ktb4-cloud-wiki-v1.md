# 다먹자 v1 인프라 위키 — 1단계: EC2 + Docker Compose 기반 초기 배포 설계

> 기준: v1 마지막 운영 구성(2026-10-19 v2 prod 전환 전, dev는 2026-10-09~11 v2 dev 전환 전). v1은 v2로 전환한 뒤 종료했다.

> KTB 4조 다먹자 팀의 v1 인프라 설계 문서입니다. 이 문서는 **1단계(EC2 + Docker Compose 기반 초기 배포)** 만 다룹니다. 2단계(CI)는 [ktb4-cloud-wiki-ci.md](ktb4-cloud-wiki-ci.md), 3단계(CD)는 [ktb4-cloud-wiki-cd.md](ktb4-cloud-wiki-cd.md)를 참고하세요.
> v1 운영 설정은 [v1 설정](../../../v1/)(prod Compose·nginx, 배포 스크립트)을, 서비스 품질 목표는 [SLI·SLO 설계](../../observability/SLI-SLO-design.md)를 기준으로 확인합니다. v1 dev Compose는 v2 dev 전환 때 삭제했습니다(커밋 `7c7940c`).

## 목차
0. [클라우드 벤더 선택](#0-클라우드-벤더-선택)
1. [초기 서비스 규모 정의](#1-초기-서비스-규모-정의)
2. [Docker 컨테이너 구성 설계](#2-docker-컨테이너-구성-설계)
3. [Docker Compose 기반 배포 구조 설계](#3-docker-compose-기반-배포-구조-설계)
4. [현재 구조의 한계 정의](#4-현재-구조의-한계-정의)

---

## 0. 클라우드 벤더 선택

- **상황**: 빠른 MVP 구축을 위해 기존 학습 경험, 서비스 요구사항, 운영 비용을 함께 검토합니다.
- **결론**: EC2·IAM 등 팀의 사용 경험이 있는 AWS를 선택합니다.
- **이유**: 새로운 플랫폼 학습 시간을 줄일 수 있습니다. 다른 벤더보다 항상 저렴하거나 성능이 우수하다는 의미는 아닙니다.

| 벤더 | 검토한 특징 | v1에서의 판단 |
|---|---|---|
| AWS | 서비스·학습 자료가 다양하며 팀의 기존 학습 경험이 있습니다. | 빠른 구현과 운영 도구 연동을 우선합니다. |
| Microsoft Azure | Microsoft 환경과의 연계, Azure Arc 기반 하이브리드 관리, 규정 준수 요구 대응을 검토할 수 있습니다. | 기존 Microsoft 시스템 연계가 필수 조건은 아닙니다. |
| Google Cloud | AI·데이터 분석, Kubernetes 생태계, 글로벌 네트워크를 활용할 수 있습니다. | v1에는 GPU나 Kubernetes가 필요하지 않습니다. |
| NAVER Cloud | Neurocloud 기반 하이브리드 구성과 CLOVA 계열 AI 연동을 검토할 수 있습니다. | 한국어 AI 연계는 향후 검토 대상이며 v1 인프라 선택의 필수 조건은 아닙니다. |
| NHN Cloud | 공공·금융·게임 분야 솔루션과 게임 운영 경험을 제공합니다. | 해당 산업에 특화된 요구가 현재 핵심 조건은 아닙니다. |

Neurocloud의 공조·서버 모듈은 고객사 설치 형태의 한 예이며, 하이브리드 클라우드를 정의하는 조건은 아닙니다. 친환경 데이터센터도 검토 관점으로 남기되, 근거 없이 비용·성능·환경 우위를 단정하지 않습니다. 공공·금융 규정 준수는 벤더 이름만으로 판단하지 않고 해당 서비스·리전의 인증 범위를 확인해야 합니다.

비용은 같은 리전·메모리·CPU 제공 방식·과금 시간을 맞춰 비교합니다. 기존 비교의 GCP 공유 코어와 AWS 버스터블 CPU, 메모리 용량이 다른 Azure 모델은 동등 성능으로 볼 수 없으므로 가격만으로 순위를 매기지 않습니다.

참고: [Azure Arc](https://azure.microsoft.com/en-us/products/azure-arc/), [Google Cloud 인프라](https://cloud.google.com/about/locations), [Neurocloud](https://www.ncloud.com/neurocloud), [NAVER AI 서비스](https://www.ncloud.com/product/aiService), [NHN Cloud](https://www.nhncloud.com/kr).

## 1. 초기 서비스 규모 정의

### MAU
- **상황**: v1은 KTB 부트캠프 데모/초기 서비스로, 훈련생 그룹과 소수 외부 사용자만 접근할 것으로 예상됨.
- **결론**: MAU 200명
- **이유**: KTB 훈련생 130명 + KTB 관계자 20명 + 외부 사용자 50명

### DAU

- **상황**: 초기 활성 사용자 수를 추정해야 합니다.
- **결론**: 평일 DAU를 약 140명으로 가정합니다.
- **이유**: MAU 200명의 70%가 아침 알림을 확인한다는 가정입니다. 알림 확인자만이 DAU의 정의는 아니며, 실제 DAU는 하루 동안 서비스를 사용한 고유 사용자 수로 측정합니다. 주말 150명 가정은 별도 시나리오이며 시간대별 인원을 단순 합산하지 않습니다.

### 사용자 흐름 및 Peak Traffic (RPS)
- **상황**: 인프라 사이징을 위해 시간대별 접근 패턴과 피크 트래픽(RPS)을 추정해야 함. v1 사용자 흐름은 로그인 → 냉장고 재고 확인 → 유통기한순 재정렬 조회.
- **결론**:

  | 시간대 | 접근자 수 | 윈도우 | RPS |
  |---|---|---|---|
  | 평일 아침 8시 (알림 버스트) | 140명 | 5분 | **1.4 RPS** |
  | 평일 점심 | 0명 | – | 0 RPS |
  | 평일 저녁 | 100명 | 1시간 | 0.083 RPS |
  | 주말 점심 | 150명 | 1시간 | 0.125 RPS |
  | 주말 저녁 | 150명 | 1시간 | 0.125 RPS |

  → 초기 부하 검증 목표: **7 RPS** (1.4 RPS의 5배)

  계산식은 접근자 수 × 세션당 API 요청 3건 ÷ 구간 길이(초)입니다. 1.4 RPS는 5분 구간 평균이므로 순간 최대 부하를 의미하지 않습니다. v1 알림 폴링, 재시도, 정적 파일 요청은 별도로 산정해야 합니다.

- **이유**: 아침 8시 알림 발송 직후 70%가 확인 접근(140명, 5분 윈도우)한다고 가정한 구간이 가장 뾰족한 피크. 평일 점심은 대부분 외부 식사라 접근 없음, 평일 저녁은 사용자 절반이 귀가(100명). 주말은 전체 200명 중 50명이 교육장 식사·외식 등으로 앱을 사용하지 않고 150명이 접근한다고 가정 — 인원은 더 많아도(150명) 윈도우가 1시간으로 넓어 RPS 부담은 적음. 실제 흐름이 예상과 다를 수 있어 3~5배 여유를 둠.

### Latency

- **상황**: 초기 인프라 응답속도 가정과 사용자 경험 목표를 구분해야 합니다.
- **결론**: 초기 200ms는 참고 목표이며 최소 응답시간이 아닙니다. 최종 평가는 [SLI·SLO 설계](../../observability/SLI-SLO-design.md)의 사용자 요청부터 화면 반영까지 기준을 따릅니다.
- **이유**: 서버 응답과 화면 반영 시간은 측정 구간이 다릅니다. 수동 재고 등록·조회는 p95 1초, p99 10초 목표를 적용하며 모든 요청이 1초 이내라고 단정하지 않습니다.

### 가용성

- **상황**: 단일 서버 구성의 비용과 장애 영향을 함께 고려해야 합니다.
- **결론**: 초기 설계에서는 99%를 가정했습니다. 운영 평가 대상·분모·평가 기간과 최종 목표는 [SLI·SLO 설계](../../observability/SLI-SLO-design.md)를 따릅니다.
- **이유**: 초기 가정만으로 가용성을 보장할 수 없습니다. 인프라 가동 시간과 사용자 요청 성공률은 서로 다른 지표입니다.

### 배포 빈도 / 전략

[Docker Compose 운영 배포 동작](https://docs.docker.com/compose/how-tos/production/)을 기준으로 교체와 롤링 배포를 구분합니다.

- **상황**: dev는 잦은 배포가 필요하고 prod는 배포 중 영향을 줄여야 합니다.
- **결론**: 배포 대상 서비스(FE 또는 BE) 컨테이너 하나를 새 이미지로 교체하고, 헬스체크에 실패하면 이전 이미지 digest와 서비스 설정으로 복구합니다([배포 스크립트](../../../v1/scripts/deploy-v1.sh)). dev는 `dev` 브랜치 병합마다, prod는 `main` 병합마다 배포합니다.
- **이유**: 서비스마다 복제본이 하나라 Rolling Update가 성립하지 않습니다. 롤링 배포를 하려면 복수 인스턴스 또는 복제본, 트래픽 분산과 준비 상태 확인이 필요한데, v1은 비용 때문에 앱 서버 1대·nginx 진입점을 택했습니다. 그래서 교체 중 짧은 중단을 감수하고, 실패한 버전이 남지 않도록 자동 복구를 둡니다. 2026-10-04 부하 테스트에서는 BE 교체 시각과 겹쳐 502가 1건 관측됐습니다([부하테스트 결과](../../../tests/k6/V1-RESULTS.md)). 상세 절차는 [CD 문서](ktb4-cloud-wiki-cd.md)를 확인합니다.

### 서버 인스턴스 — 모델 계열
- **상황**: MVP 인프라에 맞는 EC2 인스턴스 계열을 선택해야 함.
- **결론**: 범용 T 시리즈 (G/P 계열 제외)
- **이유**: MVP 구현 단계라 특정 워크로드에 특화된 인스턴스가 필요 없고, AI 파트 팀원에게 확인한 결과 v1 기능에는 GPU가 필요 없다고 판단됨.

### 서버 인스턴스 — ARM vs x86

- **상황**: 비용과 이미지·라이브러리 호환성을 함께 확인해야 합니다.
- **결론**: ARM 기반 t4g를 사용합니다. FE·BE 이미지는 GitHub Actions ARM 러너에서 `linux/arm64` 단일 플랫폼으로 빌드합니다.
- **이유**: 팀의 사용 범위에서 ARM 지원을 확인해 비용 효율을 얻습니다. 동일 성능이나 저전력을 일괄 보장하지 않으며, 이미지가 arm64 전용이므로 x86 서버로 옮기려면 빌드 플랫폼을 바꿔야 합니다.

### 서버 인스턴스 — 리소스 산정 및 결론

- **상황**: 인스턴스 크기는 부하 검증 전 메모리 예산으로 정했고, 운영 중 메모리 부족을 겪었습니다.
- **결론**: dev는 서비스 서버 t4g.small(nginx·FE·BE)과 별도 DB EC2(t4g.small, MySQL)로 나눕니다. prod는 앱 EC2 t4g.small(nginx·FE·BE)과 DB EC2 t4g.micro입니다. 컨테이너별 CPU·메모리 제한은 두지 않습니다.
- **이유**: dev를 한 대에 통합하면 메모리 예산이 2 GiB를 넘습니다. 2026-09-24 dev 서버(메모리 약 1.8 GiB, Swap 없음)에서 FE·BE·MySQL·nginx가 함께 실행되던 중 호스트 전체 메모리 부족으로 BE Java 프로세스가 OOM 종료됐습니다(컨테이너 메모리 제한 초과가 아님). 이후 2026-09-30 부하 테스트를 위해 dev를 prod와 같은 구조로 맞추며 MySQL을 dev Compose에서 빼고 별도 DB EC2로 옮겼습니다(커밋 `756534c`).

| 대상 | 추정 메모리 | 판단 |
|---|---|---|
| Dev 통합(분리 전) | nginx 100 + FE 100 + BE 1024 + OS 500 + MySQL 500 = 2224 MiB | 약 2.17 GiB로 2 GiB를 초과합니다. 실제 OOM 종료로 확인돼 DB를 분리했습니다. |
| Dev 서비스 서버·Prod 앱 | 1724 MiB | 약 1.68 GiB이며 Agent·JVM 급증 여유가 적습니다. |
| Prod DB | MySQL 500 + OS 500 = 1000 MiB | 1 GiB에 여유가 거의 없습니다. |

2 vCPU는 가정이며 T 계열 CPU 크레딧·지속 부하는 CloudWatch 알람으로 확인합니다. 리소스 제한은 총 용량 부족을 해결하지 못합니다.

### 서버 인스턴스 — 구매 방식

- **결론**: prod 앱·DB는 On-Demand입니다. dev 서비스 서버는 Spot으로 시작했고, 2026-10-01 기존 서버로 만든 AMI로 On-Demand 인스턴스를 새로 실행해 바꿨습니다.
- **운영 기준**: Spot 요청은 인스턴스와 별개의 자원입니다. 영구(persistent) 요청은 인스턴스를 종료해도 남아 대체 인스턴스를 다시 실행하므로, 서버를 교체할 때 요청도 취소합니다. AMI로 새로 실행한 인스턴스는 사설 IP가 바뀌므로 MySQL 계정의 호스트, 보안그룹, 설정 파일처럼 서버 IP가 등록된 곳을 함께 고칩니다. 2026-10-01 전환 때 두 문제를 모두 겪었습니다.

### 예상 비용

- **상황**: 월 환산 기준이 섞이지 않도록 동일 시간으로 계산합니다.
- **결론**: 가정 단가(t4g.small $0.0208/h, t4g.micro $0.0104/h)에 월 730시간을 적용하면, 마지막 운영 구성(dev 서비스 서버·dev DB·prod 앱 t4g.small 3대 + prod DB t4g.micro, 모두 On-Demand)은 **$53.14**입니다.
- **계산**: (0.0208 × 3 + 0.0104) × 730 = 53.144.
- **범위**: 계산 검산을 위한 가정이며 서울 리전 견적이 아닙니다. EBS·스냅샷·공인 IPv4·전송량·CloudWatch·Secrets Manager·SNS·Lambda 등은 별도로 포함해야 합니다.

### 아키텍처 다이어그램
![아키텍처 다이어그램](../../assets/image-12.png)

(이미지가 안 보이는 환경을 위한 Mermaid 버전 — AWS 인프라 구성만, CI/CD는 제외. 위 이미지는 dev DB를 분리하기 전 배치이므로 아래 Mermaid를 기준으로 봅니다.)

```mermaid
flowchart TB
    Client(["사용자 Client<br/>Web / Mobile"])

    subgraph DEVNET["Dev — ap-northeast-2"]
        subgraph DEV["Dev 서비스 서버 EC2 — t4g.small (On-Demand, 2026-10-01부터)"]
            DevInfo["SG In: 80,443 · 22(배포·관리)<br/>Restart Policy: no · CloudWatch Agent"]
            DevNginx["nginx<br/>80/443 → HTTPS 리다이렉트"]
            DevInfo -.- DevNginx
            DevFE["FE :3000"]
            DevBE["BE Spring/JVM :8080<br/>non-root · /actuator/health"]
            DevNginx -->|"/  :3000"| DevFE
            DevNginx -->|"/api  :8080"| DevBE
        end
        subgraph DEVDB["Dev DB EC2 — t4g.small"]
            DevDB[("MySQL 8.4 :3306<br/>EC2에 직접 설치")]
        end
        DevBE -->|"3306, 사설 IP"| DevDB
    end

    subgraph PRODVPC["Prod VPC — ap-northeast-2"]
        subgraph PRODAPP["Prod 앱서버 EC2 — t4g.small (On-Demand)"]
            ProdAppInfo["SG In: 80,443 · 22(배포·관리) · Out: 80,443,3306(→DB)<br/>Restart: unless-stopped · CloudWatch Agent"]
            ProdNginx["nginx<br/>80/443 → HTTPS 리다이렉트"]
            ProdAppInfo -.- ProdNginx
            ProdFE["FE :3000"]
            ProdBE["BE Spring/JVM :8080"]
            ProdNginx -->|"/  :3000"| ProdFE
            ProdNginx -->|"/api  :8080"| ProdBE
        end
        subgraph PRODDB["Prod DB서버 EC2 — t4g.micro"]
            ProdDBInfo["SG In: 3306(앱서버에서), 관리용 22"]
            ProdDB[("MySQL :3306<br/>EBS 저장")]
            ProdDBInfo -.- ProdDB
        end
        ProdBE -->|"3306, 사설 IP"| ProdDB
    end

    subgraph LOG["모니터링·장애 알림"]
        CW["CloudWatch<br/>지표·로그·대시보드·알람"]
        Discord["Discord<br/>SNS → Lambda"]
        CW --> Discord
    end

    Client -->|"dev.dameokja.com : 80/443"| DevNginx
    Client -->|"dameokja.com : 80/443"| ProdNginx
    DEV -.로그·지표 전송.-> CW
    PRODAPP -.로그·지표 전송.-> CW
```

**다이어그램 적용 범위**

v1 마지막 운영 배치를 나타냅니다. CloudWatch Agent는 dev 서비스 서버와 prod 앱 서버에 설치해 nginx·BE 로그와 OS·BE 지표를 보냅니다([Agent 설정](../../observability/cloudwatch-agent-setup.md)). 세부 정책은 아래 본문을 따릅니다.

## 2. Docker 컨테이너 구성 설계

### 컨테이너 실행 권한

- **상황**: 침해 시 권한 남용 범위를 줄여야 합니다.
- **결론**: 애플리케이션은 가능한 non-root로 실행하고 이미지별 실행 사용자와 파일 권한을 확인합니다.
- **이유**: non-root는 피해 범위를 줄이지만 침해를 차단하는 보장은 아닙니다. Nginx 등 기본 이미지가 모두 non-root라고 가정하지 않습니다.

### nginx / FE / BE 배치

- **상황**: 컴포넌트 분리의 운영 비용과 장애 격리를 비교합니다.
- **결론**: 초기에는 nginx·FE·BE를 한 EC2에 통합합니다.
- **이유**: 예상 부하가 작아 개별 서버의 비용·관리 부담을 줄입니다. 더 작은 인스턴스가 없어서가 아니라 운영 단순화를 위한 선택이며, FE의 SSR 부하는 실측해야 합니다.

### 로드밸런서
- **상황**: 트래픽을 받는 진입점 역할이 필요함.
- **결론**: ALB 대신 nginx 사용
- **이유**: 이 정도 규모의 인프라를 운영하기에는 ALB의 기본요금이 과함.

### DB 서비스 구현 방식
- **상황**: 관리형 DB(RDS)를 쓸지, EC2에 직접 구축할지 결정해야 함.
- **결론**: RDS 대신 EC2에 MySQL 직접 구성
- **이유**: 이 정도 규모의 서비스에서 RDS는 비용이 과함.

### 배치(알림) 작업 위치
- **상황**: 유통기한 임박 알림을 위한 배치 작업을 별도로 분리할지 결정해야 함.
- **결론**: 별도 컴포넌트로 분리하지 않고 BE에 통합
- **이유**: MAU가 크지 않아 배치 작업이 BE 리소스에 주는 부담이 크지 않을 것으로 판단.

### 로깅

- **상황**: 장애를 감지하고 원인을 추적할 중앙 관측 체계가 필요합니다.
- **결론**: CloudWatch Metrics·Logs·Dashboards·Alarms로 모니터링하고, 장애 알림은 Discord로 받습니다.
- **이유**: 별도 Loki/Promtail 서버 운영 부담을 줄이고 AWS 인프라와 연동합니다. 자동으로 수집되는 항목과 별도 설정이 필요한 항목을 구분합니다.

| 대상 | 수집·알림 방식 |
|---|---|
| CPU·상태 검사·T 계열 크레딧 | EC2 기본 지표를 조회하고 CPU·서버 중단 알람을 둡니다. |
| 메모리·파일시스템 사용률 | dev 서비스 서버·prod 앱 서버의 CloudWatch Agent로 수집합니다. EC2 기본 지표만으로는 확인할 수 없습니다. |
| Nginx·BE 로그, BE 앱 지표 | 컨테이너가 호스트에 기록한 로그 파일을 Agent가 CloudWatch Logs로 보냅니다. Docker local 드라이버의 로그가 자동 전송되는 것은 아닙니다. BE 지표는 Agent가 서버 내부의 `/actuator/prometheus`에서 읽습니다. |
| SLO·5xx·메모리·디스크 알람 | 스택 `dameokja-v1-prod-monitoring`(prod)으로 지표 필터·알람·대시보드를 관리합니다. |
| 장애·복구 알림 | CloudWatch Alarm → SNS → Lambda → Discord로 전달합니다(스택 `dameokja-v1-discord-alerts`). 2026-09-24 dev CPU 장애·복구 알림 수신을 확인했습니다. |
| Spot 중단 예고 | dev 서버가 Spot이던 동안 EventBridge → Lambda → Discord로 받도록 구성했습니다. StatusCheckFailed는 Spot 중단 사전 예고가 아닙니다. |
| CI/CD 실패·롤백 결과 | GitHub Actions 알림 워크플로가 파트별 Discord 채널로 보냅니다([CI 9번](ktb4-cloud-wiki-ci.md#9-실패-처리-정책)). |

세부 구성은 [모니터링 설계](../../observability/monitoring-alerting-design.md), [Agent 설정](../../observability/cloudwatch-agent-setup.md), [Discord 알림](../../../infra/discord-alerts/aws/README.md)을 따릅니다. Agent 자체가 멈추는 경우의 알람은 없어 로그 수신량으로만 판단합니다. 로그 보존 기간과 민감정보 제외, 수집량 비용을 관리하고 알림 실패도 점검합니다. [CloudWatch 개요](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/), [Agent 지표](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/metrics-collected-by-CloudWatch-agent.html)

### Dev 환경 구성

- **상황**: dev는 다양한 개발/테스트를 지원해야 하고, 부하 테스트는 prod와 같은 구조에서 해야 결과를 prod에 적용할 수 있음.
- **결론**: nginx·FE·BE는 dev 서비스 서버에, MySQL은 별도 DB EC2에 배치. 리소스 제한 없음. Compose는 prod와 같은 서비스 구성이고, 재시작 정책(`no`)·도메인·인증서만 다름.
- **이유**: 개발 단계에서 다양한 테스트를 자유롭게 진행할 수 있도록 리소스를 제한하지 않기로 함. 한 대에 DB까지 두면 메모리가 부족해 BE가 OOM 종료된 적이 있고(2026-09-24), 2026-09-30 부하 테스트를 앞두고 prod처럼 DB를 분리해 두 환경을 같은 구조로 맞춤. DB가 Compose 밖에 있으므로 BE 기동 순서를 `depends_on`으로 묶지 않음.

### Dev DB 데이터 유지 (Volume)

- **상황**: 개발 DB를 초기화할지와 데이터를 보존할지는 별도 정책입니다.
- **결론**: dev MySQL은 별도 DB EC2에 직접 설치해 EBS에 저장하므로, 앱 서버 재배포나 컨테이너 교체와 관계없이 데이터가 유지됩니다. 초기화가 필요하면 명시적 절차로 수행합니다.
- **이유**: 앱 서버를 교체해도 DB가 영향을 받지 않습니다. 대신 DB 계정이 앱 서버의 사설 IP로 등록돼 있어, 2026-10-01 서비스 서버를 AMI로 새로 실행했을 때 IP가 바뀌어 접속이 끊겼고 계정 호스트를 새 IP로 바꿔(`RENAME USER`) 복구했습니다.

### Prod 환경 구성

- **상황**: 앱과 DB 장애의 영향을 줄여야 합니다.
- **결론**: nginx·FE·BE는 앱 EC2에 두고 DB는 prod VPC 안의 별도 EC2로 분리합니다. 컨테이너별 CPU·메모리 제한은 두지 않습니다.
- **이유**: 앱 서버의 메모리 압박이 DB로 번지지 않게 합니다. 리소스 제한은 총 용량 부족을 해결하지 못하므로, 메모리 사용률은 알람(`dameokja-prod-memory-ticket`)으로 감시합니다. DB 분리만으로 고가용성이나 데이터 백업이 제공되지는 않습니다.

## 3. Docker Compose 기반 배포 구조 설계

### EC2 구성
- **상황**: 1번에서 정한 인스턴스 구성을 배포 구조에도 반영해야 함.
- **결론**: dev는 서비스 서버 t4g.small + DB EC2 t4g.small, prod는 앱 EC2 t4g.small + DB EC2 t4g.micro. 앱 서버는 각각 한 대이며 Compose 프로젝트 하나(`/home/ubuntu/app`)로 nginx·FE·BE를 실행.
- **이유**: ARM(t4g)이 성능 대비 비용이 저렴함. x86보다 라이브러리 호환성은 떨어지지만, MVP에서 사용하는 라이브러리 중 ARM에 호환 안 되는 것이 없어 ARM을 선택.

### Docker Network

- **상황**: 컨테이너 통신과 DB 접근 범위를 제한해야 합니다.
- **결론**: 환경마다 Compose 네트워크 하나(prod `web`, dev `default`)에 nginx·FE·BE를 함께 두고 별도 계층 분리를 하지 않습니다.
- **이유**: 구성을 단순화하지만 같은 네트워크의 컨테이너 간 접근이 가능해집니다. DB가 별도 EC2에 있어도 앱 EC2의 FE 컨테이너에서 DB로 나가는 경로가 막히는 것은 아니므로 DB 분리와 컨테이너 격리는 별개입니다. DB 인증과 보안그룹을 함께 적용해야 합니다.

### Container간 통신 포트

| 구간 | 프로토콜·포트 |
|---|---|
| 사용자 → nginx | HTTP 80 → HTTPS 443 리다이렉트 |
| nginx → FE | HTTP 3000 |
| nginx → BE | HTTP 8080 |
| BE → DB (dev·prod) | MySQL/TCP 3306, DB EC2의 사설 IP |
| CloudWatch Agent → BE | HTTP 8080, 서버 내부(`127.0.0.1`)에만 공개 |

외부 공개는 nginx 80·443만 하고 FE·BE 포트는 호스트 외부에 열지 않습니다. 내부 HTTP도 침해 시 위험이 있으므로 신뢰 경계와 전송 암호화 요구를 별도로 판단합니다.

### 서버 관리 접근 방식 — SSH vs SSM

- **상황**: 서버 관리와 배포 접근이 필요합니다.
- **결론**: v1은 익숙한 SSH를 사용합니다. 관리 접속과 GitHub Actions 배포 모두 SSH(사용자 `ubuntu`, 22번)입니다.
- **이유**: SSM은 인바운드 SSH 포트 없이 IAM으로 접근을 통제할 수 있지만 Agent·역할·서비스 연결 경로가 필요합니다. SSH를 선택하면 출발지 제한과 키·호스트 신원 검증을 운영해야 합니다.

### 외부 진입 경로 / SSH

- **결론**: 배포는 GitHub 호스팅 러너에서 SSH로 접속하며, 환경별로 저장한 known_hosts로 호스트 키를 엄격히 검증합니다(`StrictHostKeyChecking=yes`).
- **이유**: GitHub 호스팅 러너는 출발지 IP가 고정되지 않아 22번을 러너 주소로만 좁히기 어렵습니다. 그래서 키 보호와 호스트 신원 검증에 기댑니다. Git ignore는 실수로 키를 커밋하는 것을 줄일 뿐, 포트 접근 제한이나 유출된 키의 폐기를 대신하지 않습니다. 서버를 교체하면 새 호스트 키의 지문을 검증하고 known_hosts Secret을 갱신합니다.

### 보안그룹 / 포트

| 대상 | Inbound 정책 | 필요한 Outbound |
|---|---|---|
| Dev 서비스 서버 EC2 | 80·443, 배포·관리용 22 | 이미지·패키지·CloudWatch·외부 API 접근, Dev DB의 TCP 3306 |
| Dev DB EC2 | 서비스 서버에서 TCP 3306, 관리용 22 | 업데이트에 필요한 목적지 |
| Prod 앱 EC2 | 80·443, 배포·관리용 22 | 위 항목과 Prod DB의 TCP 3306 |
| Prod DB EC2 | 앱 서버에서 TCP 3306, 관리용 22 | 백업·업데이트에 필요한 목적지 |

Outbound 전체 차단은 DB 응답 자체보다 신규 로그 전송·업데이트 요청을 막습니다. 보안그룹은 상태를 추적해 허용된 연결의 응답을 허용합니다. [AWS 보안그룹](https://docs.aws.amazon.com/en_en/vpc/latest/userguide/vpc-security-groups.html)

### Volume

- **결론**: 운영 DB는 EC2 분리 여부와 관계없이 영속 저장소와 복구 가능한 백업이 필요합니다.
- **이유**: dev·prod 모두 MySQL이 앱 서버 밖의 DB EC2에 있어 앱 컨테이너 교체와 데이터가 분리됩니다. 그래도 EBS 저장 위치, DeleteOnTermination, 백업·복원 절차를 확인해야 합니다. 앱 서버의 Docker 볼륨은 로그·인증서처럼 호스트 경로를 마운트하는 용도로만 씁니다.

### Environment Variable

- **결론**: 서버 `.env`에는 배포할 이미지 digest(`FE_IMAGE`, `BE_IMAGE`)를, 서비스별 실행 설정은 `env/backend.env`·`env/frontend.env`에 둡니다. 서비스별 설정은 배포마다 GitHub Secret 값으로 다시 씁니다.
- **이유**: 배포 스크립트는 `.env`에서 배포 대상 이미지 한 줄만 바꾸고, 서비스 설정은 KEY=VALUE 형식·중복·필수 키(BE의 DB 접속 정보 등)를 검사한 뒤 반영합니다. FE는 실행 시점 환경변수가 없어 빈 설정을 허용합니다. DB 주소·계정도 운영 정보이므로 파일 권한을 `600`으로 제한합니다.

### Secret 관리

- **결론**: 배포용 애플리케이션 시크릿은 앱 저장소의 GitHub Secrets(BE `DEV_APP_ENV`·`PROD_APP_ENV`, FE `APP_ENV`), AWS 알림의 Discord 웹훅은 Secrets Manager(`damuckja/v1/discord-webhook`)로 관리합니다.
- **이유**: 각각 배포 파이프라인과 Lambda 실행 중 조회라는 사용 목적에 맞춘 선택입니다. 환경별 Secret 이름을 따로 골라 운영 값이 없을 때 개발 값으로 대체되지 않게 합니다. Compose secrets는 컨테이너에 파일로 전달하는 기능이며 외부 시크릿 저장소와 역할이 다릅니다. GitHub Secrets도 실행 서버에 전달된 값의 노출·자동 로테이션을 해결하지 못합니다.
- **운영 기준**: 최소 권한, 출력 로그 마스킹, 서버 파일 권한, 키 교체를 별도로 관리합니다. 클라우드 이전 시 Secrets Manager 연동을 변경해야 하지만 시크릿 자체가 무용지물이 되는 것은 아닙니다.

### Container Restart 정책

- **결론**: dev는 no, prod는 unless-stopped를 사용합니다.
- **이유**: dev는 종료 원인을 먼저 조사하고, prod는 명시적으로 중지하지 않은 컨테이너를 재시작합니다. unless-stopped는 정상·비정상 종료 코드만으로 구분하지 않습니다. 수동으로 중지한 컨테이너는 Docker 재시작 후에도 자동 시작하지 않습니다.
- **한계**: unhealthy 상태만으로 컨테이너를 자동 재시작하거나 EC2 장애를 복구하지는 않습니다. [Docker 재시작 정책](https://docs.docker.com/engine/containers/start-containers-automatically/)

### Image Registry
- **상황**: Docker 이미지를 어디에 보관하고 배포할지 결정해야 함.
- **결론**: **GHCR** 비공개 패키지(`ghcr.io/100-hours-a-week/ktb4-5th-be`, `ktb4-5th-fe`). 서버는 배포할 때 GitHub Actions의 단기 토큰(`GITHUB_TOKEN`, `packages: read`)으로 로그인해 이미지를 받고, 배포가 끝나면 임시 인증 정보를 지웁니다.
- **이유**: 프로젝트가 GitHub를 주로 사용할 것이 확정적이라, GitHub Actions를 잘 활용할 수 있는 GHCR을 선택. 앱 저장소는 Public이지만 패키지는 비공개로 두고 받는 쪽이 인증합니다. 서버에 장기 인증 정보를 남기지 않으려고 배포마다 임시 Docker 설정 폴더에 로그인합니다.

### Image Tag 정책

- **결론**: Git SHA 태그로 빌드 출처를 식별하고 실제 배포는 이미지 digest로 고정합니다.
- **이유**: 태그는 다시 지정될 수 있으므로 동일 이미지 보장을 위해 digest를 사용합니다. 배포 스크립트는 `ghcr.io/...@sha256:<64자리>` 형식이 아니면 거부하고, 복구도 이전 컨테이너의 digest로 합니다.

## 4. 현재 구조의 한계 정의

1. **가용성**: 앱 서버·nginx·DB에 단일 장애점이 있으며 단일 컨테이너 교체 중 중단이 발생합니다(2026-10-04 부하 테스트에서 BE 교체 중 502 1건).
2. **확장성**: Compose와 고정 인스턴스 구성에는 자동 수평 확장이 없습니다.
3. **리소스**: 2 GiB 서버 한 대에 DB까지 두면 메모리가 부족해 BE가 OOM 종료됐고(2026-09-24), DB를 분리한 뒤에도 앱 서버 여유가 작습니다. 크레딧·메모리·부하를 측정해 사양을 결정해야 합니다.
4. **데이터**: DB EC2 장애·종료 시 복구는 EBS 보존과 백업에 달려 있습니다. 별도 DB EC2만으로 데이터 안전이 보장되지는 않습니다.
5. **보안**: SSH 22번을 배포에 쓰므로 키 관리와 호스트 검증에 기대고, 컨테이너 간 접근 통제가 없습니다. Git ignore만으로 보호할 수 없습니다.
6. **시크릿**: 서버에 전달된 설정의 권한 관리와 로테이션은 별도 운영 과제입니다.
7. **관측성**: CloudWatch 모니터링과 Discord 장애 알림은 동작하지만, Agent 중단 알람이 없고 실제 사용자 장애 감지 범위는 SLO 알람에 한정됩니다. 알림 자체가 자동 복구를 의미하지 않습니다.
8. **결합도**: 알림 배치와 API가 BE 자원을 공유하므로 부하 증가 시 분리가 필요합니다.
9. **서버 교체**: 서버 설정(Compose·nginx 파일, 인증서, 로그 폴더 권한, Agent)과 IP에 묶인 설정(DB 계정 호스트, known_hosts)을 서버마다 손으로 맞춰야 합니다.

이 한계를 바탕으로 [Compose 한계 분석 및 ECS 전환 검토](../v1%20Compose%20한계%20분석%20및%20ECS%20전환%20검토.md)를 거쳐 v2(ECS)로 옮겼습니다. dev는 2026-10-09~11, prod는 2026-10-19에 전환했습니다. v1 앱 서버와 prod DB EC2는 지우고 스냅샷만 남겼고, dev DB EC2는 v2 dev가 이어서 사용했습니다. v2 설계는 [4단계 문서](ktb4-cloud-wiki-v2.md)를 따릅니다.
