# stage smoke·HPA 시험

부하 실행과 결과 수집에 필요한 파일만 둡니다. Python은 표준 라이브러리만 사용합니다.

| 파일 | 실행 시 역할 |
|---|---|
| `stage-read.js`, `plan.json` | stage 전용 k6 smoke·HPA 부하 계획 |
| `run.py`, `preflight.py`, `common.py` | 준비 상태 확인·실행·중단·결과 저장 |
| `observe.py`, `metric-queries.json` | HPA·Pod·Node·NodeClaim·EC2·ALB 시간축 및 Prometheus 구간 수집 |
| `be-hpa.yaml` | 시험 중에만 적용하는 BE 2~6개·CPU 60% HPA |
| `accounts.example.json` | stage 전용 계정 파일 형식 |

## 적용 순서

1. 기존 Helm 설치값·수동 변경을 확인한 뒤 `v3/platform/values/alloy.yaml`, `kube-state-metrics.yaml`을 해당 release에 반영합니다(Alloy chart 1.15.0, KSM chart 9.0.0). `alloy-events.yaml`은 단일 Deployment release `alloy-events`로 설치합니다.
2. platform의 `templates/stage-power-rbac.yaml`을 현재 설치값으로 렌더링하고 diff 검토 후 반영합니다. stage 종료가 시험 HPA를 삭제할 권한이 먼저 필요합니다.
3. observability 스택의 기존 plan/apply 절차로 dev 요청 패널의 환경 필터를 반영합니다. 새 대시보드는 추가하지 않습니다. stage 메트릭은 `metric-queries.json`으로 저장하며 Grafana Explore에서도 같은 쿼리를 쓸 수 있습니다.
4. stage를 켤 시점에 기존 stage power start 절차를 사용합니다. stage BE의 Actuator/histogram 설정 반영, 모든 Pod Ready, 15초 scrape 안정화(최소 1분)를 확인합니다.
5. 아래 preflight·smoke 성공 후 시험 HPA를 적용하고, current/desired/Ready replica가 모두 2로 안정화되면 HPA 부하를 실행합니다. 시험 중 Argo sync·이미지 배포·power 작업은 하지 않습니다.

## 실행

Python 3.13, k6 2.3.0, AWS CLI, kubectl과 NTP가 동기화된 별도 부하 발생 호스트가 필요합니다. AWS profile은 `dameokja-aws-cli`, region은 `ap-northeast-2`입니다. 사설 API용 원본 kubeconfig와 SSM 터널(EKS 18443, Prometheus 19090, Loki 13100)을 준비합니다. monitoring/system EC2에서 부하를 발생시키지 않습니다.

`accounts.example.json`을 참고하여 무시되는 `tests/stage/private/accounts.json`에 실제 stage 전용 계정을 저장합니다. smoke는 고유 계정 2개, HPA는 20개가 필요하며 각 계정에 접근 가능한 `refrigeratorId`가 있어야 합니다. 계정이나 시험 데이터를 자동 생성하지 않습니다.

저장소 루트에서 실행합니다. `--k6`, `--accounts`로 바이너리·계정 경로를 지정할 수 있습니다. 직접 사설 API에 닿는 호스트에서는 `--server`를 생략합니다.

```bash
# 기본은 계획 출력입니다. 네트워크 조회·기동·부하를 실행하지 않습니다.
python tests/stage/run.py --mode smoke

# preflight는 읽기 점검만 합니다. --execute로 바꾸면 smoke를 실행합니다.
python tests/stage/run.py --mode smoke --preflight \
  --kubeconfig /absolute/path/dameokja-v3-diagnosis.kubeconfig --server https://localhost:18443 \
  --prom-url http://localhost:19090 --loki-url http://localhost:13100

# smoke 성공 뒤 시험 HPA 적용. 아래 kubectl 예는 직접 사설 API에 닿는 호스트 기준입니다.
kubectl --kubeconfig /absolute/path/dameokja-v3-diagnosis.kubeconfig apply -f tests/stage/be-hpa.yaml

# 1시간 이내 성공한 smoke 결과와 실제 stage BE Target Group ARN을 지정합니다.
python tests/stage/run.py --mode hpa --execute \
  --kubeconfig /absolute/path/dameokja-v3-diagnosis.kubeconfig --server https://localhost:18443 \
  --prom-url http://localhost:19090 --loki-url http://localhost:13100 \
  --smoke-manifest tests/stage/results/실제-smoke-run-id/manifest.json \
  --target-group-arn arn:aws:elasticloadbalancing:ap-northeast-2:906462447513:targetgroup/dameokja-v3-stage-be/실제-ID
```

preflight는 계정 파일 형식·NTP·stage Ready·blue 활성 색상·Argo 자동 sync 없음·Node별 Alloy·이벤트 수집기·KSM·metrics API·BE scrape·자원/histogram/로그를 검사합니다. HPA 모드는 HPA 입력·조건·시계열·ALB healthy도 검사합니다. 실제 인증·냉장고 권한은 smoke에서 확인합니다. stage 기동과 설정·HPA 적용은 실행기가 자동 수행하지 않습니다.

## 부하·결과·종료

- smoke는 2 VU 각 1회, 각 반복은 식재료 목록 GET 2건입니다. 각 VU의 최초 CSRF·로그인 외에는 읽기만 수행합니다. 빈 목록도 정상으로 보므로 실제 시험 데이터 분포를 별도로 기록합니다.
- HPA는 23분 동안 2→5→10→20 iterations/s로 올리고 2로 낮춰 5분 유지합니다. 최대 VU 20·목록 요청 약 40/s입니다. CPU 60% 도달과 Node 증설을 보장하지 않으며, 기존 Node에 공간이 있으면 Node 추가가 생략됩니다.
- 초기 판정값은 업무 오류율 1% 미만·p95 500ms·p99 1000ms입니다. 누적 오류율 5% 이상은 30초 평가 지연 뒤 중단합니다. 인증 실패·관측 오류도 중단합니다. dropped iterations는 부하 목표 미달입니다. `completed`는 실행·수집 완료이며 HPA 확장 성공을 뜻하지 않습니다.
- `results/<run_id>/`에 preflight·계획·manifest·k6 원본/summary/log·관측 timeline·Prometheus 구간을 저장합니다. 계정·결과는 Git에서 제외하고 기존 run_id는 덮어쓰지 않습니다.
- 부하 단계→HPA desired→Pod 생성/스케줄링→NodeClaim/EC2→Node Ready→Pod Ready→ALB healthy→Pod 성공 카운터를 UTC·UID/IP로 연결합니다. API 조회는 5초, 메트릭은 15초이므로 최초 요청의 정확한 시각에는 별도 access log가 필요합니다. polling 사이 짧게 생성·삭제된 객체는 놓칠 수 있습니다.
- Ctrl+C는 k6를 중단하고 결과를 보존합니다. 시험 종료 후 HPA를 삭제하고, stage를 유지할 경우 BE replica를 2로 복원하거나 기존 power stop 절차를 실행합니다. HPA 삭제만으로 이미 늘어난 replica가 줄지는 않습니다. power stop도 시험 HPA부터 삭제합니다.

실제 배포·stage 기동·부하는 이 PR 준비 과정에서 실행하지 않았습니다. 보존 설정은 Prometheus 15일/15GiB 중 먼저 도달한 제한, Loki 7일입니다.
