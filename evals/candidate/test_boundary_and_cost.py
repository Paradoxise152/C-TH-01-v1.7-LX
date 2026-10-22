"""边界与代价：澄清/超时的固定成本、时延目标是否可达、以及数据本身的信息上限。

这些断言不评价策略好坏，只固定"在当前固定成本与数据下，什么数字是可能的"。
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
import boundary
import metrics
from voice_agent.evaluation import percentile as official_percentile
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


class FixedCosts(unittest.TestCase):
    def test_busy_clarification_costs_1200(self):
        result = run(load_by_id("c-busy-clarify"), arms.BASELINE)
        self.assertTrue(result["clarified"])
        # standard 250 + busy 澄清 1200 + 工具 80
        self.assertEqual(result["latency_ms"], 1530)
        self.assertEqual(result["commits"][0]["target"], "attic_light")

    def test_missing_interaction_mode_defaults_to_normal(self):
        scene = load_by_id("c-no-mode")
        self.assertNotIn("interaction_mode", scene["observation"])
        result = run(scene, arms.BASELINE)
        self.assertEqual(result["latency_ms"], 930)

    def test_retained_arm_keeps_timeout_handling_on_explicit_requests(self):
        scene = load_by_id("c-timeout-after")
        result = boundary.run_adjusted(scene, arms.TRUST_EXPLICIT)
        self.assertEqual(len(result["commits"]), 1)
        self.assertTrue(result["acknowledged"])
        self.assertEqual(result["latency_ms"], 830)
        self.assertNotIn("boundary_relaxed", [e["type"] for e in result["events"]])

    def test_percentile_is_the_original_nearest_rank(self):
        self.assertIs(metrics.percentile, official_percentile)
        self.assertEqual(metrics.percentile([1, 2, 3, 4, 5], 0.95), 5)


class LatencyGoalFeasibility(unittest.TestCase):
    """产品目标的"明确且低风险简单指令 P95 ≤ 600ms"在当前固定成本下的可行性。"""

    @classmethod
    def setUpClass(cls):
        scenes = load_public()
        cls.explicit_low = [
            s for s in scenes
            if s["observation"]["explicit_target"] and s["observation"]["risk"] == "low"
        ]

    def test_group_size_and_timeouts(self):
        self.assertEqual(len(self.explicit_low), 36)
        self.assertEqual(
            sum(1 for s in self.explicit_low if s["world"]["timeout"] != "none"), 4
        )

    def test_floor_exceeds_the_600ms_target(self):
        small = metrics.latency_floor(self.explicit_low, "small")
        standard = metrics.latency_floor(self.explicit_low, "standard")
        strong = metrics.latency_floor(self.explicit_low, "strong")
        self.assertEqual(small["p95_latency_ms"], 680)
        self.assertEqual(standard["p95_latency_ms"], 830)
        self.assertEqual(strong["p95_latency_ms"], 1280)
        for floor in (small, standard, strong):
            self.assertGreater(floor["p95_latency_ms"], 600)

    def test_without_timeout_samples_the_group_is_fast(self):
        no_timeout = [s for s in self.explicit_low if s["world"]["timeout"] == "none"]
        self.assertEqual(len(no_timeout), 32)
        self.assertEqual(metrics.latency_floor(no_timeout, "standard")["p95_latency_ms"], 330)

    def test_retained_arm_reaches_the_floor_not_the_goal(self):
        runs = [boundary.run_adjusted(s, arms.TRUST_EXPLICIT) for s in self.explicit_low]
        self.assertEqual(metrics.percentile([r["latency_ms"] for r in runs], 0.95), 830)
        self.assertEqual(
            metrics.latency_floor(self.explicit_low, "standard")["p95_latency_ms"], 830
        )


class InformationLimit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenes = load_public()

    def test_identical_inputs_with_divergent_truths(self):
        report = metrics.identical_input_groups(self.scenes)
        self.assertEqual(report["observable_groups"], 46)
        self.assertEqual(report["duplicated_groups"], 18)
        self.assertEqual(report["divergent_target_groups"], 5)
        self.assertEqual(report["scenes_in_divergent_target_groups"], 30)
        self.assertEqual(report["divergent_answer_groups"], 11)
        at_least_one_identical_predictions = any(
            example["identical_predictions"] for example in report["examples"]
        )
        self.assertTrue(at_least_one_identical_predictions)

    def test_ceiling_bounds_every_single_action_policy(self):
        report = metrics.ceiling(self.scenes)
        self.assertEqual(report["sessions"], 100)
        self.assertEqual(report["ceiling"], 94)
        baseline_runs = [run(s, arms.BASELINE) for s in self.scenes]
        realized = sum(
            metrics.verified_success(s, r) for s, r in zip(self.scenes, baseline_runs)
        )
        self.assertLess(realized, report["ceiling"])
        self.assertEqual(realized, 88)

    def test_same_input_pair_caps_at_one_of_two(self):
        scenes = [
            s for s in metrics.load_scenes(SCENARIOS)
            if s["id"] in ("c-same-input-a", "c-same-input-b")
        ]
        self.assertEqual(len(scenes), 2)
        self.assertEqual(len({metrics.observable_key(s) for s in scenes}), 1)
        self.assertEqual(len({s["world"]["target"] for s in scenes}), 2)
        self.assertEqual(metrics.ceiling(scenes)["ceiling"], 1)


if __name__ == "__main__":
    unittest.main()
