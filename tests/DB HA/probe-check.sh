#!/bin/bash
# probe.sh 결과 집계 (#193). 테스트가 끝나고 DNS가 새 Primary를 가리킬 때 Orchestrator 서버에서 실행
# 사용: probe-check.sh <로그 파일>
# RTO: 첫 실패 직전 성공 ~ 실패 뒤 첫 성공 (실패 구간마다), RPO: ok로 기록됐는데 지금 Primary에 없는 번호 수
set -u
LOG=${1:-/var/tmp/haprobe.log}
PW=$(aws secretsmanager get-secret-value --region ap-northeast-2 --secret-id dameokja-v2-prod/db-haprobe --query SecretString --output text)
CNF=$(mktemp)
chmod 600 "$CNF"
printf '[client]\nuser=haprobe\npassword=%s\nhost=db.prod.dameokja.internal\nconnect-timeout=3\n' "$PW" > "$CNF"
trap 'rm -f "$CNF" /tmp/probe-ok.txt /tmp/probe-db.txt' EXIT

echo "기록: $(grep -c ' ok ' "$LOG") ok, $(grep -c ' fail ' "$LOG") fail"
echo "응답한 서버(server_id별 ok 수):"
awk '$2 == "ok" {print $4}' "$LOG" | sort | uniq -c

echo "쓰기 중단 구간:"
awk '
  $2 == "ok"   { if (down) { printf "  %.3f초 (%s ~ %s, 실패 %d건)\n", $1 - last, lastseq, $3, n; down = 0 } last = $1; lastseq = $3 }
  $2 == "fail" { if (!down) { down = 1; n = 0 } n++ }
  END { if (down) print "  아직 복구되지 않음(로그 끝까지 실패)" }' "$LOG"

min=$(awk '$2 == "ok" {print $3; exit}' "$LOG")
max=$(awk '$2 == "ok" {s = $3} END {print s}' "$LOG")
awk '$2 == "ok" {print $3}' "$LOG" | sort > /tmp/probe-ok.txt
mysql --defaults-extra-file="$CNF" -N -e "SELECT seq FROM hatest.probe WHERE seq BETWEEN $min AND $max" | sort > /tmp/probe-db.txt
lost=$(comm -23 /tmp/probe-ok.txt /tmp/probe-db.txt | wc -l)
echo "RPO: 커밋 확인 $(wc -l < /tmp/probe-ok.txt)건 중 지금 Primary에 없는 것 ${lost}건"
[ "$lost" = 0 ] || comm -23 /tmp/probe-ok.txt /tmp/probe-db.txt | head -20
