#!/usr/bin/env bash
# dev SSM 값(/dameokja-v3-dev*)과 Secrets Manager GHCR 값으로 dev SealedSecret을 만듭니다. 관리자 PC Git Bash에서 실행합니다.
# 값은 화면에 출력하지 않고, 평문 임시 폴더(Git 제외 private/ 아래)는 끝나면 지웁니다. 암호문(secrets/*.yaml)만 Git에 올립니다.
# 키 목록은 infra/v2/ecs/dev/*.json의 secrets에서 environments/dev/*.yaml env(ConfigMap)가 정하는 키를 뺀 것입니다.
set -euo pipefail
# Git Bash가 /dameokja-v3-dev 같은 인자를 Windows 경로로 바꾸지 않게 합니다. 그래서 아래 경로는 모두 상대 경로입니다.
export MSYS_NO_PATHCONV=1
cd "$(git rev-parse --show-toplevel)"
K=(--kubeconfig infra/v3/private/kubeconfig)
tmp=$(mktemp -d -p infra/v3/private); chmod 700 "$tmp"; trap 'rm -rf "$tmp"' EXIT
mkdir -p v3/environments/dev/secrets

# ssm <secret> <SSM 경로> <키...>: 키마다 파일 하나(값 끝 줄바꿈 없음)
# 값을 변수에 먼저 받아 조회가 실패하면 set -e로 멈추고, 빈 값도 봉인하지 않습니다.
ssm() {
  local dir=$tmp/$1 path=$2 v; shift 2; mkdir -p "$dir"
  for k; do
    v=$(aws ssm get-parameter --with-decryption --name "$path/$k" --query Parameter.Value --output text)
    [[ -n $v ]] || { echo "SSM 값이 비어 있습니다: $path/$k" >&2; exit 1; }
    printf %s "$v" > "$dir/$k"
  done
}
# seal <secret> <kubectl create secret 인자...>: 클러스터에 만들지 않고 암호화만 합니다.
seal() {
  local name=$1; shift
  kubectl create secret "$@" "$name" -n dev --dry-run=client -o yaml \
    | kubeseal --cert v3/platform/sealed-secrets-cert.pem --format yaml > "v3/environments/dev/secrets/$name.yaml"
}

# BE: AI_BASE_URL·SPRING_DATA_REDIS_HOST·SPRING_DATA_REDIS_PORT는 ConfigMap이 정합니다.
# SENTRY_DSN은 v2 ECS JSON에 빠져 있었지만 SSM에 있고 BE application.yml이 읽어 넣습니다(#240).
ssm be-secrets /dameokja-v3-dev \
  KTB_REFRIGERATOR_ID SERVER_PORT SERVER_FORWARD_HEADERS_STRATEGY SPRING_DATASOURCE_URL \
  SPRING_DATASOURCE_USERNAME SPRING_DATASOURCE_PASSWORD SPRING_JPA_HIBERNATE_DDL_AUTO \
  DB_USERNAME DB_PASSWORD JWT_SECRET AUTH_COOKIE_SECURE PUSH_AUTH_ENCRYPTION_KEY \
  PUSH_AUTH_ENCRYPTION_KEY_VERSION PUSH_VAPID_PRIVATE_KEY PUSH_VAPID_PUBLIC_KEY \
  PUSH_VAPID_KEY_VERSION SENTRY_AUTO_INIT OTEL_TRACES_EXPORTER OTEL_METRICS_EXPORTER \
  OTEL_LOGS_EXPORTER SENTRY_ENVIRONMENT S3_BUCKET S3_REGION S3_UPLOAD_PREFIX \
  S3_PRESIGNED_URL_TTL S3_MAX_UPLOAD_BYTES S3_ALLOWED_CONTENT_TYPES AI_INTERNAL_API_KEY \
  AI_CONNECT_TIMEOUT AI_READ_TIMEOUT ANALYSIS_JOB_TTL KAKAO_REST_API_KEY KAKAO_CLIENT_SECRET \
  KAKAO_REDIRECT_URI OAUTH_FRONTEND_BASE_URL SENTRY_DSN
# AI: CALLBACK_URL·LOG_DIR은 ConfigMap이 정합니다. DB는 v2 AI EC2 로컬 PG에서 dev DB EC2로 옮겼습니다.
ssm ai-secrets /dameokja-v3-dev-ai \
  APP_ENV INTERNAL_API_KEY LOG_LEVEL LOG_RETENTION_DAYS ANALYSIS_QUEUE_MAXSIZE \
  ANALYSIS_RESULT_TTL_SECONDS ANALYSIS_RESULT_MAX_ENTRIES IMAGE_SOURCE S3_REGION \
  S3_ALLOWED_PREFIX OLLAMA_MODEL CALLBACK_TIMEOUT_SECONDS CALLBACK_MAX_ATTEMPTS S3_BUCKET \
  RUNPOD_API_KEY RUNPOD_POD_NAME_PREFIX RUNPOD_WORKER_PORT TAVILY_API_KEY \
  REMOTE_INFERENCE_API_KEY REMOTE_INFERENCE_TIMEOUT_SECONDS CALLBACK_API_KEY DATABASE_URL
sed -i 's#@127.0.0.1:5432/#@172.31.4.192:5432/#' "$tmp/ai-secrets/DATABASE_URL"
grep -q '@172.31.4.192:5432/' "$tmp/ai-secrets/DATABASE_URL" || { echo "DATABASE_URL 호스트를 바꾸지 못했습니다" >&2; exit 1; }
# 레시피 외부 API 키(식품안전나라·한국 레시피 아카이브·기상청)는 prod SSM 한 곳에만 두고 세 환경이 같은 값을 씁니다(#340).
ssm ai-secrets /dameokja-v2-prod-ai RECIPE_FOOD_SAFETY_API_KEY RECIPE_KOREAN_ARCHIVE_API_KEY RECIPE_KMA_API_KEY
# FE: v2에서 String 타입이지만 Webhook 주소라 Secret으로 둡니다.
ssm fe-secrets /dameokja-v3-dev-fe DISCORD_BUG_REPORT_WEBHOOK_URL
# Redis 비밀번호는 3단계에서 클러스터에 직접 만든 값을 그대로 옮깁니다.
mkdir -p "$tmp/cloud-secrets"
kubectl "${K[@]}" -n dev get secret cloud-secrets -o jsonpath='{.data.REDIS_PASSWORD}' | base64 -d > "$tmp/cloud-secrets/REDIS_PASSWORD"
# GHCR 이미지 pull. ECS repositoryCredentials 형식({username, password})을 dockerconfigjson으로 바꿉니다.
aws secretsmanager get-secret-value --secret-id dameokja-v2-dev/ghcr --query SecretString --output text \
  | python -c "import base64,json,sys; c=json.load(sys.stdin); a=base64.b64encode(f\"{c['username']}:{c['password']}\".encode()).decode(); json.dump({'auths': {'ghcr.io': {'auth': a}}}, open(sys.argv[1], 'w'))" "$tmp/ghcr.json"

seal be-secrets generic --from-file="$tmp/be-secrets"
seal ai-secrets generic --from-file="$tmp/ai-secrets"
seal fe-secrets generic --from-file="$tmp/fe-secrets"
seal cloud-secrets generic --from-file="$tmp/cloud-secrets"
seal ghcr-pull generic --type=kubernetes.io/dockerconfigjson --from-file=.dockerconfigjson="$tmp/ghcr.json"
ls v3/environments/dev/secrets
