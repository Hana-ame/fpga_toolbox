# Changelog — Verilator 套件缺汇总行被当 PASS

前两轮修了 SVA（零输出）与 cocotb（无完成标记）的完整性问题。本轮把同一思路套到
`normalize_sim_result` 的第三个输入源：Verilator 日志。

## 缺陷 — CHECK 之后崩溃与完整跑完结果相同

生成的 TB 必定在 `$finish` 前打印一条结束汇总：

```verilog
if (errs == 0) begin $display("PASS: apb all 8 checks passed"); $finish; end
else begin $display("FAIL: apb %0d errors", errs); $fatal(1); end
```

但归一化器只看已有的 `[CHECK]` 行。若仿真在打完若干 CHECK 之后、走到
`$finish` 之前崩溃（断言失败触发 `$fatal` 之外的原因、仿真器超时被杀等），
日志里没有汇总行——旧实现照样判 PASS：

| 日志 | 旧结果 |
|---|---|
| `== sim: apb ==` + `[CHECK] PASS` + `PASS: apb ...` | PASS |
| `== sim: apb ==` + `[CHECK] PASS`（**无汇总行**） | **PASS（假通过）** |

## 修复

按「启动的套件数 vs 汇总行数」做完整性校验：生成/运行一个套件必然打印恰好一条
汇总，**启动 N 个就必须 ≥N 条**，不足即判 FAIL + `VERILATOR_NO_SUMMARY`。

### 一次误伤与修正

第一版按「汇总行首词 == 套件名」逐个匹配：

```python
_summaries = {m.group(2).split()[0] for m in ...}   # PASS: apb ... -> "apb"
unsummarized = [s for s in started if s not in _summaries]
```

fusion 测试立刻失败：它的夹具用的是 `PASS: all 20 checks passed`——**首词是
"all" 而不是套件名**。真实 `sim.sh` 打印 `PASS: apb all 8 checks passed`
（首词=套件名），但别的 TB 完全可以打印任意文本。逐名匹配会误伤合法日志。

改为**计数法**：不解析汇总行的归属，只比较数量。这既抓住了「有套件没跑完」，
又不依赖汇总行的具体措辞。

## 验证

| 场景 | 结果 |
|---|---|
| 完整 2 套件 | PASS |
| 崩在第二个套件 | **FAIL** + `VERILATOR_NO_SUMMARY` |
| 全崩（零汇总行）| **FAIL** |
| 汇总行首词非套件名（`PASS: all 20 ...`）| **PASS**（不误伤）|
| `FAIL:` 汇总 | FAIL |
| 真实 `sim.sh` 日志（跑 apb 套件）| PASS + 8 条 CHECK |

新增 5 项断言；regression / fusion / eda_campaign / audit 10-10 /
docs_check D1–D13 / ruff / py3.10 全绿。

## 三个输入源的完整性信号至此齐备

| 输入源 | 完整性信号 | 加入轮次 |
|---|---|---|
| cocotb JSON | `completed` 字段 | 195 |
| SVA log | `[SVA:HBEAT]` 心跳 | 194 |
| Verilator log | `PASS:`/`FAIL:` 汇总行计数 | 196 |
