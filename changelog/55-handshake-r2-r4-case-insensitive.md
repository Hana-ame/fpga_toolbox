# Changelog — `lint_tb` R2/R4 规则大小写不敏感（小写 TB 握手 bug 静默漏报）

## 背景

`tb_linter.scan_handshake` 有 4 条握手规则，其中两条的**正则只认大写**：

- **R4**（等 VALID 后立刻拉低 READY）：`while\s*\(\s*!\s*(?:\()?\s*axi\.([A-Z_]+VALID)`
  且 drop 检测 `\b{READY}\b`（规范大写 `BREADY`）**大小写敏感**
- **R2**（`if(VALID)` 分支内立刻拉低 READY）：`if\s*\(\s*axi\.([A-Z_]+VALID)\s*\)`

而 R1/R3 分支（缺陷 104 已修）已改为大小写不敏感。R2/R4 是同一 case-sensitivity
缺口残留的**兄弟分支**。

## 为什么是真缺陷（假阴性，握手 bug 静默漏报）

实测同一份握手 bug、只改信号大小写：

```
R4:
  while (!axi.BVALID) @(posedge clk); axi.BREADY <= 1'b0;   -> R4 ✓ 报
  while (!axi.bvalid) @(posedge clk); axi.bready <= 1'b0;   -> 漏报（修复前）
R2:
  if (axi.BVALID) begin axi.BREADY <= 1'b0; end              -> R2 ✓ 报
  if (axi.bvalid) begin axi.bready <= 1'b0; end              -> 漏报（修复前）
```

真实的第三方 AXI TB 大量使用小写信号名（`axi.bvalid` / `bready`）。这些**真实的
握手 bug（等 VALID 后立刻拉低 READY 会死锁）被静默漏报** —— lint 说「没问题」，
实际 TB 有死锁风险。这与缺陷 104 的假阳性方向相反（假阴性），但同根：case
sensitivity 与注释声称的「大小写不敏感」不符。

## 修复

`fpga_toolbox/tb_linter.py`：

- R4 正则 `([A-Z_]+VALID)` → `([A-Za-z_]+VALID)` + `re.IGNORECASE`；映射用
  `sig4.upper()`；drop 检测 `\b{ready4}\b` 加 `re.IGNORECASE`（源码 `bready`
  要能匹配规范大写 `BREADY`）
- R2 正则同样 `([A-Za-z_]+VALID)` + `re.IGNORECASE`；映射 `sig.upper()`；drop
  检测加 `re.IGNORECASE`

## 不误伤（回归验证）

- 子串 `invalid` / `already` 不触发 R2/R4（`[A-Za-z_]+VALID` 仍受 `\b` 词边界 +
  `axi.` 前缀约束）
- 裸 `BVALID`（无 `axi.` 前缀）不触发 R2/R4（这两条规则本就只针对 `axi.` 前缀）

## 测试

`tests/test_full_coverage.py::test_handshake_r2_r4_case_insensitive`：

- R4 upper / lower 都报 `R4`（修复前 lower 漏报）
- R2 upper / lower 都报 `R2`（修复前 lower 漏报）
- 子串与非 `axi.` 前缀不误报 `R2`/`R4`

## 验证

- `ruff check fpga_toolbox tests scripts` 0 错
- 4 个测试套件（regression / full_coverage / fusion / eda_campaign）全过
- `verify_invariants.py --fast` 5/5
