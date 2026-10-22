# 自建场景与预期行为依据

`new_instances.jsonl` 全部为手工构造：ID、目标名、请求文本都不复用公开场景，目的是验证
"同一原则在新实例上仍然成立"，以及把边界与代价固定成可断言的事实。`world` 是人为设定的
真值，只用于评测断言；它不代表线上分布，也不能用来估计发生率。策略永远看不到它。

| id | 构造要点 | world 与依据 | 预期行为 |
|---|---|---|---|
| `c-explicit-missing` | 明确目标 `desk_lamp`，但 standard 候选只有 attic/corridor | 目标 `desk_lamp`、正常交互、用户会回答 | baseline 澄清（930ms）后执行 `desk_lamp`；放宽臂直接执行（330ms） |
| `c-explicit-missing-unanswered` | 同上，busy 且用户不回答 | 目标 `desk_lamp`、应答=false | baseline 只澄清、零动作（1450ms，失败）；放宽臂 330ms 成功 |
| `c-explicit-present-high` | 高风险明确目标就在候选内（0.51 略高于 0.49） | 目标 `heater` | 直接执行、不澄清（330ms）；这是 baseline 已有保障，不算本轮改善 |
| `c-high-risk-close` | 高风险且无明确目标，top 与次分接近（0.55/0.45） | 目标 `contact_zhou`、会回答 | 澄清（780ms）后执行正确目标；召回撤回臂的阈值 0.8 也不触发 |
| `c-high-risk-confident-wrong` | 高风险无明确目标，top 0.86 但**错** | 目标 `contact_lin`（模型自信地指向 zhou） | baseline 澄清得到正确目标；撤回臂会执行 `contact_zhou` → 高风险错误目标 |
| `c-timeout-after` | 明确 send_message + 执行后超时 | 目标 `contact_zhou` | 同键重试去重：恰好 1 次 commit、回执为真、830ms |
| `c-timeout-before` | 明确 send_message + 执行前超时 | 目标 `contact_zhou` | 同键重试首次真正落库：1 次 commit、830ms |
| `c-busy-clarify` | 无明确目标、候选近分、busy | 目标 `attic_light`、会回答 | 澄清 1200 + 工具 80 + standard 250 = 1530ms |
| `c-no-mode` | 旧场景没有 `interaction_mode` 字段 | 目标 `attic_light`、会回答 | 按 normal 计澄清 600ms，总 930ms |
| `c-same-input-a` | 观察与三模型候选完全相同的两条之一 | 真值 `attic_light`、会回答 | 直接执行 top（330ms）恰好正确 |
| `c-same-input-b` | 与 a 观察/候选逐字相同 | 真值 `desk_lamp`、不回答 | 同一策略必然执行 `attic_light` → 错；澄清也会因不回答而失败。两者上限都是 1/2，任何阈值都不可分 |
| `c-normal` | 普通无歧义低风险请求 | 目标 `attic_light` | 直接执行（330ms），正常回归用例 |

补充说明：

- 时延期望按 PRODUCT 的固定常量计算：模型 small 100 / standard 250 / strong 700，澄清 normal
  600 / busy 1200，工具 80，首次超时 500，重试 80。
- `c-same-input-a/b` 是 `test_boundary_and_cost.InformationLimit` 的依据：可观察输入相同、
  真值不同，所以"执行正确率"在这类样本上不可能靠调阈值提高；要区分只能澄清。
- 这些场景数量很少，用于证明"某种错误存在/某个改动有效"，不用于估计比例。
