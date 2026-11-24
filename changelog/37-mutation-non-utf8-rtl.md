# Changelog — 变异测试的非 UTF-8 RTL：崩溃 + try/finally 永久破坏风险

延续 83/84 的编码排查。核实"剩余无容错 read_text 是否真安全"时，发现
`mutation_runner.py` 有 **2 处**处理**用户输入**（不是工具自产产物），
推翻了我上一轮"剩余项都处理自产产物、风险低"的分类。

## 缺陷 85-A — 变异 spec 解码失败被包成"格式错误"

`load_mutations` 用裸 `read_text(utf-8)`。latin-1 spec（注释含 0xE9）报：

```
Invalid mutation spec: 'utf-8' codec can't decode byte 0xe9 ...
```

**`Invalid mutation spec:` 这个前缀把用户引向"spec 格式错"**，而真正的错因是
**编码**——方向完全误导。

修复：加 `errors="replace"`。

## 缺陷 85-B — 变异目标 RTL：崩溃 + try/finally 永久破坏风险（最严重）

`run_mutation` 的变异目标 RTL 也是裸 `read_text(utf-8)`。这里有**两条失败路径**：

1. 直接抛 `UnicodeDecodeError` 冒泡（实测确认）；
2. **若天真地加 `errors="replace"` 会更糟**：非法字节变 U+FFFD，而
   `finally: fp.write_text(original, utf-8)` 会把**含 U+FFFD 的文本写回**。

我实测验证了第 2 条的真实危害——模拟 `read_text(replace)` + 变异 + finally
还原后：

```
原始 0xe9 数: 2
还原后 0xe9 数: 0        ← 全部丢失
字节是否一致: False       ← RTL 被永久破坏
```

**变异测试测完把被测 RTL 改坏了**——这是变异测试最不能接受的结果，
比崩溃严重得多。而且它绕过了 E030 残留守卫（守卫只查"内容是否还原成
original"，而 original 本身已被 replace 破坏）。

## 修复

变异目标改**按字节读写**：`read_bytes()` / `write_bytes()`，
`old`/`new` 编成 UTF-8 字节去匹配替换，非 ASCII 字节原样保留。
`finally` 还原时写回的是**原始字节**，不会引入 U+FFFD。

## 验证

```
$ run_mutation（latin-1 RTL + latin-1 spec）
ok: True | M01 KILLED（a&b→a|b 真的生效）
SHA-256 完全还原：824b9f4e... = 基线
0xe9 字节：1 → 1（原样保留）
文件长度：80 → 80
```

反向验证：改回 `read_text/write_text` → 新断言立即 FAIL 并显示真实错因
`'utf-8' codec can't decode byte 0xe9 ...`。

## 过程中两次踩回自己的坑（都被测试当场抓住）

1. **定义了测试却没在 runner 里注册** —— `grep` 找不到输出，补上注册行。
2. **`check()` 参数顺序记反** —— fusion 文件的签名是 `check(cond, msg)`，
   test_regression 的是 `check(name, cond, detail)`，两套不同。5 处调用
   `TypeError` 当场暴露。
3. **变异断言写反** —— 先期待 `SURVIVED`，实际 `KILLED`（变异应用后
   grep 找不到原 pattern → rc=1 → KILLED 才是"变异生效"的正确信号）。

## 回归测试

`test_mutation_non_utf8_rtl`，5 项断言：
- 变异在 latin-1 RTL 上无解码错误地运行
- 变异真的被应用（M01 KILLED）
- try/finally **按字节**还原（无 U+FFFD 破坏）
- SHA-256 与基线一致
- 非 ASCII 字节 0xe9 保留

## 验证

```
tests/test_regression.py              ALL CHECKS PASSED
tests/test_fusion_and_cross_repo.py   ALL FUSION CHECKS PASSED（新增 5 项）
tests/test_eda_campaign.py            ALL EDA CAMPAIGN TESTS PASSED
audit_harness                         13 / 13
ruff check                            All checks passed!
docs_check                            D1–D13 全过
```

## 现在剩余的无容错 read_text

逐处核实后，仍在的是处理**工具自产产物**（cocotb JSON 结果、报告、
`@file.json` CLI 参数）的调用——这些由本工具写出、必为 UTF-8。
用户提供的**用户输入**（RTL / 契约 YAML / 变异 spec）三条路径本轮已全部修完。
