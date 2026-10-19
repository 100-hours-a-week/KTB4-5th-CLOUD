"""측정 누락·회복 없는 실패를 합격으로 오인하지 않는지 오프라인으로 검증한다."""
import unittest

from evaluate import evaluate


class MeasurementTests(unittest.TestCase):
    def sample(self, kind, seq, ok=True):
        return {"runId": "offline1", "phase": "baseline", "kind": kind, "ok": ok,
                "status": 201 if ok else 503, "startedMs": seq * 1000,
                "endedMs": seq * 1000 + 100, "durationMs": 100, "marker": f"test-{seq}" if kind == "write" else ""}

    def summary(self, mode="dns"):
        return {"runId": "offline1", "phase": "baseline", "baseUrl": "https://dameokja.com", "mode": mode,
                "seconds": 3, "readRps": 1, "writeRps": 1, "expectedRequests": {"read": 3, "write": 3}}

    def rows(self):
        return [self.sample(k, i) for k in ("read", "write") for i in range(3)]

    def test_missing_samples_fail(self):
        self.assertEqual(evaluate(self.summary(), self.rows()[:-1])["result"], "fail")

    def test_unrecovered_outage_fails(self):
        rows = self.rows()
        rows[-1] = self.sample("write", 2, False)
        self.assertEqual(evaluate(self.summary("migration"), rows, budget=3000)["result"], "fail")

    def test_unknown_budget_is_not_success(self):
        self.assertEqual(evaluate(self.summary("migration"), self.rows())["result"], "needs_review")

    def test_baseline_regression_fails(self):
        base = evaluate(self.summary(), self.rows())
        rows = self.rows()
        for row in rows:
            row["durationMs"] = 201
        self.assertEqual(evaluate(self.summary(), rows, baseline=base)["result"], "fail")

    def test_observed_gap_preserved(self):
        rows = self.rows()
        rows[4] = self.sample("write", 1, False)
        report = evaluate(self.summary("migration"), rows, budget=1500)
        self.assertEqual(report["writeOutages"][0]["successGapMs"], 2000)
        self.assertEqual(report["result"], "fail")


if __name__ == "__main__":
    unittest.main()
