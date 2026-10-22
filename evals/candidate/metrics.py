"""核实后的任务成功口径。

旧看板 ``legacy_task_success`` 只统计"是否收到工具回执"，把"澄清无人应答导致的零动作"
和"执行到错误目标"都算作成功。本模块给出一套可核实的口径，并同时保留旧口径作为对照：

- ``verified_success``：正确目标且只执行一次预期操作；对 toggle / send_message，
  重复执行本身即为失败（PRODUCT：不能用重复提交冒充一次正确完成）。
- 失败原因彼此可重叠，统计时分开计数；带标签时按 ``FAILURE_REASONS`` 顺序取第一个。

``world`` 只在本模块用于评测断言，绝不传给策略。所有比例都输出分子与分母。
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voice_agent.evaluation import percentile, summarize  # noqa: E402
from voice_agent.simulator import MODELS  # noqa: E402

# 顺序即 outcome_class 的优先级。
FAILURE_REASONS = ("no_commit", "wrong_target", "duplicate_non_idempotent")
NON_IDEMPOTENT = ("toggle", "send_message")


def failure_reasons(scene, result):
    """返回本次重放的失败原因集合；空集表示核实成功。"""
    commits = result.get("commits") or []
    action = scene["observation"]["action"]
    reasons = set()
    if not commits:
        reasons.add("no_commit")
    if any(c.get("target") != scene["world"]["target"] for c in commits):
        reasons.add("wrong_target")
    if len(commits) > 1 and action in NON_IDEMPOTENT:
        reasons.add("duplicate_non_idempotent")
    return reasons


def verified_success(scene, result):
    return not failure_reasons(scene, result)


def outcome_class(scene, result):
    """单一标签，便于分组计数；多个原因按 FAILURE_REASONS 顺序取第一个。"""
    reasons = failure_reasons(scene, result)
    for name in FAILURE_REASONS:
        if name in reasons:
            return name
    return "ok"


def high_risk_wrong_target(scene, result):
    return (
        scene["observation"]["risk"] == "high"
        and "wrong_target" in failure_reasons(scene, result)
    )


def p50(values):
    return percentile(values, 0.5)


def _latency_stats(runs):
    values = [r["latency_ms"] for r in runs]
    return {
        "n": len(values),
        "p50_latency_ms": p50(values),
        "p95_latency_ms": percentile(values, 0.95),
        "max_latency_ms": max(values) if values else None,
    }


def _ratio(numerator, denominator):
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": round(numerator / denominator, 6) if denominator else None,
    }


def summarize_verified(scenes, runs, with_groups=True):
    """同一批场景/重放的核实口径汇总；同时嵌入旧看板输出。"""
    if len(scenes) != len(runs):
        raise ValueError("scenes and runs must have the same length")
    if not scenes:
        raise ValueError("no runs")
    total = len(runs)
    classes = Counter(outcome_class(s, r) for s, r in zip(scenes, runs))
    reasons = Counter()
    for s, r in zip(scenes, runs):
        reasons.update(failure_reasons(s, r))

    high = [(s, r) for s, r in zip(scenes, runs) if s["observation"]["risk"] == "high"]
    nonidem = [
        (s, r) for s, r in zip(scenes, runs)
        if s["observation"]["action"] in NON_IDEMPOTENT
    ]
    clarified = [r for r in runs if r["clarified"]]
    answered = sum(1 for r in clarified if r["commits"])

    out = {
        "sessions": total,
        "legacy": summarize(runs),
        "verified_task_success": _ratio(sum(verified_success(s, r) for s, r in zip(scenes, runs)), total),
        "outcomes": {
            name: classes.get(name, 0) for name in ("ok",) + FAILURE_REASONS
        },
        "failure_reasons": {
            name: _ratio(reasons.get(name, 0), total) for name in FAILURE_REASONS
        },
        "high_risk": {
            "sessions": len(high),
            "wrong_target": _ratio(
                sum(1 for s, r in high if high_risk_wrong_target(s, r)), len(high)
            ),
        },
        "non_idempotent": {
            "sessions": len(nonidem),
            "duplicate": _ratio(
                sum(
                    1 for s, r in nonidem
                    if "duplicate_non_idempotent" in failure_reasons(s, r)
                ),
                len(nonidem),
            ),
        },
        "clarification": {
            "attempted": _ratio(len(clarified), total),
            "answered": _ratio(answered, total),
            "unanswered": _ratio(len(clarified) - answered, total),
            "answered_of_attempted": _ratio(answered, len(clarified)),
        },
        **_latency_stats(runs),
        "mean_model_cost_usd": round(sum(r["model_cost_usd"] for r in runs) / total, 6),
        "model_mix": dict(sorted(Counter(r["events"][0]["model"] for r in runs).items())),
        "groups": {},
    }
    if with_groups:
        groups = defaultdict(list)
        for s, r in zip(scenes, runs):
            groups[group_key(s)].append((s, r))
        out["groups"] = {
            key: _summarize_group(sel) for key, sel in sorted(groups.items())
        }
    return out


def _summarize_group(sel):
    scenes = [s for s, _ in sel]
    runs = [r for _, r in sel]
    return {
        "sessions": len(runs),
        "verified_task_success": _ratio(
            sum(verified_success(s, r) for s, r in sel), len(sel)
        ),
        "outcomes": {
            name: count for name, count in (
                (name, sum(1 for s, r in sel if outcome_class(s, r) == name))
                for name in ("ok",) + FAILURE_REASONS
            )
        },
        "high_risk_wrong_target": sum(1 for s, r in sel if high_risk_wrong_target(s, r)),
        "clarified": sum(r["clarified"] for r in runs),
        "clarification_unanswered": sum(1 for r in runs if r["clarified"] and not r["commits"]),
        "p95_latency_ms": percentile([r["latency_ms"] for r in runs], 0.95),
        "mean_model_cost_usd": round(
            sum(r["model_cost_usd"] for r in runs) / len(runs), 6
        ),
        "model_mix": dict(sorted(Counter(r["events"][0]["model"] for r in runs).items())),
    }


def group_key(scene):
    obs = scene["observation"]
    return "risk={risk}|explicit={explicit}|mode={mode}|history={history}|action={action}|timeout={timeout}".format(
        risk=obs["risk"],
        explicit="yes" if obs.get("explicit_target") else "no",
        mode=obs.get("interaction_mode", "normal"),
        history=len(obs.get("history") or []),
        action=obs["action"],
        timeout=scene["world"]["timeout"],
    )


def observable_key(scene):
    """只包含策略能看到的输入；request_id 是身份而非信号，刻意排除。"""
    obs = scene["observation"]
    return json.dumps(
        [
            obs["text"],
            obs["action"],
            obs["risk"],
            obs.get("explicit_target"),
            obs.get("history") or [],
            obs.get("interaction_mode", "normal"),
        ],
        ensure_ascii=False,
        sort_keys=True,
    )


def identical_input_groups(scenes):
    """按可观察输入分组，并给出真值/应答分歧的组。"""
    groups = defaultdict(list)
    for s in scenes:
        groups[observable_key(s)].append(s)
    duplicated = {k: v for k, v in groups.items() if len(v) > 1}
    divergent_target = {
        k: v for k, v in duplicated.items()
        if len({s["world"]["target"] for s in v}) > 1
    }
    divergent_answer = {
        k: v for k, v in duplicated.items()
        if len({bool(s["world"]["clarification_answered"]) for s in v}) > 1
    }
    return {
        "observable_groups": len(groups),
        "duplicated_groups": len(duplicated),
        "scenes_in_duplicated_groups": sum(len(v) for v in duplicated.values()),
        "divergent_target_groups": len(divergent_target),
        "scenes_in_divergent_target_groups": sum(len(v) for v in divergent_target.values()),
        "divergent_answer_groups": len(divergent_answer),
        "examples": [
            {
                "text": v[0]["observation"]["text"],
                "n": len(v),
                "world_targets": [s["world"]["target"] for s in v],
                "clarification_answered": [
                    bool(s["world"]["clarification_answered"]) for s in v
                ],
                "identical_predictions": len({
                    json.dumps(s["models"], sort_keys=True, ensure_ascii=False)
                    for s in v
                }) == 1,
            }
            for v in list(divergent_target.values())[:5]
        ],
    }


def ceiling(scenes):
    """任何"每个可观察组只做一个动作"的确定性策略的核实成功上限。

    每个组内可选的单一动作：任一模型的任一候选目标（执行），或澄清（应答时成功），
    或 abstain（0）。同一个可观察组只能选一个动作，因此组内最优值求和即上限。
    这是上界：它允许事后按组挑选最优动作。
    """
    groups = defaultdict(list)
    for s in scenes:
        groups[observable_key(s)].append(s)
    total = 0
    detail = {}
    for key, group in groups.items():
        per_model = {}
        best = 0
        for model in MODELS:
            targets = sorted({
                c["target"] for s in group for c in s["models"][model]["candidates"]
            })
            per_model[model] = {}
            for target in targets:
                correct = sum(1 for s in group if s["world"]["target"] == target)
                per_model[model][target] = correct
                best = max(best, correct)
        answered = sum(1 for s in group if s["world"]["clarification_answered"])
        best = max(best, answered)
        total += best
        detail[json.loads(key)[0]] = {
            "n": len(group),
            "best": best,
            "best_clarify": answered,
            "per_model": per_model,
        }
    return {"ceiling": total, "sessions": len(scenes), "groups": detail}


def latency_floor(scenes, model="standard"):
    """该组在"完美执行正确目标、零澄清"下的时延分布下限。

    只包含固定模型时延与工具/超时等待，不包含任何澄清。用于判断时延目标
    是否为策略可控。超时依旧按 PRODUCT 的固定常量计入。
    """
    fixed = MODELS[model]["latency_ms"]
    values = []
    for s in scenes:
        value = fixed + (80 if s["world"]["timeout"] == "none" else 500 + 80)
        values.append(value)
    return {
        "model": model,
        "n": len(values),
        "timeouts": sum(1 for s in scenes if s["world"]["timeout"] != "none"),
        "p50_latency_ms": p50(values),
        "p95_latency_ms": percentile(values, 0.95),
        "max_latency_ms": max(values) if values else None,
        "distribution": dict(sorted(Counter(values).items())),
    }


def summarize_history(records):
    """审计历史日志：它没有 world，只能核实"是否发生动作"，不能核实目标对错。"""
    def events(record, kind):
        return [e for e in record["events"] if e["type"] == kind]

    clarifications = [e for r in records for e in events(r, "clarification")]
    signals = Counter(
        e["signal"] for r in records for e in events(r, "user_signal")
    )
    zero_action = [r for r in records if not r["commits"]]
    return {
        "sessions": len(records),
        "has_world_field": any("world" in r for r in records),
        "clarifications": _ratio(len(clarifications), len(records)),
        "clarification_answered": _ratio(
            sum(1 for e in clarifications if e["answered"]), len(records)
        ),
        "clarification_unanswered": _ratio(
            sum(1 for e in clarifications if not e["answered"]), len(records)
        ),
        "zero_action_sessions": _ratio(len(zero_action), len(records)),
        "zero_action_detail": [
            {
                "id": r["id"],
                "text": r["observation"]["text"],
                "risk": r["observation"]["risk"],
                "explicit_target": r["observation"].get("explicit_target"),
                "interaction_mode": r["observation"].get("interaction_mode"),
                "latency_ms": r["latency_ms"],
                "model_cost_usd": r["model_cost_usd"],
            }
            for r in zero_action
        ],
        "tool_timeouts": sum(1 for r in records for _ in events(r, "tool_timeout")),
        "deduplicated": sum(1 for r in records for _ in events(r, "deduplicated")),
        "sessions_with_multiple_commits": sum(
            1 for r in records if len(r["commits"]) > 1
        ),
        "signals": dict(sorted(signals.items())),
        "p95_latency_ms": percentile([r["latency_ms"] for r in records], 0.95),
        "mean_model_cost_usd": round(
            sum(r["model_cost_usd"] for r in records) / len(records), 6
        ),
        "can_verify_target": False,
        "note": "历史日志只有可观察行为，没有 world；零动作与重复动作可核实，目标对错不可核实。",
    }


def load_traces(path):
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_scenes(path):
    """显式 UTF-8 读取，避免 zh-CN Windows 的 GBK 默认编码问题；并校验 id 唯一。"""
    scenes = load_traces(path)
    ids = [s["id"] for s in scenes]
    if not scenes or len(ids) != len(set(ids)):
        raise ValueError("scenario IDs must be nonempty and unique")
    return scenes
