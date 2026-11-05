#!/usr/bin/env bash
# stage 앱·Redis·Kafka를 켜고 끕니다. v3-stage-power.yml이 SSM으로 시스템 Node에서 root로 실행합니다.
# DB EC2 시작·중지는 Workflow가 따로 합니다(켤 때는 이 스크립트 전에, 끌 때는 뒤에).
# 사용: power.sh start|stop
set -euo pipefail
action=${1:?start 또는 stop}
REGION=ap-northeast-2
# Replica는 v3/environments/stage/*.yaml과 같게 둡니다(Argo stage 앱은 수동 sync라 되돌리지 않음).
APPS="fe-blue=2 be-blue=2 ai-api-blue=1"
KUBECTL_VERSION=v1.35.0
KUBECTL_SHA256=a2e984a18a0c063279d692533031c1eff93a262afcc0afdc517375432d060989

work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
curl -fsSL -o "$work/kubectl" "https://dl.k8s.io/release/$KUBECTL_VERSION/bin/linux/amd64/kubectl"
echo "$KUBECTL_SHA256  $work/kubectl" | sha256sum -c - >/dev/null
chmod +x "$work/kubectl"
# Node 역할로 stage 전용 역할을 assume하고, 그 자격 증명으로 kubeconfig를 만듭니다.
account=$(aws sts get-caller-identity --query Account --output text)
read -r AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN < <(aws sts assume-role   --role-arn "arn:aws:iam::$account:role/dameokja-v3-stage-power-kubectl" --role-session-name "stage-power-$action"   --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken]' --output text)
export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
export KUBECONFIG=$work/kubeconfig
aws eks update-kubeconfig --region "$REGION" --name dameokja-v3 >/dev/null
k() { "$work/kubectl" -n stage "$@"; }

ready() { k get "$1" -o jsonpath='{.status.readyReplicas}' 2>/dev/null; }

if [[ $action == stop ]]; then
  for a in $APPS; do k scale deploy "${a%%=*}" --replicas=0; done
  # 조정을 멈춰야 Operator가 브로커를 다시 만들지 않습니다. PVC·cluster ID는 남습니다.
  k annotate kafka stage-kafka strimzi.io/pause-reconciliation=true --overwrite
  sleep 10
  k scale deploy stage-kafka-entity-operator --replicas=0 || true
  k delete strimzipodset stage-kafka-dual-role --ignore-not-found
  k scale sts redis --replicas=0
  for i in $(seq 1 60); do [[ -z $(k get pods -o name) ]] && break; sleep 10; done
  k get pods
  echo "stage 꺼짐(빈 stage Node는 Karpenter가 5분 뒤 정리)"
  exit 0
fi

[[ $action == start ]] || { echo "start 또는 stop만 됩니다: $action" >&2; exit 1; }
k scale sts redis --replicas=1
# annotation을 지우면 Operator가 PodSet과 Entity Operator를 다시 만듭니다.
k annotate kafka stage-kafka strimzi.io/pause-reconciliation-
for i in $(seq 1 90); do
  st=$(k get kafka stage-kafka -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}')
  [[ $st == True ]] && break
  sleep 20
done
[[ $st == True ]] || { echo "Kafka가 Ready가 되지 않았습니다" >&2; k get pods; exit 1; }
# 꺼진 동안 main에 반영된 stage 이미지(v3-stage-image.yml)로 띄우려고 Argo stage 앱을 sync합니다.
for app in stage-secrets stage-be stage-ai-api stage-fe; do
  "$work/kubectl" -n tool patch application "$app" --type merge -p '{"operation":{"sync":{"revision":"main"}}}'
done
for a in $APPS; do k scale deploy "${a%%=*}" --replicas="${a#*=}"; done
for i in $(seq 1 60); do
  ok=1; for a in $APPS; do [[ $(ready "deploy/${a%%=*}") == "${a#*=}" ]] || ok=; done
  [[ $(ready sts/redis) == 1 ]] || ok=
  [[ -n $ok ]] && break; sleep 10
done
k get pods
[[ -n $ok ]] || { echo "앱이 Ready가 되지 않았습니다" >&2; exit 1; }
echo "stage 켜짐"
