# Changelog — `--rtl-file` 文件名含 make 元字符：损坏 Makefile / 命令注入面

## 背景

排查契约路径 Makefile（37/39/40/41）时，只处理了"RTL 名与产物同名冲突"。
这轮测到另一类：**RTL 文件名本身含 make 元字符**。

## 缺陷 90 — RTL 文件名含 `$` / 空格 / 引号 / `;` 破坏 Makefile 语法

`emit_testbench --spec-path` 把 RTL 文件名直接写进 Makefile 的
`VERILOG_SOURCES`（未转义）。实测 RTL 名含 `$(python3 ...)` 时：

```
$ make SIM=verilator
make: *** No rule to make target 'd+chr(116)+chr(109)+chr(112)+...).sv',
       needed by 'sim_build/Vtop.mk'.  Stop.
```

make 把注入的 `$(...)` 片段当成目标文件名，行为不可预测。这类注入与
`eda_campaign` 的 SSH/SCP 参数注入**同类**（changelog 21，那里已用白名单
修复，实测 host 含 `; touch /tmp/PWNED;` 会真的执行 touch）。

## 修复

契约路径对 `--rtl-file` 的文件名加白名单校验（与 changelog 21 同一设计）：

```python
if not re.match(r"^[A-Za-z0-9_.\-]+$", _rtl_name):
    return err_result("emit_testbench",
        f"--rtl-file {_rtl_name!r}: filename contains characters that would "
        "corrupt the generated Makefile (make metacharacters like $, spaces, "
        "quotes, semicolons). Allowed: [A-Za-z0-9_.-]. Rename the RTL file.", ...)
```

只约束**契约路径**（它会生成 Makefile）。多协议路径把 RTL 传给 verilator
命令行并加引号，且不生成 Makefile，不受此限。

## 验证

| RTL 名 | 结果 |
|---|---|
| `d$ut.sv` | 拒绝 ✅ |
| `my dut.sv`（空格）| 拒绝 ✅ |
| `d"ut.sv`（双引号）| 拒绝 ✅ |
| `d;ut.sv`（分号）| 拒绝 ✅ |
| `d'ut.sv`（单引号）| 拒绝 ✅ |
| `dut.sv`（正常）| 接受 ✅ |

错误信息写明"会损坏生成的 Makefile"并给出允许字符集与改名字建议。

## 回归测试

`test_contract_path_waveform_and_w014` 内新增 11 项断言（5 种元字符 ×
拒绝+解释，+ 正常名仍被接受）。

## 验证

```
tests/test_regression.py              ALL CHECKS PASSED（新增 11 项）
tests/test_fusion_and_cross_repo.py   ALL FUSION CHECKS PASSED
tests/test_eda_campaign.py            ALL EDA CAMPAIGN TESTS PASSED
audit_harness                         13 / 13
ruff check                            All checks passed!
docs_check                            D1–D13 全过
build_docs_html.py --check            HTML 与 md 同步
```

## 过程踩坑

- 加校验时用了 `re.match` 但忘了 `import re` → `NameError: name 're' is not
  defined`（语法 OK 但运行时炸）。补 `import re`。
- 注入测试构造了 4 次失败的载体（文件名含 `/` 导致 cp 失败、含空格导致
  文件名非法、module_name 含元字符先被 E011 拦）。最终用 `d$(python3 -c"...").sv`
  无空格载体确认 make 把它当目标名。
