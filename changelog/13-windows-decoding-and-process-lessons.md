# Changelog — Windows 解码崩溃与 CI 空跑事故

本轮修了一个只有 Windows 才暴露的真实缺陷，并顺带纠正了三个我自己的过程错误。记录在此，因为过程错误比代码缺陷更值得留档。

## 一、真实缺陷：Windows 上外部工具输出解码崩溃

### 症状

CI 的 `test (windows-latest, py3.13)` job 失败，Linux 全绿：

```
File "...\subprocess.py", line 1616, in _readerthread
File "...\encodings\cp1252.py", line 23, in decode
UnicodeDecodeError: 'charmap' codec can't decode byte 0x8f in position 7
...
File "...\core\sv_parser.py", line 449, in _run_slang
AttributeError: 'NoneType' object has no attribute 'strip'
```

### 根因

`subprocess.run(..., capture_output=True, text=True)` 在未指定 `encoding` 时用 **locale 编码**解码输出。Linux 上是 UTF-8，Windows 上是 cp1252。slang 的诊断文本含非 ASCII 字节（源码里的中文注释、UTF-8 标点），cp1252 无法解码 → 抛 `UnicodeDecodeError`。

更糟的是异常链：`_run_slang` 捕获异常后 `proc` 未完成构造，`proc.stderr` 为 `None`，下一行 `proc.stderr.strip()` 又抛 `AttributeError`，把「编码问题」伪装成「空指针问题」，整个 AST 解析被打断。

修法是两件事：

```python
proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False,
                      encoding="utf-8", errors="replace")
...
err_text = (proc.stderr or "")
out_text = (proc.stdout or "")
```

- `encoding="utf-8"`：不依赖平台 locale；
- `errors="replace"`：任何字节都能解出文本，不让编码问题吞掉解析结果；
- `(x or "")`：把 `None` 兜底，避免退化成 `AttributeError`。

同一处理应用到三处：`core/sv_parser.py::_run_slang`、`core/sv_parser.py::slang_instantiation_targets`、`sim_runner.py`（Verilator / `sim.sh` 日志同样可能含非 ASCII）。

### 为什么 Linux 永远发现不了

Linux 的 locale 编码恰好就是 UTF-8，`text=True` 与 `encoding="utf-8"` 等价。**这个缺陷只存在于 Windows 或非 UTF-8 locale 的机器上**——本机自测 100% 通过。只有 CI 的 Windows runner 能暴露它，这也是必须看 CI 而不能只看本地测试的直接证据。

## 二、过程错误（三个，均真实发生）

### 1. `git stash` 导致代码漏提交，CI 空跑出绿灯

我先 `git add -A`，随后为了对比修复前后的性能执行了 `git stash` / `git stash pop`。`stash pop` 把改动恢复成**未暂存**状态，而 commit 在 stash 之前就执行了——结果 `90a9e1e` **只提交了 changelog 文件**，代码一行没进去：

```
90a9e1e fix(index_repo): 未知例化不再静默丢弃...
 changelog/unknown-instantiation-w102.md | 60 ++++++++++++
 1 file changed, 60 insertions(+)
```

而那一轮 CI 报了 **success**——因为在没有任何代码变更的树上跑，绿得毫无意义。这与本仓库 README 批判的「静默通过」是同一类错误，只是发生在开发流程里。

**教训**：`stash` 之后必须重新 `git add`；提交前用 `git show --stat HEAD` 确认改动真的进了 commit，而不是只看 `git push` 成功。

### 2. 在错误的假设上写测试

修完编码后我写了个断言「不显式 encoding 时 `text=True` 会抛 `UnicodeDecodeError`」，它在 Windows 上失败：Windows 的解码发生在 `subprocess._readerthread` 里，异常不传播到主线程，`subprocess.run` 正常返回。跨平台行为不一致，断言它本身就是脆的。

改为只断言修复真正依赖的语义——显式 `encoding + errors="replace"` 时必定拿到 `str`（含 U+FFFD），跨平台一致。

### 3. 测试依赖了 CI 不存在的环境

W102 依赖 slang CST，而 CI 不装 slang，4 个 test job 全失败。两个方向都做了：

- 测试探测 `shutil.which("slang")`，缺失时**显式打印 skip** 并跳过依赖 CST 的断言（不能删断言——那是掩盖问题）；
- `ci.yml` 增加可选安装 slang（官方 v12.2 二进制，`continue-on-error: true`），让该特性在 CI 真正被验证。下载失败则按设计降级。

## 三、最终验证（全部来自 GitHub CI，非本地推断）

CI run `45119207334` on `8da79f6`：**6/6 job success**，含 `test (windows-latest, py3.13)`。

从 Windows job 日志确认真实执行而非跳过：

```
ok   unknown instantiation reported
ok   W102 emitted for unknown
ok   resolve_deps emits W102
ok   no false-positive W102 for func/interface
ok   explicit utf-8 + replace returns text
ok   replacement chars present
```

## 四、遗留观察

`eda_env.py` / `mutation_runner.py` 里的 `subprocess.run(text=True)` 仍在用平台 locale 解码。它们当前处理的输出（版本号、本地测试结果）在 Windows 上是纯 ASCII，CI 一直绿，所以未动。若将来这些路径会接触含非 ASCII 的外部输出，应按同一方式补 `encoding="utf-8", errors="replace"`。
