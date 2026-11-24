# Changelog — 契约路径完全没有波形支持 + `waveform=True` 静默失效 + 死宏

上一轮修了波形抓取（32-waveform-and-log-capture）。按"首次/边界/异构环境"
三枪排查，第一枪就打中了：`emit_testbench` 有**两条代码路径**，上一轮只修了
一条。

| 路径 | 入口 | 生成器 | 上一轮 |
|---|---|---|---|
| 多协议直出 | `--rtl-file --protocol` | `protocol_tb_emitter.py` | 已支持 |
| **契约路径** | `--spec-path` | `tb_emitter.py` | **完全没有** |

契约路径正是 README / docs/EXAMPLES.md 的**示例 1**用的那条 —— 也就是说，
文档里最显眼的那条端到端示例，产出的 wrapper / SVA / cocotb TB 里**一个
`$dumpfile` 都没有**。实测 `grep -rln dumpvars /tmp/ct/` → NONE。

## 缺陷 79-A — 契约路径无波形支持

修复：在 `_emit_wrap()` 生成的 `*_axil_wrap.sv` 里注入 `$dumpfile` / `$dumpvars`，
由运行期 `+WAVE` plusarg 开关控制，与协议路径保持一致。

## 缺陷 79-B — 兜底文件名裸输出，契约 TB 根本编译不过

第一版把兜底文件名写成裸标识符：

```verilog
$dumpfile(reg_slave_axil_wrap.vcd);   // ← 少了引号
```

Verilator 把 `.vcd` 当**层次引用**解析：

```
%Error: reg_slave_axil_wrap.sv:56:39: Can't find definition of 'vcd'
        in dotted variable/method: 'reg_slave_axil_wrap.vcd'
```

契约路径 TB **完全无法编译**。这是我写修复时引入的，**只有真编译一次才暴露**
（静态看代码只觉得是普通字符串）。

修复：`$dumpfile("{wave_file}")` —— 加引号。回归测试逐个检查 `$dumpfile(...)`
的实参，非变量的一律必须是带引号的字符串字面量。

## 缺陷 79-C — 死宏：编译期开关没人 define

更隐蔽的一版。我先用编译期宏控制开关：

```verilog
`ifdef WAVE_DUMP
  initial begin ... $dumpvars(0, ...); end
`endif
```

问题：`WAVE_DUMP` 是**谁 define 的**？全仓库 grep 只有一处（定义处本身），
没有任何调用方 define —— 于是这个 `ifdef` **永远不成立**，波形**永远不注入**，
而代码看起来"已经支持了"。

这正是本仓库反复强调的反模式：代码写了开关，但没有一条路径会打开它。
静默失效比报错更糟，因为 `ok=True`。

修复：改用**运行期 plusarg** `+WAVE` / `+WAVE_FILE`（与 `protocol_tb_emitter`
的 sim 脚本一致），并加死宏检测断言 `"``ifdef WAVE_DUMP" not in src`。

真编译 + 运行验证（Verilator 5.054）：

```
$ verilator --binary --trace ... tb_top.sv reg_slave_axil_wrap.sv ...
BUILD OK
$ ./obj/sim +WAVE "+WAVE_FILE=wrap.vcd"
PASS: contract wrap compiled+ran
wrap.vcd  145 signals        ← 由 wrapper 自身的 $dumpvars 产出

$ ./obj/sim            # 不加 +WAVE
（wrap.vcd 不存在 —— 证明 +WAVE 是真开关）
```

## 缺陷 79-D — `waveform=True` 静默失效（无 W014）

`run_sim(waveform=True)` 在**脚本不响应 `WAVE=1`** 时（用户手写的 `sim.sh`
根本不知道 `WAVE` 这个变量），返回 `ok=True` + 空 `files`，无声无息。

修复：加 `W014` 警告。`ok` 仍为 `True`（毕竟用户没要求硬性保证），但必须进
**`diagnostics`** —— 第一版我把它埋进 `data["waveform_warnings"]`，调用方读的
是 `diagnostics` 字段，等于没说（我的测试自己抓到了这一点）。

`require_waveform=true` 仍会把它升级成 `E024` 硬错误。

## 自审计 A13

`A13_CONTRACT_PATH_WAVEFORM_GUARD`：走 `emit_testbench --spec-path` 端到端，
断言 wrapper 里有 `$dumpfile` / `$dumpvars` / `+WAVE` plusarg / `wave_path`
声明 / **无** `ifdef WAVE_DUMP`。**不依赖 Verilator**（纯产物文本断言），
裸容器里也能持续监视。

反向验证：把 `` `ifdef WAVE_DUMP `` 重新注入 → A13 立即 FAIL，13/13 → 12/13。

## 过程中两次踩回自己的坑

1. **f-string 反斜杠（缺陷 72 的检查器抓住了我）**：A13 的 detail 我写成
   `f"...{'$test$plusargs(\"WAVE\")' in _wsrc ...}"`，py3.10/3.11 不允许。
   regression 的 `no backslash inside f-string expressions` 断言当场 FAIL，
   ruff 也报 `invalid-syntax`。改成用变量承载布尔值 + `.format()`。

2. **局部 import 遮蔽**：A13 里写 `from .tb_emitter import tool_emit_testbench`，
   而同函数前面已赋值同名变量 → `UnboundLocalError`，audit 直接 `ok=False`。
   删掉局部 import（顶部已有）。

这两次都是**我自己的测试/检查器当场抓到的**，没等 CI。

## 回归测试

`test_contract_path_waveform_and_w014`，10 项断言（端到端走 emit_testbench，
不依赖 Verilator 就能覆盖缺陷 A/B）：

| 断言 | 抓什么 |
|---|---|
| `contract wrapper has $dumpfile` | 基本支持存在 |
| `dumpfile literal arg is a quoted string` | 缺陷 B（编译不过）|
| `contract wrapper gates dump on +WAVE plusarg` | 运行期开关 |
| `no dead \`ifdef WAVE_DUMP\`` | 缺陷 C（死宏）|
| `declares wave_path variable` | 未声明会编译失败 |
| `W014 emitted / surfaced in diagnostics` | 缺陷 D |

## 验证

```
本机（Verilator 5.054）
  tests/test_regression.py              ALL CHECKS PASSED（新增 10 项）
  tests/test_fusion_and_cross_repo.py   ALL FUSION CHECKS PASSED
  tests/test_eda_campaign.py            ALL EDA CAMPAIGN TESTS PASSED
  audit_harness                         13 / 13
  ruff check                            All checks passed!
  docs_check                            D1–D13 全过（40 个诊断码）
  build_docs_html.py --check            HTML 与 md 同步

真实契约路径端到端（Verilator 5.054 真编译 + 运行）
  emit --spec-path → wrapper 带 $dumpfile/$dumpvars/+WAVE
  verilator --binary --trace 编译 BUILD OK
  ./obj/sim +WAVE → wrap.vcd 145 信号（wrapper 自身 dumpvars）
  ./obj/sim       → wrap.vcd 不存在（+WAVE 是真开关）

真实 python:3.10-slim 容器
  tests/test_regression.py  ALL CHECKS PASSED
  audit_harness             13 / 13
```

## 文档

- `docs/FEATURES.md` — 诊断码总表补 `W014`
- HTML 由 markdown 重新生成（`--check` 同步）
