# 169 · 请求错误协议时，错误信息不指出 DUT 实际是什么协议

## 现象

对一个 **AXI4-Lite** 从机传 `protocol="axi4"`：

```
Protocol 'axi4' not found in any module of axi_narrow.sv
(modules: {'axi_narrow_dut': ['axi4_lite']})
```

用户的第一反应是「分类器没认出我的总线」，于是去翻 RTL 端口、调 parser_backend
—— 而实际原因很简单：自己传错了协议名，或手上的 RTL 版本与预期不同。唯一
线索（括号里的 `axi4_lite`）被埋在句末，很容易被忽略。

## 根因

模块选择循环找不到 `want` 时，走的是兜底文案：

```python
f"{'Protocol ' + repr(want) + ' not found' if want else 'No supported multi-bus interface found'} "
f"in any module of {rp.name} (modules: {found})."
```

它只陈述「没找到」，没有把「检测到了什么」提到显眼位置。

（缺陷 148 已经改过另外两条相邻路径：auto 模式下「检测到但无模板」、以及
`want` 恰好**在**检测集里但无模板。这条「`want` 不在检测集里」的路径漏了。）

## 修复

当 `want` 非空且检测到了其它协议时：

```
Protocol 'axi4' was requested but axi_narrow.sv does not contain it.
Detected bus protocol(s): ['axi4_lite'].
(Supported protocols: ['apb', 'axi4', 'ahb_lite', 'ddr3', 'ddr4', 'hbm', 'pcie'].)
Detected per module: {'axi_narrow_dut': ['axi4_lite']}.
```

并在 `data` 里返回 `requested_protocol` / `detected_protocols` /
`supported_protocols` 三个字段，方便程序化处理。

## 验证

| 场景 | 修复前 | 修复后 |
|---|---|---|
| `axi4_lite` DUT + `protocol="axi4"` | `Protocol 'axi4' not found ...` | 点名 `Detected bus protocol(s): ['axi4_lite']` + data 三字段 |
| 真的没有任何协议 | 兜底文案 | 不变 |
| auto 模式选择 | 正常 | 不变 |

四套测试全绿。

回归测试：`test_wrong_requested_protocol_names_the_detected_one`。
