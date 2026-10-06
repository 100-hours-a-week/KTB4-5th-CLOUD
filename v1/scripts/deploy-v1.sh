#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# 서비스별 환경설정은 각 앱 저장소에서 표준입력으로 받고, 이미지 설정은 서버에 유지한다.
service="${1:?서비스 필요}"
image="${2:?이미지 필요}"
project="${3:?Compose 프로젝트명 필요}"
compose_file="${4:?Compose 파일명 필요}"
[[ "$service" == backend || "$service" == frontend ]] || exit 2
[[ "$image" =~ ^ghcr\.io/[a-z0-9._/-]+@sha256:[a-f0-9]{64}$ ]] || exit 2
[[ "$project" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || exit 2
[[ "$compose_file" =~ ^[a-zA-Z0-9._-]+\.ya?ml$ ]] || exit 2
test -f "$compose_file"
test -f .env
command -v python3 >/dev/null
command -v flock >/dev/null

case "$service" in
    frontend) service_env="env/frontend.env" ;;
    backend) service_env="env/backend.env" ;;
esac

mkdir -p .deploy env
exec 9>.deploy/lock
flock -w 600 9 || { echo "다른 배포가 진행 중입니다."; exit 1; }

work=$(mktemp -d .deploy/release.XXXXXXXX)
trap 'rm -rf -- "$work"' EXIT
cat > "$work/incoming-service.env"
if [[ "$service" == backend ]]; then
    test -s "$work/incoming-service.env"
fi
cp .env "$work/previous.env"
had_service_env=false
if [[ -f "$service_env" ]]; then
    cp "$service_env" "$work/previous-service.env"
    had_service_env=true
fi

dc() { docker compose --project-name "$project" --env-file "$1" -f "$compose_file" --profile app "${@:2}"; }
dc .env config --format json > "$work/current.json"
has_mysql=$(python3 - "$work/current.json" <<'PY'
import json, sys
with open(sys.argv[1]) as source:
    print("true" if "mysql" in json.load(source)["services"] else "false")
PY
)

previous_id=$(dc .env ps -a -q "$service")
previous_image=""
if [[ -n "$previous_id" ]]; then
    previous_image=$(docker inspect --format '{{.Config.Image}}' "$previous_id")
fi

# 새 서비스 설정을 검증하고, 서버 .env에서는 배포 대상 이미지 한 줄만 바꾼다.
python3 - "$work" "$service" "$image" "$previous_image" "$has_mysql" <<'PY'
import json, pathlib, re, sys

work = pathlib.Path(sys.argv[1])
service, image, previous, has_mysql = sys.argv[2:]
config = json.loads((work / "current.json").read_text())
images = {name: config["services"][name]["image"] for name in ("frontend", "backend")}

def parse_service_env(source):
    keys = {}
    lines = source.read_text().splitlines()
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line)
        if not match:
            raise SystemExit("환경설정은 한 줄 KEY=VALUE 형식이어야 합니다.")
        key, value = match.groups()
        if key in ("FE_IMAGE", "BE_IMAGE"):
            raise SystemExit("서비스 환경설정에 이미지 키를 넣을 수 없습니다.")
        if key in keys:
            raise SystemExit(f"환경설정 키가 중복되었습니다: {key}")
        keys[key] = value
    required = {
        # FE는 현재 실행 시점 환경변수가 없으므로 빈 설정을 허용한다.
        "frontend": (),
        "backend": (
            "SERVER_PORT",
            "SPRING_DATASOURCE_URL",
            "SPRING_DATASOURCE_USERNAME",
            "SPRING_DATASOURCE_PASSWORD",
            "SPRING_JPA_HIBERNATE_DDL_AUTO",
        ),
    }[service]
    if service == "backend" and has_mysql == "true":
        required += ("MYSQL_DATABASE", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD")
    missing = [key for key in required if not keys.get(key)]
    if missing:
        raise SystemExit("필수 환경설정이 없습니다: " + ", ".join(missing))
    return lines

def write_image_env(source, destination, selected):
    lines = []
    for line in source.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            lines.append(line)
            continue
        match = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line)
        if not match:
            raise SystemExit("서버 .env는 한 줄 KEY=VALUE 형식이어야 합니다.")
        if match.group(1) not in ("FE_IMAGE", "BE_IMAGE"):
            lines.append(line)
    values = images | {service: selected}
    lines += ["FE_IMAGE=" + values["frontend"], "BE_IMAGE=" + values["backend"]]
    destination.write_text("\n".join(lines) + "\n")

service_lines = parse_service_env(work / "incoming-service.env")
(work / "candidate-service.env").write_text("\n".join(service_lines) + "\n")
write_image_env(work / "previous.env", work / "candidate.env", image)
if previous:
    write_image_env(work / "previous.env", work / "rollback.env", previous)
PY

dc "$work/candidate.env" config --quiet
# 이미지 내려받기에 실패하면 현재 설정과 컨테이너를 건드리지 않는다.
dc "$work/candidate.env" pull "$service"

wait_healthy() {
    local deadline=$((SECONDS + 240)) id state
    while (( SECONDS < deadline )); do
        id=$(dc .env ps -a -q "$service") || return 1
        if [[ -n "$id" ]]; then
            state=$(docker inspect --format '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "$id") || return 1
            case "$state" in
                running/healthy) return 0 ;;
                */unhealthy|exited/*|dead/*|running/missing) return 1 ;;
            esac
        fi
        sleep 5
    done
    return 1
}

rollback() {
    # 이미지와 해당 서비스 환경설정을 함께 이전 상태로 복구한다.
    trap - ERR INT TERM
    echo "배포 실패: 이전 설정과 이미지로 복구합니다."
    cp "$work/previous.env" .env
    if [[ "$had_service_env" == true ]]; then
        cp "$work/previous-service.env" "$service_env"
    else
        rm -f -- "$service_env"
    fi
    if [[ -n "$previous_image" ]]; then
        cp "$work/rollback.env" .env
        if dc .env up -d --no-build --no-deps "$service" && wait_healthy; then
            echo "이전 버전 복구 완료"
        else
            echo "이전 버전 복구 확인 실패: 서버 점검이 필요합니다." >&2
        fi
    else
        dc .env stop "$service" || true
        echo "첫 배포이므로 복구할 이전 이미지가 없습니다." >&2
    fi
    exit 1
}

trap rollback ERR INT TERM
cp "$work/candidate.env" .env
cp "$work/candidate-service.env" "$service_env"
chmod 600 .env "$service_env"
dc .env config --quiet

# 개발 DB가 포함된 Compose라면 BE와 DB의 통합 설정으로 MySQL을 준비한다.
if [[ "$service" == backend && "$has_mysql" == true ]]; then
    test -s db/schema.sql || {
        echo "개발 DB 스키마 파일이 없습니다: $(pwd)/db/schema.sql" >&2
        false
    }
    dc .env up -d mysql
    mysql_deadline=$((SECONDS + 180))
    while (( SECONDS < mysql_deadline )); do
        mysql_id=$(dc .env ps -q mysql)
        if [[ -n "$mysql_id" ]] && [[ "$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$mysql_id")" == healthy ]]; then
            break
        fi
        sleep 5
    done
    [[ -n "${mysql_id:-}" ]] && [[ "$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$mysql_id")" == healthy ]] || {
        echo "개발 DB가 제한 시간 안에 healthy 상태가 되지 않았습니다." >&2
        false
    }
fi

dc .env up -d --no-build --no-deps "$service"
wait_healthy
trap - ERR INT TERM
echo "배포 완료: $service $image"
