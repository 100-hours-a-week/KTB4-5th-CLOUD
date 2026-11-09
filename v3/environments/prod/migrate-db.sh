#!/usr/bin/env bash
# v2 prod DB(MySQL app, PostgreSQL receipt_ai) 데이터를 v3 prod DB EC2(prod-db 스택)로 한 번 옮깁니다.
# 관리자 PC(Git Bash 또는 WSL)에서 실행하면 SSM Run Command로 v3 prod DB 서버 안에서 덤프·복원합니다.
# 비밀번호는 서버 안에서 prod SSM을 읽습니다(명령 기록·화면에 남기지 않음).
#
#   v3/environments/prod/migrate-db.sh check   # 읽기만: 접속·원본/대상 표 행 수 비교
#   v3/environments/prod/migrate-db.sh run     # 복원(대상이 비어 있을 때만) 후 행 수 대조
#
# 전제
# - v2 prod 앱이 꺼져 있어 원본에 쓰기가 없어야 합니다(BE·AI ECS desired 0을 확인하고, 아니면 멈춥니다).
# - v2 prod DB EC2가 켜져 있고, prod-db 스택의 SourceDbSecurityGroupId로 v2 DB SG 3306·5432가 열려 있어야 합니다.
# - v3 prod 앱(BE·AI)을 띄우기 전에 실행합니다. BE는 ddl-auto=validate라 빈 DB로는 뜨지 않고, AI는 첫 기동에 표를 만들 수 있습니다.
set -euo pipefail
export MSYS_NO_PATHCONV=1
mode=${1:-}
[[ $mode == check || $mode == run ]] || { echo "사용: $0 check|run" >&2; exit 2; }
REGION=ap-northeast-2
out() { aws cloudformation describe-stacks --region $REGION --stack-name "$1" --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text; }

target=$(out dameokja-v3-prod-db DbInstanceId)
read -r source source_ip source_state < <(aws ec2 describe-instances --region $REGION \
  --filters Name=tag:Name,Values=dameokja-prod-db Name=instance-state-name,Values=pending,running,stopping,stopped \
  --query 'Reservations[].Instances[].[InstanceId,PrivateIpAddress,State.Name]' --output text) || true
[[ -n ${target:-} && $target != None ]] || { echo "v3 prod DB(dameokja-v3-prod-db)를 찾지 못했습니다" >&2; exit 1; }
[[ ${source_state:-} == running ]] || { echo "v2 prod DB(dameokja-prod-db) 상태: ${source_state:-없음}. 먼저 시작하세요." >&2; exit 1; }
for svc in dameokja-prod-backend dameokja-prod-ai; do
  n=$(aws ecs describe-services --region $REGION --cluster dameokja-prod --services $svc --query 'services[0].[desiredCount,runningCount]' --output text | awk '{print $1+$2}')
  [[ $n == 0 ]] || { echo "v2 $svc가 아직 실행 중입니다(desired+running=$n). 원본에 쓰기가 없도록 v2 prod를 끈 뒤 실행하세요." >&2; exit 1; }
done
echo "원본 v2 $source ($source_ip) → 대상 v3 $target, 모드 $mode"

# 서버 안에서 실행할 스크립트. root(auth_socket)·postgres(peer)로 대상에 접속합니다.
# read -d ''는 끝에서 1을 돌려주므로 || true
read -r -d '' remote <<'REMOTE' || true
set -euo pipefail
mode=$1; SRC=$2; REGION=ap-northeast-2
MYPW=$(aws ssm get-parameter --region $REGION --name /dameokja-v2-prod/SPRING_DATASOURCE_PASSWORD --with-decryption --query Parameter.Value --output text)
PGPW=$(aws ssm get-parameter --region $REGION --name /dameokja-v2-prod-ai/POSTGRES_PASSWORD --with-decryption --query Parameter.Value --output text)
cnf=$(mktemp); chmod 600 "$cnf"; trap 'rm -f "$cnf" /tmp/receipt_ai.dump /tmp/receipt_ai.list' EXIT
# 옵션 파일에서 # 뒤는 주석이라 값을 큰따옴표로 감쌉니다(\·" 이스케이프). v2 prod 비밀번호에 #이 있습니다.
q=${MYPW//\\/\\\\}; q=${q//\"/\\\"}
printf '[client]\nuser=dameokja\npassword="%s"\nhost=%s\n' "$q" "$SRC" > "$cnf"
export PGPASSWORD=$PGPW

src_mysql() { mysql --defaults-extra-file="$cnf" -N -B "$@"; }
dst_mysql() { mysql -uroot -N -B "$@"; }
src_pg() { psql -h "$SRC" -U receipt_ai -d receipt_ai -tA -v ON_ERROR_STOP=1 "$@"; }
dst_pg() { sudo -u postgres psql -d receipt_ai -tA -v ON_ERROR_STOP=1 "$@"; }

mysql_counts() { # $1 src|dst
  local f=${1}_mysql t
  for t in $($f -e "SELECT table_name FROM information_schema.tables WHERE table_schema='app' AND table_type='BASE TABLE' ORDER BY 1"); do
    echo "app.$t $($f -e "SELECT COUNT(*) FROM app.\`$t\`")"
  done
}
pg_counts() {
  local f=${1}_pg t
  for t in $($f -c "SELECT schemaname||'.'||tablename FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema') ORDER BY 1"); do
    echo "$t $($f -c "SELECT COUNT(*) FROM $t")"
  done
}

echo "== 원본 접속"
src_mysql -e "SELECT CONCAT('MySQL ', VERSION())"
src_pg -c "SELECT version()" | cut -c1-40
other=$(src_pg -c "SELECT string_agg(extname, ',') FROM pg_extension WHERE extname NOT IN ('plpgsql','pg_trgm')")
[[ -z $other ]] || { echo "대상에 없는 PostgreSQL 확장이 원본에 있습니다: $other" >&2; exit 1; }

dst_tables=$(dst_mysql -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='app'")
dst_pg_tables=$(dst_pg -c "SELECT COUNT(*) FROM pg_tables WHERE schemaname='public'")
echo "대상 표 수: MySQL app $dst_tables, PostgreSQL receipt_ai $dst_pg_tables"

if [[ $mode == run ]]; then
  [[ $dst_tables == 0 && $dst_pg_tables == 0 ]] || { echo "대상 DB가 비어 있지 않아 복원하지 않습니다(덮어쓰기 방지)." >&2; exit 1; }
  echo "== MySQL app 덤프·복원"
  # 원본은 GTID가 켜져 있어 GTID_PURGED를 넣지 않습니다(대상의 자체 GTID와 충돌 방지). 쓰기가 없는 상태라 단일 트랜잭션 스냅샷이면 일관됩니다.
  mysqldump --defaults-extra-file="$cnf" --single-transaction --set-gtid-purged=OFF --no-tablespaces --triggers --hex-blob --default-character-set=utf8mb4 app \
    | mysql -uroot app
  echo "== PostgreSQL receipt_ai 덤프·복원"
  pg_dump -h "$SRC" -U receipt_ai -d receipt_ai --no-owner --no-privileges -Fc -f /tmp/receipt_ai.dump
  # pg_trgm은 대상에 이미 있어(prod-db Association) 확장 항목은 빼고, 객체 소유자는 receipt_ai로 만듭니다.
  chmod 644 /tmp/receipt_ai.dump
  sudo -u postgres pg_restore -l /tmp/receipt_ai.dump | grep -v ' EXTENSION ' > /tmp/receipt_ai.list
  chmod 644 /tmp/receipt_ai.list
  sudo -u postgres pg_restore --exit-on-error --no-owner --role=receipt_ai -L /tmp/receipt_ai.list -d receipt_ai /tmp/receipt_ai.dump
fi

echo "== 행 수 대조"
diff <(mysql_counts src) <(mysql_counts dst) && echo "MySQL 일치" || { [[ $mode == check ]] || { echo "MySQL 행 수 불일치" >&2; exit 1; }; }
diff <(pg_counts src) <(pg_counts dst) && echo "PostgreSQL 일치" || { [[ $mode == check ]] || { echo "PostgreSQL 행 수 불일치" >&2; exit 1; }; }
echo "== 원본 표별 행 수"; mysql_counts src; pg_counts src
REMOTE
b64=$(printf %s "$remote" | base64 | tr -d '\n')
# Windows aws.exe도 읽을 수 있게 저장소 안 Git 제외 경로(infra/v3/private)에 둡니다.
cd "$(git rev-parse --show-toplevel)"; mkdir -p infra/v3/private
params=$(mktemp -p infra/v3/private); trap 'rm -f "$params"' EXIT
printf '{"commands":["echo %s | base64 -d > /tmp/migrate-db.sh && bash /tmp/migrate-db.sh %s %s; rc=$?; rm -f /tmp/migrate-db.sh; exit $rc"],"executionTimeout":["3600"]}' "$b64" "$mode" "$source_ip" > "$params"
id=$(aws ssm send-command --region $REGION --instance-ids "$target" --document-name AWS-RunShellScript \
  --comment "v3 prod DB migration $mode" --parameters "file://$params" --query Command.CommandId --output text)
echo "SSM 명령 $id 실행 중"
while :; do
  st=$(aws ssm get-command-invocation --region $REGION --command-id "$id" --instance-id "$target" --query Status --output text 2>/dev/null || echo Pending)
  [[ $st == Pending || $st == InProgress || $st == Delayed ]] || break
  sleep 10
done
aws ssm get-command-invocation --region $REGION --command-id "$id" --instance-id "$target" --query '[StandardOutputContent,StandardErrorContent]' --output text
[[ $st == Success ]] || { echo "실패: $st" >&2; exit 1; }
echo "완료($mode). run이었다면 SourceDbSecurityGroupId를 비우고 prod-db를 다시 apply해 v2 DB 접근을 닫습니다."
