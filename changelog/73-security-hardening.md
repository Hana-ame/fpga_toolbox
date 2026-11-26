# Changelog — 命令注入绕道、/tmp verilator 劫持、审计报告存储型 XSS

review 第二类（安全漏洞）逐条复现后确认 4 项，全部修复。

## 1. `$` 白名单可被末尾换行绕过（命令注入）

`generate_remote_run_script` 的四个白名单正则用 `$` 结尾：

```python
_HOST_RE = re.compile(r"^[A-Za-z0-9_.@:\[\]-]+$")
_TAG_RE  = re.compile(r"^[A-Za-z0-9_.-]+$")
```

Python 的 `$` 也匹配「末尾换行符**之前**」，所以 `host='host1\n'` 能通过校验。
生成的脚本是：

```bash
HOST="host1
"
```

换行把字符串字面量从中间截断，后续内容成了独立的 shell 命令 —— 配合
`host='host1\nrm -rf ~\n'` 就是可用的命令注入。我最初用「带空格和内容的换行」
测过一次，被字符类挡下，差点错过；**纯末尾换行**才是真正的入口。

修复：四个正则全部改成 `\Z`（`_HOST_RE` / `_TAG_RE` / `_REMOTE_WORK_DIR_RE` /
`local_script_name` 的 `re.match`）。

## 2. `remote_work_dir` 只查字符不查路径语义

白名单允许 `.` 和 `/`，于是：

| 输入 | 生成 | 后果 |
|---|---|---|
| `'/'` | `WORK="//t"` | 脚本末尾 `rm -rf $WORK` → 删 `/t` |
| `'~/work/..'` | `WORK="~/work/../t"` | 删主目录下的 `t` |
| `'/dev/shm/..'` | `WORK="/dev/shm/../t"` | 删 `/t` |

生成脚本的最后一行是 `$SSH_CMD "bash -c 'rm -rf $WORK'"` —— 「清理临时目录」
会变成删除预期范围之外的目录。

修复：显式拒绝含 `..` 段的路径，以及根路径 / `~` / 空（`/`、`//`、`~`）。
合法的 `~/work`、`/tmp/foo`、`~/a/b` 仍正常通过。

## 3. `/tmp/verilator` 排在 PATH 之前 —— 多用户机器上可被劫持

```python
candidates.append(str(home / ".local" / "verilator" / "bin" / "verilator"))
candidates.append("/tmp/verilator/bin/verilator")     # <- 在 which 之前
which_v = shutil.which("verilator")
```

`/tmp` 权限是 `drwxrwxrwt`（1777，任何用户可创建目录）。在共享 EDA 服务器上，
他人预置 `/tmp/verilator/bin/verilator` 就能劫持本工具实际调用的仿真器
（本工具会用它编译/运行用户 RTL）。

修复：`shutil.which()` 提前；`/tmp` 候选降为**最后兜底**，且必须通过
`_is_owned_by_me()`（解析符号链接后仍在 `/tmp` 下、且文件属主是当前用户；
Windows 无 `getuid` 时跳过该限制）。实测：即使我在 `/tmp/verilator/bin/verilator`
放一个打印 `Verilator 9.999 HIJACKED` 的脚本，`find_verilator()` 仍返回
`~/.local/verilator/bin/verilator`（5.054）。

## 4. 审计报告存储型 XSS + 非数字 mutation_score 崩溃

`extra_meta`（由 `coverage_summary.rtl_pct` / `mutation_summary` 拼成）被
**未转义**插入 HTML：

```html
<div class="meta">... rtl_cov=<img src=x onerror=alert(1)>% ...</div>
```

report 来自外部/AI 生成的 JSON，这就是存储型 XSS。修复：每个 badge 先
`html.escape` 再拼接。全字段扫描（module / overall_status / generated_at /
checks 全部字段 / capability_matrix / raw_log / dep_graph）确认 0 泄漏。

顺带修一个崩溃：`mutation_score` 为非数字（`"n/a"`、`{"x":1}`、`[1]`）时，
`round(100.0 * float(mut.get("killed",0)) / max(1, mut.get("total",1)), 1)` 抛
`TypeError: can't multiply sequence by non-int of type 'float'`，整个报告渲染失败
—— 而这是**渲染器**，本应对任意输入都能出图。改为 `_num_or_none()` 安全转换，
六个畸形取值全部 `ok=True`。

## 验证

- 4 个测试套件全过（新增
  `test_security_hardening_injection_xss_verilator_hijack`）
- ruff 0 错 / `verify_invariants --full` **6 层全过** / docs HTML 同步
- 回归断言覆盖：4 种末尾换行注入被拒、6 种危险 `remote_work_dir` 被拒且 4 种
  合法值仍可用、`/tmp` 劫持不被选中、全字段 XSS 0 泄漏、6 种非数字
  `mutation_score` 不崩
