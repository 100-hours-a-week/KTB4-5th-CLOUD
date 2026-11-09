#!/usr/bin/env bash
# migrate-db.sh run으로 옮긴 v3 prod DB(app)를 BE가 기대하는 스키마로 올리고 필수 행을 넣습니다. v3 prod 앱(Argo)을 적용하기 전에 실행합니다.
# v2 prod 스키마는 BE main 0b8de6e보다 뒤처져 있어(표 18개 → 30개), 그대로 두면 BE(ddl-auto=validate)가 뜨지 않습니다.
# 관리자 PC(Git Bash 또는 WSL)에서 실행하면 SSM Run Command로 v3 prod DB 서버 안에서 root(auth_socket)로 적용합니다.
#
#   v3/environments/prod/migrate-schema.sh check   # 읽기만: app 스키마를 BE db/schema.sql과 비교
#   v3/environments/prod/migrate-schema.sh run     # BE DDL → prod 보완 → BE DML 적용 후 비교(차이가 있으면 실패)
#
# SQL 원본
# - BE 저장소(BE_DIR, 기본 ../KTB4-5th-BE)의 BE_REF 커밋에서 읽습니다. 2026-11-09 v2 prod 스키마 사본으로 리허설한 커밋으로 고정합니다.
#   db/migrations/20261109-main-schema-changes.sql, db/dml/20261109-required-runtime-data.sql, db/schema.sql(비교 기준)
# - prod 보완: 이 폴더의 schema-patch-ingredients-owner.sql
# DDL은 암시적으로 커밋되어 되돌릴 수 없습니다. 실패하면 다시 실행하지 말고 v2 DB에서 migrate-db.sh run부터 다시 합니다(대상 DB를 비운 뒤).
set -euo pipefail
export MSYS_NO_PATHCONV=1
mode=${1:-}
[[ $mode == check || $mode == run ]] || { echo "사용: $0 check|run" >&2; exit 2; }
REGION=ap-northeast-2
BE_REF=${BE_REF:-ade4bcfbdc5451d7a18610278916634608c77a19}
cd "$(git rev-parse --show-toplevel)"
BE_DIR=${BE_DIR:-../KTB4-5th-BE}
git -C "$BE_DIR" cat-file -e "$BE_REF^{commit}" 2>/dev/null \
  || { echo "BE 저장소($BE_DIR)에 $BE_REF가 없습니다. git -C $BE_DIR fetch origin dev 후 다시 실행하세요." >&2; exit 1; }

target=$(aws cloudformation describe-stacks --region $REGION --stack-name dameokja-v3-prod-db \
  --query "Stacks[0].Outputs[?OutputKey=='DbInstanceId'].OutputValue" --output text)
[[ -n ${target:-} && $target != None ]] || { echo "v3 prod DB(dameokja-v3-prod-db)를 찾지 못했습니다" >&2; exit 1; }
echo "대상 v3 $target, BE $BE_REF, 모드 $mode"

mkdir -p infra/v3/private
tmp=$(mktemp -d -p infra/v3/private); trap 'rm -rf "$tmp"' EXIT
git -C "$BE_DIR" show "$BE_REF:src/main/resources/db/migrations/20261109-main-schema-changes.sql" > "$tmp/1-ddl.sql"
cp v3/environments/prod/schema-patch-ingredients-owner.sql "$tmp/2-patch.sql"
git -C "$BE_DIR" show "$BE_REF:src/main/resources/db/dml/20261109-required-runtime-data.sql" > "$tmp/3-dml.sql"
git -C "$BE_DIR" show "$BE_REF:src/main/resources/db/schema.sql" > "$tmp/schema.sql"

# 서버 안에서 실행할 스크립트. read -d ''는 끝에서 1을 돌려주므로 || true
read -r -d '' remote <<'REMOTE' || true
set -euo pipefail
mode=$1; cd "$2"
my="mysql -uroot --default-character-set=utf8mb4"
# 비교: 컬럼·인덱스·CHECK·FK (주석은 제외)
shape() {
  $my -N -B -e "
SELECT CONCAT('COL ',table_name,'.',column_name,' ',column_type,' null=',is_nullable,' def=',IFNULL(column_default,'-'),' ',extra,' ',IFNULL(generation_expression,'')) FROM information_schema.columns WHERE table_schema='$1';
SELECT CONCAT('IDX ',table_name,'.',index_name,' u=',1-MAX(non_unique),' (',GROUP_CONCAT(column_name ORDER BY seq_in_index),')') FROM information_schema.statistics WHERE table_schema='$1' GROUP BY table_name,index_name;
SELECT CONCAT('CHK ',tc.table_name,'.',cc.constraint_name,' ',cc.check_clause) FROM information_schema.check_constraints cc JOIN information_schema.table_constraints tc ON tc.constraint_schema=cc.constraint_schema AND tc.constraint_name=cc.constraint_name WHERE cc.constraint_schema='$1';
SELECT CONCAT('FK ',k.table_name,'.',k.constraint_name,' (',GROUP_CONCAT(k.column_name ORDER BY k.ordinal_position),')->',k.referenced_table_name,'(',GROUP_CONCAT(k.referenced_column_name ORDER BY k.ordinal_position),') ',r.update_rule,'/',r.delete_rule) FROM information_schema.key_column_usage k JOIN information_schema.referential_constraints r ON r.constraint_schema=k.constraint_schema AND r.constraint_name=k.constraint_name WHERE k.constraint_schema='$1' GROUP BY k.table_name,k.constraint_name,k.referenced_table_name,r.update_rule,r.delete_rule;" | sort
}
compare() {
  $my -e "DROP DATABASE IF EXISTS mig_truth; CREATE DATABASE mig_truth DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_0900_as_cs"
  $my mig_truth < schema.sql
  shape app > app.txt; shape mig_truth > truth.txt
  $my -e "DROP DATABASE mig_truth"
  echo "== app ↔ BE db/schema.sql (< app, > schema.sql)"
  if diff app.txt truth.txt; then echo "차이 없음"; return 0; fi
  return 1
}

tables=$($my -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='app'")
echo "app 표 수: $tables"
if [[ $mode == check ]]; then compare || true; exit 0; fi

# run: migrate-db.sh run으로 데이터만 옮긴 상태(DDL 미적용)이고 앱이 붙어 있지 않을 때만 적용합니다.
[[ $($my -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='app' AND table_name='refrigerators'") == 1 ]] \
  || { echo "app에 refrigerators가 없습니다. migrate-db.sh run을 먼저 하세요." >&2; exit 1; }
[[ $($my -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='app' AND table_name='membership_transmissions'") == 0 ]] \
  || { echo "이미 DDL이 적용된 DB입니다(membership_transmissions 있음). 다시 적용하지 않습니다." >&2; exit 1; }
conns=$($my -N -e "SELECT COUNT(*) FROM information_schema.processlist WHERE user NOT IN ('root','system user','event_scheduler')")
[[ $conns == 0 ]] || { echo "app에 연결이 $conns개 있습니다. v3 prod BE·AI를 띄우기 전에 실행하세요." >&2; exit 1; }

for f in 1-ddl 2-patch; do
  s=$(date +%s); $my app < $f.sql; echo "$f 적용 ($(( $(date +%s)-s ))초)"
done
echo "== 3-dml"
$my -t app < 3-dml.sql | tee dml.out
grep -qE '\|\s+1 \| OK ' dml.out || { echo "DML 결과가 analysis_gate_ready=1·ktb_seed_status=OK가 아닙니다. reason을 확인하세요." >&2; exit 1; }
compare || { echo "스키마가 BE db/schema.sql과 다릅니다. 앱을 띄우지 마세요." >&2; exit 1; }
echo "app 표 수: $($my -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='app'")"
REMOTE
printf %s "$remote" > "$tmp/remote.sh"
b64=$(tar -C "$tmp" -czf - 1-ddl.sql 2-patch.sql 3-dml.sql schema.sql remote.sh | base64 | tr -d '\n')
params=$tmp/params.json
printf '{"commands":["d=$(mktemp -d); echo %s | base64 -d | tar -xzf - -C $d && bash $d/remote.sh %s $d; rc=$?; rm -rf $d; exit $rc"],"executionTimeout":["1800"]}' "$b64" "$mode" > "$params"
id=$(aws ssm send-command --region $REGION --instance-ids "$target" --document-name AWS-RunShellScript \
  --comment "v3 prod DB schema $mode" --parameters "file://$params" --query Command.CommandId --output text)
echo "SSM 명령 $id 실행 중"
while :; do
  st=$(aws ssm get-command-invocation --region $REGION --command-id "$id" --instance-id "$target" --query Status --output text 2>/dev/null || echo Pending)
  [[ $st == Pending || $st == InProgress || $st == Delayed ]] || break
  sleep 5
done
aws ssm get-command-invocation --region $REGION --command-id "$id" --instance-id "$target" --query '[StandardOutputContent,StandardErrorContent]' --output text
[[ $st == Success ]] || { echo "실패: $st" >&2; exit 1; }
echo "완료($mode)."
