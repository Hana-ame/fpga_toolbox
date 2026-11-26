# 141 · `lint_tb` 目录模式扫到 0 个文件却报 `clean: true`

## 现象

```
mkdir d && cat > d/bad_tb.sv <<'X'
module bad_tb; initial begin $display("PASS: x"); end endmodule
X
```

```
lint_tb(target_path="d")
→ {"scanned_sv_files": 0, "scanned_script_files": 0,
   "error_count": 0, "warning_count": 0, "clean": true, "findings": []}
```

**一个文件都没检查**，却报告「干净」。这是最危险的一类静默误判：用户看到
`clean: true` 就认为 TB 通过了 lint。

## 根因

`_collect_targets` 在目录模式下刻意用命名启发式筛选，避免把 DUT 当成 TB：

```python
if suf in (".sv", ".v") and (f.name.startswith("tb_") or "vip" in f.parts
                             or "driver" in f.name):
```

启发式本身合理，但**匹配数为 0 时没有任何提示**——`clean` 的语义退化成
「没发现违规」，包括「没看任何东西」。

`my_testbench.sv`、`bad_tb.sv`、`apb_tb.sv` 这类常见命名都会被静默跳过。

## 修复

- 新增 `scanned_anything` 字段（`scanned_sv_files + scanned_script_files > 0`）。
- 匹配数为 0 时追加 `W055` 诊断，说明目录模式的命名启发式，并提示
  「直接传文件路径可无视命名进行检查」。
- `strict=True` 下 `W055` 按既有规则让 `clean=false` → `ok=false`。

## 验证

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 目录含 `bad_tb.sv` | `scanned=0, clean=true, findings=[]` | `scanned=0, scanned_anything=false, W055` |
| `strict=true` | `ok=true` | `ok=false` |
| 直接传 `bad_tb.sv` 文件路径 | 正常扫 1 个文件 | 不变 |
| 目录含 `tb_good.sv` | 正常扫 | 不变，无 W055 |
| 真实 TB 目录（6 协议产物 / `examples/multi_bus_ref`） | 正常 | 不变（`anything=true, clean=true`）|

回归测试：`test_lint_tb_zero_matches_is_not_silent_clean`。

## 附带

新增诊断码 `W055`，已登记到 README 诊断码范围与 `docs/FEATURES.md` 表格。
