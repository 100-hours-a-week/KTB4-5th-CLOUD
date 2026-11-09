#!/usr/bin/env bash
# dev의 카테부 냉장고(KTB_REFRIGERATOR_ID) 재고를 stage 냉장고 하나로 한 번 복사합니다. 관리자 PC(Git Bash·WSL)에서 실행합니다.
# 냉장고와 소유자(OWNER 구성원)는 stage 앱에서 전용 계정으로 회원가입해 만들고, 그 냉장고 ID를 넣습니다.
# 구성원·회원·초대 코드는 옮기지 않습니다(dev 회원 개인정보를 stage에 넣지 않고, 가입 이벤트를 우회하지 않음).
#
#   v3/environments/stage/copy-ktb-ingredients.sh <stage 냉장고 ID>          # 확인만(쓰기 없음)
#   v3/environments/stage/copy-ktb-ingredients.sh <stage 냉장고 ID> --apply  # 복사
#
# - 원본 ID는 dev SSM /dameokja-v2-dev/KTB_REFRIGERATOR_ID에서 읽습니다(SOURCE_ID로 바꿀 수 있음).
# - 재고 등록자(owner_id)는 대상 냉장고의 OWNER로 바꿉니다. 재고 사진 키는 dev S3 경로라 stage에서 읽을 수 없어 비웁니다.
# - 냉장고 이름도 dev 값으로 바꿉니다. 대상 냉장고에 재고가 이미 있으면 복사하지 않습니다.
# - 끝나면 /dameokja-v3-stage/KTB_REFRIGERATOR_ID를 대상 ID로 바꾸고 seal-secrets.sh로 Secret을 다시 만듭니다.
set -euo pipefail
export MSYS_NO_PATHCONV=1
target=${1:-}; apply=; [[ ${2:-} == --apply ]] && apply=1
[[ $target =~ ^[1-9][0-9]*$ ]] || { echo "사용: $0 <stage 냉장고 ID> [--apply]" >&2; exit 2; }
REGION=ap-northeast-2
PY=$(command -v python3 || command -v python)
DEV_DB_NAME=${DEV_DB_NAME:-app}   # dev BE 스키마 이름(SPRING_DATASOURCE_URL의 DB)
DEV_DB=${DEV_DB:-i-0111bde7fc68be69c}   # dev DB EC2(infra/v3/README.md "dev 데이터 계층")
STAGE_DB=$(aws cloudformation describe-stacks --region $REGION --stack-name dameokja-v3-stage-db --query "Stacks[0].Outputs[?OutputKey=='DbInstanceId'].OutputValue" --output text)
SOURCE_ID=${SOURCE_ID:-$(aws ssm get-parameter --region $REGION --with-decryption --name /dameokja-v2-dev/KTB_REFRIGERATOR_ID --query Parameter.Value --output text)}
[[ $SOURCE_ID =~ ^[1-9][0-9]*$ ]] || { echo "dev 카테부 냉장고 ID가 올바르지 않습니다: $SOURCE_ID" >&2; exit 1; }
cd "$(git rev-parse --show-toplevel)"; mkdir -p infra/v3/private
tmp=$(mktemp -d -p infra/v3/private); chmod 700 "$tmp"; trap 'rm -rf "$tmp"' EXIT

# run <instance> <이름> <명령...>: SSM Run Command로 실행하고 표준 출력만 돌려줍니다.
run() {
  local instance=$1 name=$2; shift 2
  "$PY" -c 'import json,sys; json.dump({"commands": sys.argv[2:], "executionTimeout": ["600"]}, open(sys.argv[1], "w"))' "$tmp/$name.json" "$@"
  local id st
  id=$(aws ssm send-command --region $REGION --instance-ids "$instance" --document-name AWS-RunShellScript \
    --comment "stage KTB ingredients $name" --parameters "file://$tmp/$name.json" --query Command.CommandId --output text)
  while :; do
    st=$(aws ssm get-command-invocation --region $REGION --command-id "$id" --instance-id "$instance" --query Status --output text 2>/dev/null || echo Pending)
    [[ $st == Pending || $st == InProgress || $st == Delayed ]] || break
    sleep 3
  done
  if [[ $st != Success ]]; then
    aws ssm get-command-invocation --region $REGION --command-id "$id" --instance-id "$instance" --query '[StandardOutputContent,StandardErrorContent]' --output text >&2
    echo "실패($name): $st" >&2; exit 1
  fi
  aws ssm get-command-invocation --region $REGION --command-id "$id" --instance-id "$instance" --query StandardOutputContent --output text
}

echo "원본 dev 냉장고 $SOURCE_ID ($DEV_DB) → 대상 stage 냉장고 $target ($STAGE_DB)"

# 1) dev: 냉장고 이름과 재고를 INSERT 문으로 만들어 gzip+base64 한 줄로 둡니다(읽기만).
# 값은 QUOTE()로 감쌉니다. 이름에 든 줄바꿈(CR·LF)은 QUOTE()가 바꾸지 않아 \r·\n으로 바꿔 한 행이 한 줄이 되게 합니다.
export_sql=$(cat <<SQL
SELECT CONCAT('SET @name=', REPLACE(REPLACE(QUOTE(name), CHAR(13), CHAR(92,114)), CHAR(10), CHAR(92,110)), ';') FROM refrigerators WHERE refrigerator_id=$SOURCE_ID AND deleted_at IS NULL;
SELECT CONCAT('INSERT INTO ingredients (refrigerator_id, owner_id, name, category, storage_type, quantity, weight_value, weight_unit, measure_type, expiration_date, ingredient_image_key, created_at, updated_at, registration_source) VALUES (@rid, @owner, ',
  REPLACE(REPLACE(QUOTE(name), CHAR(13), CHAR(92,114)), CHAR(10), CHAR(92,110)), ',', QUOTE(category), ',', QUOTE(storage_type), ',', IFNULL(quantity, 'NULL'), ',', IFNULL(weight_value, 'NULL'), ',',
  QUOTE(weight_unit), ',', QUOTE(measure_type), ',', QUOTE(expiration_date), ', NULL, ', QUOTE(created_at), ',', QUOTE(updated_at), ',',
  QUOTE(registration_source), ');')
FROM ingredients WHERE refrigerator_id=$SOURCE_ID ORDER BY ingredient_id;
SQL
)
b64=$(printf %s "$export_sql" | base64 | tr -d '\n')
run "$DEV_DB" export "set -euo pipefail" \
  "echo $b64 | base64 -d > /tmp/ktb-export.sql" \
  "sudo mysql -N -B --raw $DEV_DB_NAME < /tmp/ktb-export.sql > /tmp/ktb.sql" \
  "grep -q '^SET @name=' /tmp/ktb.sql || { echo 'dev에 카테부 냉장고가 없거나 삭제됨'; exit 1; }" \
  "gzip -c /tmp/ktb.sql | base64 -w0 > /tmp/ktb.b64; rm -f /tmp/ktb-export.sql /tmp/ktb.sql" \
  "wc -c < /tmp/ktb.b64" > "$tmp/size"
size=$(tr -dc 0-9 < "$tmp/size")
# SSM 출력 한도(24,000자) 아래로 나눠 받습니다.
: > "$tmp/ktb.b64"
for ((start=1; start<=size; start+=20000)); do
  run "$DEV_DB" "fetch-$start" "cut -c$start-$((start+19999)) /tmp/ktb.b64 | tr -d '\n'" | tr -d '\n\r' >> "$tmp/ktb.b64"
done
run "$DEV_DB" cleanup "rm -f /tmp/ktb.b64" >/dev/null
[[ $(wc -c < "$tmp/ktb.b64") == "$size" ]] || { echo "dev 결과를 모두 받지 못했습니다" >&2; exit 1; }
base64 -d "$tmp/ktb.b64" | gunzip > "$tmp/ktb.sql"
count=$(grep -c '^INSERT INTO ingredients' "$tmp/ktb.sql" || true)
echo "dev 재고 ${count}건, 냉장고 이름 포함"

# 2) stage: 대상 냉장고·OWNER·기존 재고·용량을 확인합니다.
check=$(run "$STAGE_DB" check "mysql -uroot -N -B app -e \"SELECT COUNT(*), IFNULL(MAX(r.capacity),0), (SELECT COUNT(*) FROM ingredients WHERE refrigerator_id=$target), (SELECT COUNT(*) FROM refrigerator_members WHERE refrigerator_id=$target AND role='OWNER' AND ended_at IS NULL) FROM refrigerators r WHERE r.refrigerator_id=$target AND r.deleted_at IS NULL\"")
read -r exists capacity existing owners <<< "$check"
echo "stage 냉장고 $target: 존재 $exists, 용량 $capacity, 기존 재고 $existing, OWNER $owners"
[[ $exists == 1 ]] || { echo "stage에 냉장고 $target이 없거나 삭제됨" >&2; exit 1; }
[[ $owners == 1 ]] || { echo "stage 냉장고 $target의 OWNER가 1명이 아닙니다" >&2; exit 1; }
[[ $existing == 0 ]] || { echo "stage 냉장고 $target에 재고가 이미 있어 복사하지 않습니다(중복 방지)" >&2; exit 1; }
(( count <= capacity )) || { echo "재고 $count건이 용량 $capacity을 넘습니다" >&2; exit 1; }
if [[ -z $apply ]]; then echo "확인만 했습니다. --apply로 복사합니다."; exit 0; fi

# 3) stage: 한 트랜잭션으로 넣습니다. 오류가 나면 mysql이 멈추고 커밋되지 않습니다.
{
  echo "SET @rid=$target;"
  echo "SELECT user_id INTO @owner FROM refrigerator_members WHERE refrigerator_id=@rid AND role='OWNER' AND ended_at IS NULL;"
  echo "START TRANSACTION;"
  grep '^SET @name=' "$tmp/ktb.sql"
  echo "UPDATE refrigerators SET name=@name, updated_at=CURRENT_TIMESTAMP(6) WHERE refrigerator_id=@rid;"
  grep '^INSERT INTO ingredients' "$tmp/ktb.sql" || true
  echo "COMMIT;"
} | gzip -c | base64 | tr -d '\n' > "$tmp/load.b64"
run "$STAGE_DB" load-reset "rm -f /tmp/ktb-load.b64" >/dev/null
total=$(wc -c < "$tmp/load.b64")
for ((start=1; start<=total; start+=20000)); do
  run "$STAGE_DB" "load-$start" "printf %s $(cut -c$start-$((start+19999)) "$tmp/load.b64") >> /tmp/ktb-load.b64" >/dev/null
done
after=$(run "$STAGE_DB" load "set -euo pipefail" \
  "[ \$(wc -c < /tmp/ktb-load.b64) = $total ] || { echo '전송 길이 불일치'; exit 1; }" \
  "base64 -d /tmp/ktb-load.b64 | gunzip | mysql -uroot app; rm -f /tmp/ktb-load.b64" \
  "mysql -uroot -N -B app -e \"SELECT COUNT(*) FROM ingredients WHERE refrigerator_id=$target\"")
echo "stage 냉장고 $target 재고: $after건 (dev $count건)"
[[ $after == "$count" ]] || { echo "건수가 다릅니다" >&2; exit 1; }
cat <<EOF
완료. 다음:
  aws ssm put-parameter --overwrite --name /dameokja-v3-stage/KTB_REFRIGERATOR_ID --value $target
  v3/environments/stage/seal-secrets.sh   # be-secrets.yaml만 바뀌어야 함 → 커밋 → stage-be 동기화
EOF
