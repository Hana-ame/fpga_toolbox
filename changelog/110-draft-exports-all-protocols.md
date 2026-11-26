# 179–180 · draft 只导出 AXI/APB，且非 AXI 契约无法通过自家校验

## 179 · draft 凭空谎报 `axi4_lite`，真实协议段丢失

对一个 **DDR3** 控制器执行 `extract_interfaces --emit-draft-yaml`：

```yaml
boundary:
  module_name: ddr3_mem_ctrl
  ...
  axi4_lite:            # ← 完全不存在这种总线
    addr_width: 12
    data_width: 32
    role: slave
    port_map: {}        # ← 空
```

`ddr3` 段**完全不存在**。根因是 draft 生成逻辑只处理两种接口：

```python
axil = next((i for i in ifaces if i["protocol"] in ("axi4_lite", "axi4")), None)
apb  = next((i for i in ifaces if i["protocol"] == "apb"), None)
if axil or not apb:      # 「两者都没有」也成立 → 塞空 AXI 段
    boundary["axi4_lite"] = {...}
```

后果：下游拿这份 draft 选协议/模板，会把 DDR3 控制器当成 AXI 设备。

修复：

- **所有**已识别接口按协议名导出（`ddr3` / `ddr4` / `hbm` / `pcie` /
  `ahb_lite` / `axi4_stream` / `spi` / `ethernet`），带 `role`、`port_map`、
  以及可用时的 `addr_width` / `data_width` / `split_port_map` /
  `pcie_gen` / `pcie_lanes` / `missing_signals`；
- `axi4_lite` 段只在**确实存在** AXI 接口时写（条件由 `axil or not apb`
  收紧为 `if axil`）；
- 只有「一个接口都没识别到」时，才保留 AXI 占位段（契约层需要统一出口）。

## 180 · 纯 DDR3/HBM/PCIe 契约被 17 条 E001 判死

用上面修好的 DDR3 draft 走 `validate_contract`：

```
E001  Missing required 'awaddr'.
E001  Missing required 'awvalid'.
... 共 17 条
```

`has_axil` 的判定是：

```python
has_axil = isinstance(axil_raw, dict) or not has_apb
```

对「既没有 `axi4_lite` 段、也没有 `apb` 段」的 spec，`not has_apb` 为真 →
强行进入 AXI 校验分支，按 AXI 的必需信号清单报错。即契约层**假设设备一定是
AXI 或 APB**。

修复：先检查 spec 里是否声明了其它已知总线（`ddr3`/`ddr4`/`ddr5`/`hbm`/
`pcie`/`spi`/`ethernet`/`axi4_stream`/`ahb_lite`），只有都没声明时才回退到
AXI 假定（保持「空 spec 当 AXI」的旧行为）。

## 验证

| DUT | draft 总线段 | validate_contract |
|---|---|---|
| `apb_slave.sv` | `['apb']` | ✅ |
| `axi4_full_slave.sv` | `['axi4_lite']` | ✅（2 条 W001 可选未映射） |
| `ahb_lite_slave.sv` | `['ahb_lite']` | ✅ |
| `ddr3_mem_ctrl.sv` | `['ddr3']` + `split_port_map.dq` | ✅ |
| `ddr4_mem_ctrl.sv` | `['ddr4']` | ✅ |
| `hbm_pc_ctrl.sv` | `['hbm']` | ✅ |
| `pcie_tlp_ep.sv` | `['pcie']` | ✅ |
| 无总线 DUT | `['axi4_lite']`（占位） | ✅ |

修复前：DDR3/DDR4/HBM/PCIe 的 draft 都含空 `axi4_lite` 且 validate 失败。

回归测试：`test_draft_yaml_exports_all_protocols_and_validates`。
