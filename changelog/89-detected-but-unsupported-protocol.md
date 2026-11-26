# 148 · 「协议没识别到」与「识别到了但没有 TB 模板」被混为一谈

## 现象

`axi4_lite` 是分类器认得的协议，但 `protocol_tb_emitter` **没有任何模板**
（`SUPPORTED_MULTI_PROTOCOLS` 只有 apb/axi4/ahb_lite/ddr3/ddr4/hbm/pcie）。
对一个 AXI4-Lite 主设备：

```
emit_testbench(protocol=None)
→ No supported multi-bus interface found in any module of axi_master.sv
  (modules: {'axi_master_dut': ['axi4_lite']})
```

错误信息自相矛盾：括号里明明列出了检测到的 `axi4_lite`，前半句却说「没找到
支持的多总线接口」。用户会以为是分类器的问题，去查 RTL 端口，而实际是
**分类器认出了、只是没有对应模板**。

显式传 `protocol="axi4_lite"` 时：

```
Unsupported protocol: axi4_lite
```

也没给出「支持哪些」。

## 根因

分类器（`iface_extractor`）与模板层（`protocol_tb_emitter`）的能力集不同：
前者 11 种协议，后者 7 种模板。错误信息只按「有没有找到匹配接口」二分，
没有把「检测到了但不支持」这一状态表达出来。

## 修复

- auto 模式：若检测到的协议全部/部分不在 `SUPPORTED_MULTI_PROTOCOLS`，报

  ```
  Cannot emit a testbench for xxx.sv: the RTL's bus protocol(s) ['axi4_lite']
  were detected but this emitter has no testbench template for them.
  Supported protocols: ['apb', 'axi4', ...]. Detected per module: {...}.
  ```

  并在 `data` 里返回 `detected_protocols` / `unsupported_protocols` /
  `supported_protocols`。

- 显式指定检测到但无模板的协议：同样解释成因。
- 早期 `Unsupported protocol: X` 分支补上支持列表与同样的 `data` 字段。
- 真正「没识别到任何支持协议」时保持原文案不变。

## 验证

| 场景 | 修复前 | 修复后 |
|---|---|---|
| auto，DUT 只有 `axi4_lite` | `No supported multi-bus interface found`（自相矛盾） | `detected but ... no testbench template` + 支持列表 |
| `protocol="axi4_lite"` | `Unsupported protocol: axi4_lite` | 附支持列表 + `data.unsupported_protocols` |
| 6 个受支持参考 DUT | 正常生成 | 不变（全部 `ok=True`） |

回归测试：`test_detected_but_unsupported_protocol_is_distinguished`。
