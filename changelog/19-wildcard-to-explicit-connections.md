# Changelog — `.*` 通配连接改为显式连接（否则 DUT 有额外端口就编译失败）

上一轮（18）以为给 TB 插 `lint_off PINMISSING` 就解决了"DUT 带额外端口"的问题。本轮把扫描器从 APB 扩到全部 7 个协议后，发现 **6/7 协议仍报 BAD**——`lint_off` 完全无效。

### Fixed

- **6 个协议模板用 `.*` 通配连接，DUT 有 TB 未声明的端口时编译失败**（`protocol_tb_emitter.py`）。

  TB 里写的是：

  ```verilog
  {mod} dut (.*);
  ```

  `.*` 是 SystemVerilog 的隐式端口连接：它把 DUT 的每个端口连到 TB 里**同名**的变量。问题在于只有当 DUT 的每个端口在 TB 里都有同名变量时才成立。DUT 若带有 TB 不关心的额外端口（`dbg`/`test_mode`/`scan_en`，真实设计极常见），Verilator 报的是：

  ```
  %Error: Can't find definition of variable: 'dbg_status'
  ```

  **这是硬错误，不是 PINMISSING 警告**——上一轮的 `lint_off PINMISSING` 抑制不了它，因为错误类型根本不同。我上轮只测了 APB（APB 模板当时已改成显式连接？没有——APB 模板本来就没用 `.*`），所以误以为通用修复已完成。

### 修复

把全部 6 处 `.*` 换成显式端口连接，逐条列出模板实际声明并驱动的变量：

```verilog
{mod} dut (
  .{clk}({clk}), .{rst}({rst}),
  .{p['awaddr']}({p['awaddr']}), .{p['awvalid']}({p['awvalid']}), .{p['awready']}({p['awready']}),
  ...
);
```

换完之后，"DUT 有额外端口"退化为 `PINMISSING` **警告**（未连接的引脚），这才轮到上一轮加的 `lint_off PINMISSING` 生效。

### 附带修复：`lint_off` 对多行 module 头不生效

`add_lint_off()` 原来只在 `module tb_xxx;`（单行、以分号结尾）后插入注释。PCIe 的 TB 是参数化多行头：

```verilog
module tb_pcie_tlp_ep_pcie #(
  parameter int GEN = 3,
  ...
);
```

导致 PCIe 的 `PINMISSING` 一直没被抑制。修复：跟踪 module 头状态，等到 `);` 才插入注释。

### 一次判断失误与回退

我还尝试把 DDR3/DDR4 模板的位宽也参数化（响应 `narrow` 变异里的 `WIDTHTRUNC`）。DDR3 侧是合理的（地址/数据位宽独立于语义）。但 **DDR4 模板是 64 位定宽模型**：`ddr4_adr` 的高 3 位编码命令（ACT/READ/WRITE）、`midx = {bg, ba, adr[1:0]}` 的索引方式、数据常量、DM/DBI 掩码全部与 64 位语义强耦合。按 `data_width` 参数化后，`DDR4_001/002` 从 PASS 变成 `exp=0xDEADBEEF_CAFE0123 got=0x0000000000000000`。

已完整回退 DDR4 的位宽参数化，并在代码里写明这是有意保持定宽的模型，避免后人重蹈。教训：**位宽参数化必须以"测试语义不变"为前提**——对强耦合语义的模板硬塞参数化，就是把一个已知的窄总线警告换成更严重的功能回归。

### 扫描结果对比

| 阶段 | BAD | 说明 |
|---|---|---|
| 本轮开始（7 协议 × 4 变异） | **6** | 全部是 `extra`（DUT 带额外端口）|
| 换成显式连接后 | **0** | 硬错误全消，剩余为可抑制的 warning |

### 验证

- 7 协议生成全 OK；实跑 APB 8/8、AXI4 6/6、AHB 6/6、DDR3/DDR4/HBM PASS、**PCIe 9/9**。
- regression / fusion / audit 9-9 / docs_check D1–D13 / ruff / py3.10 全绿。
