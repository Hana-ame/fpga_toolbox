# 138 / 139 · typedef 端口位宽未解析时生成非法或致败代码

纯文本解析（无 slang）拿不到 `typedef enum/struct` 与 `interface` 端口的位宽，
`extract_interfaces` 里这些端口的 `width` 都是 `0`。两处下游因此出错。

## 138 · tie_off 生成非法字面量 `0'b0`

`iface_extractor` 对 out-of-scope 输入端口算 tie-off：

```python
item = {..., "tie_off": f"{p.width}'b0"}     # width=0 → "0'b0"
```

零位宽字面量是**非法 SV**，而 `tb_emitter` 会把它直接写进生成代码
（`.EXTRA_STATE_IN(0'b0)`），产物必然编译失败。

修复：位宽未知时使用 SV 的 unsized 全零字面量 `'0`（宽度由上下文决定），
并在该端口条目上附 `warning` 说明位宽未解析。

## 139 · 观察端口被声明成 1 位 → WIDTHTRUNC 硬失败

`tb_emitter._emit_wrap` 为 out-of-scope 输出端口生成观察变量：

```python
w = f"[{o.width - 1}:0] " if o.width > 1 else ""   # width=0 → 无位选 = 1 位
```

DUT 侧 `output dbg_e dbg_state_o` 实际是 **3 位**（enum），wrapper 里却声明成
1 位并连接 —— Verilator 报 `WIDTHTRUNC`，默认配置下
`%Error: Exiting due to N warning(s)`，wrapper 直接构建失败。
改成 64 位兜底同样不行：2 位端口会转成 `WIDTHTRUNC`（实测两种猜测都失败）。

修复：位宽未知的观察端口改用**显式空连接** `.port()`，实测零告警；同时

- 记录进 `unconnected_observation_ports`（返回体），
- 逐端口发 `W054` 诊断，明确告诉调用方「该端口未被观察」。

## 验证

AXI4-Lite DUT 带 `input dbg_e dbg_mode_i` + `output dbg_e dbg_state_o`：

| 项目 | 修复前 | 修复后 |
|---|---|---|
| `tie_off` | `0'b0`（非法） | `'0` |
| wrapper 连接 | `.dbg_state_o(obs_dbg_state_o)`，1 位 vs 3 位 | `.dbg_state_o()` |
| Verilator lint | `WIDTHTRUNC` → 构建失败 | 0 warning |

`verilator --lint-only --top-module axil_typed_axil_wrap *.sv` →
`final warn count=0`。

回归测试：`test_typedef_port_width_placeholders_are_legal`。

## 附带

新增诊断码 `W054`（`emit_testbench`：输出端口位宽未解析、wrapper 未连接该观察
端口），已登记到 README 诊断码范围与 `docs/FEATURES.md` 表格。
