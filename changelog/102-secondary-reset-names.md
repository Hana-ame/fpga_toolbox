# 165 · 第二复位 `rst_extra` / `rst2` / `rst_i` 识别不到，被静默 tie 到固定电平

## 现象

```systemverilog
module tworeset (
  input logic clk,
  input logic rst_n,
  input logic rst_extra,    // 第二复位域
  ...
);
```

`extract_interfaces` 只认出 `rst_n`，`rst_extra` 落进 `out_of_scope_ports`
被 tie 到固定电平，且**没有任何警告**。

与缺陷 164（第二时钟）**完全同构**：多复位域设计里把第二个复位拉死，
整块逻辑永不出复位态，而仿真可能照样跑完并报 PASS。

## 根因

`_RST_RE` 同样要求复位词出现在名称**末尾**，只允许一个极性后缀：

```python
r"^(?:[a-z0-9]+_)*(?:[ahps]?reset|[ahps]?rst|nreset|nrst"
r"|n?por|n?poreset|n?power_?on_?reset|n?pon)(?:_?[nb])?$"
```

识别不到的常见命名：`rst_extra`、`rst2`、`rst_i`、`reset_extra`、
`rst_async`、`rst_sync_i`、`rst_hard`、`reset_sync`。

## 修复

保留极性后缀 `_?[nb]`（`rst_n` / `rst_b`），其后允许叠加修附属后缀与序号：

```python
r"(?:_?[nb])?"
r"(?:_(?:in|i|o|extra|aux|2nd|b|p|n|async|sync|a|hard|soft))*"
r"(?:\d+)?"
r"$"
```

## 验证

| 名称 | 修复前 | 修复后 | 极性 |
|---|---|---|---|
| `rst_n` / `reset_n` / `nrst` / `aresetn` / `hresetn` / `presetn` / `por_n` | ✓ | ✓ | active-low ✓ |
| `rst_extra` / `rst2` / `rst_i` / `reset_extra` / `rst_async` / `rst_sync_i` / `rst_hard` | ✗（静默 tie） | ✓ + warning | active-high ✓ |
| `rst_b` | ✓ | ✓ | active-low ✓ |
| `rst_en` / `reset_en` / `restart` / `result` / `rstdone` / `burst` | 不误判 | 不误判 | — |

复位极性判定对新增形态全部正确（12/12），7 个参考 DUT 的 clock/reset 不变。

回归测试：`test_secondary_reset_names_are_recognised`。
