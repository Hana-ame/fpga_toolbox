# Changelog — 多总线 TB 生成器未校验必需端口（静默产出坏产物）

### Fixed

- **`emit_testbench --protocol <p>` 对缺端口的 DUT 生成无法编译的 testbench，却仍返回 `ok=True`**（`protocol_tb_emitter.py`）。

  协议分类器与 TB 模板对端口的要求不一致：`_APB_PATS` 只需 **4** 个信号就能把接口认成 APB（`min_cnt=4`，APB3 允许无 `pstrb`/`pslverr`），但 `_emit_apb_tb` 模板**硬连全部 9 个**信号，含 `pstrb`、`pslverr`：

  ```python
  pstrb = _pm(iface, "pstrb", "pstrb")     # 缺失时退回同名占位
  pslverr = _pm(iface, "pslverr", "pslverr")
  ...
  .{paddr}({paddr}), .{pwdata}({pwdata}), .{pstrb}({pstrb}),
  ```

  于是 APB3 风格设备（无 `pstrb`）会得到 `.pstrb(pstrb)` 连到不存在端口的 TB：

  ```
  %Error-PINNOTFOUND: tb_apb_no_pstrb_apb.sv:13:38: Pin not found: 'pstrb'
  ```

  而工具返回的是 `ok=True` + 产物路径——**静默生成坏产物**，正是本项目反复批判的行为：错误被推迟到 Verilator 编译期，且此时调用方已相信工具成功了。

### 修复

新增 `REQUIRED_TB_PORTS` 表（按协议列出模板**实际硬连**的端口）与 `_missing_tb_ports()`，在写文件之前校验 DUT 真实端口清单；缺失即 `err_result` 并列出缺哪个端口、以及检测到的全部端口：

```
Cannot emit a 'apb' testbench for apb3_no_pstrb: DUT is missing port(s)
pstrb->pstrb, pslverr->pslverr that the generated template drives.
Detected RTL ports: ['clk', 'paddr', 'penable', 'prdata', 'pready', 'psel', 'pslverr', 'pwrite', 'pwdata', 'rst']
```

关键点：

- 表必须与**模板实际连线**一致，不能照抄分类器的 `min_cnt`——第一版按分类器口径把 `pstrb`/`pslverr` 当可选，修复因此无效（实测仍 `ok=True`），已改正；
- `extract_interfaces` 的模块数据不含 `ports` 字段（只有 `total_ports`），校验前需 `parse_sv_file` 兜底取真实端口名；
- 端口齐全的正常路径不受影响。

### Tests

`tests/test_fusion_and_cross_repo.py::test_multi_bus_emitter_port_guard`（6 项检查）：缺 `pstrb` 的 APB 被拒绝、错误信息点名缺失端口、**不写出坏产物**、完整 APB 仍正常生成、TB 确实驱动 `pstrb`。

### 验证

- 7 协议生成全部成功；Verilator 实跑 APB/AXI4/AHB/DDR3/DDR4/HBM 均 PASS，无回归。
- regression / fusion / audit 9-9 / docs_check D1–D13 / ruff / py3.10 语法门禁全绿。
