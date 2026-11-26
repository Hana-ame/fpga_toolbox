# Changelog — DDR3/DDR4 控制信号（CKE/CS_n/RAS_n/…）位宽写死 1 位

## 背景

DDR3/DDR4 模板把整组**控制信号**塞进同一个 `logic` 标量声明：

```systemverilog
logic clk = 0, rst_n = 0, ddr3_cke = 1, ddr3_cs_n = 1,
      ddr3_ras_n = 1, ddr3_cas_n = 1, ddr3_we_n = 1, ddr3_rd_valid;
```

DDR4 同理（`ddr4_act_n` / `ddr4_cs_n` / `ddr4_odt` / `ddr4_rd_valid`）。

## 为什么是真缺陷

**多 rank 的 DDR 控制器会用多位 CKE / CS_n** —— 每个 rank 一位（`CKE[1:0]`
同时使能两个 rank，`CS_n[3:0]` 片选 4 个 rank）。这是 JEDEC 标准做法，不是
非常规实现。

DUT 用 2 位 `ddr3_cke` 时：

```
%Warning-WIDTHEXPAND: tb_ddr3_mem_ctrl_ddr3.sv:15:6: Input port connection
  'ddr3_cke' expects 2 bits on the pin connection, but pin connection's
  VARREF 'ddr3_cke' generates 1 bits.
%Error: Exiting due to 2 warning(s)
```

Verilator 默认**警告即错误** → 构建失败，而 `emit_testbench` 返回 `ok=True`。
这是缺陷 110/111/122/123 同一模式的最新一处：**规范默认值 ≠ 真实端口位宽**。

## 修复

`fpga_toolbox/protocol_tb_emitter.py`：

- **DDR3**：`cke` / `cs_n` / `ras_n` / `cas_n` / `we_n` / `rd_valid` 全部由
  `_pw()` 取真实端口位宽，各自独立声明（不再合并进 1 位标量串）。
- **DDR4**：`act_n` / `cs_n` / `odt` / `rd_valid` 同样处理。
- 驱动方式改成按位宽安全的形式：
  - `ddr3_cmd` 里的 `{ras, cas, we} = cmd` 拼接连线改为**逐位赋值**
    （`ddr3_ras_n = cmd[2]` 等）—— 多位信号无法用一个 3 位拼接整体赋值。
  - `cs_n = 0` → `cs_n = '0`（`'0` 是「所有位全 0」，语义正是「全部 rank 都选中」）。
  - DDR4 的 `cs_n` / `act_n` / `odt` 同样改成 `'0` / `'1` 全位赋值。

这条「多位信号不能整体拼接赋值」是修复时的关键细节：如果只改声明不改 task
内部，会得到 `WIDTHTRUNC`（3 位拼接塞进 2 位 cs_n）。

## 验证

| 场景 | 修复前 | 修复后 |
|---|---|---|
| DDR3 2 位 `cke` | TB 侧 2 条 WIDTHEXPAND | **TB 侧 0 告警** |
| DDR4 2 位 `cs_n` | TB 侧宽度告警 | **TB 侧 0 告警** |
| 6 个参考 DUT（apb/axi4/ahb/hbm/ddr3/ddr4）| — | 全 `rc=0` / 0 告警 / 各 1 条 PASS |

> 两台变体 DUT 的 `rc` 仍是 1，但剩余告警全部来自 **fixture 自身**（我只改了
> 端口位宽，DUT 内部的 `CKE <= 1'b0` 之类赋值仍是 1 位）。判据是
> `grep '%Warning.*tb_ddr' = 0`，即 TB 侧干净。

回归 `test_ddr_control_widths_follow_port`：覆盖 `_pw` 取值、DDR3 `cke` 声明
`[1:0]`、旧「1 位标量串」形态不得残留、DDR4 `cs_n` 声明 `[1:0]`。
