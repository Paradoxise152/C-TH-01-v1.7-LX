"""人工复核入口：把 runs/*/traces.jsonl 变成可读、可核对的差异与统计。

它不是评测本身，而是给"人工检查"用的：一条命令看清改动改了什么、为什么改、
哪些场景变了。默认只读，不写 runs/。

用法（项目根目录，zh-CN Windows 先设 PYTHONUTF8=1）：

    python evals/candidate/review.py summary runs/before/traces.jsonl
    python evals/candidate/review.py zero-action runs/before/traces.jsonl
    python evals/candidate/review.py diff runs/before/traces.jsonl runs/after/traces.jsonl
    python evals/candidate/review.py scene s-fca8479a13a9246b

`diff` 默认同时写一份中文 Markdown 到 `evals/candidate/results/changed_scenes.md`。

（文件名不叫 inspect.py：脚本目录会进入 sys.path，那样会遮蔽标准库 ``inspect``。）
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for path in (str(ROOT), str(HERE)):
    if path not in sys.path:
        sys.path.insert(0, path)

import metrics  # noqa: E402

DEFAULT_SCENARIOS = ROOT / "data" / "public_scenarios.jsonl"
DEFAULT_MD = HERE / "results" / "changed_scenes.md"


def load_pairs(scenarios_path, traces_path):
    scenes = metrics.load_scenes(scenarios_path)
    traces = metrics.load_traces(traces_path)
    by_id = {t["id"]: t for t in traces}
    if len(by_id) != len(traces):
        raise ValueError("duplicate ids in %s" % traces_path)
    pairs = []
    for scene in scenes:
        if scene["id"] not in by_id:
            raise ValueError("missing trace for scene %s in %s" % (scene["id"], traces_path))
        pairs.append((scene, by_id[scene["id"]]))
    if len(pairs) != len(scenes):
        raise ValueError("trace/scene count mismatch")
    return pairs


def summary_row(pairs):
    return metrics.summarize_verified(
        [s for s, _ in pairs], [r for _, r in pairs], with_groups=False
    )


def _describe(scene, run):
    commits = run["commits"]
    if not commits:
        action = "no_action"
    elif len(commits) > 1:
        action = "commit x%d" % len(commits)
    else:
        action = "commit"
    return {
        "id": scene["id"],
        "text": scene["observation"]["text"],
        "risk": scene["observation"]["risk"],
        "explicit": scene["observation"].get("explicit_target"),
        "mode": scene["observation"].get("interaction_mode", "normal"),
        "outcome": metrics.outcome_class(scene, run),
        "verified": metrics.verified_success(scene, run),
        "action": action,
        "targets": [c["target"] for c in commits],
        "clarified": run["clarified"],
        "acknowledged": run["acknowledged"],
        "latency_ms": run["latency_ms"],
        "model": run["events"][0]["model"] if run["events"] else None,
        "model_cost_usd": run["model_cost_usd"],
    }


def changed_rows(before_pairs, after_pairs):
    """返回 outcome / 提交 / 时延任一变化的场景。"""
    if [s["id"] for s, _ in before_pairs] != [s["id"] for s, _ in after_pairs]:
        raise ValueError("trace files must cover the same scenes in the same order")
    rows = []
    for (scene, before), (_, after) in zip(before_pairs, after_pairs):
        if (
            metrics.outcome_class(scene, before) != metrics.outcome_class(scene, after)
            or before["commits"] != after["commits"]
            or before["latency_ms"] != after["latency_ms"]
        ):
            rows.append({"before": _describe(scene, before), "after": _describe(scene, after)})
    return rows


def zero_action_rows(pairs):
    return [_describe(s, r) for s, r in pairs if not r["commits"]]


def print_summary(name, summary):
    verified = summary["verified_task_success"]
    legacy = summary["legacy"]["legacy_task_success"]
    print("%s: sessions=%d legacy=%d/%d verified=%d/%d zero_action=%d wrong_target=%d high_risk_wrong=%d dup=%d clarified=%d unanswered=%d p95=%d cost=%.5f" % (
        name,
        summary["sessions"],
        legacy["numerator"], legacy["denominator"],
        verified["numerator"], verified["denominator"],
        summary["outcomes"]["no_commit"],
        summary["outcomes"]["wrong_target"],
        summary["high_risk"]["wrong_target"]["numerator"],
        summary["non_idempotent"]["duplicate"]["numerator"],
        summary["clarification"]["attempted"]["numerator"],
        summary["clarification"]["unanswered"]["numerator"],
        summary["p95_latency_ms"],
        summary["mean_model_cost_usd"],
    ))


def print_changed(rows):
    print("changed scenes: %d" % len(rows))
    print("id | risk | explicit | before(outcome,clarified,latency) | after(outcome,clarified,latency)")
    for row in rows:
        b, a = row["before"], row["after"]
        print("%s | %s | %s | %s,%s,%sms | %s,%s,%sms" % (
            b["id"], b["risk"], b["explicit"] or "-",
            b["outcome"], "clarify" if b["clarified"] else "execute", b["latency_ms"],
            a["outcome"], "clarify" if a["clarified"] else "execute", a["latency_ms"],
        ))


def changed_markdown(rows):
    lines = [
        "# before → after 变化的场景",
        "",
        "由 `python evals/candidate/review.py diff runs/before/traces.jsonl runs/after/traces.jsonl` 生成。",
        "判定变化的口径：核实结果、提交内容或时延任一不同。",
        "",
        "| id | 文本 | risk | explicit | before | after |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        b, a = row["before"], row["after"]
        lines.append("| `%s` | %s | %s | %s | %s %s %sms | %s %s %sms |" % (
            b["id"], b["text"], b["risk"], b["explicit"] or "—",
            b["outcome"], "澄清" if b["clarified"] else "执行", b["latency_ms"],
            a["outcome"], "澄清" if a["clarified"] else "执行", a["latency_ms"],
        ))
    lines += ["", "## 逐条细节", ""]
    for row in rows:
        b, a = row["before"], row["after"]
        lines += [
            "### `%s` %s" % (b["id"], b["text"]),
            "",
            "- before：模型 %s，%s，目标 %s，澄清=%s，回执=%s，时延 %sms，成本 %s" % (
                b["model"], b["action"], b["targets"] or "—", b["clarified"],
                b["acknowledged"], b["latency_ms"], b["model_cost_usd"]),
            "- after：模型 %s，%s，目标 %s，澄清=%s，回执=%s，时延 %sms，成本 %s" % (
                a["model"], a["action"], a["targets"] or "—", a["clarified"],
                a["acknowledged"], a["latency_ms"], a["model_cost_usd"]),
            "",
        ]
    return "\n".join(lines).rstrip("\n") + "\n"


def print_scene(pairs, scene_id):
    matched = [(s, r) for s, r in pairs if s["id"] == scene_id]
    if not matched:
        raise SystemExit("scene not found: %s" % scene_id)
    scene, run = matched[0]
    print(json.dumps(scene, ensure_ascii=False, indent=1))
    print("--- trace ---")
    for event in run["events"]:
        print(json.dumps(event, ensure_ascii=False))
    print("--- result ---")
    print(json.dumps({k: run[k] for k in (
        "latency_ms", "model_cost_usd", "clarified", "acknowledged", "commits"
    )}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="human review over traces")
    parser.add_argument("--scenarios", default=str(DEFAULT_SCENARIOS))
    sub = parser.add_subparsers(dest="command", required=True)

    p_summary = sub.add_parser("summary", help="核实口径汇总")
    p_summary.add_argument("traces")
    p_summary.add_argument("--json", action="store_true")

    p_zero = sub.add_parser("zero-action", help="列出零动作场景")
    p_zero.add_argument("traces")
    p_zero.add_argument("--json", action="store_true")

    p_diff = sub.add_parser("diff", help="比较两份 traces")
    p_diff.add_argument("before")
    p_diff.add_argument("after")
    p_diff.add_argument("--md", default=str(DEFAULT_MD))
    p_diff.add_argument("--json", action="store_true")

    p_scene = sub.add_parser("scene", help="打印单个场景的输入与事件")
    p_scene.add_argument("scene_id")
    p_scene.add_argument("--traces", nargs="+", default=[str(ROOT / "runs" / "before" / "traces.jsonl")])

    args = parser.parse_args()

    if args.command == "summary":
        pairs = load_pairs(args.scenarios, args.traces)
        summary = summary_row(pairs)
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True))
        else:
            print_summary(args.traces, summary)
        return 0

    if args.command == "zero-action":
        pairs = load_pairs(args.scenarios, args.traces)
        rows = zero_action_rows(pairs)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
        else:
            print("zero-action: %d" % len(rows))
            for row in rows:
                print("%s | %s | risk=%s | explicit=%s | mode=%s | %sms | clarified=%s" % (
                    row["id"], row["text"], row["risk"], row["explicit"] or "-",
                    row["mode"], row["latency_ms"], row["clarified"]))
        return 0

    if args.command == "diff":
        before = load_pairs(args.scenarios, args.before)
        after = load_pairs(args.scenarios, args.after)
        rows = changed_rows(before, after)
        print_changed(rows)
        Path(args.md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.md).write_text(changed_markdown(rows), encoding="utf-8", newline="\n")
        print("wrote %s" % args.md)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0

    for traces in args.traces:
        print("=== %s ===" % traces)
        print_scene(load_pairs(args.scenarios, traces), args.scene_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
