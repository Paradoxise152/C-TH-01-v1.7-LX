# 改善报告

- 一句话成果：一份高质量的审计/评测成果，外加一个产品修复。用户现在可以少做错一个判断——不再把看板上的 90/100 当任务成功：核实只有 88/100；旧看板还会把“从不澄清”评成 100/100，而它制造 9 次高风险错误目标；同时知道“明确指令的多余澄清”只值 5/50，改用 strong 修会破坏成本与 P95，用最小边界放宽可零成本修掉。

## 问题与判断

真正的问题不是阈值，而是“成功”的口径。旧看板只统计是否收到工具回执，于是 10 次“澄清没人回答、什么都没做”和 2 次“执行到错误目标”都被记成成功；按它做取舍会把方向选反。先纠正这个判断，比再调一个阈值更值得投入。

事实来自 100 条公开场景的重放（分子/分母见下表与 `evals/candidate/results/audit.json`）。假设：`explicit_target` 是产品已确认的说法。未知：它是否会在真实使用中过期；澄清应答率能否外推；100 条合成样本不代表线上分布。优先级按 PRODUCT：先保高风险正确与无重复非幂等，再谈完成率、多余澄清与成本。

## 成果与证据

复现：`evals/candidate/run.cmd`（zh-CN Windows 会自动设 `PYTHONUTF8=1`）；证据文件与人工复现步骤见下文“证据文件与人工复现”。核实口径 = 目标正确且只执行一次；`set_power` 重复只计开销，`toggle`/`send_message` 重复即失败。

| 臂 | 旧口径 | 核实 | 零动作 | 错目标 | 高风险错 | 澄清/未答 | 明确低风险 P95 | 成本 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| before（baseline） | 90/100 | 88/100 | 10 | 2 | 0 | 43/10 | 930 | 0.00314 |
| after（保留） | 91/100 | 89/100 | 9 | 2 | 0 | 38/9 | 830 | 0.00314 |
| after-contract-strong（拒绝） | 91/100 | 89/100 | 9 | 2 | 0 | 38/9 | 1280 | 0.00614 |
| withdraw-highrisk-execute（撤回） | 90/100 | 85/100 | 10 | 5 | 3 | 37/10 | 930 | 0.00314 |
| legacy-counterexample（反例） | 100/100 | 81/100 | 0 | 19 | 9 | 0/0 | 830 | 0.003 |

三条支撑：

1. 反例臂“从不澄清、总执行 top 候选”在旧口径是 100/100，核实只有 81/100，并新增 9 次高风险错误目标——旧看板不能用于策略取舍。
2. 信息上限：5 组可观察输入完全相同却对应不同真值（覆盖 30 条），其中一组三个模型的候选逐字相同、真值与是否回答都是 2:2；按“每个可观察组只做一个动作”算，核实成功上限是 94/100，baseline 88。阈值类改动最多值 6 条，且需要事后信息。
3. 明确且低风险组 36 条里有 4 条超时（固定 500ms）：零澄清、完美执行的 P95 下界是 830ms（standard）/680ms（small），600ms 目标不是策略能达成的；剔除超时样本后该组 P95 是 330ms。

保留改动：只在“所选模型候选缺 `explicit_target`”时，以已确认目标执行（`boundary.run_adjusted` + `TrustExplicit`，独立入口）。5 条明确指令不再被多余澄清：核实 88→89，澄清 43→38，该组 P95 930→830，成本不变，高风险错误与重复仍为 0。

回归与反证：`run_adjusted` 在 baseline 策略下与官方 `run` 逐字段相同（放宽是最小的）；同键超时重试仍只 1 次 commit；若 `explicit` 本身错误，保留臂会执行错目标 1 次（人为场景，如实计代价）。

## 证据文件与人工复现

证据文件（都在仓库内，可直接打开）：

| 要核对的东西 | 文件 | 内容 |
|---|---|---|
| 逐条运行记录 | `runs/<臂>/traces.jsonl` | 每臂 100 行；含模型、候选、澄清、提交、超时/去重、时延、成本 |
| 旧看板原始输出 | `runs/before/summary.json` | 官方 CLI 生成，未改写 |
| 核实指标 | `runs/<臂>/verified_summary.json`、`evals/candidate/results/audit.json` | 分子/分母、分组、零动作明细、相同输入分组、单动作上限、时延下界 |
| 五臂对照 | `evals/candidate/results/arms.md` | 即上表 |
| 改动落点 | `evals/candidate/results/changed_scenes.md` | before→after 的 5 条逐条细节 |
| 历史日志审计 | `evals/candidate/results/history_audit.json` | 9 条零动作、39 次澄清、无 world 的核实缺口 |
| 自建场景与 oracle | `evals/candidate/scenarios/new_instances.jsonl`、`scenarios/README.md` | 12 条，逐条写明 world 依据 |
| AI 记录 | `ai-sessions/cth01-session-01.jsonl`、`ai-sessions/INDEX.md` | 902 事件（含工具调用）与关键消息位置 |

人工复现（约 5 分钟，只读 `runs/`；命令见 `evals/candidate/README.md`）：

1. 跑全流程：`evals\candidate\run.cmd`，应得到 `Ran 6 tests OK`、`Ran 52 tests OK` 与五臂表。
2. 对表：确认 `results/arms.md` 五行与上表一致；`before` 行必须与 `runs/before/summary.json` 完全一致。
3. 看改动：`review.py diff runs/before/traces.jsonl runs/after/traces.jsonl` 应输出 `changed scenes: 5`，其中 `s-fca8479a13a9246b` 从 `no_commit,1450ms` 变为 `ok,830ms`。
4. 抽查单条：`review.py scene s-fca8479a13a9246b` 查看输入与完整事件流。
5. 看反证：`review.py summary runs/legacy-counterexample/traces.jsonl` 应显示旧口径 100/100、核实 81/100、高风险错误 9。

## 最终取舍与限制

保留：明确目标优先于候选列表。拒绝：明确→strong（多 1 条完成，但成本 0.00614 超过 0.004、该组 P95 升到 1280）。撤回：放开高风险直接执行（核实降到 85，并新增 3 次高风险错误目标）。继续观察：10 次零动作在当前可观察输入下不可区分，保留澄清。

边界放宽的收益不当作原条件下的产品改善，原约束结果在同一张表保留。未完成：用更好的追问替代澄清、更多自建场景、明确指令下更强的候选约束。已知限制：explicit 的权威性没有现实证据；该组 P95 830 仍高于 600。

## 个人判断与 AI 交互

主攻方向由用户在选项答复中确定（A：评测口径纠偏 + 明确指令小修复；分工“我全做”；提交形式待定），我据此写计划、获批准后实现。实现中我的一次统计错误（把“5 组/30 条”记成 10 条）被 `test_boundary_and_cost.InformationLimit` 的断言暴露，已在 EVAL_PLAN 追加更正；另一次是反例臂沿用 baseline 路由导致数字漂到 78/100，固定为 standard 后回到 81/100 并写入计划。对应消息位置见 `ai-sessions/INDEX.md`。

字数核对（表格与命令不计，上限 1200）：

```sh
uv run --quiet --offline --no-project python -c "import re; t=open('REPORT.md',encoding='utf-8').read(); t='\n'.join(l for l in t.splitlines() if not l.strip().startswith(('|','```'))); print(len(re.findall(r'[\u4e00-\u9fff]', t)))"
```
