#!/usr/bin/env bash
# 동시간대 테스트 냉장고 URI만 조회해 전체 사용자 지표와 시험 부하를 구분한다. AWS 자원을 변경하지 않는다.
set -euo pipefail
umask 077
: "${TEST_FRIDGE_ID:?전용 테스트 냉장고 ID 필요}" "${START_EPOCH:?UTC 시작 epoch 초 필요}" "${END_EPOCH:?UTC 종료 epoch 초 필요}" "${OUT_DIR:?새 결과 디렉터리 필요}"
[[ "$TEST_FRIDGE_ID" =~ ^[0-9]+$ && "$START_EPOCH" =~ ^[0-9]+$ && "$END_EPOCH" =~ ^[0-9]+$ ]]
(( START_EPOCH < END_EPOCH ))
mkdir "$OUT_DIR"
filter="filter uri = '/api/v1/refrigerators/$TEST_FRIDGE_ID/ingredients'"
query() {
  local label=$1 expression=$2 id response status
  id=$(aws --profile dameokja-aws-cli logs start-query --region ap-northeast-2 \
    --log-group-names /dameokja/prod/nginx-access /dameokja/v2-prod/nginx \
    --start-time "$START_EPOCH" --end-time "$END_EPOCH" --query-string "$expression" \
    --query queryId --output text)
  for ((i=0; i<60; i++)); do
    response=$(aws --profile dameokja-aws-cli logs get-query-results --region ap-northeast-2 --query-id "$id" --output json)
    status=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' <<< "$response")
    case "$status" in
      Complete) printf '%s\n' "$response" > "$OUT_DIR/$label.json"; return ;;
      Failed|Cancelled|Timeout|Unknown) echo '로그 조회 실패'; return 1 ;;
    esac
    sleep 1
  done
  echo "조회 시간 초과. 실행 중인 읽기 쿼리 ID: $id"
  return 1
}
query requests "$filter | stats count(*) as requests, pct(request_time,95) as p95_seconds by @log, bin(1m)"
query errors "$filter | filter status >= 500 | stats count(*) as errors by @log, bin(1m)"
python3 "$(dirname -- "$0")/cloudwatch-report.py" "$OUT_DIR"
