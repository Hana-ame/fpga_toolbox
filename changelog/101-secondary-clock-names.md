# 164 · 第二时钟 `clk_extra` / `clk2` / `clk_i` 识别不到，被静默 tie 到 0

## 现象

```systemverilog
module twoclk (
  input logic clk,
  input logic clk_extra,     // 第二时钟
  input logic rst_n,
  ...
);
```

`extract_interfaces` 输出：

```json
{"clock": {"port": "clk"},
 "out_of_scope_ports": [
   {"port": "clk_extra", "dir": "input", "width": 1, "tie_off": "1'b0"}],
 "diagnostics": []}
```

`clk_extra` 被 tie 到 `1'b0`，**而且没有任何警告**。多时钟设计里把第二个时钟
拉成常量是灾难性的（整个时钟域永不工作，仿真可能仍然"跑完"并报 PASS）。

讽刺的是代码里**本来就有**这条警告：

```python
if _is_clock(p.name) or _is_reset(p.name):
    item["warning"] = "clock/reset-like port left out of scope; verify tie-off is intended"
```

## 根因

`_CLK_RE` 要求时钟词出现在名称**末尾**：

```python
re.compile(r"^(?:[a-z0-9]+_)*(?:[ahps]?clk|clock)$", re.I)
```

于是 `clk_extra`、`clk2`、`clk_i`、`clk_in`、`clk_aux`、`clk_extra_i` 全部
识别不到 —— 连那条已存在的警告都触发不了。命中的只有 `clk` / `aclk` /
`hclk` / `pclk` / `sys_clk` 这类「时钟词在末尾」的写法。

## 修复

正则允许时钟词后带可选后缀（可叠加）与序号：

```python
_CLK_RE = re.compile(
    r"^(?:[a-z0-9]+_)*"
    r"(?:[ahps]?clk|clock)"
    r"(?:_(?:in|i|o|extra|aux|2nd|b|p|n))*"
    r"(?:\d+)?"
    r"$", re.I)
```

## 验证

| 名称 | 修复前 | 修复后 |
|---|---|---|
| `clk` / `aclk` / `hclk` / `pclk` / `sys_clk` | ✓ | ✓ |
| `clk_extra` / `clk2` / `clk_i` / `clk_in` / `clk_aux` / `clk_extra_i` / `clk_p` / `clk_n` | ✗（静默 tie 0） | ✓ 并带 warning |
| `clken` / `clk_en` / `clock_en` / `clk_gate_en` / `clear` | ✓（不误判） | ✓（仍不误判） |

7 个参考 DUT 的 `clock`/`reset` 检测结果完全不变
（`HCLK`/`pclk`/`aclk`/`clk` 与对应复位）。

回归测试：`test_secondary_clock_names_are_recognised`。
