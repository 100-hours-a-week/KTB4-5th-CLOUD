#!/usr/bin/env python3
"""로그 조회 결과만 읽어 v1/v2 요청 비율·오류율을 합산한다. DNS의 독립 표본이라고 가정하지 않는다."""
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
def rows(name):
    data = json.loads((root / name).read_text(encoding="utf-8"))
    if data.get("status") != "Complete":
        raise ValueError("완료되지 않은 로그 조회")
    return [{item["field"]: item["value"] for item in row} for row in data["results"]]
counts = {"v1": 0, "v2": 0}
errors = {"v1": 0, "v2": 0}
def side(row):
    group = row["@log"].split(":", 1)[-1]
    mapping = {"/dameokja/prod/nginx-access": "v1", "/dameokja/v2-prod/nginx": "v2"}
    if group not in mapping:
        raise ValueError("예상하지 않은 로그 그룹")
    return mapping[group]
request_rows = rows("requests.json")
for row in request_rows:
    counts[side(row)] += int(row["requests"])
for row in rows("errors.json"):
    errors[side(row)] += int(row["errors"])
total = sum(counts.values())
report = {"requests": counts, "errors5xx": errors,
          "v2SharePercent": 100 * counts["v2"] / total if total else None,
          "errorPercent": {key: 100 * errors[key] / counts[key] if counts[key] else None for key in counts},
          "minuteRequestAndP95": request_rows,
          "scope": "전용 냉장고 URI의 실제 요청 분포. 전체 사용자 지표·DNS 가중치 정밀 일치 판정과 구분",
          "observed": total > 0}
(root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"requests": counts, "v2SharePercent": report["v2SharePercent"], "observed": total > 0}))
raise SystemExit(0 if total else 1)
