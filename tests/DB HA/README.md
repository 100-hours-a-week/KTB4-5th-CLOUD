# DB HA 개선 효과 테스트 설계

관련 이슈: #120(prod DB Multi-AZ + Orchestrator 고도화). 부하를 키우면서 **장애가 시작되는 시점**과 **자동 장애 대응이 어떻게 동작하는지**를 데이터로 남긴다.

## 1. 목적

- v2 가용성 목표 99.9%(월 오류 예산 약 43분)를 지금 구성으로 지킬 수 있는지, 고도화로 얼마나 나아지는지 숫자로 확인한다.
- 10/19 전환에서 본 문제(DB 전환 78ms인데 BE 주소 변경·재배포 때문에 사용자 쓰기 실패 약 5분)가 고정 DNS로 줄어드는지 확인한다.

## 2. 현재 자동 장애 대응 (main ae51fd4 `infra/v2/prod.yaml` 기준)

| 계층 | 자동 대응 | 비고 |
| --- | --- | --- |
| 앱(ECS) | 있음 | 태스크 2~4개, CPU 60% Target Tracking, ALB·컨테이너 헬스 체크로 태스크 교체, 배포 circuit breaker |
| 앱 서버(EC2) | 있음 | ASG 2~4대 |
| DB(MySQL) | **없음** | EC2 1대(`AWS::EC2::Instance`), Replica·Orchestrator·EC2 자동 복구 알람 없음. mysqld 프로세스 재시작은 systemd 설정에 달림(미확인) |

DB 장애에 대한 자동 대응이 없으므로 **스트레스 테스트는 #120 적용 후에 한다.** 지금은 점진 부하 테스트만 한다.

## 3. 테스트 종류

| 테스트 | 시점 | 부하 형태 | 얻는 데이터 |
| --- | --- | --- | --- |
| 점진 부하 (T1) | 지금(고도화 전) | 10 RPS에서 시작해 3분마다 +20 RPS, 오류가 날 때까지 | 장애 시작 시점(RPS), 처음 무너지는 곳(앱·커넥션 풀·DB), 앱 오토스케일링 반응 시간 |
| 스트레스 (T2) | #120 적용 후 | 아침 버스트 스파이크(10 → T1 한계의 1.5배를 30초 만에) 유지 중 DB 장애 주입 | 자동 failover 시간, 쓰기 실패 시간, 5xx, RTO·RPO |
| 점진 부하 재측정 (T1') | #120 적용 후 | T1과 같음 | 고도화 전후 한계 RPS 비교 |

부하 기준: 서비스 특성은 아침 알림 직후 버스트(설계 목표 Peak 약 95 RPS, CCU 약 7,200, 10분 창)다.

### T1 점진 부하

- `ramp.js`. k6 `ramping-arrival-rate`로 요청 수를 단계별로 올린다. 요청 1건 = 재고 조회 80% / 재고 등록 20%(`WRITE_RATIO`). 로그인은 VU마다 처음 한 번만 하고, access token(15분)은 10~14분 사이 임의 시각에 refresh token으로 갱신한다(401이 나도 갱신). VU 상한 `MAX_VUS`(기본 300): 응답이 느려질 때 VU가 늘며 새 로그인이 몰리는 것을 막는다(2026-10-27 결과 참고).
- 재고 등록은 이름 `hatest`, 유통기한 2029-01-01~03-01 중 하나(BE는 오늘부터 4년 이내만 허용)(60가지)로 보내 대부분 기존 항목에 수량이 합쳐진다(UPDATE). 항목당 수량 상한 100, 냉장고 한도 1,500을 넘지 않는다(300 RPS까지 가도 계정당 항목 60개, 항목당 평균 약 27). 끝나면 시험 계정의 `hatest` 항목을 지운다.
- 각 단계 3분 유지. 단계마다 p95·5xx·Hikari 활성/대기 커넥션·ECS 태스크 수·DB CPU를 기록한다.
- **장애 시작 시점**: 5xx 1% 초과 또는 p95 1초 초과가 1분 넘게 이어진 첫 단계.
- 그 단계에서 3분 더 유지해 오토스케일링으로 회복되는지 보고, 회복되지 않으면 중단한다.

### T1-DB DB 한계 부하

- `db-stress.js`. T1(`ramp.js`)은 요청이 가벼워 DB CPU 약 40%에서 부하 발생기·로그인이 먼저 한계였다. 그래서 요청마다 DB를 많이 쓰는 API만 섞어 DB가 먼저 버티지 못하는 지점을 찾는다.
- 요청 1건 = 읽기 45%(목록 35%: 50개 + 개수 + 정렬, 절반은 앞뒤 % 검색어, 30%는 다음 페이지 / 마이페이지 10%) + 쓰기 55%(등록 17.5%: 1행 INSERT / 소비 17.5%: 상세 ETag 후 1행 DELETE / 수정 20%: 상세 ETag 후 수량 UPDATE).
- 측정마다 같은 조건에서 시작한다. 측정 전 `db-reset.js`가 시험 냉장고 50개의 항목을 모두 지우고 같은 내용의 항목 1,000개(`ITEMS`)를 다시 넣은 뒤 개수를 확인한다. 등록과 소비 비율을 같게 둬 측정 중에도 항목 수가 거의 그대로다.
- 가득 찬 냉장고의 등록 409(`fridge_full`)와 다른 VU가 먼저 바꾼 412(`stale_version`)는 실패로 세지 않는다.
- 2026-10-28 이전 버전은 등록이 409로 끝나고 일괄 폐기 API가 유통기한 지난 항목만 지워, 실제 쓰기가 거의 없었다(결과.md 참고).
- 50 RPS에서 2분마다 +25, 400 RPS까지(약 30분). 누적 실패율 30%를 넘으면 자동 중단한다.
- 앱 커넥션 풀(태스크당 10)이 DB 동시 쿼리 수를 막으므로, DB가 프로세스째 죽기보다 DB CPU 포화 → 쿼리 지연 → 풀 대기·타임아웃(30초) → 5xx 순서로 무너질 것으로 본다. DB(t3.medium)는 AI용 PostgreSQL과 같은 서버라 AI에도 영향이 간다.

### T2 스트레스 + DB 장애 주입

| ID | 장애 | 주입 방법 | 기대 |
| --- | --- | --- | --- |
| F1 | mysqld 프로세스 죽음 | `kill -9 mysqld` | 재시작 또는 승격, 1분 이내 |
| F2 | Primary 인스턴스 중단(AZ 장애 가정) | EC2 stop | Standby 자동 승격, 1분 이내 |
| F3 | Primary 네트워크만 단절 | Primary에서 iptables로 3306 차단 후 해제 | 승격 후 기존 Primary는 fencing으로 쓰기 거부(split-brain 없음) |
| F4 | 계획된 Primary 교체 | Orchestrator graceful takeover | 수 초(10/19 약 5분과 비교) |
| F5 | Orchestrator 인스턴스 죽음 | EC2 terminate | 서비스 영향 0, ASG가 복구 |

스파이크 유지 1분 뒤 장애를 주입하고, 복구 뒤 3분 관찰한다. 순서는 영향이 작은 것부터 F4 → F5 → F1 → F3 → F2, 앞 단계가 기준을 못 넘으면 멈춘다.

## 4. 측정 도구

| 도구 | 하는 일 | 보는 지표 |
| --- | --- | --- |
| k6 | 위 부하 형태, 쓰기·읽기 API 따로 집계 | 5xx 수·비율, p95·p99, 타임아웃, 쓰기 실패 구간 |
| DB 쓰기 프로브 (T2) | 0.1초마다 `hatest` 스키마에 일련번호 INSERT, 커밋 응답 받은 번호를 로컬에 기록 (`tests/db-migration/migtest.py` 방식) | RTO, RPO |
| CloudWatch | ALB 5xx·응답 시간, ECS 태스크 수·CPU, HikariCP 지표, DB CPU | 서버 쪽 원인 확인 |

### 실행 위치

| 테스트 | 위치 | 이유 |
| --- | --- | --- |
| T1·T1' | 사용자 PC(WSL)의 k6 | 비용 0. 수백 RPS는 PC 한 대로 충분. 집~서울 리전 왕복 지연이 섞이므로 서버 응답 시간은 ALB `TargetResponseTime`으로 판단. 목표 RPS를 못 내면 k6 `dropped_iterations`에 나타남 |
| T2 | prod VPC 안 임시 EC2(t3.small, IaC로 생성하고 테스트 후 삭제) | 프로브가 사설 서브넷 DB에 직접 붙어야 함. PC에서 SSM 포트 포워딩을 쓰면 failover 때 터널이 같이 끊겨 RTO가 오염됨 |

## 5. 지표 정의

- **한계 RPS (T1)**: 장애 시작 직전 단계의 RPS.
- **쓰기 실패 시간**: k6 쓰기 요청의 첫 실패 ~ 연속 성공 복구.
- **RTO**: 프로브 마지막 성공 ~ 새 Primary 첫 성공. 감지·승격·DNS 전파·커넥션 재연결로 나눠 기록.
- **RPO**: 커밋 확인된 프로브 번호 중 장애 후 Primary에 없는 개수.
- **split-brain (F3)**: 기존·새 Primary에 동시에 쓰기가 들어간 기록 수.

## 6. 합격 기준 (#120 적용 후)

- T1' 한계 RPS가 T1보다 낮아지지 않는다.
- F1·F2·F3 쓰기 실패 60초 이하, 사람 개입 없음. F4 10초 이하. F5 서비스 영향 0.
- 유실 0건이 목표. 비동기 복제라 유실이 나오면 개수를 기록하고 semi-sync 도입을 판단한다.
- split-brain 0건.

## 7. 환경과 안전장치

- **prod**에서 한다(비용 때문에 stage를 켜지 않음, 2026-10-26 결정). 실제 사용자에게 영향이 간다.
- 새벽에 하고 팀에 미리 공지한다. 시작 전 DB EBS 스냅샷과 `mysqldump`를 만든다.
- 시험 데이터는 시험 계정과 `hatest` 스키마에만 쓰고 끝나면 지운다.
- 중단: T1은 회복되지 않는 장애 단계에서, T2는 장애가 5분 넘게 이어지면 바로 부하를 멈추고 수동 복구한다.
- 장애 주입·AWS CLI 실행 전 대상·명령·영향·되돌리는 방법을 공유하고 허락을 받는다.
- #120은 stage 리허설 없이 prod에 바로 적용하므로, 적용 PR의 change set 미리보기에서 기존 DB Replacement가 없는지 확인한다.

## 8. 미리 확인할 것

- prod mysqld의 systemd `Restart` 설정(SSM으로 읽기만).
- BE HikariCP `connectionTimeout`·`maximumPoolSize`·`maxLifetime`(BE 팀 범위). 10/20에 기본 풀 10 고갈로 p99 50초가 나온 적 있음.
- k6 쓰기 API(재고 등록), 시험 계정 수(높은 RPS에 맞게), 시험 데이터 삭제 방법.
- prod 시험 계정 50개 생성(`provision_accounts.py`는 dev 주소 고정이라 대상 주소를 바꿔야 함).

## T1 실행

prod 시험 계정 파일이 필요하다(`../k6/private/prod-accounts.json`, Git 제외, `../k6/ACCOUNTS.md`와 같은 형식에 `baseUrl: https://dameokja.com`). 50개면 충분하다(VU가 계정을 나눠 씀).

```bash
cd "tests/DB HA" && mkdir -p private
k6 run -e ACCOUNTS=../k6/private/prod-accounts.json --out csv=private/ramp.csv ramp.js
```

조정: `START`(10), `STEP`(20), `STEP_SECONDS`(180), `MAX`(300), `WRITE_RATIO`(0.2), `MAX_VUS`(300). 300 RPS까지 가면 약 45분이다. 단계별 결과는 csv의 `step` 태그로 나눠 본다.

DB 한계 부하는 `db-stress.js`로 같은 방식으로 실행한다. 기본값 `START`(50), `STEP`(25), `STEP_SECONDS`(120), `MAX`(400), `MAX_VUS`(300).

## 파일

- `README.md`: 이 설계
- `ramp.js`: T1 점진 부하
- `db-stress.js`: T1-DB DB 한계 부하
- `db-reset.js`: T1-DB 측정 전 시험 냉장고 초기화
- `stress.js`: T2 스파이크 (예정)
- `probe.py`: DB 쓰기 프로브(run / verify) (예정)
- `결과.md`: 측정 결과
