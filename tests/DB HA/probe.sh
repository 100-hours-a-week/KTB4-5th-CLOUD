#!/bin/bash
# T2 DB 쓰기 프로브 (#193). Orchestrator 서버(VPC 안)에서 SSM으로 실행
# 0.1초마다 db.prod.dameokja.internal로 hatest.probe에 일련번호 INSERT. 커밋 응답을 받은 줄만 ok로 기록
# 사용: probe.sh <실행 초> <로그 파일>   예) probe.sh 600 /var/tmp/haprobe-f4.log
# 로그 한 줄: <epoch 초.밀리초> ok|fail <seq> <응답한 서버 server_id 또는 오류>
set -u
DUR=${1:-600}
OUT=${2:-/var/tmp/haprobe.log}
PW=$(aws secretsmanager get-secret-value --region ap-northeast-2 --secret-id dameokja-v2-prod/db-haprobe --query SecretString --output text)
CNF=$(mktemp)
chmod 600 "$CNF"
printf '[client]\nuser=haprobe\npassword=%s\nhost=db.prod.dameokja.internal\nconnect-timeout=1\n' "$PW" > "$CNF"
trap 'rm -f "$CNF"' EXIT

# 실행마다 겹치지 않게 시작 시각(초)으로 번호를 만듦
seq=$(( $(date +%s) * 100000 ))
end=$(( $(date +%s) + DUR ))
: > "$OUT"
while [ "$(date +%s)" -lt "$end" ]; do
  t=$(date +%s.%3N)
  if r=$(timeout 2 mysql --defaults-extra-file="$CNF" -N -e "INSERT INTO hatest.probe VALUES ($seq, NOW(3)); SELECT @@server_id" 2>&1); then
    echo "$t ok $seq $r" >> "$OUT"
  else
    echo "$t fail $seq $(echo "$r" | tail -1 | tr ' ' '_')" >> "$OUT"
  fi
  seq=$((seq + 1))
  sleep 0.1
done
echo "끝: $(grep -c ' ok ' "$OUT") ok, $(grep -c ' fail ' "$OUT") fail -> $OUT"
