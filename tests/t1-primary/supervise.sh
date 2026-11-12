#!/bin/sh
# 관리 연결이 끊긴 채 부하가 계속되지 않도록 90초 heartbeat 제한을 둔다.
set -eu
name=$1
warmup=$2
seconds=$3
scale=$4
date +%s > /work/heartbeat
k6 run --quiet --out "json=/work/$name.json.gz" -e "SUMMARY=/work/$name-summary.json" -e "WARMUP=$warmup" -e "SECONDS=$seconds" -e "SCALE=$scale" /work/load.js > "/work/$name.log" 2>&1 &
load_pid=$!
echo "$load_pid" > /work/load.pid
while kill -0 "$load_pid" 2>/dev/null; do
  now=$(date +%s)
  heartbeat=$(cat /work/heartbeat)
  if [ $((now-heartbeat)) -gt 90 ]; then
    echo '관리 관측 heartbeat 상실: 부하 중단' >> "/work/$name.log"
    kill -INT "$load_pid"
    break
  fi
  sleep 5
done
set +e
wait "$load_pid"
result=$?
echo "$result" > "/work/$name.exit"
exit "$result"
