#!/usr/bin/env bash
# dev SSM 값을 v2 이름(/dameokja-v2-dev*)에서 v3 이름(/dameokja-v3-dev*)으로 한 번 복사합니다. 관리자 PC(Git Bash·WSL)에서 실행합니다.
# 기본은 복사할 이름만 출력하고, --apply일 때 복사합니다. 값은 화면에 출력하지 않고 이미 있는 v3 이름은 덮어쓰지 않습니다.
# 형식(String·SecureString)과 KMS 키는 원본을 따릅니다. v2 경로 값은 지우지 않습니다(확인 뒤 마지막에 나오는 명령으로 지움).
set -euo pipefail
export MSYS_NO_PATHCONV=1
apply=; [[ ${1:-} == --apply ]] && apply=1
cd "$(git rev-parse --show-toplevel)"; mkdir -p infra/v3/private
tmp=$(mktemp -d -p infra/v3/private); chmod 700 "$tmp"; trap 'rm -rf "$tmp"' EXIT
copied=0; skipped=0; old=()

for suffix in "" -ai -fe; do
  src=/dameokja-v2-dev$suffix dst=/dameokja-v3-dev$suffix
  # seal-secrets.sh가 읽는 바로 아래 키만 옮깁니다(v2 ECS 전용 하위 경로 cloudwatch-agent 등은 제외).
  aws ssm describe-parameters --parameter-filters "Key=Path,Option=OneLevel,Values=$src" \
    --query 'Parameters[].[Name,Type,KeyId]' --output text > "$tmp/list"
  while IFS=$'\t' read -r name type key; do
    [[ -n $name ]] || continue
    target=$dst/${name##*/}
    old+=("$name")
    if aws ssm get-parameter --name "$target" --query Parameter.Name --output text >/dev/null 2>&1; then
      echo "건너뜀(이미 있음) $target"; skipped=$((skipped+1)); continue
    fi
    if [[ -z $apply ]]; then echo "복사 예정 $name → $target ($type)"; continue; fi
    echo "복사 $name → $target ($type)"
    # 값은 파일로 넘겨 명령줄·화면에 남기지 않습니다.
    aws ssm get-parameter --with-decryption --name "$name" --query Parameter.Value --output text > "$tmp/value"
    truncate -s -1 "$tmp/value"
    args=(--name "$target" --type "$type" --value "file://$tmp/value")
    [[ $type == SecureString && $key != None && $key != alias/aws/ssm ]] && args+=(--key-id "$key")
    aws ssm put-parameter "${args[@]}" >/dev/null
    copied=$((copied+1))
  done < "$tmp/list"
done

if [[ -n $apply ]]; then echo "완료: 복사 $copied, 건너뜀 $skipped"; else echo "확인만 했습니다(건너뜀 $skipped). --apply로 실제 복사합니다."; fi
if [[ -n $apply && ${#old[@]} -gt 0 ]]; then
  echo "v3 dev Secret을 다시 만들어 확인한 뒤 v2 값을 지우려면(10개씩):"
  printf '%s\n' "${old[@]}" | xargs -n 10 echo aws ssm delete-parameters --names
fi
