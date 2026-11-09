#!/usr/bin/env bash
# v2 prod SSM 값으로 prod SealedSecret을 만듭니다. 관리자 PC Git Bash(또는 WSL)에서 실행합니다(stage/seal-secrets.sh와 같은 방식).
# 값은 화면에 출력하지 않고, 평문 임시 폴더(Git 제외 private/ 아래)는 끝나면 지웁니다. 암호문(secrets/*.yaml)만 Git에 올립니다.
# DB는 v3 prod DB EC2(prod-db 스택)이고 계정·비밀번호는 v2 prod와 같습니다.
# v2 prod BE의 SPRING_DATASOURCE_URL은 SSM이 아니라 Task 환경 변수(infra/v2/ecs/prod/backend.json)라 여기서 주소만 바꿔 만듭니다.
set -euo pipefail
export MSYS_NO_PATHCONV=1
PY=$(command -v python3 || command -v python)
cd "$(git rev-parse --show-toplevel)"
K=(--kubeconfig infra/v3/private/kubeconfig)
DB_HOST=$(aws cloudformation describe-stacks --stack-name dameokja-v3-prod-db --query "Stacks[0].Outputs[?OutputKey=='DbPrivateIp'].OutputValue" --output text)
[[ $DB_HOST =~ ^10\.20\.[0-9]+\.[0-9]+$ ]] || { echo "prod DB 주소를 찾지 못했습니다: $DB_HOST" >&2; exit 1; }
tmp=$(mktemp -d -p infra/v3/private); chmod 700 "$tmp"; trap 'rm -rf "$tmp"' EXIT
mkdir -p v3/environments/prod/secrets

# ssm <secret> <SSM 경로> <키...>: 키마다 파일 하나(값 끝 줄바꿈 없음)
ssm() {
  local dir=$tmp/$1 path=$2; shift 2; mkdir -p "$dir"
  for k; do printf %s "$(aws ssm get-parameter --with-decryption --name "$path/$k" --query Parameter.Value --output text)" > "$dir/$k"; done
}
seal() {
  local name=$1; shift
  kubectl create secret "$@" "$name" -n prod --dry-run=client -o yaml \
    | kubeseal --cert v3/platform/sealed-secrets-cert.pem --format yaml > "v3/environments/prod/secrets/$name.yaml"
}

# BE: v2 prod Task Definition의 secrets 목록에서 AI_BASE_URL·SPRING_DATA_REDIS_*(ConfigMap이 정함)를 뺀 키입니다.
ssm be-secrets /dameokja-v2-prod \
  KTB_REFRIGERATOR_ID SERVER_PORT SERVER_FORWARD_HEADERS_STRATEGY \
  SPRING_DATASOURCE_USERNAME SPRING_DATASOURCE_PASSWORD DB_USERNAME DB_PASSWORD \
  SPRING_JPA_HIBERNATE_DDL_AUTO JWT_SECRET AUTH_COOKIE_SECURE PUSH_AUTH_ENCRYPTION_KEY \
  PUSH_AUTH_ENCRYPTION_KEY_VERSION PUSH_VAPID_PRIVATE_KEY PUSH_VAPID_PUBLIC_KEY \
  PUSH_VAPID_KEY_VERSION SENTRY_AUTO_INIT OTEL_TRACES_EXPORTER OTEL_METRICS_EXPORTER \
  OTEL_LOGS_EXPORTER SENTRY_ENVIRONMENT S3_BUCKET S3_REGION S3_UPLOAD_PREFIX \
  S3_PRESIGNED_URL_TTL S3_MAX_UPLOAD_BYTES S3_ALLOWED_CONTENT_TYPES AI_INTERNAL_API_KEY \
  AI_CONNECT_TIMEOUT AI_READ_TIMEOUT ANALYSIS_JOB_TTL KAKAO_REST_API_KEY KAKAO_CLIENT_SECRET \
  KAKAO_REDIRECT_URI OAUTH_FRONTEND_BASE_URL
# v2 prod에 없던 키는 dev 값을 씁니다(SENTRY_ENVIRONMENT로 환경을 나눔). dev SSM은 v3 경로로 옮겨졌습니다(#309).
ssm be-secrets /dameokja-v3-dev SENTRY_DSN
# v2 prod Task 환경 변수의 URL에서 호스트(db.prod.dameokja.internal)만 v3 prod DB로 바꿉니다.
printf %s "jdbc:mysql://$DB_HOST:3306/app?serverTimezone=Asia/Seoul&characterEncoding=UTF-8&connectTimeout=3000&socketTimeout=10000" > "$tmp/be-secrets/SPRING_DATASOURCE_URL"

# AI: CALLBACK_URL·LOG_DIR은 ConfigMap이 정합니다. DATABASE_URL은 호스트만 바꿉니다.
ssm ai-secrets /dameokja-v2-prod-ai \
  APP_ENV INTERNAL_API_KEY LOG_LEVEL LOG_RETENTION_DAYS ANALYSIS_QUEUE_MAXSIZE \
  ANALYSIS_RESULT_TTL_SECONDS ANALYSIS_RESULT_MAX_ENTRIES IMAGE_SOURCE S3_REGION \
  S3_ALLOWED_PREFIX OLLAMA_MODEL CALLBACK_TIMEOUT_SECONDS CALLBACK_MAX_ATTEMPTS S3_BUCKET \
  RUNPOD_API_KEY RUNPOD_POD_NAME_PREFIX RUNPOD_WORKER_PORT \
  REMOTE_INFERENCE_API_KEY REMOTE_INFERENCE_TIMEOUT_SECONDS CALLBACK_API_KEY DATABASE_URL
# BE→AI 호출 키가 셋 모두 같아야 합니다(stage에서 401이었던 원인). 다르면 멈춥니다.
cmp -s "$tmp/be-secrets/AI_INTERNAL_API_KEY" "$tmp/ai-secrets/INTERNAL_API_KEY" && cmp -s "$tmp/be-secrets/AI_INTERNAL_API_KEY" "$tmp/ai-secrets/CALLBACK_API_KEY" \
  || { echo "v2 prod SSM의 AI_INTERNAL_API_KEY·INTERNAL_API_KEY·CALLBACK_API_KEY가 서로 다릅니다" >&2; exit 1; }
# v2 prod에 없던 키는 stage와 같이 dev 값을 씁니다.
ssm ai-secrets /dameokja-v3-dev-ai TAVILY_API_KEY
sed -i -E "s#@[^:/@]+:5432/#@$DB_HOST:5432/#" "$tmp/ai-secrets/DATABASE_URL"
grep -q "@$DB_HOST:5432/" "$tmp/ai-secrets/DATABASE_URL" || { echo "DATABASE_URL 호스트를 바꾸지 못했습니다" >&2; exit 1; }

ssm fe-secrets /dameokja-v2-prod-fe DISCORD_BUG_REPORT_WEBHOOK_URL
# Redis 비밀번호는 prod-data 설치 때 클러스터에 직접 만든 값을 그대로 옮깁니다.
mkdir -p "$tmp/cloud-secrets"
kubectl "${K[@]}" -n prod get secret cloud-secrets -o jsonpath='{.data.REDIS_PASSWORD}' | base64 -d > "$tmp/cloud-secrets/REDIS_PASSWORD"
[[ -s $tmp/cloud-secrets/REDIS_PASSWORD ]] || { echo "prod cloud-secrets(REDIS_PASSWORD)가 없습니다. prod-data 설치 단계를 먼저 하세요." >&2; exit 1; }
aws secretsmanager get-secret-value --secret-id dameokja-v2-dev/ghcr --query SecretString --output text \
  | "$PY" -c "import base64,json,sys; c=json.load(sys.stdin); a=base64.b64encode(f\"{c['username']}:{c['password']}\".encode()).decode(); json.dump({'auths': {'ghcr.io': {'auth': a}}}, open(sys.argv[1], 'w'))" "$tmp/ghcr.json"

seal be-secrets generic --from-file="$tmp/be-secrets"
seal ai-secrets generic --from-file="$tmp/ai-secrets"
seal fe-secrets generic --from-file="$tmp/fe-secrets"
seal cloud-secrets generic --from-file="$tmp/cloud-secrets"
seal ghcr-pull generic --type=kubernetes.io/dockerconfigjson --from-file=.dockerconfigjson="$tmp/ghcr.json"
ls v3/environments/prod/secrets
