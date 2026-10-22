"""核实口径自身的测试：分母、优先级、上限与历史日志缺口。

这些测试直接构造场景/重放字典，不依赖模拟器，用来锁住"什么算成功"的定义。
"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for path in (str(ROOT), str(HERE)):
    if path not in sys.path:
        sys.path.insert(0, path)

import metrics


def scene(
    text="打开那个",
    action="set_power",
    risk="low",
    explicit=None,
    history=None,
    mode="normal",
    target="lamp",
    timeout="none",
    answered=True,
    request_id="r-1",
    candidates=None,
):
    candidates = candidates or [{"target": target, "confidence": 0.9}]
    pred = {"candidates": candidates}
    return {
        "id": request_id,
        "observation": {
            "request_id": request_id,
            "text": text,
            "action": action,
            "risk": risk,
            "explicit_target": explicit,
            "history": history or [],
            "interaction_mode": mode,
        },
        "models": {"small": pred, "standard": pred, "strong": pred},
        "world": {
            "target": target,
            "timeout": timeout,
            "clarification_answered": answered,
            "signals": [],
        },
    }


def result(commits=None, latency=330, cost=0.003, model="standard", clarified=False, ack=True):
    commits = commits or []
    return {
        "id": "x",
        "observation": {},
        "events": [{"type": "model", "model": model, "prediction": {"candidates": []}}],
        "latency_ms": latency,
        "model_cost_usd": cost,
        "clarified": clarified,
        "acknowledged": ack,
        "commits": commits,
    }


def commit(target, key="k1", action="set_power"):
    return {"target": target, "action": action, "key": key}


class FailureReasons(unittest.TestCase):
    def test_no_commit_is_failure(self):
        s = scene()
        r = result(commits=[], ack=False)
        self.assertEqual(metrics.failure_reasons(s, r), {"no_commit"})
        self.assertFalse(metrics.verified_success(s, r))
        self.assertEqual(metrics.outcome_class(s, r), "no_commit")

    def test_wrong_target_is_failure(self):
        s = scene(target="lamp")
        r = result(commits=[commit("tv")])
        self.assertIn("wrong_target", metrics.failure_reasons(s, r))
        self.assertEqual(metrics.outcome_class(s, r), "wrong_target")

    def test_duplicate_non_idempotent_is_failure_even_when_target_correct(self):
        s = scene(action="toggle", target="lamp")
        r = result(commits=[commit("lamp", "k1", "toggle"), commit("lamp", "k2", "toggle")])
        self.assertEqual(
            metrics.failure_reasons(s, r), {"duplicate_non_idempotent"}
        )
        self.assertFalse(metrics.verified_success(s, r))

    def test_duplicate_set_power_is_not_failure(self):
        # PRODUCT：set_power 重复不改变最终开状态，只是增加开销。
        s = scene(action="set_power", target="lamp")
        r = result(commits=[commit("lamp", "k1"), commit("lamp", "k2")])
        self.assertEqual(metrics.failure_reasons(s, r), set())
        self.assertTrue(metrics.verified_success(s, r))

    def test_precedence_no_commit_before_others(self):
        s = scene(action="send_message", target="contact_liu")
        r = result(commits=[commit("contact_wang"), commit("contact_wang")])
        self.assertIn("wrong_target", metrics.failure_reasons(s, r))
        self.assertEqual(metrics.outcome_class(s, r), "wrong_target")


class Summary(unittest.TestCase):
    def setUp(self):
        self.scenes = [
            scene(target="lamp"),                                     # ok
            scene(target="tv"),                                       # wrong target
            scene(action="send_message", target="contact_liu"),       # zero action
            scene(action="toggle", target="fan"),                     # duplicate
        ]
        self.runs = [
            result(commits=[commit("lamp")]),
            result(commits=[commit("fan")]),
            result(commits=[], clarified=True, ack=False, latency=850),
            result(commits=[commit("fan", "k1", "toggle"), commit("fan", "k2", "toggle")]),
        ]

    def test_numerators_and_denominators(self):
        out = metrics.summarize_verified(self.scenes, self.runs)
        self.assertEqual(out["sessions"], 4)
        self.assertEqual(out["verified_task_success"], {"numerator": 1, "denominator": 4, "rate": 0.25})
        self.assertEqual(out["outcomes"]["ok"], 1)
        self.assertEqual(out["outcomes"]["wrong_target"], 1)
        self.assertEqual(out["outcomes"]["no_commit"], 1)
        self.assertEqual(out["outcomes"]["duplicate_non_idempotent"], 1)
        self.assertEqual(out["clarification"]["attempted"], {"numerator": 1, "denominator": 4, "rate": 0.25})
        self.assertEqual(out["clarification"]["unanswered"], {"numerator": 1, "denominator": 4, "rate": 0.25})
        self.assertEqual(out["clarification"]["answered_of_attempted"]["numerator"], 0)
        self.assertEqual(out["high_risk"]["sessions"], 0)
        self.assertEqual(out["non_idempotent"]["sessions"], 2)
        self.assertEqual(out["non_idempotent"]["duplicate"]["numerator"], 1)

    def test_legacy_summary_kept_for_contrast(self):
        out = metrics.summarize_verified(self.scenes, self.runs)
        self.assertEqual(out["legacy"]["sessions"], 4)
        # 3/4 收到回执，但核实只有 2/4 完成。
        self.assertEqual(out["legacy"]["legacy_task_success"]["numerator"], 3)
        self.assertNotEqual(
            out["legacy"]["legacy_task_success"]["numerator"],
            out["verified_task_success"]["numerator"],
        )

    def test_nearest_rank_percentile(self):
        self.assertEqual(metrics.percentile([1, 2, 3, 4, 5], 0.95), 5)
        self.assertEqual(metrics.percentile([100] * 19 + [500], 0.95), 100)
        self.assertIsNone(metrics.percentile([], 0.95))

    def test_group_key_separates_timeout_and_explicit(self):
        a = scene(explicit="lamp")
        b = scene(explicit=None)
        c = scene(explicit="lamp", timeout="after_commit")
        self.assertNotEqual(metrics.group_key(a), metrics.group_key(b))
        self.assertNotEqual(metrics.group_key(a), metrics.group_key(c))


class IdenticalInputs(unittest.TestCase):
    def test_request_id_is_not_a_signal(self):
        a = scene(request_id="r-a", target="lamp")
        b = scene(request_id="r-b", target="tv")
        report = metrics.identical_input_groups([a, b])
        self.assertEqual(report["duplicated_groups"], 1)
        self.assertEqual(report["divergent_target_groups"], 1)
        self.assertEqual(report["scenes_in_divergent_target_groups"], 2)

    def test_ceiling_is_one_action_per_observable_group(self):
        # 同输入、真值不同：执行 top 只能中 1/2，澄清应答 1/2 → 上限 1。
        a = scene(request_id="r-a", target="lamp", candidates=[{"target": "lamp", "confidence": 0.6}])
        b = scene(
            request_id="r-b", target="tv", answered=False,
            candidates=[{"target": "lamp", "confidence": 0.6}],
        )
        report = metrics.ceiling([a, b])
        self.assertEqual(report["ceiling"], 1)
        self.assertEqual(report["sessions"], 2)
        self.assertEqual(report["groups"]["打开那个"]["best_clarify"], 1)

    def test_ceiling_allows_candidate_that_is_not_top(self):
        a = scene(request_id="r-a", target="tv", candidates=[
            {"target": "lamp", "confidence": 0.9},
            {"target": "tv", "confidence": 0.1},
        ])
        self.assertEqual(metrics.ceiling([a])["ceiling"], 1)


class LatencyFloor(unittest.TestCase):
    def test_floor_includes_fixed_timeout_cost(self):
        scenes = [scene(timeout="none")] * 9 + [scene(timeout="after_commit")]
        standard = metrics.latency_floor(scenes, "standard")
        small = metrics.latency_floor(scenes, "small")
        self.assertEqual(standard["timeouts"], 1)
        # nearest-rank：n=10，p95 取第 10 个 → 就是那个超时样本。
        self.assertEqual(standard["p95_latency_ms"], 830)
        self.assertEqual(small["p95_latency_ms"], 680)
        # 明确且低风险的目标是 600ms；固定超时成本使 small 也无法达到。
        self.assertGreater(small["p95_latency_ms"], 600)

    def test_floor_distribution_counts(self):
        scenes = [scene(timeout="none"), scene(timeout="before_commit")]
        out = metrics.latency_floor(scenes, "standard")
        self.assertEqual(out["distribution"], {330: 1, 830: 1})


class HistoryAudit(unittest.TestCase):
    def test_zero_action_detected_and_target_not_verifiable(self):
        records = [
            {
                "id": "s-1",
                "observation": {
                    "text": "打开hall_light。",
                    "action": "set_power",
                    "risk": "low",
                    "explicit_target": "hall_light",
                    "interaction_mode": "busy",
                },
                "events": [
                    {"type": "model", "model": "standard", "prediction": {"candidates": []}},
                    {"type": "clarification", "answered": False},
                    {"type": "assistant_response", "accepted": False},
                ],
                "latency_ms": 1450,
                "model_cost_usd": 0.003,
                "clarified": True,
                "acknowledged": False,
                "commits": [],
            },
            {
                "id": "s-2",
                "observation": {"text": "打开那个", "action": "set_power", "risk": "low",
                                "explicit_target": None, "interaction_mode": "normal"},
                "events": [
                    {"type": "model", "model": "standard", "prediction": {"candidates": []}},
                    {"type": "tool_committed", "target": "lamp", "action": "set_power", "key": "k"},
                    {"type": "assistant_response", "accepted": True},
                ],
                "latency_ms": 330,
                "model_cost_usd": 0.003,
                "clarified": False,
                "acknowledged": True,
                "commits": [commit("lamp")],
            },
        ]
        out = metrics.summarize_history(records)
        self.assertFalse(out["has_world_field"])
        self.assertFalse(out["can_verify_target"])
        self.assertEqual(out["zero_action_sessions"], {"numerator": 1, "denominator": 2, "rate": 0.5})
        self.assertEqual(out["zero_action_detail"][0]["explicit_target"], "hall_light")


if __name__ == "__main__":
    unittest.main()
