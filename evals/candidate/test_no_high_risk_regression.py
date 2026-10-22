"""正常回归：保留臂不得引入高风险错误目标 / 重复非幂等动作，且放宽必须最小。

同时锁住两个"取舍依据"的数字：明确→strong 臂的成本、放开高风险臂的错误数。
"""
import copy
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for path in (str(ROOT), str(HERE)):
    if path not in sys.path:
        sys.path.insert(0, path)

import arms
import boundary
import metrics
from voice_agent.simulator import run

PUBLIC = ROOT / "data" / "public_scenarios.jsonl"
SCENARIOS = HERE / "scenarios" / "new_instances.jsonl"


def load_public():
    return metrics.load_scenes(PUBLIC)


def load_by_id(scene_id):
    for scene in metrics.load_scenes(SCENARIOS):
        if scene["id"] == scene_id:
            return scene
    raise KeyError(scene_id)


def replay(scenes, policy, runner=run):
    return [runner(scene, policy) for scene in scenes]


class RetainedArmRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenes = load_public()
        cls.baseline_runs = replay(cls.scenes, arms.BASELINE)
        cls.retained_runs = replay(cls.scenes, arms.TRUST_EXPLICIT, boundary.run_adjusted)

    def test_baseline_numbers_match_original_board(self):
        summary = metrics.summarize_verified(self.scenes, self.baseline_runs, with_groups=False)
        self.assertEqual(summary["sessions"], 100)
        self.assertEqual(summary["legacy"]["legacy_task_success"]["numerator"], 90)
        self.assertEqual(summary["verified_task_success"]["numerator"], 88)
        self.assertEqual(summary["high_risk"]["wrong_target"]["numerator"], 0)
        self.assertEqual(summary["non_idempotent"]["duplicate"]["numerator"], 0)
        self.assertEqual(summary["p95_latency_ms"], 1380)
        self.assertEqual(summary["mean_model_cost_usd"], 0.00314)

    def test_retained_arm_keeps_zero_high_risk_errors_and_duplicates(self):
        summary = metrics.summarize_verified(self.scenes, self.retained_runs, with_groups=False)
        self.assertEqual(summary["verified_task_success"]["numerator"], 89)
        self.assertEqual(summary["legacy"]["legacy_task_success"]["numerator"], 91)
        self.assertEqual(summary["high_risk"]["wrong_target"]["numerator"], 0)
        self.assertEqual(summary["non_idempotent"]["duplicate"]["numerator"], 0)
        self.assertEqual(summary["clarification"]["attempted"]["numerator"], 38)
        self.assertEqual(summary["clarification"]["unanswered"]["numerator"], 9)
        self.assertEqual(summary["mean_model_cost_usd"], 0.00314)

    def test_retained_arm_never_asks_for_an_explicit_target(self):
        for scene, result in zip(self.scenes, self.retained_runs):
            if scene["observation"].get("explicit_target") and result["clarified"]:
                self.fail("explicit request was clarified: %s" % scene["id"])

    def test_retained_arm_only_differs_on_the_five_explicit_scenes(self):
        changed = [
            (s["id"], b, r) for s, b, r in zip(self.scenes, self.baseline_runs, self.retained_runs)
            if b["latency_ms"] != r["latency_ms"] or b["commits"] != r["commits"]
        ]
        self.assertEqual(len(changed), 5)
        for scene_id, base, retained in changed:
            scene = next(s for s in self.scenes if s["id"] == scene_id)
            self.assertTrue(scene["observation"]["explicit_target"])
            self.assertTrue(base["clarified"])
            self.assertFalse(retained["clarified"])
            self.assertEqual(len(retained["commits"]), 1)
            self.assertEqual(retained["commits"][0]["target"], scene["observation"]["explicit_target"])


class Minimality(unittest.TestCase):
    def test_adjusted_runner_is_identical_under_baseline_policy(self):
        for scene in load_public():
            self.assertEqual(run(scene, arms.BASELINE), boundary.run_adjusted(scene, arms.BASELINE))

    def test_policy_never_sees_world_or_scene_id(self):
        seen = []

        class Spy(arms.Baseline):
            def select_model(self, request):
                seen.append(dict(request))
                return "standard"

            def choose_action(self, request, prediction):
                seen.append(dict(request))
                return {"kind": "abstain"}

            def retry_key(self, request, timeout):
                seen.append(dict(timeout))
                return None

        scene = load_public()[0]
        run(scene, Spy())
        boundary.run_adjusted(scene, Spy())
        self.assertTrue(seen)
        for request in seen:
            self.assertNotIn("world", request)
            self.assertNotIn("id", request)
            self.assertNotIn("models", request)


class IdempotentRetryUnchanged(unittest.TestCase):
    def test_after_commit_retry_deduplicates(self):
        scene = load_by_id("c-timeout-after")
        result = run(scene, arms.BASELINE)
        types = [e["type"] for e in result["events"]]
        self.assertIn("tool_timeout", types)
        self.assertIn("deduplicated", types)
        self.assertEqual(len(result["commits"]), 1)
        self.assertTrue(result["acknowledged"])
        self.assertEqual(result["latency_ms"], 830)

    def test_before_commit_retry_commits_once(self):
        scene = load_by_id("c-timeout-before")
        result = run(scene, arms.BASELINE)
        self.assertEqual(len(result["commits"]), 1)
        self.assertTrue(result["acknowledged"])
        self.assertEqual(result["latency_ms"], 830)
        self.assertNotIn("deduplicated", [e["type"] for e in result["events"]])


class Determinism(unittest.TestCase):
    def test_adjusted_runner_is_deterministic_and_pure(self):
        scene = load_by_id("c-explicit-missing")
        before = copy.deepcopy(scene)
        first = boundary.run_adjusted(scene, arms.TRUST_EXPLICIT)
        second = boundary.run_adjusted(scene, arms.TRUST_EXPLICIT)
        self.assertEqual(first, second)
        self.assertEqual(scene, before)


class RejectedAlternatives(unittest.TestCase):
    """两个"不采用"的决定，用数字锁住原因。"""

    def test_explicit_to_strong_fixes_one_but_breaks_cost_target(self):
        scenes = load_public()
        runs = replay(scenes, arms.EXPLICIT_TO_STRONG)
        summary = metrics.summarize_verified(scenes, runs, with_groups=False)
        self.assertEqual(summary["verified_task_success"]["numerator"], 89)
        self.assertGreater(summary["mean_model_cost_usd"], 0.004)
        explicit_low = [
            (s, r) for s, r in zip(scenes, runs)
            if s["observation"]["explicit_target"] and s["observation"]["risk"] == "low"
        ]
        self.assertEqual(
            metrics.percentile([r["latency_ms"] for _, r in explicit_low], 0.95), 1280
        )

    def test_high_risk_execute_creates_high_risk_errors(self):
        scenes = load_public()
        runs = replay(scenes, arms.HIGH_RISK_EXECUTE)
        summary = metrics.summarize_verified(scenes, runs, with_groups=False)
        self.assertEqual(summary["high_risk"]["wrong_target"]["numerator"], 3)
        self.assertEqual(summary["verified_task_success"]["numerator"], 85)

    def test_always_execute_top_maximizes_legacy_board(self):
        scenes = load_public()
        runs = replay(scenes, arms.ALWAYS_EXECUTE_TOP)
        summary = metrics.summarize_verified(scenes, runs, with_groups=False)
        self.assertEqual(summary["legacy"]["legacy_task_success"]["numerator"], 100)
        self.assertEqual(summary["verified_task_success"]["numerator"], 81)
        self.assertEqual(summary["high_risk"]["wrong_target"]["numerator"], 9)


if __name__ == "__main__":
    unittest.main()
