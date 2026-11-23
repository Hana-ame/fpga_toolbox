# Changelog — `clean_pt_netlist` 静默产出未清理的网表 + 补 A10 自审计

上一轮把 `clean_pt_netlist` 注册成第 13 个工具。本轮验证它**真的能证明自己工作**，
发现它只检查「有没有清理动作」，不检查「清理有没有生效」。

## 缺陷 — 实例语法未被匹配时静默报成功

`tool_clean_pt_netlist` 清理后直接返回 `ok_result`：

```python
outp.write_text(cleaned, encoding="utf-8")
return ok_result("clean_pt_netlist", stats)   # 不看产物里还有没有黑盒
```

实测一个缺分号的网表（DC 输出被截断、手工编辑后常见）：

```verilog
module t(o);
  output [7:0] o;
  RDBUS u (.o_data(o))     // ← 缺分号
endmodule
```

结果：

```
ok=True, removed_instances=0
输出网表仍含 RDBUS
Verilator: 2 个硬错误（syntax error, unexpected endmodule）
```

**工具报成功，产物是坏的。** 这正是 PrimeTime link_design 失败的直接原因，而工具
本该是防止这件事的。

## 修复

清理后做**产物自检**：若输出里仍有黑盒模块的实例化形态，返回 `err_result` 并带
`E051` 诊断：

```
Blackbox instance(s) still present in the cleaned netlist: ['RDBUS'].
Their instantiation syntax was not matched (missing semicolon / truncated
statement / unsupported construct). PrimeTime would still fail link_design.
```

检测前先剥离注释与字符串（复用审计中已有的 `_SKIP` 思路）——否则
`// RDBUS is used elsewhere` 这种纯注释提及会被误判成残留。这一点是实测发现的：
第一版没剥注释，导致「注释含黑盒名」的正常网表被误报为失败。

## 补 A10 自审计项

`audit_harness` 原本只覆盖 A1–A9，新工具没有任何故障注入验证。加入：

**A10_PT_NETLIST_RESIDUAL_GUARD** — 用一个黑盒无法清理的网表喂给
`tool_clean_pt_netlist`，断言它必须返回 `ok=False` 且诊断含 `E051`。

验证 A10 是真护栏而非摆设：临时禁用 E051 检查后，A10 立刻 FAIL、整体降为 9/10；
恢复后回到 10/10。

README / docs 的 `A1–A9` 同步为 `A1–A10`，诊断码表补 `E051`。

## 验证

- 缺分号网表：`ok=False` + `E051`（修复前 `ok=True`）
- 正常参数化 / 单行压缩 / 无黑盒 / 注释含黑盒名：全部 `ok=True`（无误报）
- `audit_harness` 10/10；新增 `test_clean_pt_netlist_residual_guard`（3 组断言）
- eda_campaign / regression / fusion / docs_check D1–D13 / ruff / py3.10 全绿
