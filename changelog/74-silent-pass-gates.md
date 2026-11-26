# Changelog — 四处静默假通过（UVM_FATAL / 零帧 / completed 绕过 / JUnit XML 结构）

review 第三类（静默误判）逐条复现，确认 4 组共 9 种绕过形态，全部修复。

## 1. `parse_uvm_log` 忽略 `UVM_FATAL`

```python
_UVM_ERR_RE = re.compile(r"UVM_ERROR\s*:\s+(" + _NUM + r")")
uvm_error = _last_int(_UVM_ERR_RE)
```

只读 `UVM_ERROR`。`UVM_FATAL` 是**并列且更严重**的失败计数（仿真会立即中止）。
实测一份日志：

```
delivered=64 ... UVM_ERROR :    0
UVM_FATAL :    3
```

`passed=True` —— 明确报告了 3 个致命错误的仿真被当成通过。

修复：新增 `_UVM_FATAL_RE`，`passed` 增加 `not (uvm_fatal or 0)`，data 里输出
`uvm_fatal`，错误消息带上它。

## 2. 零帧仿真被判通过（`0 == 0`）

```python
target_frames = expected_frames if expected_frames is not None else frames_obs
passed = ... and delivered == target_frames ...
```

未传 `expected_frames` 时 `target_frames = frames_obs`，于是 `delivered=0` +
`frames=0` 满足 `0 == 0` —— 一个**什么都没跑**的仿真通过。

修复：额外要求 `target_frames > 0` 且 `delivered > 0`。

## 3. `COCOTB_INCOMPLETE` 完整性门禁三种绕过

```python
if cocotb_completed is False:      # 只认 Python 的 False
```

结果文件是**序列化后再读回**的，实测三种形态都能绕过：

| 形态 | 修复前 | 修复后 |
|---|---|---|
| `"completed": "false"`（字符串）| PASS | FAIL + COCOTB_INCOMPLETE |
| `"completed": 0`（整数）| PASS | FAIL + COCOTB_INCOMPLETE |
| `{"tests": [...], "completed": false}` | PASS（`tests` 分支直接 `return ... None`，把 completed 丢了）| FAIL + COCOTB_INCOMPLETE |

修复：新增 `_norm_completed()` 归一（`False`/`"false"`/`0`/`"no"`/`"aborted"` →
False；真值 → True；缺失 → None），`tests` 分支也透传 `completed`，门禁改用
归一结果。显式 `True` 与字段缺省都不误伤。

## 4. JUnit XML 结构校验缺失（4 种）

`_from_xml` 只遍历 `<testcase>`，不校验根标签、不读 suite 级汇总：

| 输入 | 修复前 | 修复后 |
|---|---|---|
| 非 JUnit XML（`<foo><bar/></foo>`）| `ok=True` + `NO_RESULTS` | `ok=False`（明确报「不是 JUnit 报告」）|
| suite 级 `<errors><error/></errors>` | PASS | FAIL + `TESTSUITES_ERROR_NODE` |
| `<testsuite failures="3">`（只有属性）| PASS | FAIL + `SUITE_*` |
| `<testcase name="   "/>`（纯空白名）| PASS | FAIL + `UNNAMED_TESTCASE` |

修复：

- 校验根标签必须是 `testsuites` / `testsuite`，否则抛 `ValueError`；
- 读 `<testsuite failures="N" errors="M">` 与 suite 子树内的 `<error>` 节点；
- root 级的顶层 `<errors>` / `<failure>` 段落单独判 FAIL
  （第一版只查 suite 内部，反而漏了挂在 `<testsuites>` 下的那种，实测暴露后补上）；
- 无名检查从 `if not name` 改成 `if not (name or "").strip()` —— 纯空白 name 是
  truthy，旧判据放行。

> 修这处时我自己也走了一趟弯路：第一次只处理 suite 内部的 `<error>`，测出
> `suite-level error` 用例仍是 PASS —— 因为那个 fixture 把 `<errors>` 放在
> `<testsuites>` 下、`<testsuite>` 之外。**测试用例的构造位置决定了能否发现漏洞**，
> 和上一轮 `$` 换行的教训同类。

## 验证

不误伤对照：干净 UVM 日志仍 PASS、`UVM_ERROR=0` 且 `fid_bad=0` 正常；
`completed: true` 与字段缺省仍 PASS；干净 JUnit（有名字、无 failure）仍 PASS。

- 4 个测试套件全过（新增
  `test_silent_pass_gates_uvm_fatal_zero_frames_completed_xml`，覆盖 9 种绕过 +
  5 种不误伤）
- ruff 0 错 / `verify_invariants --full` **6 层全过** / docs HTML 同步
