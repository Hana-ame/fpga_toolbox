# Changelog — `por_n` 等 power-on-reset 端口名不被识别为复位（6 协议 DUT 全路径被挡）

## 背景

`iface_extractor` 用一组锚定正则识别时钟/复位端口名。复位家族原来只覆盖
`rst` / `reset` / `nreset` / `nrst`（带 `a/h/p/s` 前缀与 `_n`/`n`/`b` 后缀）：

```python
_RST_RE = re.compile(r"^(?:[a-z0-9]+_)*(?:[ahps]?reset|[ahps]?rst|nreset|nrst)(?:_?[nb])?$", re.I)
```

**`por` / `poreset` / `power_on_reset` / `pon` 完全不在其中** —— 而
`por_n` 是工业界非常标准的 power-on-reset 端口名。

## 为什么是真缺陷

仓库自带的 `examples/multi_bus_ref/omni_hub/soc_omni_bus_hub.sv` 是一个集成了
PCIe + DDR4 + AXI4 + APB + SPI + Ethernet 六种协议的 DUT，它用：

```systemverilog
module soc_omni_bus_hub #(...) (
  input logic clk,
  input logic por_n,        // <-- 唯一复位，命名就是 por_n
  ...
always_ff @(posedge clk) begin
  if (!por_n) begin
    ...
```

后果不是"少一条 warning"，而是**这个 DUT 的所有协议路径全被挡掉**：

```
$ emit_testbench --rtl-file soc_omni_bus_hub.sv --protocol pcie
Cannot emit a 'pcie' testbench for soc_omni_bus_hub: no reset port detected in
the RTL. The generated template hard-wires a reset pin, so a fabricated name
would not compile (PINNOTFOUND). Add a reset port (rst/rst_n/aresetn/hresetn/...)
or drive the DUT without one.

（ddr4 / axi4 / apb 同样全部 False）
```

报错文案也误导：用户明明写了 `por_n`，提示却让他"加一个复位端口"，并列出
`rst/rst_n/aresetn/hresetn` —— 一个都不像他写的名字。这是"检测逻辑覆盖不到的
合法输入"，在本仓库最重要的多协议示例 DUT 上 100% 复现。

## 修复

`fpga_toolbox/iface_extractor.py`：

- `_RST_RE` 增加 POR 家族：`por` / `poreset` / `power_on_reset` / `pon`，
  同时支持 `n` 前缀（`npor` / `nporeset`）与既有 `_n`/`n`/`b` 后缀。
- `_reset_active_low` 同步覆盖：
  - 后缀式 `por_n` / `poresetn` / `power_on_reset_n` → active-low
  - 前缀式 `npor` / `nporeset` / `npon` → active-low
  - 裸 `por` / `pon` → active-high（POR 通常低有效，但名字没标极性时不擅自反转）
  - **保留原有 `[ahps]?` 前缀分支**，`s_axi_aresetn` / `hresetn` / `presetn` /
    `resetb` / `resetn` 全部保持 active-low（改这处时一度把后缀分支写坏，
    导致 `s_axi_aresetn` 退化成 active-high，回归用例抓到了）。

## 不误伤

正则锚定 + 后缀严格可选，`port` / `ponder` / `por_ready` / `por_cnt` /
`por_init_guard` / `por_cke_mrs_zqcl` / `power_on_reset_done` / `reset_done`
全部仍**不**被当作复位（已加断言）。

## 验证

- 16 个复位名极性用例 0 mismatch（POR 家族 + 既有家族 + 不误伤集合）。
- omni hub 现在 `reset = {"port": "por_n", "active_low": True}`，`emit_testbench`
  的 `pcie` / `axi4` / `apb` 不再被挡（`ddr4` 仍被挡，但原因是**真的缺端口**
  `ddr4_ck_t/ddr4_ck_c/ddr4_odt/...`，属正确报错）。
- 7 个参考 DUT 回归全绿：`rc=0`、0 条告警、各 1 条 `PASS`（pcie 9 个 suite
  全 PASS）。

回归 `tests/test_full_coverage.py::test_power_on_reset_names_detected`：覆盖
POR 家族识别 + 极性 + 不误伤集合 + omni hub 端到端（reset 必须检出为
`por_n`/active-low，且 `emit_testbench(protocol="pcie")` 必须成功）。

完整验证：4 个测试套件全过 / ruff 0 错（0.17.6 本地非系统）/
`verify_invariants --fast` 5 层全过 / docs HTML 同步。
