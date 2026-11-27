# 112 · 修复内置 SV 解析器把自定义 `typedef` 端口误判为 1-bit（181）

> 对应验收项：`changelog/111-acceptance-review.md` #181（P1）

## 现象

在未安装 `slang`、退回到内置 SV 解析器的环境中，执行 `test_typedef_port_width_placeholders_are_legal` 失败：

```
AssertionError: {'port': 'dbg_mode_i', 'dir': 'input', 'width': 1, 'tie_off': "1'b0"}
  assert oos["dbg_mode_i"]["tie_off"] == "'0"
```

对 `input dbg_e dbg_mode_i` / `output dbg_e dbg_state_o`（`dbg_e` 为 3-bit `enum`），内置后端输出 `width=1`、`tie_off="1'b0"`，而非 Defect 138/139 要求的 `width=0`、`tie_off="'0"`；输出端也被生成 1-bit 观察线而非显式空连接 `.dbg_state_o()`。

## 根因

`fpga_toolbox/core/sv_parser.py` 的 `_parse_ansi` 与 `_parse_non_ansi` 在解析端口声明时：

- 只识别内置类型集合 `_TYPES`（`logic`/`wire`/`int` 等）；
- 遇到自定义 `typedef` 类型名（如 `dbg_e`、`pkg::type_t`）时提前跳出类型循环；
- 随后在尾部 token 中只保留最后一个 `IDENT` 作为端口名，却仍沿用标量默认宽度 `cur_w = 1`。

## 修复

1. **`fpga_toolbox/core/sv_parser.py`**：
   - 在 `_parse_ansi` 与 `_parse_non_ansi` 中记录是否出现显式位宽范围 `[...]`（`explicit_range`），并收集端口名之前、赋值/解包维度之前的全部标识符与作用域符 `::`；
   - 当存在多于 1 个标识符或 `::` 作用域前缀且无显式位宽范围时，判定为纯文本无法求值的自定义 `typedef` 端口，将 `width` 置为 `0`、`width_expr` 记为类型名（如 `dbg_e` 或 `pkg::t_e`），触发下游 `'0` tie-off 与 `.port()` 空连接 + `W054` 保护。
2. **`tests/test_full_coverage.py`**：
   - 在 `test_typedef_port_width_placeholders_are_legal` 中显式补充 `parser_backend="builtin"` 断言，锁死无 `slang` 降级路径不再回归。

## 验证

- `python3 tests/test_full_coverage.py`：`test_typedef_port_width_placeholders_are_legal` 通过（`builtin` 与默认后端均返回 `width=0, tie_off="'0"`，wrapper 含 `.dbg_mode_i('0)` 与 `.dbg_state_o()` 并报 `W054`）。
- `python3 tests/test_regression.py`、`python3 tests/test_fusion_and_cross_repo.py` 全通过。
