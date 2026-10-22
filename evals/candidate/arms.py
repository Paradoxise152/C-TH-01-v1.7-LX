"""对照臂：只读 observation / prediction / timeout，不读 world。

``retry_key`` 一律委托 baseline（幂等重试是本轮不动的已有保障）。每个臂只改一个决定，
便于把结果差异归因到单点。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voice_agent import policy as baseline  # noqa: E402


def ranked(prediction):
    return sorted(
        prediction["candidates"], key=lambda c: c["confidence"], reverse=True
    )


def top_margin(prediction):
    items = ranked(prediction)
    second = items[1]["confidence"] if len(items) > 1 else 0.0
    return items[0]["confidence"] - second


class Baseline:
    """现状：官方 policy，用于确认对照与原始基线一致。"""

    name = "baseline"
    description = "官方 policy.py，未改动"

    def select_model(self, request):
        return baseline.select_model(request)

    def choose_action(self, request, prediction):
        return baseline.choose_action(request, prediction)

    def retry_key(self, request, timeout):
        return baseline.retry_key(request, timeout)


class ExplicitToStrong(Baseline):
    """拒绝臂：把明确指令一律路由到 strong，使候选列表必然包含 explicit_target。

    用于检验"在原契约内消除多余澄清"的代价：正确率 +1，但成本与 P95 都变差。
    """

    name = "explicit-to-strong"
    description = "明确目标请求改走 strong；其余与 baseline 相同"

    def select_model(self, request):
        if request.get("explicit_target"):
            return "strong"
        return baseline.select_model(request)


class HighRiskExecute(Baseline):
    """撤回臂：高风险且无明确目标时，按置信度直接执行，而不是澄清。

    用于量化"用高风险直接执行换完成率"的代价；一旦出现高风险错误目标即撤回。
    """

    name = "high-risk-execute"
    description = "高风险无明确目标时按 conf/margin 直接执行"

    def __init__(self, confidence=0.8, margin=0.4):
        self.confidence = confidence
        self.margin = margin

    def choose_action(self, request, prediction):
        if request["risk"] == "high" and not request.get("explicit_target"):
            items = ranked(prediction)
            if items[0]["confidence"] >= self.confidence and top_margin(prediction) >= self.margin:
                return {"kind": "execute", "target": items[0]["target"]}
            return {"kind": "clarify"}
        return baseline.choose_action(request, prediction)


class FixedModel(Baseline):
    """探索臂：所有请求固定走同一个模型，只改路由、不改决策。"""

    name = "fixed-model"
    description = "固定模型，其余与 baseline 相同"

    def __init__(self, model):
        self.model = model

    def select_model(self, request):
        return self.model


class ThresholdPolicy(Baseline):
    """探索臂：只改 baseline 的“无明确目标时执行/澄清”阈值。

    高风险且无明确目标仍然澄清；明确目标仍然优先执行。用于检查“调阈值”
    是否真能换来完成率，还是只在不同失败之间搬运。
    """

    name = "threshold"
    description = "保持路由与规则，只改置信度/差距阈值"

    def __init__(self, confidence=0.8, margin=0.4):
        self.confidence = confidence
        self.margin = margin

    def choose_action(self, request, prediction):
        if request["risk"] == "high" and not request.get("explicit_target"):
            return {"kind": "clarify"}
        explicit = request.get("explicit_target")
        items = ranked(prediction)
        if explicit:
            if any(c["target"] == explicit for c in items):
                return {"kind": "execute", "target": explicit}
            return {"kind": "clarify"}
        if items[0]["confidence"] >= self.confidence and top_margin(prediction) >= self.margin:
            return {"kind": "execute", "target": items[0]["target"]}
        return {"kind": "clarify"}


class TrustExplicit(Baseline):
    """保留臂：只与 boundary.run_adjusted 配合使用。

    与 baseline 唯一的差异：当 baseline 本会澄清、而请求带着已确认的 explicit_target 时，
    直接以该目标执行。它不改变 explicit 的语义，也不读候选列表以外的信息。
    """

    name = "trust-explicit"
    description = "明确目标不再被候选列表否决（需配合 run_adjusted）"

    def choose_action(self, request, prediction):
        decision = baseline.choose_action(request, prediction)
        if decision["kind"] == "clarify" and request.get("explicit_target"):
            return {"kind": "execute", "target": request["explicit_target"]}
        return decision


class AlwaysExecuteTop(Baseline):
    """反例臂：演示旧看板会给出满分。

    它固定用 standard（与大多数请求的 baseline 路由一致，便于同口径比较），
    总执行置信度最高的候选、从不澄清；旧口径（收到回执）因此 100/100。
    """

    name = "always-execute-top"
    description = "standard + 总执行 top 候选、从不澄清；旧看板满分"

    def select_model(self, request):
        return "standard"

    def choose_action(self, request, prediction):
        return {"kind": "execute", "target": ranked(prediction)[0]["target"]}


POLICIES = {
    "baseline": Baseline,
    "explicit-to-strong": ExplicitToStrong,
    "high-risk-execute": HighRiskExecute,
    "trust-explicit": TrustExplicit,
    "always-execute-top": AlwaysExecuteTop,
}

# 直接可用的实例：模拟器调的是实例方法，测试与 audit 都用这些。
BASELINE = Baseline()
EXPLICIT_TO_STRONG = ExplicitToStrong()
HIGH_RISK_EXECUTE = HighRiskExecute()
TRUST_EXPLICIT = TrustExplicit()
ALWAYS_EXECUTE_TOP = AlwaysExecuteTop()
