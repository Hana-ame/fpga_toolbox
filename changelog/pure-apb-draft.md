# Changelog — 纯 APB 设备的 draft 契约自相矛盾

### Fixed

- **`extract_interfaces --emit-draft-yaml` 对纯 APB 设备生成自家 validator 拒绝的 draft**（`iface_extractor.py`）。

  生成 draft 契约时，`axi4_lite` 段是**无条件**写出的：即使 RTL 里只有一套 APB 端口、根本没有 AXI4-Lite 接口，也会写出

  ```yaml
  axi4_lite:
    addr_width: 12
    data_width: 32
    role: slave
    port_map: {}     # ← 空的
  ```

  而 `validator.py` 用 `has_axil = isinstance(axil_raw, dict) or not has_apb` 判定"是否存在 AXI 边界"——空 dict 也是 dict，于是 `has_axil=True`，接着对空 `port_map` 逐条检查 16 个必需 AXI 信号，产生 **17 条 E001**（`Missing required 'awaddr'` … `'rready'`）。

  后果：对一个合法的纯 APB 寄存器文件，`extract_interfaces` 生成的 draft **过不了本项目自己的 `validate_contract`**，21/22 条诊断都是用它根本不需要的 AXI 信号刷出来的。用户看到 17 条 E001 只会以为自己契约写错，而不是"工具只支持 AXI4-Lite 设备"。

  这不是理论问题：按 README 推荐的两步流程（`extract_interfaces --emit-draft-yaml` → `emit_testbench --spec-path`）对纯 APB RTL 执行，第一步产物即被第二步以 17 条 E001 拒绝。

### 修复

只在 RTL 真的存在 AXI4-Lite 接口时才写 `axi4_lite` 段：

```python
if axil or not apb:            # 有 AXI 接口，或两种接口都没有（保持旧行为）
    boundary["axi4_lite"] = {...}
```

- **纯 APB 设备**：draft 只含 `apb` 段 → `validate_contract` 返回 `ok: True`、0 条 ERROR（修复前 17 条）。
- **AXI4-Lite 设备 / 无接口设备**：行为不变（`axil or not apb` 为真），向后兼容。
- `emit_testbench` 的报错也随之变得可读：原来是 17 条 E001，现在是
  `Spec has no AXI4-Lite boundary (boundary.axi4_lite.port_map is empty). emit_testbench generates an AXI4-Lite wrapper; ...`

### Tests

新增 `tests/test_regression.py::test_pure_apb_draft`（4 项检查）：纯 APB draft 含 `apb` 段、**不含** `axi4_lite` 段、`validate_contract` 通过且 0 ERROR。

### 验证

- 纯 APB：`extract → draft` 后 `validate_contract` → `ok: True`，0 ERROR（HEAD 同场景 17 ERROR）。
- AXI↔APB 桥（`axi_apb_bridge_sv.sv`）：修复前后 ERROR 数均为 25（差异为 0），确认为**既有的** SV interface 端口无法展开成扁平 `port_map` 问题，与本次修复无关，未被改动。
- 全量回归：`tests/test_regression.py` ALL CHECKS PASSED，`tests/test_fusion_and_cross_repo.py` ALL FUSION CHECKS PASSED，`audit_harness` ok，`docs_check` D1–D13 全通过。
