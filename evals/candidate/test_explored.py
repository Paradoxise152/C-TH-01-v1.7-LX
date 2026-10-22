"""探索过的邻域：把“试过但没赢”的变体固定成可核对的事实。

结论是：没有任何变体能在“不增加高风险错误/重复、平均成本 ≤0.004、明确低风险 P95
不升高”的前提下提高核实成功。数字都由 audit.explored_row 现场重算，不读结果文件。
"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for path in (str(ROOT), str(HERE)):
    if path not in sys.path:
        sys.path.insert(0, path)

import arms
import audit
import metrics
from voice_agent.simulator import run

PUBLIC = ROOT / "data" / "public_scenarios.jsonl"


class ExploredNeighborhood(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenes = metrics.load_scenes(PUBLIC)
        baseline_runs = [run(s, arms.BASELINE) for s in cls.scenes]
        cls.baseline = audit.arm_row("baseline", "baseline", cls.scenes, baseline_runs)
        cls.rows = {
            key: audit.explored_row(key, label, cls.scenes, audit.replay(cls.scenes, policy, runner))
            for key, label, policy, runner in audit.EXPLORED
        }

    def assert_row(self, key, verified, wrong, high_risk, clarified, cost, p95el):
        row = self.rows[key]
        self.assertEqual(row["verified_task_success"]["numerator"], verified)
        self.assertEqual(row["verified_task_success"]["denominator"], 100)
        self.assertEqual(row["wrong_target"], wrong)
        self.assertEqual(row["high_risk_wrong_target"], high_risk)
        self.assertEqual(row["duplicate_non_idempotent"], 0)
        self.assertEqual(row["clarified"], clarified)
        self.assertEqual(row["p95_explicit_low_ms"], p95el)
        self.assertAlmostEqual(row["mean_model_cost_usd"], cost, places=5)

    def test_baseline_reference(self):
        self.assertEqual(self.baseline["verified_task_success"]["numerator"], 88)
        self.assertEqual(self.baseline["high_risk_wrong_target"]["numerator"], 0)
        self.assertEqual(self.baseline["explicit_low"]["p95_latency_ms"], 930)
        self.assertEqual(self.baseline["mean_model_cost_usd"], 0.00314)

    def test_cheap_models_lose_completion(self):
        self.assert_row("all-small", 83, 3, 0, 57, 0.001, 1280)
        self.assert_row("all-standard", 86, 2, 0, 48, 0.003, 930)

    def test_strong_model_gains_one_but_breaks_cost(self):
        self.assert_row("all-strong", 89, 2, 0, 38, 0.009, 1280)
        self.assertGreater(self.rows["all-strong"]["mean_model_cost_usd"], audit.COST_TARGET_USD)

    def test_thresholds_move_failures_around(self):
        # 降低阈值：澄清少了，但错误目标从 2 升到 5，核实成功没有提高。
        self.assert_row("threshold-0.6", 88, 5, 0, 31, 0.00314, 930)
        self.assertEqual(self.rows["threshold-0.6"]["legacy_task_success"]["numerator"], 93)
        # 提高阈值：错误目标降到 0，但零动作从 10 升到 14。
        self.assert_row("threshold-0.9", 86, 0, 0, 55, 0.00314, 930)
        self.assertEqual(self.rows["threshold-0.9"]["zero_action"], 14)

    def test_regressions_are_kept_visible(self):
        self.assert_row("explicit-to-strong", 89, 2, 0, 38, 0.00614, 1280)
        self.assert_row("high-risk-execute", 85, 5, 3, 37, 0.00314, 930)

    def test_no_variant_dominates_baseline(self):
        for key, row in self.rows.items():
            with self.subTest(variant=key):
                self.assertFalse(
                    audit.dominates_baseline(row, self.baseline),
                    "%s already dominates baseline; the report claim must change" % key,
                )

    def test_all_explored_keys_are_covered(self):
        self.assertEqual(len(self.rows), len(audit.EXPLORED))
        self.assertEqual(
            set(self.rows),
            {"all-small", "all-standard", "all-strong", "threshold-0.6",
             "threshold-0.9", "explicit-to-strong", "high-risk-execute"},
        )


if __name__ == "__main__":
    unittest.main()
