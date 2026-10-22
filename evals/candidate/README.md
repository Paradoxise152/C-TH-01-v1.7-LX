# 自建评测

在此新增 test_*.py，通过标准库 unittest 执行；test_example.py 只是用法示例，不算完成所选问题的评测。

至少覆盖所选问题的失败、新实例、正常回归及边界/代价。可复制公开场景结构后创建新输入，但要说明为什么预期该行为。目标名称可变化，不依赖原始场景 ID。

推荐在独立 analyze.py 中输出带分子、分母的指标 JSON，保存复现命令和结果。不要求固定指标数量或图表。策略只接收运行时 observation/prediction/timeout；world 仅用于评测断言。

---

## 本提交新增了什么

| 文件 | 作用 |
|---|---|
| `metrics.py` | 核实口径：`verified_success` / `failure_reasons` / `outcome_class`；分组汇总、可观察输入分组、单动作上限 `ceiling`、时延下界 `latency_floor`、历史日志缺口 `summarize_history` |
| `arms.py` | 对照臂：`Baseline`、`ExplicitToStrong`（拒绝）、`HighRiskExecute`（撤回）、`TrustExplicit`（保留，配 boundary 使用）、`AlwaysExecuteTop`（旧口径反例） |
| `boundary.py` | 最小系统边界入口 `run_adjusted`：只放宽"explicit_target 可执行"，其余与 `voice_agent.simulator.run` 逐行等价并留痕 |
| `audit.py` | 重放所有臂，写 `runs/*` 与 `evals/candidate/results/*` |
| `review.py` | 人工复核入口：`summary` / `zero-action` / `diff` / `scene`，只读 `runs/`，`diff` 写 `results/changed_scenes.md` |
| `scenarios/` | 手工构造的新实例，逐条 oracle 依据见 `scenarios/README.md` |
| `test_candidate_metrics.py` | 口径自身的定义测试（分母、优先级、上限、历史日志） |
| `test_explicit_target.py` | 失败案例、新实例、以及在"explicit 本身错误"时如实计代价 |
| `test_no_high_risk_regression.py` | 保留臂无高风险错误/重复非幂等；放宽的最小性；两个"不采用"的数字依据 |
| `test_boundary_and_cost.py` | 固定成本、时延目标可行性、数据的信息上限 |
| `test_review.py` | 复核工具的差异判定（只看核实结果/提交/时延）与零动作过滤 |

## 口径

- **核实成功**：目标正确，且只执行一次预期操作；`toggle` / `send_message` 重复执行即失败
  （`set_power` 重复不改变最终状态，只计开销，见 PRODUCT）。
- **旧口径**：`legacy_task_success` 只统计是否收到工具回执，原样保留对照，不删除。
- **P95**：nearest-rank，直接复用 `voice_agent.evaluation.percentile`。
- 所有比例都带分子/分母；小样本"未发现错误"不等于错误率为 0。

## 复现（项目根目录）

zh-CN Windows 的 `Path.read_text()` 默认 GBK，会让中文数据报 `UnicodeDecodeError`；
先设 `PYTHONUTF8=1`（`run.ps1` 已包含）。Linux/macOS 默认 UTF-8 无需额外设置。

```sh
uv run --quiet --offline --no-project python task.py check
uv run --quiet --offline --no-project python task.py run --out runs/before
uv run --quiet --offline --no-project python evals/candidate/audit.py
```

`audit.py` 只读取 `runs/before/traces.jsonl`（由上面的文档命令生成），不改写它的
`summary.json`；重复运行输出字节相同。

## 结果文件

- `runs/before/`：官方 CLI + baseline（`summary.json` 是旧看板原生格式）
- `runs/before/verified_summary.json`：对同一份 traces 的核实口径
- `runs/after/`：保留方案（`boundary.run_adjusted` + `TrustExplicit`）
- `runs/after-contract-strong/`：被拒绝的替代方案（原契约内，明确→strong）
- `runs/withdraw-highrisk-execute/`：被撤回的实验（结果与命令保留）
- `runs/legacy-counterexample/`：旧看板满分的反例
- `evals/candidate/results/audit.json`、`arms.md`、`history_audit.json`
- `evals/candidate/results/changed_scenes.md`：before→after 的逐条变化（由下面的 `diff` 生成）

## 人工复核（四条命令，全部只读 runs/）

```sh
# 1) 两个口径各说一遍：旧看板 vs 核实（预期 before: legacy 90/100、verified 88/100）
uv run --quiet --offline --no-project python evals/candidate/review.py summary runs/before/traces.jsonl
uv run --quiet --offline --no-project python evals/candidate/review.py summary runs/after/traces.jsonl
# 2) 零动作场景明细（预期 10 条，其中最后一条 explicit=hall_light 会在 after 消失）
uv run --quiet --offline --no-project python evals/candidate/review.py zero-action runs/before/traces.jsonl
# 3) 改动到底改了哪几条（预期 changed scenes: 5，含 1 条 no_commit→ok）
uv run --quiet --offline --no-project python evals/candidate/review.py diff runs/before/traces.jsonl runs/after/traces.jsonl
# 4) 看单条场景的完整事件流（含模型选择、澄清、提交、时延）
uv run --quiet --offline --no-project python evals/candidate/review.py scene s-fca8479a13a9246b
```

预期输出（可直接核对）：

| 命令 | 关键行 |
|---|---|
| `summary before` | `verified=88/100 zero_action=10 wrong_target=2 high_risk_wrong=0 clarified=43 unanswered=10 p95=1380` |
| `summary after` | `verified=89/100 zero_action=9 wrong_target=2 high_risk_wrong=0 clarified=38 unanswered=9 p95=1380` |
| `zero-action before` | `zero-action: 10` |
| `diff` | `changed scenes: 5`，其中 `s-fca8479a13a9246b ... no_commit,clarify,1450ms -> ok,execute,830ms` |

`review.py` 不改写 `runs/`；`diff` 会重写 `results/changed_scenes.md`（内容确定）。

## 已知限制

- 100 条公开场景是合成数据，所有结论只在当前模拟范围内成立；定向反例不用于估计线上比例。
- `data/history_sessions.jsonl` 没有 world，只能核实"是否发生动作"，无法核实目标对错。
- 保留方案依赖"`explicit_target` 是已确认说法"这一产品假设；若它过期，代价见
  `test_explicit_target.KnownCost`，属于未测的真实风险。
- 明确且低风险的 P95 目标（600ms）在固定 500ms 超时成本下仍不可达（下界 830/680ms），
  本轮只把该组从 930ms 降到 830ms。
