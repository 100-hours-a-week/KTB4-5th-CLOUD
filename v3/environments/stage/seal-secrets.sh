#!/usr/bin/env bash
# stage SSM 값(/dameokja-v3-stage*)으로 stage SealedSecret을 만듭니다. 관리자 PC Git Bash에서 실행합니다(dev/seal-secrets.sh와 같은 방식).
# 값은 화면에 출력하지 않고, 평문 임시 폴더(Git 제외 private/ 아래)는 끝나면 지웁니다. 암호문(secrets/*.yaml)만 Git에 올립니다.
# DB는 v3 stage DB EC2(stage-db 스택)이고 비밀번호는 prod와 같은 값이라 계정·비밀번호는 prod SSM에서 읽습니다.
set -euo pipefail
export MSYS_NO_PATHCONV=1
cd "$(git rev-parse --show-toplevel)"
K=(--kubeconfig infra/v3/private/kubeconfig)
DB_HOST=$(aws cloudformation describe-stacks --stack-name dameokja-v3-stage-db --query "Stacks[0].Outputs[?OutputKey=='DbPrivateIp'].OutputValue" --output text)
tmp=$(mktemp -d -p infra/v3/private); chmod 700 "$tmp"; trap 'rm -rf "$tmp"' EXIT
mkdir -p v3/environments/stage/secrets

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
seal() {
  local name=$1; shift
  kubectl create secret "$@" "$name" -n stage --dry-run=client -o yaml \
    | kubeseal --cert v3/platform/sealed-secrets-cert.pem --format yaml > "v3/environments/stage/secrets/$name.yaml"
}

# BE: AI_BASE_URL·SPRING_DATA_REDIS_*는 ConfigMap이 정합니다.
ssm be-secrets /dameokja-v3-stage \
  KTB_REFRIGERATOR_ID SERVER_PORT SERVER_FORWARD_HEADERS_STRATEGY SPRING_DATASOURCE_URL \
  SPRING_JPA_HIBERNATE_DDL_AUTO JWT_SECRET AUTH_COOKIE_SECURE PUSH_AUTH_ENCRYPTION_KEY \
  PUSH_AUTH_ENCRYPTION_KEY_VERSION PUSH_VAPID_PRIVATE_KEY PUSH_VAPID_PUBLIC_KEY \
  PUSH_VAPID_KEY_VERSION SENTRY_AUTO_INIT OTEL_TRACES_EXPORTER OTEL_METRICS_EXPORTER \
  OTEL_LOGS_EXPORTER SENTRY_ENVIRONMENT S3_BUCKET S3_REGION S3_UPLOAD_PREFIX \
  S3_PRESIGNED_URL_TTL S3_MAX_UPLOAD_BYTES S3_ALLOWED_CONTENT_TYPES AI_INTERNAL_API_KEY \
  AI_CONNECT_TIMEOUT AI_READ_TIMEOUT ANALYSIS_JOB_TTL KAKAO_REST_API_KEY KAKAO_CLIENT_SECRET \
  KAKAO_REDIRECT_URI OAUTH_FRONTEND_BASE_URL
ssm be-secrets /dameokja-v2-prod SPRING_DATASOURCE_USERNAME SPRING_DATASOURCE_PASSWORD DB_USERNAME DB_PASSWORD
# stage SSM에 없는 키는 dev 값을 씁니다(SENTRY_ENVIRONMENT로 환경을 나눔).
ssm be-secrets /dameokja-v3-dev SENTRY_DSN
sed -i -E "s#jdbc:mysql://[^:/]+:3306/#jdbc:mysql://$DB_HOST:3306/#" "$tmp/be-secrets/SPRING_DATASOURCE_URL"
grep -q "jdbc:mysql://$DB_HOST:3306/" "$tmp/be-secrets/SPRING_DATASOURCE_URL" || { echo "SPRING_DATASOURCE_URL 호스트를 바꾸지 못했습니다" >&2; exit 1; }

# AI: CALLBACK_URL·LOG_DIR은 ConfigMap이 정합니다. DATABASE_URL은 prod 값(같은 비밀번호)에서 호스트만 바꿉니다.
ssm ai-secrets /dameokja-v3-stage-ai \
  APP_ENV INTERNAL_API_KEY LOG_LEVEL LOG_RETENTION_DAYS ANALYSIS_QUEUE_MAXSIZE \
  ANALYSIS_RESULT_TTL_SECONDS ANALYSIS_RESULT_MAX_ENTRIES IMAGE_SOURCE S3_REGION \
  S3_ALLOWED_PREFIX OLLAMA_MODEL CALLBACK_TIMEOUT_SECONDS CALLBACK_MAX_ATTEMPTS S3_BUCKET \
  RUNPOD_API_KEY RUNPOD_POD_NAME_PREFIX RUNPOD_WORKER_PORT \
  REMOTE_INFERENCE_API_KEY REMOTE_INFERENCE_TIMEOUT_SECONDS CALLBACK_API_KEY
ssm ai-secrets /dameokja-v2-prod-ai DATABASE_URL
# stage SSM은 BE의 AI_INTERNAL_API_KEY와 AI의 INTERNAL_API_KEY·CALLBACK_API_KEY가 서로 달라(v2에서 옮긴 값) BE→AI 호출이 401이었습니다.
# dev·prod처럼 BE 값 하나를 세 키에 씁니다.
cp "$tmp/be-secrets/AI_INTERNAL_API_KEY" "$tmp/ai-secrets/INTERNAL_API_KEY"
cp "$tmp/be-secrets/AI_INTERNAL_API_KEY" "$tmp/ai-secrets/CALLBACK_API_KEY"
ssm ai-secrets /dameokja-v3-dev-ai TAVILY_API_KEY
# 레시피 외부 API 키(식품안전나라·한국 레시피 아카이브·기상청)는 prod SSM 한 곳에만 두고 세 환경이 같은 값을 씁니다(#340).
ssm ai-secrets /dameokja-v2-prod RECIPE_FOOD_SAFETY_API_KEY RECIPE_KOREAN_ARCHIVE_API_KEY RECIPE_KMA_API_KEY
sed -i -E "s#@[^:/@]+:5432/#@$DB_HOST:5432/#" "$tmp/ai-secrets/DATABASE_URL"
grep -q "@$DB_HOST:5432/" "$tmp/ai-secrets/DATABASE_URL" || { echo "DATABASE_URL 호스트를 바꾸지 못했습니다" >&2; exit 1; }

ssm fe-secrets /dameokja-v3-stage-fe DISCORD_BUG_REPORT_WEBHOOK_URL
# Redis 비밀번호는 stage-data 설치 때 클러스터에 직접 만든 값을 그대로 옮깁니다.
mkdir -p "$tmp/cloud-secrets"
kubectl "${K[@]}" -n stage get secret cloud-secrets -o jsonpath='{.data.REDIS_PASSWORD}' | base64 -d > "$tmp/cloud-secrets/REDIS_PASSWORD"
aws secretsmanager get-secret-value --secret-id dameokja-v2-dev/ghcr --query SecretString --output text \
  | python -c "import base64,json,sys; c=json.load(sys.stdin); a=base64.b64encode(f\"{c['username']}:{c['password']}\".encode()).decode(); json.dump({'auths': {'ghcr.io': {'auth': a}}}, open(sys.argv[1], 'w'))" "$tmp/ghcr.json"

seal be-secrets generic --from-file="$tmp/be-secrets"
seal ai-secrets generic --from-file="$tmp/ai-secrets"
seal fe-secrets generic --from-file="$tmp/fe-secrets"
seal cloud-secrets generic --from-file="$tmp/cloud-secrets"
seal ghcr-pull generic --type=kubernetes.io/dockerconfigjson --from-file=.dockerconfigjson="$tmp/ghcr.json"
ls v3/environments/stage/secrets
