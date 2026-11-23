# Changelog — eda_campaign 审计（第三轮）：千分位截断导致假通过

前两轮修了 6 个缺陷。本轮深审 `parse_uvm_log` 的**正则层**（前两轮只看了控制流），
用真实 VCS 日志的格式变体压测，发现 2 个。

## 缺陷 A（严重）— 千分位逗号被截断，可导致假通过

所有数字正则都只写 `(\d+)`：

```python
_UVM_DELIVERED_RE = re.compile(r"\[C3900\]\s+FID:\s+delivered=(\d+)")
```

真实 VCS 日志常用千分位 `delivered=10,000`，`(\d+)` 到逗号就停，解析出 **10**：

| 场景 | 解析结果 | 判定 |
|---|---|---|
| `delivered=10,000`，`expected_frames=10000` | delivered=**10** | **FAIL**（实际交付满额）|
| `delivered=10,000`，`expected_frames=10` | delivered=**10** | **PASS（假通过）** |

第二种是最危险的形态：实际交付 10000 帧，却因为截断成 10 恰好等于 `expected=10`
而判**通过**。一个门禁工具给出与事实相反的结论。

修复：引入 `_NUM = r"\d[\d,]*"` 统一数字模式 + `_to_int` / `_to_float` 转换前
去掉逗号。UVM 与 DC/PT QoR 两侧全部 11 处转换点都改用它：

- `delivered` / `fid_bad` / `all_retry` / `send_nums` / `uvm_error` / `win_span`
  / `frames` / `min` / `avg` / `max` / `pcs_words` / `flips_act`
- QoR：`Leaf Cell Count: 74,265` / `Combinational Area: 23,456.789` /
  `No. of Violating Paths` / `Critical Path Slack` / `Total Negative Slack`

## 缺陷 B — `delivered=` 依赖字段顺序

```python
_UVM_DELIVERED_RE = re.compile(r"\[C3900\]\s+FID:\s+delivered=(\d+)")
```

要求 `delivered=` **紧跟** `[C3900] FID:`。但日志里这几个字段的顺序并无规范约束，
写成 `[C3900] FID: fid_bad=0 delivered=10000` 时正则完全匹配不到 →
`delivered=None` → 一个正常场次被判 FAIL（`delivered is None` 直接使 `passed=False`）。

修复：只锚定 `\bdelivered=`，不依赖前后字段顺序。`fid_bad` 同理早就是独立正则。

## 审计中确认正确的部分

- 缺 `min` / `max` 的可选字段（`(?:\s+min=...)?`）正确处理
- `UVM_ERROR:0`（无空格）与 `UVM_ERROR : 0`（有空格）都能匹配
- 多行多次 `UVM_ERROR` 取最后一次（符合「最终汇总」语义）
- 只有 2 行且无 `UVM_ERROR` 汇总时正确触发 E050 截断判定（不误放行）
- 真实 K-2015.06 `report_qor` 解析与文档基准仍完全吻合（Leaf 74265 / Area 50656.129）

## 验证

新增 2 个测试（共 9 个）：
`test_uvm_thousands_separator_and_field_order`（含「10000 帧不得因截断判成
expected=10 通过」的关键断言）、`test_qor_thousands_separator`。
eda_campaign / regression / fusion / audit 9-9 / docs_check D1–D13 / ruff /
py3.10（含 `-W error::SyntaxWarning`）全绿。
