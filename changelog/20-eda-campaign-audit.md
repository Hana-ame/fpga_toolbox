# Changelog — eda_campaign 审计：3 个让清理器失效的真实缺陷

上一轮合并的 `fpga_toolbox/eda_campaign.py`（366 行）只跑过它自带的测试。本轮做独立审计，
用「真实 DC 网表形态」压测 `clean_netlist_for_pt`，发现 3 个真实缺陷——都落在这个函数要解决
的主场景上。

## 缺陷 A — 参数化 blackbox 实例完全不被清理

`inst_re` 用非贪婪 `\((.*?)\)` 匹配实例端口块：

```python
inst_re = re.compile(rf"^[ \t]*(?:{bb_alt})\s+[A-Za-z0-9_\$]+\s*\((.*?)\)\s*;", re.M | re.S)
```

遇到参数化覆盖时，正则从 `RDBUS` 之后先撞上 `#(` 里的第一个 `)`：

```
RDBUS #(.WIDTH(8), .DEPTH(4)) u_rdbus (.i_data(a), .o_data(o[3:0]));
              ↑ 旧正则在这里截断，整条实例匹配失败
```

实测 `removed_instances == 0`，网表原样输出、`RDBUS` 仍在。而这恰恰是文档 §3 要解决的
**「参数化 RDBUS/CLKCROSSBUS 位宽不一致导致 PT link_design 失败」主场景**——工具最该
生效的地方完全没工作。

修复：把端口块与可选 `#(...)` 参数块都改成可嵌套模式：

```python
_nested = r"(?:[^()]|\((?:[^()]|\([^()]*\))*\))*"
inst_re = re.compile(
    rf"^[ \t]*(?:{bb_alt})\b(?:\s*#\s*\(\s*{_nested}\s*\))?"
    rf"\s+[A-Za-z0-9_\$]+\s*\(\s*({_nested}\s*)\)\s*;",
    re.M | re.S,
)
```

## 缺陷 B — 注释里的 `endmodule` 让成块扫描错位，并往注释里注入 assign

两处叠加：

1. `mod_re = re.compile(r"(module\s+\w+.*?endmodule)", re.S)` 会在**注释中的**
   `endmodule` 上提前截断。若实例恰在该注释之后，它落在截断块外，永远不会被清理；
2. 插入 tie-off 时 `re.sub(r"\bendmodule\b", ..., count=1)` 同样会命中注释里的
   `endmodule`，产出语法错误的网表：

```verilog
  // this comment mentions   assign o = 1'b0;
endmodule but is not the real one
```

修复：模块切分与 assign 定位都改用「跳过注释/字符串」的扫描模式
（`_SKIP = r'''/\*.*?\*/|//[^\n]*|"(?:[^"\\]|\\.)*"'''`），并用 `finditer` 找**最后一个**
真正的 `endmodule` 作为插入点。

## 缺陷 C — 单行压缩的网表完全不清理

`inst_re` 以 `^[ \t]*` 锚定行首。DC 导出的网表常把整个模块压成一行：

```verilog
module t(o); output o; RDBUS #(.W(8)) u (.o_data(o)); endmodule
```

此时 `RDBUS` 前面是 `; ` 而不是行首，锚点永不满足，`removed_instances == 0`。

修复：把锚点从「行首」放宽到「标识符边界」：

```python
rf"(?<![\w.$])(?:{bb_alt})\b"
```

用负向后视确保不会误伤名字里含 `RDBUS` 的其它模块（如 `my_RDBUS_wrapper`），
已加断言覆盖。

## 一次自我纠偏：位选择不应被合并

审计中我一度把 `_flatten_nets` 改成「剥离位选择取根名」（`o[3:0]` -> `o`），理由是
「`assign o[3:0] = 1'b0;` 只驱动低 4 位」。但仓库自带测试立刻失败：

```
assert stats["tied_off_nets_count"] == 3
```

对 `{q_a[1], q_a[0]}`，正确输出是**两条标量 assign**（`assign q_a[1]=1'b0;`
`assign q_a[0]=1'b0;`），合并成 `assign q_a = 1'b0;` 反而会用 1 位驱动多位向量，
引入位宽不匹配——与注释里写的「逐根插入标量 assign」意图相违。

已完整回退该改动，只在 `_flatten_nets` 里保留「去重」这一无害改进，并在 docstring
写明为什么必须保留位选择。

## 附带修正

`parse_uvm_log` 失败分支原用 `err_result(...) | {"data": data}` 把数据挂上去。
`err_result` 早已支持 `data=` 关键字参数，改用显式传参，避免 dict 合并依赖
返回结构的键名（脆弱写法）。

## 验证

| 场景 | 修复前 | 修复后 |
|---|---|---|
| `RDBUS #(.WIDTH(8)) u (...)` | `removed=0`，实例残留 | `removed=1`，正确清理 |
| 注释含 `endmodule` + 其后实例 | `removed=0`，且 assign 注入注释 | `removed=1`，注释原样保留 |
| 两缺陷合并的真实网表 | — | `removed=2`，**Verilator 编译 0 硬错误** |

自带 4 项测试 + 新增 `test_clean_netlist_param_and_comment_edges`（3 组断言）；
regression / fusion / audit 9-9 / docs_check D1–D13 / ruff / py3.10 全绿。
