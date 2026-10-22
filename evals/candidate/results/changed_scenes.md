# before → after 变化的场景

由 `python evals/candidate/review.py diff runs/before/traces.jsonl runs/after/traces.jsonl` 生成。
判定变化的口径：核实结果、提交内容或时延任一不同。

| id | 文本 | risk | explicit | before | after |
|---|---|---|---|---|---|
| `s-6e786cde91ad466b` | 打开lamp。 | low | lamp | ok 澄清 930ms | ok 执行 330ms |
| `s-8192b7fa1f3eb87a` | 发送这条测试消息给contact_liu。 | high | contact_liu | ok 澄清 930ms | ok 执行 330ms |
| `s-996f0a1e7ed80e90` | 发送这条测试消息给contact_liu。 | high | contact_liu | ok 澄清 930ms | ok 执行 330ms |
| `s-7e6ed4d7d3b0c847` | 打开fan。 | low | fan | ok 澄清 930ms | ok 执行 330ms |
| `s-fca8479a13a9246b` | 打开hall_light。 | low | hall_light | no_commit 澄清 1450ms | ok 执行 830ms |

## 逐条细节

### `s-6e786cde91ad466b` 打开lamp。

- before：模型 standard，commit，目标 ['lamp']，澄清=True，回执=True，时延 930ms，成本 0.003
- after：模型 standard，commit，目标 ['lamp']，澄清=False，回执=True，时延 330ms，成本 0.003

### `s-8192b7fa1f3eb87a` 发送这条测试消息给contact_liu。

- before：模型 standard，commit，目标 ['contact_liu']，澄清=True，回执=True，时延 930ms，成本 0.003
- after：模型 standard，commit，目标 ['contact_liu']，澄清=False，回执=True，时延 330ms，成本 0.003

### `s-996f0a1e7ed80e90` 发送这条测试消息给contact_liu。

- before：模型 standard，commit，目标 ['contact_liu']，澄清=True，回执=True，时延 930ms，成本 0.003
- after：模型 standard，commit，目标 ['contact_liu']，澄清=False，回执=True，时延 330ms，成本 0.003

### `s-7e6ed4d7d3b0c847` 打开fan。

- before：模型 standard，commit，目标 ['fan']，澄清=True，回执=True，时延 930ms，成本 0.003
- after：模型 standard，commit，目标 ['fan']，澄清=False，回执=True，时延 330ms，成本 0.003

### `s-fca8479a13a9246b` 打开hall_light。

- before：模型 standard，no_action，目标 —，澄清=True，回执=False，时延 1450ms，成本 0.003
- after：模型 standard，commit，目标 ['hall_light']，澄清=False，回执=True，时延 830ms，成本 0.003
