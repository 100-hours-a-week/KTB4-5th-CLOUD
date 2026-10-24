#!/usr/bin/env python3
"""k6 원시 표본으로 오류율·p95·사용자 쓰기 실패 구간을 계산한다. 네트워크 요청은 하지 않는다."""
import argparse
import json
import math
from pathlib import Path


def percentile(values, p):
    return sorted(values)[max(0, math.ceil(len(values) * p) - 1)] if values else None


def read_samples(path):
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if '"type":"sample"' not in line and '"type": "sample"' not in line:
            continue
        # k6 버전에 따라 console 출력에 접두·접미 문구가 붙어도 JSON 부분만 읽는다.
        row = json.loads(line[line.index("{"):line.rindex("}") + 1])
        if row.get("type") == "sample":
            if row.get("kind") not in ("read", "write") or row["endedMs"] < row["startedMs"]:
                raise ValueError("잘못된 원시 표본")
            rows.append(row)
    if not rows:
        raise ValueError("업무 표본 없음")
    return rows


def metrics(rows):
    return {"count": len(rows), "errors": sum(not r["ok"] for r in rows),
            "errorRate": sum(not r["ok"] for r in rows) / len(rows) if rows else None,
            "server5xxRate": sum(r["status"] >= 500 for r in rows) / len(rows) if rows else None,
            "p95Ms": percentile([r["durationMs"] for r in rows], .95)}


def write_outages(rows):
    """실패를 둘러싼 성공 응답 간격을 보수적 관측 구간으로 남긴다. 재시도·DB 복구는 하지 않는다."""
    episodes, pending, previous = [], None, None
    for row in sorted(rows, key=lambda r: r["endedMs"]):
        if not row["ok"]:
            if pending is None:
                pending = {"lastSuccessMs": previous, "firstFailureMs": row["endedMs"],
                           "firstFailureRequestStartedMs": row["startedMs"], "failedRequests": 0}
            pending["lastFailureMs"] = row["endedMs"]
            pending["failedRequests"] += 1
        else:
            if pending is not None:
                pending["firstRecoveryMs"] = row["endedMs"]
                pending["failureToRecoveryMs"] = row["endedMs"] - pending["firstFailureMs"]
                pending["successGapMs"] = row["endedMs"] - previous if previous is not None else None
                episodes.append(pending)
                pending = None
            previous = row["endedMs"]
    if pending is not None:
        pending["firstRecoveryMs"] = None
        episodes.append(pending)
    return episodes


def evaluate(summary, rows, baseline=None, budget=None):
    for row in rows:
        if row["runId"] != summary["runId"] or row["phase"] != summary["phase"]:
            raise ValueError("다른 실행 또는 단계 표본 혼입")
    grouped = {kind: [r for r in rows if r["kind"] == kind] for kind in ("read", "write")}
    counts_ok = all(len(grouped[k]) == summary["expectedRequests"][k] for k in grouped)
    markers = [r["marker"] for r in grouped["write"]]
    if len(set(markers)) != len(markers):
        raise ValueError("쓰기 표식 중복: 같은 실행 ID 재사용 여부 확인")
    endpoints = {k: metrics(v) for k, v in grouped.items()}
    outcomes = write_outages(grouped["write"])
    windows = []
    t0 = min(r["startedMs"] for r in rows)
    for i in range(math.ceil(summary["seconds"] / 60)):
        selected = [r for r in rows if i * 60000 <= r["startedMs"] - t0 < (i + 1) * 60000]
        windows.append({"minute": i, "endpoints": {k: metrics([r for r in selected if r["kind"] == k]) for k in grouped}})
    latency_ok = None
    if baseline:
        for key in ("baseUrl", "seconds", "readRps", "writeRps"):
            if baseline[key] != summary[key]:
                raise ValueError("기준 측정 조건 불일치")
        if baseline.get("result") != "pass":
            raise ValueError("실패한 기준 측정은 사용할 수 없음")
        latency_ok = all(endpoints[k]["p95Ms"] is not None
                         and baseline["endpoints"][k]["p95Ms"] > 0
                         and endpoints[k]["p95Ms"] <= 2 * baseline["endpoints"][k]["p95Ms"] for k in grouped)
    dropped = summary.get("metrics", {}).get("dropped_iterations", {}).get("values", {}).get("count", 0)
    complete = counts_ok and dropped == 0
    if summary["mode"] == "dns":
        passed = complete and all(x["errors"] == 0 for x in endpoints.values()) and latency_ok is not False
        result = "pass" if passed else "fail"
    else:
        # 시간 예산이 미확정이거나 회복이 관측되지 않았다면 성공으로 간주하지 않는다.
        bounded = all(x.get("successGapMs") is not None and x["successGapMs"] <= budget for x in outcomes) if budget else None
        read_errors_outside_pause = [r for r in grouped["read"] if not r["ok"] and not any(
            outage["firstFailureRequestStartedMs"] <= r["startedMs"] <= (outage.get("firstRecoveryMs") or float("inf"))
            for outage in outcomes)]
        result = "needs_review" if budget is None else ("pass" if complete and bounded and not read_errors_outside_pause else "fail")
    return {**{k: summary[k] for k in ("runId", "phase", "mode", "baseUrl", "seconds", "readRps", "writeRps")},
            "result": result, "complete": complete, "droppedIterations": dropped, "endpoints": endpoints,
            "latencyWithinBaselineX2": latency_ok, "writeOutages": outcomes, "minuteWindows": windows,
            "writeObservationIntervalMs": 1000 / summary["writeRps"],
            "notes": ["HTTP 성공의 DB 반영 여부·누락·중복은 별도 대조 필요", "실패 미관측은 중단 시간 0의 증거가 아님",
                      "v2 요청 비율과 전체 사용자 지표는 동시간대 CloudWatch/로그에서 별도 확인"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--samples", required=True)
    parser.add_argument("--baseline")
    parser.add_argument("--max-user-write-gap-ms", type=float)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    summary = json.loads(Path(args.summary).read_text(encoding="utf-8"))
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8")) if args.baseline else None
    report = evaluate(summary, read_samples(args.samples), baseline, args.max_user_write_gap_ms)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"result": report["result"], "endpoints": report["endpoints"]}, ensure_ascii=False))
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
