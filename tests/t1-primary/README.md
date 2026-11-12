# T1 Primary 단독 95 HTTP RPS 검증

대상 이슈: [CLOUD #329](https://github.com/100-hours-a-week/KTB4-5th-CLOUD/issues/329).

최종 실행 결과는 [2026-11-11~12 검증 보고서](github/results/결과.md)에 기록한다. 독립 검수·자원 정리 확인·결과 파일 검사를 모두 통과한 뒤 `finalize_report.py`로 최종 판정과 SHA256 목록을 확정한다.

## 범위와 해석

2026-11-11 현재 v3 stage를 실제로 조회해 사용한다. 과거 v2 재현 시험이 아니다. MySQL 8.4.13 EC2 Primary 한 대에 읽기·쓰기를 보낸다. `SHOW REPLICA STATUS`가 비어 있고 BE의 실제 JDBC 대상은 stage DB로 확인했다. 운영 DB·운영 배포를 변경하지 않는다.

이 시험은 냉장고 재고 목록과 수정 경로의 **설계 가정 시나리오 검증**이다. 설계 문서의 알림·이미지 분석 등 모든 사용자 흐름을 재현하지 않는다. 쓰기 방식도 등록·소비가 아닌 수량 수정으로 고정한다. 실제 사용자 트래픽 전체, HA 불필요, Replica의 일반적 불필요를 결론으로 삼지 않는다.

설계 95 RPS의 원래 산식은 `54,625 × 5 / 3,600 × 1.25 ≈ 94.84`이며 인증 요청도 포함한다. 이번 이슈는 인증을 제외한 업무 HTTP 요청만 95/s로 요구하므로 인증을 추가로 집계한다. 설계의 7:1~14:1은 세션 비율이며 HTTP 읽기:쓰기 비율이 아니다.

## 고정한 업무 모델

| 업무 | HTTP 호출 | 도착률 |
|---|---|---|
| 읽기 | 목록 50개 + 다음 페이지 50개 | 855 세션 / 22초 |
| 수정 | 사전 목록 + 특정 재료 상세 + PATCH 수량 변경 + 상세 재조회 검증 | 95 세션 / 22초 |

읽기:쓰기 세션은 9:1, HTTP GET:PATCH는 21:1이다. 총 업무 요청률은 `(855×2 + 95×4)/22 = 95/s`다. iteration을 HTTP 요청 수로 대신하지 않고 요청 직전·완료 시 각각 계수한다. 목록 정렬은 만료일·생성일·이름을 순환한다. 재시도·자동 redirect는 하지 않는다. HTTP 오류는 즉시 중단한다.

전용 계정 50개, 서로 다른 냉장고 50개, 냉장고별 1,000개 재료를 사용한다. 읽기 VU 100개·쓰기 VU 50개를 미리 할당하고 각 VU는 별도 로그인 세션을 사용한다. 같은 냉장고를 쓰는 VU끼리도 수정 대상 행은 겹치지 않는다. 재료는 수량 1, 여섯 카테고리 순환, 냉장:냉동 7:3, 만료일 2027-06-01이다. 실제 사용자 데이터 분포를 대표한다고 주장하지 않는다. 수정 VU는 해당 재료의 수량을 1↔2로 바꾸고 즉시 재조회한다. 회차별 첫 세 재료의 수량을 1로 복구하고 전체 건수를 다시 검증한다.

## 환경

- BE: digest `f922bd797fc700d8d85e53e635902f39eb29fb60444fdcf77ae668aa987cc083`, 서로 다른 m7i.large 노드에 두 Pod, 각각 CPU request 500m / limit 1, 메모리 request 1Gi / limit 2Gi.
- 연결 풀: BE별 Hikari 최대 10개. MySQL max_connections 151, buffer pool 128MiB, REPEATABLE-READ, binlog 활성, flush/sync 1.
- DB: stage 전용 t3.medium. 크레딧 상태는 회차 전후 CloudWatch 원본을 저장한다.
- stage Kafka 3개와 Redis도 기존 설정으로 기동한다. 두 BE 노드에는 Kafka가 함께 배치된다. 이 동거 구성을 결과 해석에 포함한다.
- 발생기: 별도 네 번째 stage m7i.large 노드의 k6 2.3.0 Pod. CPU request 1 / limit 1.5, 메모리 request 1Gi / limit 2Gi. 실제 imageID는 환경 원본에 남긴다.
- 결과 보관: 시험 전용 10Gi gp3 PVC. 1MiB 청크별 전송·재시도 후 원격/로컬 SHA256과 gzip 끝까지 읽기를 검증한다. 로컬 전송 실패 시에도 PVC 원본을 유지하며 최종 검수 후에만 PVC를 정리한다.
- 노드풀 상한 8 vCPU/32Gi, 자발적 교체 예산 0, expireAfter Never. stage HPA 없음. 측정 중 노드·Pod·이미지·재시작 변경 시 중단한다.
- 경로: 발생기 → `https://stage.dameokja.com` → 기존 stage 경로 → BE. 클라이언트 관측 지연에는 해당 네트워크 경로가 포함된다.
- 기존 Hibernate SQL 로그 수준을 변경하지 않는다.

## 시험과 판정

1. 낮은 부하 예비 시험과 95 HTTP RPS 예비 시험을 통과해야 본시험을 시작한다.
2. 사전 인증 → 5분 예열 → 30분 측정을 독립 3회 수행한다. 회차별 데이터 수량 초기화, 환경 동일성, 크레딧 상태를 기록한다.
3. 업무 요청률은 전체 구간 및 1분 창별 95 ±1%를 요구한다. 전송 계획은 회차당 약 171,000건이며 도착률 스케줄의 정수 경계와 처리 중인 업무의 경계를 원시 시각으로 검수한다.
4. 미전송(dropped_iterations) 0, 예상하지 않은 상태 코드·timeout 0, API별 p95 ≤1초, 업무 정합성 불일치 0이 합격 조건이다. p99도 별도로 보고한다.
5. 발생기 CPU·메모리·네트워크, BE CPU·메모리·연결 풀 active/pending, DB CPU·메모리·IO·연결·크레딧을 보존한다.
6. 원시 HTTP 완료 지표의 phase는 요청 시작 구간을 뜻한다. 완료 시각을 별도로 사용해 측정 창 안에서 완료된 요청과 종료 후 완료된 요청을 구분한다.
7. 3회 개별 기준을 모두 충족해야 전체 통과다. 중앙값으로 실패 회차를 숨기지 않는다.

오류·정합성 불일치는 즉시 중단한다. 누적 지연 p95 5초 이상이면 30초 이후 자동 중단한다. 관측 실패 3회 연속, DB 관측 60초 초과 지연, DB 가용 메모리 128MiB 미만, DB 디스크 여유 1GiB 미만이면 중단한다. 관리 heartbeat가 90초 끊기면 발생기 자체가 중단한다. DB 관측 프로세스는 네 시간 후 종료한다.

## 산출물과 복구

`private/`에는 인증 정보가 있으므로 전체를 커밋하지 않는다. 계정 비밀번호·쿠키·Secret을 보고서에 싣지 않는다. 원시 메트릭·관측 JSON·실패 회차·환경 정보 중 공개 가능한 부분만 검수 후 결과 디렉터리에 복사한다.

복원 기준은 `private/*-before.json`이다. 생성한 시험 데이터만 정리하고 stage BE·Redis를 0으로, Kafka reconciliation을 이전 정지 상태로 되돌린다. T1 발생기·Secret·ConfigMap·NetworkPolicy 및 시험용 stage 노드를 정리하고 stage DB를 원래의 stopped 상태로 복원한다. 운영 Deployment의 기준 상태와 비교한다. 스냅샷 `snap-0445874aad50607c7`은 정리 검증 전까지 복구 자료로 보존한다.

첫 35분 실행은 k6 자체 오류·미전송 없이 끝났으나 20초 복사 제한으로 원시 gzip이 잘려 유효 회차로 인정하지 않았다. 해당 부분 파일과 summary·관측 기록은 `private/invalid-round-1/`에 보존했다. 전송 방식과 원본 보관을 보완한 후 세 회차를 새로 실행한다.

## 실행 도구

- `prepare.js`: 순차 계정·데이터 생성, 재개, 회차별 초기화. `fixtures_ready` 개수로 실패를 검출한다.
- `load.js`: 세션당 HTTP 호출 수가 고정된 k6 시나리오. 인증은 setup과 갱신으로 분리한다.
- `supervise.sh`: 발생기 heartbeat 감시.
- `run.py`: 한 회차 실행·관측·중단·원시 자료 회수.
- `suite.py`: 회차별 초기화와 세 회차 순차 실행. 실패한 회차에서 중단한다.
- `analyze.py`: 전송·완료, 1분 창, API별 지연·오류의 원시 자료 집계.
- `db-observe.sh`, `remote.py`: 명시한 stage DB의 관측과 SSM 실행.
- `audit.py`: 요청별 독립 재집계, 원본 해시·관측 완전성, 서버 요청 카운터 대조.
- `report.py`: 비식별화 결과 묶음과 자원 그래프 생성.
- `verify_cleanup.py`: 실제 환경 복원 및 운영 Deployment 보존 검수.
- `check_artifacts.py`, `finalize_report.py`: 결과 파일 자격 증명 검사와 최종 판정 확정.

로컬 관리 프로세스가 종료되면 원격 heartbeat가 부하는 중단하지만, 로컬 자동 정리 감시도 함께 종료될 수 있다. 실제로 11월 11일 중단 회차에서 stage DB·노드가 밤새 남았고 다음 날 복원했다. `suite.py --resume`은 완료된 유효 회차를 보존하고 고정 소스 해시를 확인한 뒤 남은 회차를 이어간다. 중단 회차 원본은 재개 전에 별도로 보존해야 하며, 로컬 프로세스의 종료만으로 클라우드 정리를 완료했다고 판단해서는 안 된다.

도구 동작 근거: [k6 constant-arrival-rate](https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/constant-arrival-rate/), [k6 metrics](https://grafana.com/docs/k6/latest/using-k6/metrics/reference/), [k6 cookies](https://grafana.com/docs/k6/latest/using-k6/cookies/).
