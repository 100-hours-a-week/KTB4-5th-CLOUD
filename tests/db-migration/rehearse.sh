#!/usr/bin/env bash
# 부하를 유지한 채 초기 복사 → GTID 복제 → 스키마·데이터 검사 → 쓰기 중단 전환 → 결과 검증을 한 번에 실행한다
# 환경변수는 migrate.sh·migtest.py와 같다. 결과는 $WORK_DIR/<run_id>/ 에 남는다
#   BASELINE_S(기준 측정, 기본 120) STEADY_S(전환 전 안정 구간, 60) AFTER_S(전환 후 측정, 120)
#   CUTOVER_OPT(전환 옵션 JSON, 기본 {"budget_s":1.0,"hard_timeout_s":10}), PRE_CUTOVER_CMD(전환 직전 명령, 실패 분기 시험용)
#   LOAD_ARGS(migtest.py run 추가 인자, 예: --write-rate 100 --read-rate 200)
set -euo pipefail
cd "$(dirname "$0")"

RUN_ID=${RUN_ID:-$(date +%Y%m%d-%H%M%S)}
export WORK_DIR=${WORK_DIR:-./work}/$RUN_ID
OUT=$WORK_DIR/load CTL=$WORK_DIR/control
mkdir -p "$OUT" "$CTL"
exec > >(tee -a "$WORK_DIR/rehearse.log") 2>&1

phase() { echo "$1" > "$CTL/phase"; echo "[$(date '+%F %T.%3N')] ---- 단계: $1"; }
hold() { sleep "$1"; }

./migrate.sh preflight
# 기준 데이터가 없을 때만 만든다 (stage처럼 이미 데이터가 있으면 건너뜀)
python3 migtest.py seed ${SEED_ARGS:-}
phase baseline
python3 migtest.py run --run-id "$RUN_ID" --out "$OUT" --control "$CTL" ${LOAD_ARGS:-} > "$WORK_DIR/load.log" 2>&1 &
LOAD_PID=$!
trap 'touch "$CTL/stop"; wait $LOAD_PID || true' EXIT
hold "${BASELINE_S:-120}"

phase copy
./migrate.sh copy
phase replicate
./migrate.sh replicate
# 복제 오류가 있거나 따라잡지 못하면 이후 단계를 진행하지 않음
for i in $(seq 1 120); do
  s=$(./migrate.sh status)
  grep -q 'Last_SQL_Errno: [1-9]\|Last_IO_Errno: [1-9]' <<<"$s" && { echo "$s"; echo "복제 오류로 중단"; exit 1; }
  grep -q 'Replica_SQL_Running: Yes' <<<"$s" && grep -q 'Seconds_Behind_Source: 0' <<<"$s" && break
  sleep 1
done
./migrate.sh status
phase replicating
hold "${STEADY_S:-60}"

phase verify
./migrate.sh schema-diff
./migrate.sh checksum
./migrate.sh disk

phase steady
hold "${STEADY_S:-60}"
# 실패 분기 시험용: 전환 직전에 실행할 명령 (예: 신규 DB 복제 적용 중지)
[[ -n "${PRE_CUTOVER_CMD:-}" ]] && eval "$PRE_CUTOVER_CMD"
DEFAULT_OPT='{"budget_s":1.0,"hard_timeout_s":10}'
echo "${CUTOVER_OPT:-$DEFAULT_OPT}" > "$CTL/cutover"
for i in $(seq 1 300); do [[ -f "$OUT/cutover.json" ]] && break; sleep 0.2; done
hold "${AFTER_S:-120}"
touch "$CTL/stop"
wait $LOAD_PID || true
trap - EXIT
cat "$OUT/cutover.json"
# 취소된 경우 신규 DB는 계속 복제본이므로, 원본에서 재개한 쓰기까지 따라잡은 뒤 검증
if grep -q '"result": "aborted"' "$OUT/cutover.json"; then
  for i in $(seq 1 60); do ./migrate.sh status | grep -q '^missing_gtid_on_target=$' && break; sleep 1; done
  ./migrate.sh status
fi
python3 migtest.py verify --out "$OUT"
