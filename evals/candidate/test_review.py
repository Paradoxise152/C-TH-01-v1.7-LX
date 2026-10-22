"""人工复核工具的测试：差异判定只看核实结果/提交/时延，零动作只列没有提交的场景。"""
import importlib.util
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for path in (str(ROOT), str(HERE)):
    if path not in sys.path:
        sys.path.insert(0, path)

spec = importlib.util.spec_from_file_location("candidate_review", HERE / "review.py")
candidate_review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate_review)


def scene(scene_id, text="打开那个", risk="low", explicit=None, target="lamp", mode="normal"):
    return {
        "id": scene_id,
        "observation": {
            "request_id": scene_id + ":r",
            "text": text,
            "action": "set_power",
            "risk": risk,
            "explicit_target": explicit,
            "history": [],
            "interaction_mode": mode,
        },
        "models": {"standard": {"candidates": [{"target": target, "confidence": 0.9}]}},
        "world": {"target": target, "timeout": "none", "clarification_answered": True, "signals": []},
    }


def run(target="lamp", latency=330, clarified=False, commits=None, ack=True):
    commits = commits if commits is not None else [
        {"target": target, "action": "set_power", "key": "k1"}
    ]
    return {
        "id": "x",
        "observation": {},
        "events": [{"type": "model", "model": "standard", "prediction": {"candidates": []}}],
        "latency_ms": latency,
        "model_cost_usd": 0.003,
        "clarified": clarified,
        "acknowledged": ack,
        "commits": commits,
    }


class ChangedRows(unittest.TestCase):
    def test_records_latency_and_outcome_changes_only(self):
        a = scene("s-a")
        b = scene("s-b")
        c = scene("s-c")
        before = [(a, run(latency=930, clarified=True)), (b, run()), (c, run())]
        after = [(a, run(latency=330)), (b, run()), (c, run(target="tv"))]
        rows = candidate_review.changed_rows(before, after)
        self.assertEqual([row["before"]["id"] for row in rows], ["s-a", "s-c"])
        self.assertEqual(rows[0]["before"]["latency_ms"], 930)
        self.assertEqual(rows[0]["after"]["latency_ms"], 330)
        self.assertEqual(rows[1]["before"]["outcome"], "ok")
        self.assertEqual(rows[1]["after"]["outcome"], "wrong_target")

    def test_rejects_mismatched_scene_sets(self):
        with self.assertRaises(ValueError):
            candidate_review.changed_rows(
                [(scene("s-a"), run())], [(scene("s-b"), run())]
            )

    def test_markdown_mentions_every_changed_scene(self):
        rows = candidate_review.changed_rows(
            [(scene("s-a"), run(latency=930, clarified=True))],
            [(scene("s-a"), run(latency=330))],
        )
        markdown = candidate_review.changed_markdown(rows)
        self.assertIn("s-a", markdown)
        self.assertIn("930", markdown)
        self.assertIn("330", markdown)


class ZeroAction(unittest.TestCase):
    def test_only_scenes_without_commits(self):
        pairs = [
            (scene("s-a"), run(commits=[], clarified=True, ack=False)),
            (scene("s-b"), run()),
        ]
        rows = candidate_review.zero_action_rows(pairs)
        self.assertEqual([row["id"] for row in rows], ["s-a"])
        self.assertTrue(rows[0]["clarified"])

    def test_summary_counts_verified_success(self):
        pairs = [
            (scene("s-a"), run()),
            (scene("s-b"), run(commits=[], clarified=True, ack=False)),
        ]
        summary = candidate_review.summary_row(pairs)
        self.assertEqual(summary["verified_task_success"], {"numerator": 1, "denominator": 2, "rate": 0.5})
        self.assertEqual(summary["outcomes"]["no_commit"], 1)


if __name__ == "__main__":
    unittest.main()
