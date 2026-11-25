# Changelog — `_ready_raised_before` 大小写不敏感（共用 helper 残留的假 R3）

## 背景

`tb_linter.scan_handshake` 的 `_ready_raised_before(idx, ready_sig)` 被 **R1/R3/R2
三条规则共用**，判断「READY 是否已在前面拉高」。`ready_sig` 是规范大写（`BREADY`），
但源码里可能是小写（`bready`）。旧实现 `\b{ready_sig}\b` **大小写敏感**。

缺陷 104（R1/R3 映射）、105（R2/R4 正则 + drop 检测）都改了各自的位置，但**漏了这个
三条规则共用的 helper** —— 它仍是 case-sensitive。

## 为什么是真缺陷（假 R3 错误）

实测小写 TB 里 READY 已拉高：

```
bready = 1;    wait (axi.bvalid);   -> 修复前 R3（假 ERROR），修复后 R1（safe）
bready <= 1;   wait (axi.bvalid);   -> 修复前 R3（假 ERROR），修复后 R1（safe）
BREADY = 1;    wait (axi.BVALID);   -> R1（一直正确，对照）
```

同一个共用的 helper 对小写 READY 拉高识别不到 -> R1/R3/R2 全部误判成「READY 未拉高」
而报假错误。真实第三方 TB 大量用小写信号名，全部中招。这是**假阳性**（驱使用户去
「修」本已正确的 TB），与缺陷 104 同方向。

## 修复

`fpga_toolbox/tb_linter.py` 的 `_ready_raised_before`：两处 `\b{ready_sig}\b` 搜索
（`1'b0` 判低、`1` 判高）都加 `re.IGNORECASE`。

## 不误伤（假阴性回归验证）

- READY 被**拉低**（`bready <= 1'b0`）后立刻 wait -> 仍报 **R3**（真风险）
- **完全没有** READY 赋值 -> 仍报 **R3**
- 大写、小写两种写法都验证，不得因 IGNORECASE 变假通过

## 测试

`tests/test_full_coverage.py::test_ready_raised_before_case_insensitive`：

- READY 已拉高（大写 `BREADY = 1` / 小写 `bready = 1` / 非阻塞 `bready <= 1`）→
  都报 `R1`，不得假 `R3`（修复前两个小写用例报 R3）
- READY 被拉低（大写/小写）→ 仍报 `R3`
- 完全没有 READY → 仍报 `R3`

## 验证

- `ruff check fpga_toolbox tests scripts` 0 错（ruff 0.17.6 本地非系统安装）
- 4 个测试套件（regression / full_coverage / fusion / eda_campaign）全过
- `verify_invariants.py --fast` 5/5
