"""v3 기반 스택의 입력 연결과 명시적 Change Set 처리를 수행합니다."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = "906462447513"
REGION = "ap-northeast-2"
STACKS = ("eks", "network-addons", "system-node", "dns-addon")
OUTPUT_KEYS = {
    "network-addons": ("ClusterName", "OidcProviderArn", "OidcIssuerHostPath"),
    "system-node": (
        "ClusterName", "ClusterEndpoint", "ClusterCertificateAuthority",
        "ServiceIpv4Cidr", "WorkerSecurityGroupId", "SystemNodeRoleName",
    ),
    "dns-addon": ("ClusterName",),
}
PREVIOUS = {"network-addons": "eks", "system-node": "network-addons", "dns-addon": "system-node"}
STABLE = {"CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE"}


def aws(*args):
    result = subprocess.run(
        ["aws", *args, "--region", REGION, "--output", "json", "--no-cli-pager"],
        text=True, encoding="utf-8", capture_output=True, check=False,
        env={**os.environ, "AWS_CLI_FILE_ENCODING": "UTF-8"},
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout) if result.stdout.strip() else {}


def stack_name(key):
    if key not in STACKS:
        raise ValueError("허용하지 않은 스택입니다.")
    return f"dameokja-v3-{key}"


def describe_stack(key, allow_missing=False):
    try:
        return aws("cloudformation", "describe-stacks", "--stack-name", stack_name(key))["Stacks"][0]
    except RuntimeError as error:
        # 권한·네트워크 오류를 신규 스택으로 처리하지 않습니다.
        if allow_missing and "ValidationError" in str(error) and "does not exist" in str(error):
            return None
        raise


def parameters(key, outputs):
    entries = json.loads((ROOT / "params" / f"{key}.example.json").read_text(encoding="utf-8"))
    required = OUTPUT_KEYS.get(key, ())
    missing = [name for name in required if not outputs.get(name)]
    if missing:
        raise ValueError(f"선행 스택 Output이 없습니다: {', '.join(missing)}")
    for entry in entries:
        name = entry["ParameterKey"]
        if name in required:
            entry["ParameterValue"] = outputs[name]
        if "REPLACE_" in entry["ParameterValue"]:
            raise ValueError(f"자리표시자가 남았습니다: {name}")
    return entries


def check_change_set(change, key, sha):
    if change.get("StackName") != stack_name(key):
        raise ValueError("선택한 스택과 Change Set이 다릅니다.")
    if change.get("Description") != f"v3:{sha}:{key}":
        raise ValueError("현재 main 커밋으로 생성한 Change Set이 아닙니다. plan을 다시 실행합니다.")
    if change.get("Status") != "CREATE_COMPLETE" or change.get("ExecutionStatus") != "AVAILABLE":
        raise ValueError("실행 가능한 Change Set이 아닙니다.")
    for item in change.get("Changes", []):
        resource = item["ResourceChange"]
        if resource["Action"] not in {"Add", "Modify"} or resource.get("Replacement") in {"True", "Conditional"}:
            raise ValueError("삭제·교체 가능 변경은 이 초기 배포 경로에서 실행하지 않습니다.")


def report(change):
    lines = ["## v3 CloudFormation", f"- Stack: `{change['StackName']}`",
             f"- Change Set ARN: `{change['ChangeSetId']}`", "", "| 자원 | 동작 | 교체 |", "|---|---|---|"]
    for item in change.get("Changes", []):
        resource = item["ResourceChange"]
        lines.append(f"| {resource['LogicalResourceId']} | {resource['Action']} | {resource.get('Replacement', '-')} |")
    message = "\n".join(lines) + "\n"
    print(message)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("plan", "apply"))
    parser.add_argument("stack", choices=STACKS)
    parser.add_argument("--change-set", default="")
    args = parser.parse_args()
    if os.environ.get("GITHUB_REF") != "refs/heads/main" or os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        raise ValueError("main 브랜치의 수동 Workflow에서만 실행합니다.")
    if os.environ.get("V3_INFRA_ENABLED") != "true":
        raise ValueError("v3 기반 배포가 활성화되지 않았습니다.")
    sha = os.environ.get("GITHUB_SHA", "")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("유효한 Git 커밋 SHA가 필요합니다.")
    role = os.environ.get("EXECUTION_ROLE", "")
    if role != f"arn:aws:iam::{ACCOUNT}:role/dameokja-cfn-execution":
        raise ValueError("확인된 CloudFormation 실행 역할만 사용합니다.")
    if aws("sts", "get-caller-identity")["Account"] != ACCOUNT:
        raise ValueError("대상 AWS 계정이 아닙니다.")
    key = args.stack
    if key in PREVIOUS:
        previous = describe_stack(PREVIOUS[key])
        if previous["StackStatus"] not in STABLE:
            raise ValueError("앞 단계 스택이 안정 상태가 아닙니다.")
    outputs = {}
    if key != "eks":
        foundation = describe_stack("eks")
        if foundation["StackStatus"] not in STABLE:
            raise ValueError("EKS 기반 스택이 안정 상태가 아닙니다.")
        outputs = {item["OutputKey"]: item["OutputValue"] for item in foundation.get("Outputs", [])}
    current = describe_stack(key, allow_missing=True)
    if current and current["StackStatus"] not in STABLE | {"REVIEW_IN_PROGRESS"}:
        raise ValueError("진행 중이거나 복구가 필요한 스택입니다.")
    if args.operation == "plan":
        values = parameters(key, outputs)
        kind = "CREATE" if not current or current["StackStatus"] == "REVIEW_IN_PROGRESS" else "UPDATE"
        name = f"v3-{sha[:12]}-{os.environ.get('GITHUB_RUN_ID', 'manual')}-{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "parameters.json"
            path.write_text(json.dumps(values), encoding="utf-8")
            response = aws(
                "cloudformation", "create-change-set", "--stack-name", stack_name(key),
                "--change-set-name", name, "--change-set-type", kind,
                "--template-body", (ROOT / "cloudformation" / f"{key}.yaml").as_uri(),
                "--parameters", path.as_uri(), "--capabilities", "CAPABILITY_IAM",
                "--role-arn", role, "--description", f"v3:{sha}:{key}",
            )
        arn = response["Id"]
        try:
            aws("cloudformation", "wait", "change-set-create-complete", "--change-set-name", arn)
        except RuntimeError:
            failed = aws("cloudformation", "describe-change-set", "--change-set-name", arn)
            reason = failed.get("StatusReason", "")
            if failed.get("Status") == "FAILED" and (
                "didn't contain changes" in reason or "No updates are to be performed" in reason
            ):
                print("변경 사항이 없습니다. apply는 필요하지 않습니다.")
                return
            raise
        change = aws("cloudformation", "describe-change-set", "--change-set-name", arn)
        report(change)
        check_change_set(change, key, sha)
        print("plan 완료: AWS 자원에는 아직 적용하지 않았습니다.")
        return

    # plan에서 확인한 ARN만 실행하며 apply에서 변경 계획을 다시 만들지 않습니다.
    if not args.change_set.startswith(f"arn:aws:cloudformation:{REGION}:{ACCOUNT}:changeSet/v3-"):
        raise ValueError("plan 결과의 Change Set ARN이 필요합니다.")
    change = aws("cloudformation", "describe-change-set", "--change-set-name", args.change_set)
    check_change_set(change, key, sha)
    report(change)
    creating = current is not None and current["StackStatus"] == "REVIEW_IN_PROGRESS"
    aws("cloudformation", "execute-change-set", "--change-set-name", args.change_set)
    waiter = "stack-create-complete" if creating else "stack-update-complete"
    aws("cloudformation", "wait", waiter, "--stack-name", stack_name(key))
    print(f"완료: {stack_name(key)}. Node Ready·DNS 기능은 별도 검증합니다.")


if __name__ == "__main__":
    main()
