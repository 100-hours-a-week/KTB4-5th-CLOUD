"""독립 실행형 결과 알림 YAML을 파트별로 생성한다."""
from pathlib import Path


def render(part):
    code = Path(__file__).with_name("notify.py").read_text(encoding="utf-8")
    header = '''name: Discord CI-CD 결과 알림

on:
  workflow_run:
    workflows: ["*"]
    types: [completed]

# 완료된 작업 메타데이터와 로그만 읽고 저장소 코드는 실행하지 않는다.
permissions:
  actions: read

jobs:
  notify:
    if: >-
      github.event.workflow_run.event == 'push' ||
      github.event.workflow_run.event == 'pull_request'
    runs-on: ubuntu-24.04
    timeout-minutes: 5
    steps:
      - name: 실패 단계와 배포 결과를 Discord에 전달
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          DISCORD_WEBHOOK_URL: ${{ secrets.DISCORD_PART_WEBHOOK_URL }}
          DISCORD_USER_MAP: ${{ secrets.DISCORD_USER_MAP }}
          NOTIFY_SUCCESS: ${{ vars.DISCORD_NOTIFY_SUCCESS }}
          PART: part_name
        shell: bash
        run: |
          python3 - <<'PY'
'''
    return (header.replace("DISCORD_PART_WEBHOOK_URL", f"DISCORD_{part.upper()}_WEBHOOK_URL")
            .replace("part_name", part) + "".join(("          " + line if line else "") + "\n" for line in code.splitlines()) + "          PY\n")


if __name__ == "__main__":
    output_dir = Path(__file__).resolve().parent / "workflows"
    output_dir.mkdir(parents=True, exist_ok=True)
    for part in ("fe", "be", "ai", "cloud"):
        (output_dir / f"discord-cicd-{part}.yml").write_text(render(part), encoding="utf-8")
