# Changelog — DDR3/DDR4/HBM/PCIe 模板端口校验缺口

上一轮（14）给 `emit_testbench --protocol` 加了生成前端口校验，但只覆盖
`apb`/`axi4`/`ahb_lite` 三个协议——`REQUIRED_TB_PORTS` 里没有
`ddr3`/`ddr4`/`hbm`/`pcie`，这四个协议仍可静默产出无法编译的 TB。

### Fixed

- **`ddr3`/`ddr4`/`hbm`/`pcie` 的 TB 模板端口名与 DUT 不一致时，生成坏 TB 却报 `ok=True`**（`protocol_tb_emitter.py`）。

  这四个 emitter 与前三个的实现方式不同：**完全不用 `port_map`**，直接按标准端口名声明 TB 变量，并用 `.*` 通配兜底：

  ```verilog
  ddr3_partial dut (.clk(clk), .rst_n(rst_n), .ddr3_ck_p(clk), .ddr3_ck_n(~clk), .*);
  ```

  `.*` 只会连接**TB 里已声明**的同名变量。因此当 DUT 的端口名与模板声明不匹配时——真实设计里很常见的 `inout [15:0] ddr3_dq`（而模板声明的是分离的 `ddr3_dq_in` / `ddr3_dq_out`）——Verilator 直接编译失败：

  ```
  %Error: tb_ddr3_wild_ddr3.sv:10:13: Can't find definition of variable: 'ddr3_dq'
  %Error: tb_ddr3_wild_ddr3.sv:10:13: Can't find definition of variable: 'ez_extra_port'
  ```

  而工具返回的仍是 `ok=True` + 产物路径。

  为什么自带靶场没暴露：`examples/multi_bus_ref/ddr3_mem_ctrl.sv` 恰好用的是**分离端口** `ddr3_dq_in`/`ddr3_dq_out`，与模板声明一致，`.*` 正常工作。真实第三方 DDR3 控制器普遍用三态 `inout` 总线。

### 修复

按各 emitter 模板**实际声明**的端口名补齐 `REQUIRED_TB_PORTS`：

| 协议 | 校验端口数 |
|---|---|
| `ddr3` | 11（含 `ddr3_dq_out`、`ddr3_rd_valid`）|
| `ddr4` | 11（含 `ddr4_dm_dbi_n`、`ddr4_rd_valid`）|
| `hbm` | 18（完整 AXI 式读写通道 + ID）|
| `pcie` | 11（tx/rx tvalid/tready/tdata/tlast + link_up/cfg_speed/cfg_lanes）|

至此 7 个协议全部纳入生成前校验（`SUPPORTED_MULTI_PROTOCOLS` 与
`REQUIRED_TB_PORTS` 的 key 集合一致）。

### Tests

`test_multi_bus_emitter_port_guard` 从 6 项扩到 12 项，新增：

- `inout ddr3_dq` 形态的 DUT 被拒绝、错误信息点名 `ddr3_dq_out`、**不写出坏 TB**；
- 自带靶场的 `ddr3`/`hbm`/`pcie` 三个参考 DUT 仍能正常生成（防止把好路径堵死）。

### 验证

- 官方靶场 7 协议生成全部成功；Verilator 实跑 APB 8/8、AXI4 6/6、AHB 6/6、
  DDR3/DDR4/HBM PASS、**PCIe 9/9 套件 PASS**。
- regression / fusion / audit 9-9 / docs_check D1–D13 / ruff / py3.10 全绿。
