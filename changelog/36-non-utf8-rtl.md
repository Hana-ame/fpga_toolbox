# Changelog — 非 UTF-8 RTL：误导性诊断 + 未捕获异常

按"异构环境"这一枪排查。工业界 RTL 常含 GBK / Shift-JIS / latin-1 注释
（`// capacité`、`// 容量` 等），这些文件**不是合法 UTF-8**。而工具箱里
`read_text(encoding="utf-8")` 有 10 处不带 `errors=` 容错。

## 缺陷 83-A — RTL 编码失败被误报成"端口不存在"

我构造了一份**端口完全正确**的 latin-1 RTL（只在注释里放了 `0xE9` = é），
`emit_testbench` 却报：

```
Spec has 19 error(s): E011: Port 's_axi_awaddr' missing in RTL.;
E011: Port 's_axi_awprot' missing in RTL.; ... （共 19 条）
```

真正的错因是**编码无法解码**（`'utf-8' codec can't decode byte 0xe9`），
`read_text` 抛 `UnicodeDecodeError` 后整份 RTL 变空，解析器一个端口都看不到
——于是报成"19 个端口不存在"。**报错方向完全错误**：用户会去检查端口映射、
模块名、RTL 路径，而不是文件编码。

修复：`core/sv_parser.py` 新增 `_read_rtl(path, diagnostics)`，用
`errors="replace"` 容错解码（非法字节变 U+FFFD，注释乱码不影响模块/端口结构），
并在 diagnostics 里留一条可见信号说明"文件不是合法 UTF-8、有字节被替换"
（与 slang 回退可见性同一设计原则）。

修复后同一份 RTL：解析出 `reg_slave` + 3 端口，工具照常工作。

## 缺陷 83-B — 复制 RTL 时未捕获异常直接冒泡

上一轮（35）让 `emit_testbench` 把 RTL 复制进 out_dir，但我用的是
`write_text(rtl_src.read_text(encoding="utf-8"))`。非 UTF-8 RTL 会抛**未捕获的
`UnicodeDecodeError`**，直接冒泡成：

```
ok: false
error: 'utf-8' codec can't decode byte 0xe9 in position 45: invalid continuation byte
```

`ok=false` + 一句 CPython 内部异常文本，**完全没指向编码问题**。这是我上一轮
引入的。

修复：改 `write_bytes(rtl_src.read_bytes())` —— 按字节复制，**保留原始编码**，
交由解析器容错。实测 latin-1 RTL 复制后字节完全一致（`cmp` 通过），端到端
`make` 照常跑通并产波形。

## 验证

反向验证：把 `write_bytes` 改回 `read_text` →
`non-UTF-8 RTL still emits ok (tolerant decode)` 立即 FAIL，并显示真实错因
`'utf-8' codec can't decode byte 0xe9 ...`。

```
tests/test_regression.py              ALL CHECKS PASSED（新增 3 项）
tests/test_fusion_and_cross_repo.py   ALL FUSION CHECKS PASSED
tests/test_eda_campaign.py            ALL EDA CAMPAIGN TESTS PASSED
audit_harness                         13 / 13
ruff check                            All checks passed!
docs_check                            D1–D13 全过
build_docs_html.py --check            HTML 与 md 同步

端到端（latin-1 RTL，真编译）
  emit_testbench → ok=True，RTL 字节完整复制
  make SIM=verilator VERILATOR_TRACE=1 → TESTS=1 PASS=1
  dump.vcd 6034B/145信号，reg_slave_axil_wrap.vcd 42736B/145信号
```

## 新增回归断言

`test_contract_path_waveform_and_w014` 内新增 3 项：
- `non-UTF-8 RTL still emits ok (tolerant decode)`
- `non-UTF-8 RTL copied byte-for-byte`
- `no spurious E011 port-missing from encoding issue`

## 未修的相关项（记录）

仓库里另有 8 处 `read_text(encoding="utf-8")` 无容错（cli / validator /
mutation_runner / result_normalizer / eda_campaign）。它们处理的是**工具自己
生成的产物**（契约 YAML、变异 spec、cocotb JSON、报告），这些由本工具写出、
必为 UTF-8，风险低于用户提供的 RTL。本轮只修**用户输入的 RTL** 这条最高价值
路径；其余若将来发现真实案例再逐个修。
