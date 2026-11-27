# 功能增强 · 新增 `axi4_stream` 原生自检 TB 模板（7 → 8 协议）

## 背景

`axi4_stream` 早就能被分类器识别、也能被 canonical 绑定层正确绑定
（`s_axis_*` → slave，`m_axis_*` → master），但 `SUPPORTED_MULTI_PROTOCOLS`
里没有它 —— `emit_testbench` 只能诚实报「识别到了，但没有模板」。这是
「能力缺口」而不是缺陷：工具没撒谎，只是没做。

AXI4-Stream 也是真实 RTL 里最常见的接口之一（DMA、视频/网络流水线、DSP
链），缺它意味着整类设计没法用这个工具生成 TB。

## 实现

新增 `_emit_axi4_stream_tb()`，**不复用** AXI4-Full 结构 —— 两者语义根本不同：
AXI4-Stream 没有地址、没有读通道，唯一的流控是 TREADY/TVALID 握手。

自检围绕「握手是否真的完成」：

| 检查 | 判定方式 |
|---|---|
| 每个 beat 都被接受 | 逐 beat 等 TREADY，1000 拍未拉起即 FAIL 并报出卡在第几拍 |
| beat 计数精确相等 | `accepted !== driven` 即 FAIL（吞掉/重复接受都算错） |
| TVALID 期间 payload 稳定 | 每拍复查 data/keep/last 未被改动（AXI4-Stream 强制规则） |
| TKEEP/TLAST 包边界 | 4-beat 包末拍 TKEEP 收窄，之后再来一个单 beat 短包 |

可选信号（`TLAST`/`TKEEP`/`TID`/`TDEST`/`TUSER`）**按 DUT 是否真的暴露**
决定是否生成；实测最小合法实现只有 `TDATA`/`TVALID`/`TREADY` 三个信号，
硬编码全部 9 个会给它生成 `PINNOTFOUND` 的坏 TB。位宽一律取端口真实值
（含非 8 倍数位宽，如 12 位 TDATA）。

## 首次实现踩到的两个真实问题（已修复并固化为断言）

1. **模块名与 runner 的 `--top-module` 不一致**：runner 统一用
   `tb_{mod}_{proto}`，模板若自己起名 `tb_{mod}_axis`，Verilator 直接
   `Specified --top-module ... was not found in design`，构建失败。

2. **`errs` 用 `logic` 会静默变成 PASS**：`logic errs` 只有 1 位，累加偶数次
   错误溢出回 0。一个**完全卡死**的 DUT（5 次握手超时 + 1 次 beat 计数不符 =
   6 次错误）因此打印 `PASS` 并 `rc=0` —— 正是本工具最该防的假通过，却在
   自己的新模板里复现了。改成 `int errs` 后卡死 DUT 正确 `FAIL` + `rc=1`。

这两个都是新代码的真实 bug，不是事后补的防御断言。

## 落地

- 注册进 `SUPPORTED_MULTI_PROTOCOLS`（8 种）、registry `inputSchema.enum`；
- `REQUIRED_TB_PORTS["axi4_stream"]` 只列必需的 3 个信号（可选信号不列，
  否则精简 DUT 会被误判缺端口）；
- `EXPECTED_TB_DIR["axi4_stream"]`：TB 驱动 payload/TVALID，读 TREADY ——
  从机把 TREADY 写成 `input` 时生成前就拦住（实测报
  `tready->s_axis_tready (expected DUT output, got input)`）；
- 参考 DUT `examples/multi_bus_ref/axi4_stream_dut.sv` + `tb_axi4_stream.sv`
  接入 `sim.sh`；
- 文档：`FEATURES.md` / `EXAMPLES.md` / `README.md` 协议清单同步 8 种。

## 验证（真实 Verilator 5.054）

| 场景 | 结果 |
|---|---|
| 参考 FIFO DUT（32-bit + TLAST/TKEEP/TID/TDEST） | `PASS: axi4_stream 5 beats accepted ...` rc=0，0 警告 |
| 最小 DUT（仅 3 信号，12-bit 非 8 倍数 TDATA） | PASS rc=0，无 PINNOTFOUND / WIDTH 告警 |
| 卡死 DUT（TREADY 恒 0） | `FAIL: axi4_stream 6 errors` rc=1（修复 `errs` 前是假 PASS） |
| TREADY 方向写成 input | 生成前拒绝，报出期望/实际方向 |
| `examples/multi_bus_ref/sim.sh axi4_stream` | PASS rc=0 |

六层门禁 6/6：函数覆盖 **171/171**（+1，新模板已覆盖），19/19 DUT 解析，
48 诊断码不变。
