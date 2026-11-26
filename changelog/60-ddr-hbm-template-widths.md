# Changelog — DDR3/DDR4/HBM TB 模板位宽跟随 DUT（防宽度告警阻断构建却报 ok）

## 背景

`emit_testbench(protocol=...)` 的 DDR3 / DDR4 / HBM 模板把端口位宽、数据常量、
`$display` 的格式宽度全部写死成"标准实现"的形态。Verilator **默认警告即错误**
（`WIDTHTRUNC` / `WIDTHEXPAND` / `WIDTHDIGITS` 都会让构建以非零退出），所以
DUT 只要位宽不同，生成出的 TB 就编译不过 —— 而工具照旧返回 `ok=True`，DUT 一点
没验到。

## 为什么是真缺陷（三个协议，均实测复现）

### DDR3

模板写死 `logic [2:0] ddr3_ba`、数据常量 `8'hBEEF` / `8'h1234`、格式 `%04h`。
DUT 用 2 位 BA + 8 位 DQ + 10 位 addr：

```
%Warning-WIDTHDIGITS: tb.sv:42: Too many digits for 8 bit number: '8'hBEEF'
%Warning-WIDTHDIGITS: tb.sv:44: Too many digits for 8 bit number: '8'h1234'
%Warning-WIDTHDIGITS: tb.sv:47: Too many digits for 8 bit number: '8'hBEEF'
%Warning-WIDTHDIGITS: tb.sv:55: Too many digits for 8 bit number: '8'h1234'
%Warning-WIDTHTRUNC:  tb.sv:16: 'ddr3_ba' expects 2 bits, ... generates 3 bits
%Error: Exiting due to 5 warning(s)
```

### DDR4

模板写死 `ddr4_adr` 17 位、`ddr4_dq_*` 64 位、`ddr4_dm_dbi_n` 8 位。
DUT 用 14 / 8 / 1 位：4 条 `WIDTHTRUNC`（adr 17→14、dq 64→8 ×2、dm_dbi_n 8→1）
后 `%Error: Exiting due to 4 warning(s)`。

> 旧代码里还留着一句「DDR4 模板是 64 位定宽模型，按 data_width 参数化会破坏
> 命令编码语义」的注释 —— 那是把「命令编码在高位」与「端口位宽必须匹配」混为
> 一谈。命令位段是 `ddr4_adr[16:14]` 的**语义**，与 TB 网线声明的**位宽**是两件
> 事：网线必须等于 DUT 端口位宽，否则连编译都过不去，谈何验语义。

### HBM

模板写死 `wdata`/`rdata` 256 位、ID 6 位、addr 28 位。HBM pseudo-channel 各厂商
差别很大（数据 64/128/256、ID 4~8 位、地址 30~34 位）：

- DUT 64 位数据 → 2 条 `WIDTHEXPAND`（wdata 256→64、rdata 256→64）
- DUT 512 位数据 + 8 位 ID + 34 位地址 → 6 条 `WIDTHEXPAND`

## 修复

`fpga_toolbox/protocol_tb_emitter.py`：

- 新增 `_pw(iface, canon, default)`：从 `port_details`（扫描 DUT 端口得到）取
  **RTL 真实位宽**；缺失时按 canon 语义退回 `addr_width` / `data_width`；再缺失
  才用标准默认值（绝不返回 0，否则生成 `[-1:0]`）。
- DDR3：`ddr3_ba` / `ddr3_addr` / `ddr3_dq_*` 全部按 `_pw`；数据常量按真实位宽
  掩码（8 位总线上 `0xBEEF → 0xEF`）；十六进制位数与 `%0Nh` 同步；bank 值按
  `(1<<ba_d)-1` 截断。
- DDR4：`ddr4_bg/ba/adr/dq_*/dm_dbi_n` 全部按 `_pw`；数据常量掩码 + hex 位数同步；
  命令位段改为 `(0b100 << (aw_d - 3))` 计算，窄 adr 时自然退化到 0（不越界）。
- HBM：`awid/bid/arid/rid`、`awaddr/araddr`、`wdata/rdata` 全部按 `_pw`；ID 值按
  位宽截断；数据图案按**位**构造 —— 低 64 位放常量、高位按 64 位块重复再取整
  dw 位。这里踩过一个坑：一开始按十六进制字符数截断（`pat_hex[:16]`），
  dw=256 时 16 字符 × 5 = 80 截到 64，低 64 位变成 `0xF123456789ABCDEF`
  （多进一个半字节），实测让 HBM_002 的期望值直接错掉。改按位构造后正确。

## 验证

6 个参考 DUT（apb / axi4 / ahb_lite / ddr3 / ddr4 / hbm）全部 `rc=0`、**0 条
宽度告警**、`PASS:` 各 1 条。窄/宽变体（DDR3 2 位 BA + 8 位 DQ、DDR4 14/8/1 位、
HBM 64 位与 512 位数据）现在都是 0 宽度告警 —— 之前全部 `%Error` 退出；
剩下的 CHECK FAIL 是 stub DUT 本身不实现存储导致的**真实**失败，不是模板问题。

回归 `tests/test_full_coverage.py::test_ddr_hbm_templates_follow_port_widths`：
覆盖 `_pw` 的三级回退（port_details → addr_width/data_width → 默认），以及三个
模板的网线位宽、常量掩码、`%0Nh` 格式宽度、HBM 图案按位构造（显式断言
`64'hF123456789ABCDEF` 不出现）。

完整验证：4 个测试套件全过 / ruff 0 错（0.17.6 本地非系统）/
`verify_invariants --fast` 5 层全过 / docs HTML 同步。
