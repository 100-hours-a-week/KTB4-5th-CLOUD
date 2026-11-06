# stage smoke·HPA 부하시험 준비

2026-11-06 점검의 stage 수집 공백을 보완하는 설정과 시험 도구입니다. **이 변경을 준비하는 동안 배포·stage 기동·부하는 실행하지 않았습니다.** 실제 준비 완료 여부는 stage 기동 후 preflight와 smoke로 판단합니다.

## 준비한 변경

| 항목 | 준비 내용 |
|---|---|
| Alloy | stage taint 허용, dev/stage BE discovery, kubelet·cAdvisor·KSM·BE 15초 scrape, Karpenter scrape |
| KSM | HPA collector 및 차트가 생성하는 HPA 읽기 권한, Node 환경 라벨 |
| 이벤트 | 별도 `alloy-events` Deployment 1개로 API 이벤트 → Loki 수집. DaemonSet 중복 수집 방지 |
| stage BE | Actuator health/prometheus 노출 및 HTTP histogram 설정. 애플리케이션 코드는 수정하지 않음 |
| Grafana | 새 `dameokja-v3-stage-load-test` 화면, 환경 필터·요청·5xx 비율·p95/p99·Pod/Node 자원·requests 비율·HPA·로그·이벤트. 기존 dev BE 패널도 namespace 고정 |
| HPA | 시험 때만 적용하는 BE blue 2~6개·CPU requests의 60%, 30초에 최대 2개 증가, 축소 안정화 300초 |
| 종료 | stage `power.sh stop`은 시험 HPA부터 삭제. RBAC는 `be-load-test`의 get/delete만 추가 |
| k6 | stage 주소 고정, 고유 계정, 읽기 흐름, smoke/HPA 분리, run_id 태그, JSON 원본·summary·실행 manifest |
| 관측 | HPA/Pod/Node/NodeClaim/이벤트/EC2/ALB를 5초 조회하여 UTC·객체 시각 기록, Prometheus 원본 구간 저장 |

`run.py`를 인자 없이 실행하면 계획만 출력하며 네트워크 조회도 하지 않습니다. `--preflight`는 읽기 점검만 수행합니다. `--execute`만 부하를 발생시킵니다. stage를 자동으로 켜거나 HPA·설정을 자동 적용하는 기능은 없습니다.

## 적용 전 검토와 순서

1. 이 변경을 main에 반영하기 전에 diff와 검증 결과를 검토합니다. 기본 CLOUD checkout의 미커밋 파일을 복사·덮어쓰지 않습니다. 실행 중인 Helm values·Grafana 파일에 별도 수동 변경이 있으면 먼저 diff를 확인하고 보존합니다.
2. 기존 `infra-v3-cfn.yml`의 **observability plan → 변경 검토 → apply** 절차로 대시보드가 포함된 SSM Association 설정을 적용합니다. 데이터 볼륨·보존값은 변경하지 않습니다. Association이 설정 차이를 발견하면 모니터링 컨테이너를 재생성하므로 시험 전에 완료·healthy를 확인합니다.
3. 사설 EKS API에 연결한 관리자 세션에서 Alloy와 KSM을 아래와 같이 갱신합니다. `--reuse-values`로 기존 설치별 값을 유지하되 새 파일의 지정 항목은 덮어씁니다. stage는 계속 꺼져 있어도 됩니다.

```bash
export AWS_PROFILE=dameokja-aws-cli AWS_REGION=ap-northeast-2
export KUBECONFIG=/absolute/path/dameokja-v3-diagnosis.kubeconfig
# EKS 사설 API에 직접 닿는 호스트의 예입니다. PC 터널은 아래 연결 절차를 따릅니다.
helm get values alloy -n tool -o yaml > /secure/path/alloy-before.yaml
helm get values kube-state-metrics -n tool -o yaml > /secure/path/ksm-before.yaml
helm upgrade alloy alloy --repo https://grafana.github.io/helm-charts --version 1.15.0 \
  -n tool --reuse-values -f v3/platform/values/alloy.yaml --wait --timeout 5m
helm upgrade kube-state-metrics kube-state-metrics --repo https://prometheus-community.github.io/helm-charts --version 9.0.0 \
  -n tool --reuse-values -f v3/platform/values/kube-state-metrics.yaml --wait --timeout 5m
helm upgrade --install alloy-events alloy --repo https://grafana.github.io/helm-charts --version 1.15.0 \
  -n tool -f v3/platform/values/alloy-events.yaml --wait --timeout 5m
```

4. platform의 stage-power RBAC만 렌더링해 diff를 확인합니다. 기존 설치의 SG·역할 값을 보존하고, 예상 밖 규칙 삭제가 보이면 적용을 중단합니다. 전체 차트를 다시 적용할 필요는 없습니다.

```bash
mkdir -p tests/stage/private
helm get values platform -n tool -o yaml > tests/stage/private/platform-before.yaml
helm template platform v3/charts/platform -n tool -f tests/stage/private/platform-before.yaml \
  --show-only templates/stage-power-rbac.yaml > tests/stage/private/stage-power-rbac.yaml
kubectl diff -f tests/stage/private/stage-power-rbac.yaml
# diff 검토 뒤 적용합니다. diff의 종료 코드 1은 차이가 있다는 뜻입니다.
kubectl apply -f tests/stage/private/stage-power-rbac.yaml
```
5. stage 기동을 진행할 시점에 기존 `v3-stage-power.yml` start 절차로 DB·Kafka·Redis·앱을 켭니다. stage-be ConfigMap이 새 Actuator 설정을 반영했는지 확인하고 최소 1분 동안 scrape를 안정화합니다. Secret/이미지 설정 때문에 endpoint 접근이 안 되면 기능 코드를 바꾸지 말고 원인을 별도로 해결합니다.
6. smoke를 성공시킨 뒤 `tests/stage/be-hpa.yaml`을 적용합니다. 기존 자동 HPA와 겹치지 않게 하며 BE 활성 Service 색상은 blue여야 합니다. 시험 동안 image 배포·수동 Argo sync·stage power 작업을 실행하지 않습니다. 현재 stage-be는 수동 sync이며 preflight에서 자동 sync가 켜진 경우 거부합니다.

## 연결과 실행 호스트

부하 발생 호스트에는 Python 3.13, k6 2.3.0, AWS CLI, kubectl, NTP 동기화가 필요합니다. 관측 API는 외부에 공개하지 않고 SSM 터널로 연결합니다. 부하 발생 호스트를 monitoring/system EC2와 분리해 관측·클러스터 자원을 부하 도구가 소비하지 않게 합니다.

PC에서는 각각 별도 터미널로 터널을 유지합니다(이미 열린 포트를 중복 사용하지 않음).

```bash
aws ssm start-session --target i-04994dbed6ed19164 \
  --document-name AWS-StartPortForwardingSessionToRemoteHost \
  --parameters '{"host":["070D7A26E983A20EBF48361944AA2372.gr7.ap-northeast-2.eks.amazonaws.com"],"portNumber":["443"],"localPortNumber":["18443"]}'
aws ssm start-session --target i-0def9ccfade509ce5 \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["9090"],"localPortNumber":["19090"]}'
aws ssm start-session --target i-0def9ccfade509ce5 \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["3100"],"localPortNumber":["13100"]}'
```

EKS 터널은 kubectl/Helm에도 `--server https://localhost:18443 --tls-server-name 070D7A26E983A20EBF48361944AA2372.gr7.ap-northeast-2.eks.amazonaws.com`을 전달합니다. Python 도구는 `--server https://localhost:18443`만 지정하면 TLS 이름을 설정합니다. 원본 kubeconfig는 dameokja-v3 클러스터를 가리켜야 합니다.

NTP는 Linux `timedatectl ... NTPSynchronized=yes`, Windows `w32tm /query /status`의 동기화 계층 1~15·윤초 상태를 확인하며 로컬 CMOS 시계는 거부합니다. 조회가 거부되거나 비동기 상태이면 실행을 막습니다. stage 신규 Node의 chrony도 별도로 확인하고, Grafana는 UTC 표시를 사용합니다.

## 계정과 데이터

`accounts.example.json`을 참고하여 `tests/stage/private/accounts.json`을 만듭니다. smoke에는 고유 stage 전용 계정 2개, HPA에는 20개가 필요합니다. 각 계정은 접근 가능한 `refrigeratorId`를 가져야 합니다. 사용자 계정을 자동 생성하거나 DB를 수정하지 않습니다. 계정·결과 폴더는 gitignore 대상입니다.

각 VU가 자신의 계정으로 CSRF·로그인을 한 번 수행한 뒤 `CREATED_DESC`와 `EXPIRATION_ASC` 식재료 목록을 반복 조회합니다. **1 iteration = 목록 GET 2건**이며 최초 로그인 요청은 별도입니다. 빈 목록도 정상 응답으로 허용하므로 운영에 가까운 재고 수·분포를 가진 시험 데이터를 미리 준비하고 이를 결과에 기록합니다. 계정 생성·세션 발급 및 로그인 감사 기록은 서버에 남을 수 있습니다.

## 계획·preflight·실행

저장소 루트에서 실행합니다. 아래 실제 실행 명령은 stage 기동·설정 적용 뒤에만 사용합니다.

```bash
# 로컬 계획 확인: 기본 동작이며 부하·네트워크 조회 없음
python tests/stage/run.py --mode smoke
python tests/stage/run.py --mode hpa

# 읽기 점검: 부하 없음
python tests/stage/run.py --mode smoke --preflight \
  --kubeconfig "$KUBECONFIG" --server https://localhost:18443 \
  --prom-url http://localhost:19090 --loki-url http://localhost:13100

# smoke: 2 VU, 각 목록 두 번 조회
python tests/stage/run.py --mode smoke --execute \
  --kubeconfig "$KUBECONFIG" --server https://localhost:18443 \
  --prom-url http://localhost:19090 --loki-url http://localhost:13100

# smoke 성공 뒤 시험 HPA 적용. 직접 API에 닿는 호스트 기준 예입니다.
kubectl apply -f tests/stage/be-hpa.yaml

# HPA: 성공한 smoke manifest가 1시간 이내여야 합니다.
python tests/stage/run.py --mode hpa --execute \
  --kubeconfig "$KUBECONFIG" --server https://localhost:18443 \
  --prom-url http://localhost:19090 --loki-url http://localhost:13100 \
  --smoke-manifest tests/stage/results/실제-smoke-run-id/manifest.json \
  --target-group-arn arn:aws:elasticloadbalancing:ap-northeast-2:906462447513:targetgroup/dameokja-v3-stage-be/실제-ID
```

`--accounts`와 `--k6`로 파일·바이너리 경로를 지정할 수 있습니다. 기본 계정 파일은 `private/accounts.json`입니다. HPA 실행의 Target Group ARN은 `aws elbv2 describe-target-groups --names dameokja-v3-stage-be`로 찾습니다.

preflight는 stage Ready·활성 색상·자동 Argo sync 없음·Node별 Alloy·KSM HPA collector·metrics API·BE up·15초 샘플·CPU/메모리/requests/histogram·최근 로그를 확인합니다. HPA 모드에서는 CPU 목표·조건·HPA 시계열·ALB target health와 current/desired/Ready 모두 2인 시작 기준도 확인합니다. 준비 상태를 통과하지 못하면 k6를 시작하지 않습니다. 사전 점검은 계정 파일 형식만 검사하므로 실제 인증·냉장고 권한 검증은 smoke가 수행합니다.

## 부하와 판정

HPA 계획은 총 23분입니다. 5분 warmup 2 iterations/s → 30초 ramp 및 3분 hold 5 → 같은 방식으로 10 → 30초 ramp 및 5분 hold 20 → 30초 동안 2로 낮춤 → 5분 recovery입니다. 전체 부하 단계와 목표는 `plan.json`에 있습니다. 최대 VU 20, 최대 목록 요청 약 40/s이며 초기 로그인은 추가됩니다.

초기 p95 500ms·p99 1000ms·업무 오류율 1% 미만은 **시험용 판정값**으로 운영 SLO를 확정한 것이 아닙니다. 오류율 5% 이상은 30초 평가 지연 후 중단합니다. k6의 누적 threshold이므로 짧은 최근 오류 폭증을 즉시 탐지하는 회로는 아닙니다. 인증 실패는 즉시 중단하고 dropped iterations가 있으면 부하 목표 미달로 실패 처리합니다. 수집기가 죽거나 조회 error가 기록되면 runner가 부하를 중단합니다. CPU가 60%에 못 미쳐 HPA가 늘지 않았다면 성공으로 간주하지 말고 부하 부족으로 기록하고 계획을 다시 검토합니다. runner의 completed는 부하 threshold/수집 완료이며 HPA 확장 성공을 자동 판정하지 않습니다.

Pod 확장 시험과 Node 추가가 필요한 시험을 구분합니다. 기존 Node에 여유가 있으면 NodeClaim·EC2 증설이 없어도 정상입니다. CPU 포화 여부, DB 지연, Kafka·시스템 부하, stage NodePool 한도 CPU 16/64Gi 및 m7i.large/ap-northeast-2b를 함께 확인합니다. 임의 CPU stress·requests 축소로 트리거를 조작하지 않습니다. 20 iterations/s에서 트리거되지 않으면 바로 더 높은 부하를 실행하지 않고 계획을 검토합니다.

## 결과와 시간축

`results/<run_id>/`에 다음이 저장됩니다.

- `preflight.json`, `plan.json`, `manifest.json`: 시각·실행 호스트·설정·종료 코드·관측 완전성.
- `samples.jsonl`, `summary.json`, `k6.log`: 요청별 UTC 표본·run_id·phase·percentile·오류율·threshold.
- `timeline.jsonl`, `observer.log`: 5초 조회의 요청 시작/응답 관측 UTC와 객체의 생성/조건/이벤트 원본 시각. Pod env/Secret 값은 저장하지 않음.
- `prometheus.json`: 시험 구간을 15초 step으로 내보낸 대시보드 쿼리 원본. Loki 원본은 보존 기간 안에 run 시간 구간으로 조회. 이벤트는 Loki와 timeline에 남음.

관측 도구는 초기 snapshot을 전부 수집한 뒤 k6를 시작합니다. 종료 후 35초를 더 수집합니다. stage Pod 시계열은 15초, API 상태는 5초 관측 오차가 있으며 polling 사이에 생성·삭제된 객체는 놓칠 수 있습니다. 30초 heartbeat와 error 기록을 함께 확인합니다.

`부하 단계 변화 → HPA desired 증가 → Pod 생성 → 스케줄링 대기 → NodeClaim/EC2 생성 → Node Ready → Pod Scheduled/Ready → ALB Healthy → Pod별 성공 요청 카운터 증가`를 UID/IP로 연결합니다. 단계 변화 시각은 k6 phase_observed 표본과 scenario_started_epoch_ms를 기준으로 정렬합니다. phase_observed는 VU가 실제 반복에 진입한 시각이어서 계획 경계와 차이가 날 수 있습니다. HPA controller 내부의 정확한 임계 초과 판단 시각은 외부 수집만으로 확정할 수 없습니다.

**Pod별 최초 성공 카운터는 최초 요청 시각의 근사 증거입니다.** 정확한 신규 Pod 첫 요청 시각을 입증하려면 run_id가 남는 Pod별 HTTP access log 또는 ALB access log가 추가로 필요합니다. 현재 앱 코드를 수정하지 않고 이 부분의 정확도를 보장하지 않습니다. Loki의 Karpenter 로그·클러스터 범위 Node/NodeClaim 이벤트는 stage namespace 이벤트 화면 밖에 있을 수 있으므로 함께 조회합니다.

보존은 Prometheus 15일/15GiB 중 먼저 도달한 제한, Loki 7일입니다. 증가한 15초 수집량과 Alloy·monitoring 메모리·디스크 여유를 시험 전에 확인합니다. 각 실행 결과는 별도로 보관하고 기존 run_id 폴더는 덮어쓰지 않습니다.

## 중단·종료·원복

Ctrl+C로 runner를 중단하면 k6를 종료하고 관측 결과·manifest를 보존합니다. 비정상 종료 결과는 성공으로 처리하지 않습니다. observer는 기본 최대 2시간 후 종료하지만 HPA나 stage를 자동으로 끄지는 않습니다.

1. 부하와 관측 기록 종료를 확인하고 시험 HPA를 삭제합니다: `kubectl delete hpa be-load-test -n stage --ignore-not-found`.
2. stage를 계속 켜둘 경우 BE replica를 기준값 2로 되돌립니다: `kubectl scale deployment be-blue -n stage --replicas=2`. 꺼둘 경우 기존 stage power stop 절차를 실행합니다. stop도 시험 HPA를 먼저 삭제합니다.
3. 대상 stage Node 0·Pod 0과 DB 중지 여부를 확인합니다. HPA 삭제 자체는 이미 늘어난 replica를 줄이지 않습니다.
4. 관측 설정 원복은 저장한 values 및 이전 변경 커밋으로 합니다. 15초 수집은 설정 준비값이며 실제 수집 비용·OOM이 늘면 시험 종료 후 원래 주기를 검토합니다.

## 배포 없는 검증

`stage-load-test-validate.yml`은 PR/수동 실행에서 AWS 인증 없이 Helm 렌더링·Alloy validate·PromQL parse·k6 inspect·Python 경계 테스트·CloudFormation lint만 수행합니다. `k6 run`은 CI에 없습니다.

```bash
python -m pip install -r tests/stage/requirements-validation.txt
mkdir -p tests/stage/.cache
helm pull alloy --repo https://grafana.github.io/helm-charts --version 1.15.0 --destination tests/stage/.cache
helm pull kube-state-metrics --repo https://prometheus-community.github.io/helm-charts --version 9.0.0 --destination tests/stage/.cache
python -m unittest discover -s tests/stage -p test_preparation.py -v
python tests/stage/validate.py --alloy /absolute/path/alloy --k6 /absolute/path/k6
```

대시보드 수정은 `build_dashboard.py`에서 수행한 뒤 `python tests/stage/build_dashboard.py --write`로 CloudFormation에 반영합니다. 검증 시 생성 결과 일치를 검사합니다. 검증용 가짜 계정은 임시 폴더에서 inspect에만 사용하며 서버로 보내지 않습니다.

참고: [Kubernetes HPA](https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/), [Alloy 이벤트 수집](https://grafana.com/docs/alloy/latest/reference/components/loki/loki.source.kubernetes_events/), [k6 arrival-rate](https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/ramping-arrival-rate/), [Spring Boot 메트릭](https://docs.spring.io/spring-boot/reference/actuator/metrics.html).
