# 163 · 黑盒输出端口未被识别，清理后留下悬空 wire

## 现象

```
module top (input clk, output [3:0] q, output v);
  wire [3:0] d;
  wire valid;
  RDBUS #(.WIDTH(4)) u1 (.clk(clk), .din(clk), .dout(d), .vout(valid));
  assign q = d;
  assign v = valid;
endmodule
```

`clean_pt_netlist(blackbox_modules=["RDBUS"])` 输出：

```
  // [fpga_toolbox:pt_clean] removed unresolved parameterized blackbox instance
  assign q = d;
  assign v = valid;
endmodule
```

实例删掉了，但 `d` 和 `valid` **没有任何驱动**。`verilator -Wall` 报 4 条
`UNDRIVEN`，而 PrimeTime 的 `link_design` 正是拒绝未驱动网络 —— 工具本意是
「把 PrimeTime 不接受的黑盒实例去掉」，结果把问题换成了另一种它同样不接受的
形式。

## 根因

输出端口识别只有一个前缀：

```python
output_port_prefixes: Sequence[str] = ("o_",)
```

`dout` / `vout` / `q` / `out` / `data_out` / `valid_o` 这些真实命名全部漏判，
于是 `_strip_inst` 里

```python
if any(pname.startswith(pref) for pref in output_port_prefixes):
```

不成立，网络不进 `mod_ties`，就没有 `assign <net> = 1'b0;`。

## 修复

扩充识别面（前缀 + 后缀两族，仍保守、不猜数据流向）：

```python
output_port_prefixes = ("o_", "out_", "out", "dout", "q_", "q", "data_out", "rd_", "rdata")
output_port_suffixes = ("_o", "_out", "_dout", "_q", "out", "dout", "valid")
```

判据变成「前缀命中 **或** 后缀命中」。

## 验证

| 项目 | 修复前 | 修复后 |
|---|---|---|
| `.dout(d)` 的输出网络 `d` | 悬空（UNDRIVEN） | `assign d = 1'b0;` |
| `.vout(valid)` 的输出网络 `valid` | 悬空 | `assign valid = 1'b0;` |
| `verilator -Wall` UNDRIVEN 条数 | 4 | **0** |
| 标量赋值形式（PrimeTime 不接受拼接） | — | 每条独立 `assign`，在 `endmodule` 之前 |
| 输入端口（`clk`/`din`） | 不 tie | 仍不 tie（由外部驱动） |

四套测试全绿。

回归测试：`test_netlist_cleaner_ties_off_blackbox_outputs`。
