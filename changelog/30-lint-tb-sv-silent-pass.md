# Changelog — `lint_tb` 的头号宣传能力未实现：SV 测试台静默通过完全看不见

做文档时发现的问题。README 与 `docs/FEATURES.md` 开头都用这个症状作为工具箱的
核心卖点：

> 症状：CI 全绿，上板挂
> 真实原因：测试台只 `$display("DONE")`，没有任何判定
> 对策：`lint_tb` 的 S1/S2/S3 规则检出静默通过

按文档写的例子跑，工具**什么都没报**。

## 缺陷 — `scan_silent_pass` 只被喂 shell/脚本，SV 文件从不进入

`tool_lint_tb` 的分发逻辑：

```python
for f in sv_files:
    if "clocking" in enabled:
        findings.extend(scan_clocking(f))
    if "handshake" in enabled:
        findings.extend(scan_handshake(f))
    # ← 这里没有 silent_pass
for f in script_files:
    if "silent_pass" in enabled:
        findings.extend(scan_silent_pass(f))
```

`scan_silent_pass` 里三条规则全部是 shell/脚本形态：

| 码 | 实际口径 | 能否匹配 SV 测试台 |
|---|---|---|
| `S1` | `func() { return 0; }` 形态的 stub 函数 | 否（正则要求花括号单行体）|
| `S2` | `SKIP` 后紧跟 `return 0` / `exit 0` | 否（SV 用 `$finish`）|
| `S3` | `grep -c PASS` + `-ge N` 计数门禁 | 否（shell 专有）|

所以对一个只打印 `TEST DONE` 的 SV 测试台：

```
$ python3 -m fpga_toolbox lint_tb --target-path tb_silent.sv --rules '["silent_pass"]'
ok: True
findings: []
```

**返回"干净"。** 而这正是文档保证它能抓的第一号场景。

复现（修复前）：

```verilog
module tb_silent;
  initial begin
    #100;
    $display("TEST DONE");
    $finish;
  end
endmodule
```

```
$ call_tool("lint_tb", {"target_path": "...", "rules": ["silent_pass"]})
{'ok': True, 'findings': [], 'error_count': 0}
```

后果比"少一个检查"严重：这个 TB 的仿真是**必然通过**的——它从不表达失败。
用户按文档的承诺以为 lint 已经把关，实际拿到的是 `ok=True` 的虚假保证。
另一种同样坏的结果是「只数 PASS 行数」式门禁（S3 覆盖的那类）被搬到 SV 里，
而 SV 侧连这个都没有。

## 修复 — 新增 S4：SV 测试台必须有判定

`scan_sv_silent_pass(path)`，接入 `tool_lint_tb` 的 `sv_files` 循环，
`silent_pass` 规则组现在同时覆盖 `.sv` 与 `.sh`/`.py`：

判定口径（保守，优先零误报）：

1. 去注释（行注释 + 块注释）后，文件里必须存在 `$display`；
2. 且**不存在**任何判定构造：
   - `assert` / `$assert*` / `$error` / `$fatal`
   - `check_*` / `expect_*` / `verify_*` / `compare_*` / `scoreboard_*` / `fail*` 任务调用
   - `if (...) $error(...)` / `if (...) $fatal(...)` 邻近组合
3. 三条同时满足 → `S4`，`severity=ERROR`，定位到第一个 `$display` 行。

```python
def scan_sv_silent_pass(path: Path) -> List[Dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    code = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    code = re.sub(r"//[^\n]*", "", code)
    if not re.search(r"\$display\b", code):
        return []
    judges = [
        r"\bassert\b", r"\$error\b", r"\$fatal\b", r"\$assert\b", r"\$assertoff\b",
        r"\$\w*assert\w*\b",
        r"\b(?:check|expect|verify|compare|scoreboard|fail|error)(?:ing|_)?\w*\s*\(",
        r"\bif\s*\([^)]*\)[^;\n]*\$(?:error|fatal)\b",
    ]
    for pat in judges:
        if re.search(pat, code, re.IGNORECASE):
            return []
    ...
    return [{"severity": "ERROR", "code": "S4", ...}]
```

报错文案直接说明后果，而不是只报"有违规"：

```
SV testbench emits $display but contains no pass/fail decision
(no assert/$error/$fatal/check task). The simulation can only pass;
it never expresses failure.
```

## 验证

### 正向：三种静默形态全部检出

| fixture | 结果 |
|---|---|
| `#100; $display("TEST DONE"); $finish;` | `ok=False` `S4` |
| reset 序列 + `$display("reset ok")` | `ok=False` `S4` |
| 多段 `$display("phase1"/"phase2")` | `ok=False` `S4` |

### 反向：真实判定形态零误报

| fixture | 结果 |
|---|---|
| `assert (d==8'h12) else $error("bad");` | `ok=True` |
| `if (d !== 8'h12) $error("mismatch");` | `ok=True` |
| `if (d !== 8'h12) $fatal(1, "mismatch");` | `ok=True` |
| `check_eq(d, 8'h12);`（自研 task 内部 `$error`）| `ok=True` |
| 仓库内 18 个真实 TB（`examples/multi_bus_ref`）| `ok=True`，零 `S4` |

### 完整套件

```
$ python3 tests/test_regression.py              ALL CHECKS PASSED（含新增 4 项反向断言）
$ python3 tests/test_fusion_and_cross_repo.py   ALL FUSION CHECKS PASSED
$ python3 tests/test_eda_campaign.py            ALL EDA CAMPAIGN TESTS PASSED
$ python3 -m fpga_toolbox audit_harness         11 / 11
```

## 附带：让自审计真的盯住它（A11）

只加测试不够——测试是快照，护栏需要被持续监视。新增自审计项：

```python
A11_SV_SILENT_PASS_DETECT
  静默 TB 必须 ok=False 且含 S4
  带断言 TB 必须 ok=True          ← 防止"只会说坏"的检查器
```

反向验证它确实在监视（临时把 `scan_sv_silent_pass` 换成空函数，模拟回退）：

```
$ python3 -c "... L.scan_sv_silent_pass = lambda p: [] ..."
passed: 10 / 11
FAIL A11_SV_SILENT_PASS_DETECT | silent ok=True codes=[] judged_ok=True
```

整体从 11/11 降为 10/11 并明确指出是哪一项失效。

## 文档同步

- `docs/FEATURES.md` — 规则表补 `S4`，并写明 S 组对 `.sv` 与脚本都生效
- `docs/EXAMPLES.md` 示例 4 — 真实输出改为 `C1` + `S4` 两条，附 4 种"正确判定
  不会误报"的对照写法，以及 `S1`–`S4` 的适用文件类型
