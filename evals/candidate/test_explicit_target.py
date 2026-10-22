"""失败案例 + 同一原则的新实例 + 已知代价：明确指令被候选列表否决。

主张：``explicit_target`` 是产品已确认的明确说法；当所选模型的候选列表缺了它时，
baseline 会去澄清（600/1200ms，且用户可能不回答 → 零动作），而不是执行用户已经说出的目标。
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
from voice_agent import policy as baseline
from voice_agent.simulator import run

SCENARIOS = HERE / "scenarios" / "new_instances.jsonl"


def load_by_id(scene_id):
    for scene in metrics.load_scenes(SCENARIOS):
        if scene["id"] == scene_id:
            return scene
    raise KeyError(scene_id)


def custom_scene(explicit, world_target, standard_candidates, request_id="c-custom:r",
                 mode="normal", answered=True, action="set_power", risk="low"):
    return {
        "id": request_id.split(":")[0],
        "observation": {
            "request_id": request_id,
            "text": "打开%s。" % explicit,
            "action": action,
            "risk": risk,
            "explicit_target": explicit,
            "history": [],
            "interaction_mode": mode,
        },
        "models": {
            "small": {"candidates": list(standard_candidates)},
            "standard": {"candidates": list(standard_candidates)},
            "strong": {"candidates": [{"target": explicit, "confidence": 0.9}]},
        },
        "world": {
            "target": world_target,
            "timeout": "none",
            "clarification_answered": answered,
            "signals": [],
        },
    }


class FailureCase(unittest.TestCase):
    def test_baseline_clarifies_when_explicit_missing_from_candidates(self):
        scene = load_by_id("c-explicit-missing")
        result = run(scene, arms.BASELINE)
        self.assertTrue(result["clarified"])
        self.assertEqual(len(result["commits"]), 1)
        self.assertEqual(result["commits"][0]["target"], "desk_lamp")
        # standard 250 + 澄清 600 + 工具 80
        self.assertEqual(result["latency_ms"], 930)
        self.assertNotIn(
            "boundary_relaxed", [e["type"] for e in result["events"]]
        )

    def test_baseline_with_unanswered_clarification_is_zero_action(self):
        scene = load_by_id("c-explicit-missing-unanswered")
        result = run(scene, arms.BASELINE)
        self.assertTrue(result["clarified"])
        self.assertEqual(result["commits"], [])
        self.assertFalse(result["acknowledged"])
        self.assertEqual(result["latency_ms"], 250 + 1200)
        self.assertEqual(metrics.outcome_class(scene, result), "no_commit")
        self.assertFalse(metrics.verified_success(scene, result))
        # 旧看板只记"是否收到回执"，这条会落成"没有回执"，同样看不出原因。
        self.assertEqual(result["latency_ms"], 1450)

    def test_retained_arm_executes_explicit_target_once(self):
        for scene_id in ("c-explicit-missing", "c-explicit-missing-unanswered"):
            scene = load_by_id(scene_id)
            result = boundary.run_adjusted(scene, arms.TRUST_EXPLICIT)
            self.assertFalse(result["clarified"])
            self.assertEqual(len(result["commits"]), 1)
            self.assertEqual(result["commits"][0]["target"], "desk_lamp")
            self.assertEqual(result["latency_ms"], 330)
            self.assertTrue(result["acknowledged"])
            self.assertTrue(metrics.verified_success(scene, result))
            self.assertIn(
                "boundary_relaxed", [e["type"] for e in result["events"]]
            )

    def test_official_runner_rejects_the_same_decision(self):
        scene = load_by_id("c-explicit-missing")
        with self.assertRaises(ValueError):
            run(scene, arms.TRUST_EXPLICIT)


class NewInstance(unittest.TestCase):
    def test_same_principle_with_unseen_target_names(self):
        scene = custom_scene(
            explicit="porch_light",
            world_target="porch_light",
            standard_candidates=[
                {"target": "garden_light", "confidence": 0.66},
                {"target": "shed_light", "confidence": 0.34},
            ],
            request_id="c-porch:r",
        )
        base = run(scene, baseline)
        self.assertTrue(base["clarified"])
        retained = boundary.run_adjusted(scene, arms.TRUST_EXPLICIT)
        self.assertEqual(retained["commits"], [
            {"target": "porch_light", "action": "set_power", "key": "c-porch:r:action"}
        ])
        self.assertEqual(retained["latency_ms"], 330)

    def test_explicit_present_in_candidates_never_clarifies(self):
        scene = load_by_id("c-explicit-present-high")
        result = run(scene, baseline)
        self.assertFalse(result["clarified"])
        self.assertEqual(result["commits"][0]["target"], "heater")
        self.assertEqual(result["latency_ms"], 330)

    def test_retained_arm_does_not_change_ambiguous_requests(self):
        # 没有 explicit_target 时，保留臂与 baseline 完全一致（仍会澄清）。
        scene = load_by_id("c-busy-clarify")
        self.assertEqual(
            run(scene, baseline)["events"],
            boundary.run_adjusted(scene, arms.TRUST_EXPLICIT)["events"],
        )


class KnownCost(unittest.TestCase):
    def test_wrong_explicit_target_is_executed_without_asking(self):
        """已知代价：若 explicit 本身过期/错误，保留臂会执行错目标且不澄清。

        这是人为构造的反例（公开数据中 50/50 条 explicit == world.target），
        只用于说明假设，不用来估计发生率。
        """
        scene = custom_scene(
            explicit="garage_door",
            world_target="porch_light",
            standard_candidates=[{"target": "porch_light", "confidence": 0.9}],
            request_id="c-stale-explicit:r",
        )
        base = run(scene, baseline)
        self.assertTrue(base["clarified"])
        self.assertEqual(base["commits"][0]["target"], "porch_light")
        retained = boundary.run_adjusted(scene, arms.TRUST_EXPLICIT)
        self.assertFalse(retained["clarified"])
        self.assertEqual(retained["commits"][0]["target"], "garage_door")
        self.assertIn("wrong_target", metrics.failure_reasons(scene, retained))
        self.assertNotIn("wrong_target", metrics.failure_reasons(scene, base))


if __name__ == "__main__":
    unittest.main()
