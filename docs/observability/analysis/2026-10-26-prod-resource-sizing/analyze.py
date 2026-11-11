import json
import math
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))
START = datetime.fromisoformat("2026-10-19T07:00:00+00:00")
END = datetime.fromisoformat("2026-10-26T07:10:00+00:00")
RAW = json.loads((ROOT / "metrics-7d.json").read_text(encoding="utf-8-sig"))


def percentile(values, fraction):
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summarize(points):
    if not points:
        return {"count": 0}
    values = [value for _, value in points]
    peak_time, peak_value = max(points, key=lambda point: point[1])
    return {
        "count": len(values),
        "minimum": min(values),
        "mean": statistics.fmean(values),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "maximum": peak_value,
        "maximum_at_kst": peak_time.astimezone(KST).isoformat(),
        "first_at_kst": points[0][0].astimezone(KST).isoformat(),
        "last_at_kst": points[-1][0].astimezone(KST).isoformat(),
    }


series = {}
for result in RAW["MetricDataResults"]:
    points = list(zip(
        [datetime.fromisoformat(value) for value in result["Timestamps"]],
        result["Values"],
    ))
    series.setdefault(result["Id"], []).extend(points)
for points in series.values():
    points.sort()

summary = {
    "start_kst": START.astimezone(KST).isoformat(),
    "end_kst_exclusive": END.astimezone(KST).isoformat(),
    "expected_minutes": int((END - START).total_seconds() / 60),
    "result_statuses": [
        {"id": item["Id"], "status": item["StatusCode"]}
        for item in RAW["MetricDataResults"]
    ],
    "next_token": RAW.get("NextToken"),
    "seven_days": {name: summarize(points) for name, points in series.items()},
    "last_24_hours": {
        name: summarize([point for point in points if point[0] >= END - timedelta(days=1)])
        for name, points in series.items()
    },
    "batch_windows": {
        name: summarize([
            point for point in points
            if point[0].astimezone(KST).hour in (3, 8)
            and point[0].astimezone(KST).minute < 15
        ])
        for name, points in series.items()
    },
    "daily": {},
}

for name, points in series.items():
    days = sorted({point[0].astimezone(KST).date().isoformat() for point in points})
    summary["daily"][name] = {
        day: summarize([
            point for point in points
            if point[0].astimezone(KST).date().isoformat() == day
        ])
        for day in days
    }

summary["request_total"] = sum(value for _, value in series.get("requests", []))
summary["peak_minute_average_rps"] = (
    summary["seven_days"]["requests"]["maximum"] / 60
)
summary["last_24h_memory_maximum_task_mib_at_3712_mib"] = (
    summary["last_24_hours"]["memmax"]["maximum"] * 3712 / 100
)

(ROOT / "summary.json").write_text(
    json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
)
print(json.dumps({key: value for key, value in summary.items() if key != "daily"}, indent=2))
