#!/usr/bin/env bash
# prod 앱·Redis·Kafka를 켜고 끕니다. v3-prod-power.yml이 SSM으로 시스템 Node에서 root로 실행합니다.
# DB EC2 시작·중지는 Workflow가 따로 합니다(켤 때는 이 스크립트 전에, 끌 때는 뒤에).
# stage(v3/environments/stage/power.sh)와 다른 점:
#  - Argo prod 앱은 자동 동기화라 끌 때 자동 동기화를 끄고, 켤 때 되돌립니다(꺼진 동안 main 커밋이 AI를 1대로 되살리지 않게).
#  - 대상 Deployment는 활성 색상(Service be·fe·ai-api의 selector)에서 고릅니다.
#  - BE·FE는 HPA가 있어 켤 때 최소 대수로 올리면 그 뒤로는 HPA가 조정합니다.
# 사용: power.sh start|stop
set -euo pipefail
action=${1:?start 또는 stop}
REGION=ap-northeast-2
# 켤 때 대수. FE·BE는 v3/environments/prod/*.yaml의 autoscaling.minReplicas, AI는 colors.<활성 색상>.replicas와 같게 둡니다.
APPS="fe=2 be=2 ai-api=1"
ARGO_APPS="prod-fe prod-be prod-ai-api"
# v3/argocd/prod-applications.yaml의 syncPolicy.automated와 같게 둡니다.
ARGO_AUTOMATED='{"prune":false,"selfHeal":false}'
KUBECTL_VERSION=v1.35.0
KUBECTL_SHA256=a2e984a18a0c063279d692533031c1eff93a262afcc0afdc517375432d060989

work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
curl -fsSL -o "$work/kubectl" "https://dl.k8s.io/release/$KUBECTL_VERSION/bin/linux/amd64/kubectl"
echo "$KUBECTL_SHA256  $work/kubectl" | sha256sum -c - >/dev/null
chmod +x "$work/kubectl"
# Node 역할로 prod 전용 역할을 assume하고, 그 자격 증명으로 kubeconfig를 만듭니다.
account=$(aws sts get-caller-identity --query Account --output text)
read -r AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN < <(aws sts assume-role --role-arn "arn:aws:iam::$account:role/dameokja-v3-prod-power-kubectl" --role-session-name "prod-power-$action" --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken]' --output text)
export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
export KUBECONFIG=$work/kubeconfig
aws eks update-kubeconfig --region "$REGION" --name dameokja-v3 >/dev/null
k() { "$work/kubectl" -n prod "$@"; }
argo() { "$work/kubectl" -n tool patch application "$1" --type merge -p "{\"spec\":{\"syncPolicy\":{\"automated\":$2}}}"; }

ready() { k get "$1" -o jsonpath='{.status.readyReplicas}' 2>/dev/null; }
# Service가 가리키는 활성 색상의 Deployment 이름(예: be → be-blue)
declare -A DEPLOY
for a in $APPS; do
  name=${a%%=*}
  color=$(k get svc "$name" -o jsonpath='{.spec.selector.dameokja\.com/color}')
  [[ $color == blue || $color == green ]] || { echo "Service $name의 활성 색상을 찾지 못했습니다: '$color'" >&2; exit 1; }
  DEPLOY[$name]=$name-$color
done

if [[ $action == stop ]]; then
  for app in $ARGO_APPS; do argo "$app" null; done
  for a in $APPS; do k scale deploy "${DEPLOY[${a%%=*}]}" --replicas=0; done
  # 조정을 멈춰야 Operator가 브로커를 다시 만들지 않습니다. PVC·cluster ID는 남습니다.
  k annotate kafka prod-kafka strimzi.io/pause-reconciliation=true --overwrite
  sleep 10
  k scale deploy prod-kafka-entity-operator --replicas=0 || true
  k delete strimzipodset prod-kafka-dual-role --ignore-not-found
  k scale sts redis --replicas=0
  for i in $(seq 1 60); do [[ -z $(k get pods -o name) ]] && break; sleep 10; done
  k get pods
  echo "prod 꺼짐(빈 prod Node는 Karpenter가 5분 뒤부터 1대씩 정리)"
  exit 0
fi

[[ $action == start ]] || { echo "start 또는 stop만 됩니다: $action" >&2; exit 1; }
k scale sts redis --replicas=1
# annotation을 지우면 Operator가 PodSet과 Entity Operator를 다시 만듭니다(이미 켜져 있으면 지울 것이 없음).
k annotate kafka prod-kafka strimzi.io/pause-reconciliation- || true
for i in $(seq 1 90); do
  st=$(k get kafka prod-kafka -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}')
  [[ $st == True ]] && break
  sleep 20
done
[[ $st == True ]] || { echo "Kafka가 Ready가 되지 않았습니다" >&2; k get pods; exit 1; }
for a in $APPS; do k scale deploy "${DEPLOY[${a%%=*}]}" --replicas="${a#*=}"; done
for i in $(seq 1 60); do
  ok=1; for a in $APPS; do [[ $(ready "deploy/${DEPLOY[${a%%=*}]}") -ge ${a#*=} ]] || ok=; done
  [[ $(ready sts/redis) == 1 ]] || ok=
  [[ -n $ok ]] && break; sleep 10
done
k get pods
[[ -n $ok ]] || { echo "앱이 Ready가 되지 않았습니다" >&2; exit 1; }
# 자동 동기화를 되돌리면 꺼진 동안 main에 반영된 변경(v3-prod-image.yml의 이미지 등)을 Argo가 sync합니다.
for app in $ARGO_APPS; do argo "$app" "$ARGO_AUTOMATED"; done
echo "prod 켜짐"
