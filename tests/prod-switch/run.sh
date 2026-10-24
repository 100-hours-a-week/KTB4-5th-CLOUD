#!/usr/bin/env bash
# 승인 전에는 inspect만 실행한다. 계정 경로는 WSL 절대 경로이며 private/ 안에 별도로 보관한다.
set -euo pipefail
umask 077
cd -- "$(dirname -- "$0")"
action=${1:-inspect}
[[ "$action" == inspect || "$action" == run ]] || { echo 'inspect 또는 run만 지원합니다.'; exit 2; }
: "${ACCOUNT_FILE:?계정 파일의 절대 경로 필요}" "${RUN_ID:?중복되지 않는 실행 ID 필요}" "${PHASE:?단계 필요}"
[[ "$ACCOUNT_FILE" == /* && -f "$ACCOUNT_FILE" ]]
[[ "$RUN_ID" =~ ^[a-zA-Z0-9_-]{6,24}$ && "$PHASE" =~ ^[a-zA-Z0-9_-]{1,32}$ ]]
export BASE_URL=${BASE_URL:-https://dameokja.com}
out="$PWD/results/${RUN_ID}-${PHASE}"
export RESULT_FILE="$out/summary.json"
api=${K6_BIN:-k6}
if [[ "$action" == inspect ]]; then
  "$api" inspect --include-system-env-vars load.js
  exit
fi
[[ "${PROD_CONFIRMED:-}" == true ]] || { echo '사용자 실행 승인이 필요합니다.'; exit 1; }
mkdir -p results
# 이전 원시 표본이나 쓰기 식별자를 덮어쓰지 않는다.
mkdir "$out"
"$api" run --log-format raw --console-output "$out/samples.jsonl" load.js
