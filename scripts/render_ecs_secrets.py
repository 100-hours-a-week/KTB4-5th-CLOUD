"""SSM의 값 조회 없이 서비스별 환경변수 참조를 생성한다."""

import argparse
import json
from pathlib import Path
import re
import subprocess


SSM_PATHS = {
    "frontend": "/dameokja-v2-dev-fe/",
    "backend": "/dameokja-v2-dev/",
}
SERVICES = (*SSM_PATHS, "nginx", "redis")


def parameter_names(path):
    # CLI의 자동 페이지 처리를 유지해 50개를 넘는 파라미터도 모두 수집한다.
    result = subprocess.run(
        [
            "aws", "ssm", "describe-parameters",
            "--parameter-filters", json.dumps([
                {"Key": "Path", "Option": "OneLevel", "Values": [path.rstrip("/")]}
            ]),
            "--query", "Parameters[].Name", "--output", "json", "--no-cli-pager",
        ],
        check=True, capture_output=True, text=True,
    )
    names = json.loads(result.stdout)
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        raise ValueError("SSM 파라미터 이름 조회 결과가 올바르지 않습니다.")
    return names


def render(task, service):
    if service not in SERVICES:
        raise ValueError("지원하지 않는 서비스입니다.")
    if service not in SSM_PATHS:
        return task
    containers = [c for c in task["containerDefinitions"] if c["name"] == service]
    if len(containers) != 1:
        raise ValueError("대상 서비스 컨테이너가 정확히 하나 있어야 합니다.")
    container = containers[0]
    path = SSM_PATHS[service]
    secrets = []
    for full_name in sorted(set(parameter_names(path))):
        # nginx/default.conf 같은 하위 경로와 다른 서비스 경로는 주입하지 않는다.
        if not full_name.startswith(path):
            continue
        name = full_name[len(path):]
        if "/" in name:
            continue
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError("환경변수 이름으로 사용할 수 없는 SSM 파라미터가 있습니다.")
        secrets.append({"name": name, "valueFrom": full_name})
    if not secrets:
        raise ValueError("SSM 환경변수 목록이 비어 있어 배포를 중단합니다.")
    static_names = {item["name"] for item in container.get("environment", [])}
    if static_names.intersection(item["name"] for item in secrets):
        raise ValueError("SSM 환경변수와 Task Definition의 고정 환경변수 이름이 중복됩니다.")
    # 이전 목록과 합치지 않아 SSM에서 삭제한 변수도 다음 배포에 반영한다.
    container["secrets"] = secrets
    return task


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", required=True, choices=SERVICES)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        task = render(json.loads(args.input.read_text(encoding="utf-8")), args.service)
    except subprocess.CalledProcessError:
        # AWS 응답 본문은 출력하지 않는다. 실패하면 기존 파일도 유지한다.
        parser.exit(1, "SSM 목록 조회 실패: AWS 연결과 ssm:DescribeParameters 권한을 확인하세요.\n")
    except (ValueError, KeyError) as error:
        parser.exit(1, f"환경변수 참조 생성 실패: {error}\n")
    args.output.write_text(json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
