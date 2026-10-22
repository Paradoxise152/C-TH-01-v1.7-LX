"""最小系统边界实验入口（独立于 voice_agent/simulator.py）。

唯一差异（BOUNDARY_DELTA）：把 ``observation.explicit_target`` 视为可信执行来源，
因此当 ``choose_action`` 返回 ``execute`` 且目标正是该 explicit_target 时，即使它不在
所选模型的候选列表里也允许执行，并追加一条 ``boundary_relaxed`` 事件留痕。

其它一切（模型时延/价格、工具 80ms、首超时 500ms、同键去重、澄清 600/1200ms、
user_correction / user_complaint 的产生、返回结构）与 ``voice_agent.simulator.run``
逐行等价。world 依然不进入策略；本模块读 world 只为模拟事实与计费。

原约束下的结果必须分别报告：本入口的收益不能直接当作原条件下的产品改善。
"""
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voice_agent.simulator import MODELS, validate  # noqa: E402

BOUNDARY_DELTA = (
    "execute 目标可以等于 observation.explicit_target，即使不在所选模型候选列表内"
)


def run_adjusted(scene, policy):
    validate(scene)
    obs, world = deepcopy(scene["observation"]), scene["world"]
    events = []
    model = policy.select_model(deepcopy(obs))
    if model not in MODELS:
        raise ValueError("unknown model selected")
    timing = MODELS[model]
    latency, cost = timing["latency_ms"], timing["cost_usd"]
    prediction = deepcopy(scene["models"][model])
    events.append({"type": "model", "model": model, "prediction": prediction})
    decision = policy.choose_action(deepcopy(obs), deepcopy(prediction))
    kind = decision.get("kind")
    if kind not in ("execute", "clarify", "abstain"):
        raise ValueError("decision kind must be execute, clarify or abstain")
    clarified, target, acknowledged = kind == "clarify", None, False
    if clarified:
        latency += 1200 if obs.get("interaction_mode") == "busy" else 600
        answered = world["clarification_answered"]
        events.append({"type": "clarification", "answered": answered})
        if answered:
            target = world["target"]
    elif kind == "execute":
        target = decision["target"]
        candidates = [v["target"] for v in prediction["candidates"]]
        if target not in candidates:
            explicit = obs.get("explicit_target")
            if target is not None and target == explicit:
                events.append({
                    "type": "boundary_relaxed",
                    "target": target,
                    "reason": BOUNDARY_DELTA,
                })
            else:
                raise ValueError(
                    "execute target must come from selected model candidates"
                )
    commits, seen_keys = [], set()
    first_key = obs["request_id"] + ":action"

    def commit(key):
        if key in seen_keys:
            events.append({"type": "deduplicated", "key": key})
            return
        seen_keys.add(key)
        effect = {"target": target, "action": obs["action"], "key": key}
        commits.append(effect)
        events.append({"type": "tool_committed", **effect})

    if target is not None:
        mode = world["timeout"]
        latency += 80 if mode == "none" else 500
        if mode != "before_commit":
            commit(first_key)
        if mode == "none":
            acknowledged = True
        else:
            events.append({"type": "tool_timeout", "key": first_key})
            retry = policy.retry_key(deepcopy(obs), {
                "original_key": first_key, "target": target, "attempt": 1,
            })
            if retry is not None:
                if not isinstance(retry, str) or not retry:
                    raise ValueError("retry key must be nonempty string or None")
                latency += 80
                commit(retry)
                acknowledged = True
    events.append({"type": "assistant_response", "accepted": acknowledged})
    if any(c["target"] != world["target"] for c in commits):
        events.append({"type": "user_signal", "signal": "user_correction"})
    if len(commits) > 1 and obs["action"] in ("toggle", "send_message"):
        events.append({"type": "user_signal", "signal": "user_complaint"})
    for signal in world.get("signals", []):
        events.append({"type": "user_signal", "signal": signal})
    return {
        "id": scene["id"], "observation": deepcopy(obs), "events": events,
        "latency_ms": latency, "model_cost_usd": cost,
        "clarified": clarified, "acknowledged": acknowledged,
        "commits": commits,
    }
