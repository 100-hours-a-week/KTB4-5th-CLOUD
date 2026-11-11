# prod 관측 데이터 기반 Pod 자원 검토

> 확인일: 2026-10-26 / 시간대: Asia/Seoul

운영 CloudWatch 데이터를 초기 자원 예산의 근거로 활용했습니다. 현재 지표는 FE·BE·nginx·CloudWatch Agent를 묶은 ECS 앱 서비스 단위이므로, FE·BE의 개별 사용량으로 해석하지 않았습니다. 기존 초기 requests·limits는 유지하고, 개별 자원 측정과 목표 부하에서의 정밀 조정은 고도화 범위로 남겼습니다.

## 1. 조회 범위

| 항목 | 값 |
|---|---|
| CloudWatch 대시보드 | `dameokja-v2-prod-infra` |
| ECS 클러스터·서비스 | `dameokja-prod` / `dameokja-prod-app` |
| 조회 기간 | 2026-10-19 16:00 이상 ~ 2026-10-26 16:10 미만 |
| 집계 간격 | 60초 |
| CPU·메모리·요청량 표본 | 각 10,090개, 요청한 구간의 분 단위 표본 수와 일치 |
| API 결과 | 모든 시계열 `Complete`, 후속 페이지 없음 |

조회 시점의 Task Definition `dameokja-prod-app:13`과 이전 버전 11·12의 자원 설정을 확인했습니다. 세 버전 모두 task-level CPU·메모리는 지정하지 않았고, 각 컨테이너의 CPU는 `0`입니다. 컨테이너별 메모리 hard limit은 다음과 같으며 memoryReservation은 없습니다.

| 컨테이너 | 메모리 limit |
|---|---:|
| nginx | 256MiB |
| FE | 768MiB |
| BE | 2,560MiB |
| CloudWatch Agent | 128MiB |
| 합계 | 3,712MiB |

## 2. 조회 결과

아래 p95는 조회한 1분 집계값들을 시간순으로 모아 계산한 분위수입니다. 요청별 응답 시간의 p95나 순간 최대 사용량과 구분합니다.

| 지표 | 전체 구간 평균 | 전체 구간 p95 | 전체 구간 최대 |
|---|---:|---:|---:|
| ECS CPUUtilization / Average | 0.427% | 0.670% | 38.994% |
| ECS MemoryUtilization / Average | 22.569% | 24.219% | 25.318% |
| ECS MemoryUtilization / Maximum | 23.291% | 25.004% | 27.398% |
| ALB RequestCount / Sum | 14.159건/분 | 32건/분 | 2,294건/분 |

전체 구간에서 가장 요청이 많았던 1분의 평균은 `2,294 / 60 = 38.23RPS`입니다. ALB를 통과한 요청을 집계한 값이며, BE 업무 API별 요청량과 같다고 전제하지 않았습니다.

최근 24시간인 2026-10-25 16:10 ~ 2026-10-26 16:10 구간의 메모리 Maximum 최댓값은 `25.05792025862069%`입니다. 현행 메모리 예약 기준인 3,712MiB를 적용하면 다음과 같습니다.

```text
Task 메모리 최대 사용량의 근삿값
= 3,712MiB × 25.05792025862069 / 100
= 약 930.15MiB
```

이 값은 하나의 앱 Task 안에 있는 FE·BE·nginx·Agent를 합친 메모리입니다. FE 또는 BE 한 개의 사용량을 뜻하지 않습니다.

03시·08시부터 각각 15분간의 Batch 구간도 포함해 확인했습니다. 해당 구간은 210개 표본이며 메모리 Maximum 최댓값은 약 25.054%였습니다. Batch 부하가 포함된 관측값이지만 목표 트래픽에서의 자원 요구량을 검증한 결과로 사용하지 않았습니다.

## 3. FE·BE 개별 산정의 범위

- prod의 `ECS/ContainerInsights` CPU·메모리 지표는 이번 조회에서 확인되지 않았습니다.
- `CWAgent`의 Java·Node 프로세스별 CPU·RSS 지표가 조회됐지만, 현재 남아 있는 해당 인스턴스는 `dameokja-dev-v2`입니다. 종료된 과거 인스턴스의 지표도 섞여 있어 이를 현재 prod의 FE·BE 사용량으로 사용하지 않았습니다.
- `Dameokja/v2-prod/App`에서 확인한 지표는 HikariCP 연결 관련 항목이며, BE 전체 CPU·메모리는 포함되지 않았습니다.
- ECS 서비스 CPU 사용률은 task/container CPU 예약 설정과 관련된 지표입니다. 현재 설정에는 명시적인 CPU 예약량이 없으므로, 관측 사용률을 특정 vCPU 수에 곱해 FE·BE의 CPU request로 직접 환산하지 않았습니다.
- 1분 지표는 그보다 짧은 CPU·메모리 피크를 완전히 설명하지 않습니다.

## 4. 초기 자원 판단

| 대상 | CPU request | CPU limit | 메모리 request | 메모리 limit |
|---|---|---|---|---|
| FE | 250m | 1000m | 512Mi | 1Gi |
| 통합 BE | 500m | 1000m | 1Gi | 2Gi |

FE·BE 각각 하나의 메모리 request를 합치면 1,536MiB입니다. 최근 24시간 앱 Task 전체의 관측 최대 약 930MiB를 참고하여 초기 예산에 여유를 두는 방향을 유지했습니다. 다만 현재 ECS Task와 전환 후 Pod는 구성요소·배치·메모리 집계 방식이 다를 수 있어 이 비교를 용량 보장으로 사용하지 않았습니다.

FE·BE 사이의 자원 배분과 CPU 설정은 초기 설계값으로 유지했습니다. 개별 컨테이너의 실측값으로부터 각각 산출한 값은 아닙니다. 운영 데이터가 있는 범위는 초기 예산 검토에 활용하고, 개별 사용량과 목표 약 300RPS에서의 부하 시험으로 정밀 조정하기로 했습니다. 현재 관측 요청량에 단순 배수를 곱해 목표 자원을 산정하지 않았습니다.

## 5. 재현 자료

- [조회 정의](metric-queries.json)
- CloudWatch 조회 원본 `metrics-7d.json`(약 5.5MB)은 저장소에 올리지 않습니다. [조회 조건](metric-queries.json)으로 `aws cloudwatch get-metric-data`를 다시 실행해 같은 이름으로 저장하면 `analyze.py`가 읽습니다.
- [집계 결과](summary.json)
- [집계 스크립트](analyze.py)

```powershell
python docs/observability/analysis/2026-10-26-prod-resource-sizing/analyze.py
```

AWS 조회는 읽기 전용으로 수행했으며, 운영 모니터링이나 ECS 설정은 변경하지 않았습니다.

참고: [ECS 서비스 사용률의 계산 기준](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/service_utilization.html), [ECS CloudWatch 지표](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/available-metrics.html)
