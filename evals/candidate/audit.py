"""重放所有对照臂，产出可核实的指标与交付用的 runs/ 目录。

用法（在项目根目录，zh-CN Windows 需先设 PYTHONUTF8=1）：

    python evals/candidate/audit.py

固定输出（重复运行字节相同）：
    runs/before/verified_summary.json   # 对官方产物 runs/before/traces.jsonl 的核实口径
    runs/after/                          # 保留方案（独立边界入口）
    runs/after-contract-strong/          # 被拒绝的替代方案（原契约内）
    runs/withdraw-highrisk-execute/      # 被撤回的实验
    runs/legacy-counterexample/          # 旧口径满分的反例
    evals/candidate/results/audit.json
    evals/candidate/results/arms.md
    evals/candidate/results/history_audit.json

`runs/before/` 本身必须由文档命令 `python task.py run --out runs/before` 生成；
本脚本只读取它，不改写它的 summary.json，这样提交物与文档命令一一对应。
"""
import argparse
import collections
import json
import sys
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

DEFAULT_SCENARIOS = ROOT / "data" / "public_scenarios.jsonl"
DEFAULT_HISTORY = ROOT / "data" / "history_sessions.jsonl"

ARMS = [
    {
        "key": "after",
        "label": "保留：明确目标不再被候选列表否决（独立边界入口）",
        "command": "python evals/candidate/audit.py",
        "policy": arms.TRUST_EXPLICIT,
        "runner": boundary.run_adjusted,
    },
    {
        "key": "after-contract-strong",
        "label": "拒绝：明确指令改走 strong（原契约内）",
        "command": "python evals/candidate/audit.py",
        "policy": arms.EXPLICIT_TO_STRONG,
        "runner": run,
    },
    {
        "key": "withdraw-highrisk-execute",
        "label": "撤回：高风险无明确目标时按置信度直接执行",
        "command": "python evals/candidate/audit.py",
        "policy": arms.HIGH_RISK_EXECUTE,
        "runner": run,
    },
    {
        "key": "legacy-counterexample",
        "label": "反例：从不澄清、总执行 top 候选（旧看板满分）",
        "command": "python evals/candidate/audit.py",
        "policy": arms.ALWAYS_EXECUTE_TOP,
        "runner": run,
    },
]

EXPLICIT_LOW = lambda s: bool(s["observation"].get("explicit_target")) and s["observation"]["risk"] == "low"  # noqa: E731


def replay(scenes, policy, runner):
    return [runner(scene, policy) for scene in scenes]


def subgroup(scenes, runs, predicate):
    pairs = [(s, r) for s, r in zip(scenes, runs) if predicate(s)]
    if not pairs:
        return {"sessions": 0}
    return {
        "sessions": len(pairs),
        "verified_task_success": {
            "numerator": sum(1 for s, r in pairs if metrics.verified_success(s, r)),
            "denominator": len(pairs),
        },
        "zero_action": sum(1 for _, r in pairs if not r["commits"]),
        "wrong_target": sum(
            1 for s, r in pairs if "wrong_target" in metrics.failure_reasons(s, r)
        ),
        "clarified": sum(1 for _, r in pairs if r["clarified"]),
        "p95_latency_ms": metrics.percentile([r["latency_ms"] for _, r in pairs], 0.95),
        "mean_model_cost_usd": round(
            sum(r["model_cost_usd"] for _, r in pairs) / len(pairs), 6
        ),
        "model_mix": dict(sorted(collections.Counter(
            r["events"][0]["model"] for _, r in pairs
        ).items())),
    }


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def zero_action_detail(scenes, runs):
    return [
        {
            "id": s["id"],
            "text": s["observation"]["text"],
            "risk": s["observation"]["risk"],
            "explicit_target": s["observation"].get("explicit_target"),
            "interaction_mode": s["observation"].get("interaction_mode"),
            "latency_ms": r["latency_ms"],
            "clarified": r["clarified"],
        }
        for s, r in zip(scenes, runs)
        if not r["commits"]
    ]


def arm_row(key, label, scenes, runs):
    summary = metrics.summarize_verified(scenes, runs, with_groups=False)
    return {
        "key": key,
        "label": label,
        "legacy_task_success": summary["legacy"]["legacy_task_success"],
        "verified_task_success": summary["verified_task_success"],
        "outcomes": summary["outcomes"],
        "high_risk_wrong_target": summary["high_risk"]["wrong_target"],
        "duplicate_non_idempotent": summary["non_idempotent"]["duplicate"],
        "clarification": summary["clarification"],
        "p95_latency_ms": summary["p95_latency_ms"],
        "mean_model_cost_usd": summary["mean_model_cost_usd"],
        "explicit_low": subgroup(scenes, runs, EXPLICIT_LOW),
        "zero_action": zero_action_detail(scenes, runs),
        "summary": summary,
    }


def ascii_table(rows):
    """控制台输出只用 ASCII：Windows 控制台代码页不一定是 UTF-8。"""
    header = (
        "arm | legacy | verified | zero_action | wrong | high_risk_wrong | dup | "
        "clarified/unanswered | p95 | p95_explicit_low | cost"
    )
    lines = [header, "-" * len(header)]
    for row in rows:
        lines.append(
            "{key} | {legacy}/{den} | {ok}/{den} | {zero} | {wrong} | {hi} | {dup} | "
            "{clar}/{unans} | {p95} | {p95el} | {cost}".format(
                key=row["key"],
                legacy=row["legacy_task_success"]["numerator"],
                den=row["legacy_task_success"]["denominator"],
                ok=row["verified_task_success"]["numerator"],
                zero=row["outcomes"]["no_commit"],
                wrong=row["outcomes"]["wrong_target"],
                hi=row["high_risk_wrong_target"]["numerator"],
                dup=row["duplicate_non_idempotent"]["numerator"],
                clar=row["clarification"]["attempted"]["numerator"],
                unans=row["clarification"]["unanswered"]["numerator"],
                p95=row["p95_latency_ms"],
                p95el=row["explicit_low"]["p95_latency_ms"],
                cost=row["mean_model_cost_usd"],
            )
        )
    return "\n".join(lines)


def markdown_table(rows):
    lines = [
        "| 臂 | 旧口径成功 | 核实成功 | 零动作 | 错误目标 | 高风险错误目标 | 重复非幂等 | 澄清/未应答 | P95 | 明确低风险 P95 | 平均成本 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| {key} | {legacy}/{den} | {ok}/{den} | {zero} | {wrong} | {hi} | {dup} | {clar}/{unans} | {p95} | {p95el} | {cost} |".format(
                key=row["key"],
                legacy=row["legacy_task_success"]["numerator"],
                den=row["legacy_task_success"]["denominator"],
                ok=row["verified_task_success"]["numerator"],
                zero=row["outcomes"]["no_commit"],
                wrong=row["outcomes"]["wrong_target"],
                hi=row["high_risk_wrong_target"]["numerator"],
                dup=row["duplicate_non_idempotent"]["numerator"],
                clar=row["clarification"]["attempted"]["numerator"],
                unans=row["clarification"]["unanswered"]["numerator"],
                p95=row["p95_latency_ms"],
                p95el=row["explicit_low"]["p95_latency_ms"],
                cost=row["mean_model_cost_usd"],
            )
        )
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="C-TH-01 verified audit")
    parser.add_argument("--scenarios", default=str(DEFAULT_SCENARIOS))
    parser.add_argument("--runs", default=str(ROOT / "runs"))
    parser.add_argument("--results", default=str(HERE / "results"))
    args = parser.parse_args()
    runs_dir = Path(args.runs)
    results_dir = Path(args.results)
    scenes = metrics.load_scenes(args.scenarios)

    before_traces = runs_dir / "before" / "traces.jsonl"
    if not before_traces.exists():
        raise SystemExit(
            "missing %s; run `python task.py run --out runs/before` first" % before_traces
        )
    before_runs = metrics.load_traces(before_traces)
    if len(before_runs) != len(scenes):
        raise SystemExit("runs/before traces do not match scenario count")

    rows = []
    before_row = arm_row("before", "baseline：官方 CLI 产出", scenes, before_runs)
    rows.append(before_row)
    dump(runs_dir / "before" / "verified_summary.json", {
        "note": "对官方 runs/before/traces.jsonl 的核实口径；summary.json 仍是旧看板原生输出。",
        "verified": before_row["summary"],
        "explicit_low": before_row["explicit_low"],
        "zero_action": before_row["zero_action"],
    })

    for arm in ARMS:
        runs = replay(scenes, arm["policy"], arm["runner"])
        (runs_dir / arm["key"]).mkdir(parents=True, exist_ok=True)
        (runs_dir / arm["key"] / "traces.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in runs),
            encoding="utf-8",
            newline="\n",
        )
        row = arm_row(arm["key"], arm["label"], scenes, runs)
        dump(runs_dir / arm["key"] / "summary.json", row["summary"])
        dump(runs_dir / arm["key"] / "verified_summary.json", {
            "verified": row["summary"],
            "explicit_low": row["explicit_low"],
            "zero_action": row["zero_action"],
            "boundary_delta": boundary.BOUNDARY_DELTA if arm["runner"] is boundary.run_adjusted else None,
        })
        rows.append(row)

    history = metrics.summarize_history(metrics.load_scenes(DEFAULT_HISTORY))
    identical = metrics.identical_input_groups(scenes)
    ceiling = metrics.ceiling(scenes)
    ceiling = {"ceiling": ceiling["ceiling"], "sessions": ceiling["sessions"]}
    floors = {
        model: metrics.latency_floor(
            [s for s in scenes if EXPLICIT_LOW(s)], model
        )
        for model in ("small", "standard", "strong")
    }

    audit = {
        "scenarios": str(Path(args.scenarios).relative_to(ROOT)) if str(args.scenarios).startswith(str(ROOT)) else args.scenarios,
        "sessions": len(scenes),
        "boundary_delta": boundary.BOUNDARY_DELTA,
        "arms": {row["key"]: row for row in rows},
        "history": history,
        "identical_inputs": identical,
        "ceiling": ceiling,
        "latency_floor_explicit_low": floors,
        "definitions": {
            "verified_task_success": "正确目标且只执行一次预期操作；toggle/send_message 重复执行即失败",
            "legacy_task_success": "旧看板：只统计是否收到工具回执（保留对照）",
            "p95": "nearest-rank，与 voice_agent.evaluation.percentile 相同",
        },
    }
    dump(results_dir / "audit.json", audit)
    dump(results_dir / "history_audit.json", history)
    (results_dir / "arms.md").write_text(markdown_table(rows), encoding="utf-8", newline="\n")

    print(ascii_table(rows))
    print("ceiling=%d/100  identical_inputs(divergent_target groups)=%d/%d scenes" % (
        ceiling["ceiling"],
        identical["divergent_target_groups"],
        identical["scenes_in_divergent_target_groups"],
    ))
    print("explicit_low p95 floor: small=%s standard=%s strong=%s (target 600)" % (
        floors["small"]["p95_latency_ms"],
        floors["standard"]["p95_latency_ms"],
        floors["strong"]["p95_latency_ms"],
    ))
    print("history: zero_action=%s unanswered_clarification=%s can_verify_target=%s" % (
        history["zero_action_sessions"]["numerator"],
        history["clarification_unanswered"]["numerator"],
        history["can_verify_target"],
    ))
    print("wrote %s" % results_dir)


if __name__ == "__main__":
    main()
