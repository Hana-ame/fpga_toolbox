# Changelog — SVA checker 零输出导致「全通过」与「没跑」不可区分

前几轮修的是入口校验（`call_tool` 的 required/enum/type）。本轮查**输出侧**：
13 个工具的 ToolResult 信封结构完全一致（字段齐全、`tool` 名匹配、类型正确），
没有缺陷。继续查输出**内容**时，在 SVA 这条链上发现问题。

## 缺陷 — 断言引擎「通过」与「不存在」产生同一份报告

`_emit_sva` 生成的 checker 只有失败分支输出：

```verilog
assert (!bvalid && !rvalid) else $error("[RULE:P_002] Response valid high during reset");
if (prev_awvalid && !prev_awready) assert (awvalid) else $error("[RULE:P_001] AWVALID dropped");
```

**通过时零输出**。于是 `normalize_sim_result` 面对一份没有 `[RULE:...]` 行的日志时：

| 实际发生了什么 | 归一化结果 |
|---|---|
| SVA 全部通过（正常） | `NO_RESULTS`, 0 checks |
| SVA checker 根本没 bind / 没跑 | `NO_RESULTS`, 0 checks |

两者**完全一样**。这恰恰是本项目反复强调的反静默通过：一个「SVA 通过」的结论，
与「断言引擎压根没运行」的结论无法区分。用户看到 `NO_RESULTS` 也可能以为
「没事，没有违例」。

## 修复

**生成侧**：SVA checker 增加心跳探针，证明引擎在场且正在监控：

```verilog
int unsigned sva_hb_cnt;
always_ff @(posedge clk) begin
  if (!rst_n) sva_hb_cnt <= 0;
  else begin
    sva_hb_cnt <= sva_hb_cnt + 1;
    if (sva_hb_cnt % 100 == 99)
      $display("[SVA:HBEAT] rule=P_000 monitored_cycles=%0d status=ACTIVE", sva_hb_cnt + 1);
  end
end
```

初值放在复位分支而不是声明处——否则 `-Wall` 报 `PROCASSINIT`（声明初值 + 过程赋值）。
实测改为复位清零后，SVA checker **零错误零警告**编译通过。

**归一化侧**：识别心跳，区分三态：

| 日志内容 | 结果 |
|---|---|
| 有心跳、无 `[RULE:*]` 违例 | **PASS** + 合成 `SVA_P000` check |
| 有心跳、有违例 | **FAIL** + 逐条 `P_xxx` |
| 无心跳、无违例（空/没跑）| **NO_RESULTS**（保持原样，不伪造 PASS）|

关键点：**不能**把「无心跳」也判成 PASS——那会把 bind 失败掩盖成通过，正是要防的事。

## 验证

- 三态区分：心跳+无违例 → PASS；空文件 → NO_RESULTS；心跳+违例 → FAIL
- 生成的 SVA checker 含 `[SVA:HBEAT]`、`sva_hb_cnt <= 0` 复位、可干净编译
- 7 协议 TB 生成仍全部成功（SVA 改动未影响 emitter 其他路径）
- 新增 10 项断言（`test_sva_heartbeat_distinguishes_pass_from_notrun` /
  `test_generated_sva_checker_has_heartbeat`）
- regression / fusion / eda_campaign / audit 10-10 / docs_check D1–D13 / ruff /
  py3.10 全绿
