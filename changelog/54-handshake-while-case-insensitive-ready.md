# Changelog — `lint_tb` while-loop READY 映射大小写不敏感（防假 R3 错误）

## 背景

`tb_linter.scan_handshake` 检测「等 VALID 但未同时检查 READY」的握手风险，两种等价
写法都要覆盖：`wait (BVALID);` 与 `while (!BVALID) @(posedge clk);`。两分支都要把
VALID 信号映射到对应 READY（`BVALID`→`BREADY`），映射表 `_READY_OF` 键为**全大写**。

- `wait()` 分支：`_READY_OF.get(sig.split(".")[-1].upper())` ✓ 正确归一
- `while()` 分支：`_READY_OF.get(sig.replace("axi.", ""))` ✗ **不做大小写归一**

注释声称两分支都「大小写不敏感 / 兼容裸信号名与带前缀」，但 while 分支的实现只去掉
`axi.` 前缀、没 `.upper()`。

## 为什么是真缺陷（假错误，非漏报）

实测同一份代码、READY 已拉高（`BREADY = 1;`），**只改 VALID 信号的大小写**：

```
while (!axi.BVALID) @(posedge clk);   -> R1  (safe, WARNING)
while (!axi.bvalid) @(posedge clk);   -> R3  (假 ERROR)
```

纯大小写差异导致判定不同 —— 小写 / 混合大小写的 `while` 循环被误报成 `R3 ERROR`
（「等 VALID 未检查 READY」），而语义完全相同的 UPPERCASE 写法正确报 `R1 WARNING`。
这是**假阳性**：会驱使用户去「修」本已正确的 TB，或对 lint 输出失去信任。

真实的第三方 TB 大量使用小写信号名（`bvalid`/`axi.bvalid`），全部误报。

## 修复

`fpga_toolbox/tb_linter.py` 的 while 分支改用与 wait() 分支一致的
`.split(".")[-1].upper()`（去前缀 + 大小写归一）。同时清理了上方已过时的注释。

## 测试

`tests/test_full_coverage.py::test_handshake_while_case_insensitive_ready`：

- 5 种写法（`axi.BVALID` / `axi.bvalid` / `BVALID` / `bvalid` / `axi.Bvalid`）×
  wait() / while() 两分支，BREADY 已拉高时**全部一致报 R1**
- 明确断言 while 分支**不再因大小写产生假 R3**

## 验证

- `ruff check fpga_toolbox tests scripts` 0 错
- 4 个测试套件（regression / full_coverage / fusion / eda_campaign）全过
- `verify_invariants.py --fast` 5/5
