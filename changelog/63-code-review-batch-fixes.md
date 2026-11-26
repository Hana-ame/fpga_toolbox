# Changelog — 代码评审批量修复（变异得分假通过 / 索引覆盖 / lint 路由 / 输入健壮性）

对 v0.5.0（commit `5d491ec`）做了一轮外部评审，逐条复现后确认并修复 14 项问题。
以下按「为什么是真缺陷」分组，每条都给出实测复现证据。

## 1. `run_mutation` 无 baseline 预检 → 假 100% 变异得分（最严重）

旧实现只看「变异后 `test_cmd` 是否非零」就判 `KILLED`。若 `test_cmd` 本身是坏的
（名字写错、缺二进制、或**测试台本来就是失败的**），每个变异都以非零退出 →
全部 `KILLED` → 报 **100% 变异得分**。变异得分的定义前提是「未变异版本必须通过」，
而旧实现从未验证过这个前提。

重现（`test_cmd = ["false"]`，永远返回 1）：

```
total=2 killed=2 survived=0 score=100.0     <- 一个故障都没真正验到
```

同组还有三个问题：

- `subprocess.run` 只 catch 了 `TimeoutExpired`，命令不存在时
  `FileNotFoundError` 直接冒泡成裸堆栈 → 现在归一成结构化错误。
- `survived = total - killed` 把 `status="ERROR"`（缺文件 / 越界）也算成
  「存活」并留在分母：一个文件写错就凭空多一个存活变异，把 100% 拖到 50%，
  而 survived 的语义（「测试台没杀掉的故障」）被污染。
- 全 `ERROR` / 全 `SKIP` 时 `total` 是分母但 `evaluated=0`，报「0% 得分」
  毫无意义。

修复：变异前先跑一次**未变异**的 `test_cmd`，非零即 fail-fast（一行源码都不改）；
分开统计 `evaluated / killed / survived / errors / skipped`，`score = killed/evaluated`；
`evaluated == 0` 时显式失败；`OSError` 也捕获。

## 2. `index_repo` 用展开后的源码覆写子模块数据

`` `include `` 会让 slang 把子模块展开进顶层文件，于是 `parsed` 里有
`(datapath.v, datapath)` 和 `(top.v, datapath)` 两条。旧实现两条都处理，后者用
`top.v` 的源码算 `instantiated_modules` / `includes`，把顶层的例化与 include
**覆盖**到子模块上。实测：

```
datapath  file=rtl/datapath.v  insts=[]           includes=["datapath.v"]   <- 错！
top       file=rtl/top.v       insts=[dp, sub]    includes=["datapath.v"]
```

`datapath` 自己并没有 include 任何东西，却被标成 include 了 `datapath.v`。
修复：`owner` 判定后跳过「不属于本文件」的解析结果；同时把
`for fpath: for mname in <全部模块名>` 的 O(文件数 × 模块数) 次 `_module_body`
（每次都 `sv_tokenize` 整份源码）改成**每文件只 tokenize 一次**并取出它原始文本里
声明的模块名。

## 3. `lint_tb` 规则路由错位 + 三类盲区

- **S1 在错误的扫描器里**：SV 常量-stub 检测写在 `scan_handshake` 内，导致
  `rules=["silent_pass"]` **完全跳过** S1，而 `rules=["handshake"]` 反而报 S1。
  已抽成 `_scan_s1_sv_function_stubs()` 并由 `scan_sv_silent_pass` 调用。
- **`_IFACE_RE` 匹配不了 `s_apb`/`m_apb`**：`[sm]_[ap]x[i]` 只能匹配
  `s_axi`/`m_axi`/`s_pxi`/`m_pxi`，与紧邻注释声称的覆盖范围矛盾。改成
  `[sm]_(?:axi|axil|apb)`。
- **R2/R4 写死 `axi.` 前缀**（R1/R3 没写死）：`while (!s_axi.bvalid)` /
  `if (m_axi.bvalid)` 的拉低 READY 死锁 bug **完全静默漏报**。改成可选的多级
  层次前缀 `(?:ident\.)*`，顺带覆盖裸信号名。
- **S4 可被字符串绕过**：`scan_sv_silent_pass` 用朴素 `re.sub(r"//[^\n]*")` 剥注释
  且不剥字符串字面量。`$display("no error(s)")` 里的 `error(` 会被当成判定构造，
  把「只打印不判定」的 TB 静默放行。改用 `_strip_comment` + 先剥字符串字面量。

## 4. `call_tool` 参数校验

- **Optional enum 拒绝 None**：`{"protocol": null}`（MCP 客户端把省略的可选字段
  序列化成 null 的常见行为）被报「非法枚举值」，而类型校验明确写着「显式 None
  交给函数处理」——两处语义矛盾。加 `params[pname] is not None`。
- **required 接受显式 None**：`{"out_dir": None}` 同时通过类型校验（None 跳过）
  和 required 校验（key 存在），None 漏进函数体后报出与参数无关的下游错误
  （实测 `RTL file not found: x.sv`，而真因是 out_dir 为 None）。
- **`number` 接受 bool**：`isinstance(True, (int, float))` 为真，`min_score=True`
  被当数字收下。
- **原地改写调用方 dict**：boolean 字符串强转写的是 `params[pname]`，调用方复用
  同一个 dict 发第二次调用时会拿到被偷偷改过的值。入口 `params = dict(params)`。

## 5. `sv_parser` 整数类型宽度与 `$clog2` 边界

- `byte` / `shortint` / `int` / `longint` 被归进「明确的 1 位标量类型」，
  `output int o` 报 `width=1`。真实宽度应为 8 / 16 / 32 / 64。builtin 与 slang
  两个后端都修了（builtin 原本遇到类型名只 `idx += 1`，完全不设宽度）。
- `$clog2(1)` 返回 1，IEEE 1800 定义为 **0**（`$clog2(0)` 也是 0）。原式
  `max(1, (v-1).bit_length())` 在 `v=1` 时给 1 —— 深度 1 的存储器需要 0 位地址。

## 6. `cli.run_single_tool` 缺 @file 错误处理

`main()` 有 `except ValueError`，`run_single_tool()`（工具模块被直接当脚本跑时
走这条）没有。同一个 `@file` 路径不存在，走 `main()` 得到结构化错误，走
`run_single_tool()` 得到裸 Python 堆栈。现补齐为同构输出 + `rc=1`。

## 7. `_to_int` 对 17 个字段仍会抛 ValueError

`offset` 早在缺陷 103 改成容错的 `_to_int_or_none`，但
`addr_width` / `data_width` / `reset_value` / `write_mask` / `max_resp_cycles` /
`iterations` / `width` / `seed` 等 17 个调用点仍是裸 `int(str(val), 0)`。
`addr_width: "abc"` 让 `validate_contract` 抛
`ValueError: invalid literal for int() with base 0: 'abc'` —— 裸堆栈、无字段定位、
不进诊断码体系。改成解析失败返回 default，并补 **W010** 诊断说明「该字段被忽略、
用了默认值」（静默退化到默认同样是假信息）。

## 8. `eda_campaign` 远端路径与 PT 网表编码

- `use_dev_shm=False` 时 `mkdir/cd` 用的是 `{remote_work_dir}/{tag}`，但脚本末尾
  打印给用户的 `scp -r ...:/dev/shm/$TAG/report` 与 `rm -rf /dev/shm/$TAG`
  仍硬编码 `/dev/shm` —— 用户照着提示去 `/dev/shm` 找一个不存在的目录
  （报告其实在 remote_work_dir 下），`rm -rf` 也删不到真正的工作目录。
  引入 `WORK` 变量统一两处。
- `clean_pt_netlist` 读网表用裸 `read_text(utf-8)`（与 sv_parser / validator 的
  `errors="replace"` 不一致），非 UTF-8 注释会抛 UnicodeDecodeError。
- 残留检测的 `\b{name}\b` 匹配不到 Verilog **转义实例名**（`\u_bb/0 (...)`，
  `\b` 在 `\` 之后不成立），会漏报「其实没清干净」。

## 9. 生成产物的插值未转义

- `_emit_cocotb` 用 `json.loads(r"""...""")` 嵌入契约 JSON：寄存器描述里只要
  出现连续三个双引号或结尾反斜杠，生成的 cocotb 测试台就是 `SyntaxError`，
  而工具已报 `ok=True`。改用 `repr()`。
- `_emit_wrap` 把 `os.environ["WAVE_FILE"]` **原文**插进 `$dumpfile("...")`：
  Windows 路径 `C:\tmp\w.vcd` 的反斜杠会产生非法 SV。现在归一成正斜杠并剥掉引号
  （`C:\tmp\w"x.vcd` → `C:/tmp/wx.vcd`，合法且语义不变）。

## 10. 复位极性靠脆弱的字符串 replace

active-high 复位原本靠两处 `tb_src.replace(f", {rst} = 0", ...)` /
`tb_src.replace(f"    {rst} = 1;", ...)` 打补丁。模板缩进或写法一变，replace
静默匹配不到 —— 生成的 TB 带着**反相复位**跑下去，仿真不报错，只是 DUT 永远
停在复位（或永远不复位），表现为莫名 FAIL/超时。现在改为正则按语义改写
（容忍任意空白），并在改写后**断言确实改到了**；改不到就返回错误，绝不静默生成
一份极性不可信的 TB。实测 active-high APB DUT：声明 `preset = 1`、释放
`preset = 0;`，Verilator 实跑 `rc=0` / 0 告警 / 8 项检查全 PASS。

## 11. 文档/注释漂移

`registry.py` 首行「12 atomic tools」（实际 13）、`self_auditor.py` 首行
`Tool 12`（实际 Tool 13）、`README.md` 的「A1–A11」（实际 A1–A14）。全部改正，
并用 `list_tool_schemas()` / `audit_harness` 的返回值核实计数。

## 验证

- 4 个测试套件全过（`test_full_coverage` / `test_regression` /
  `test_fusion_and_cross_repo` / `test_eda_campaign`）。
- ruff 0 错（0.17.6，`PYTHONPATH=/tmp/ruffpkg`，非系统安装）。
- `verify_invariants --fast` 5 层全过；`--full` 6 层（含 100% 函数覆盖）。
- 新增 4 个回归测试：
  `test_review_fixes_mutation_baseline_and_accounting`、
  `test_review_fixes_index_repo_and_linter_routing`、
  `test_review_fixes_registry_parser_and_types`、
  `test_review_fixes_artifact_escaping_and_polarity`。
- 既有测试中依赖「坏 test_cmd 也能拿分」的用例（`["false"]`）已改用真正有
  判别力的命令（`grep -q`），这正是这轮修复要消灭的那类假通过。
