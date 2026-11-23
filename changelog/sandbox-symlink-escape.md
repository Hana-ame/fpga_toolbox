# Changelog — copy_sandbox 符号链接逃逸（隔离泄漏）

### Fixed

- **`copy_sandbox` 跟随符号链接，把项目根之外的文件内容拷进沙箱**（`sandbox_copier.py`）。

  工具的声明是「把解析出的 RTL/头文件拷进隔离沙箱」，安全契约写的是相对布局保留、沙箱不可为项目根等。它对路径的唯一校验是字符串层面的：

  ```python
  if rel_path.is_absolute() or ".." in rel_path.parts:
      return err_result(...)
  src = root / rel_path
  if not src.is_file():          # ← 跟随符号链接
      ...
  shutil.copy2(src, dst)         # ← 也跟随符号链接
  ```

  `is_file()` 与 `shutil.copy2()` 都会跟随符号链接，因此**项目内的一个符号链接就能把项目外的任意文件读进沙箱**，并且被算进 sha256、计入 `file_count`。两条实际逃逸路径均已实测确认：

  | 形态 | 例子 | 修复前 | 修复后 |
  |---|---|---|---|
  | 文件符号链接 | `rtl/link.sv -> /outside/creds.txt` | ok=True，密文进入沙箱 | **拒绝**，无泄漏 |
  | 目录符号链接 | `rtl/extdir -> /outside`，取 `rtl/extdir/creds.txt` | ok=True，密文进入沙箱 | **拒绝**，无泄漏 |

  这是「声称隔离却泄漏」——比功能错误更值得修：沙箱的存在意义就是不把项目外的东西带进来，而旧实现只要项目里有一个符号链接（很常见：共享头文件目录、monorepo 软链）就会破坏这个契约。

### 修复

在字符串校验之后补一次真实路径校验（`resolve(strict=True)` 会跟随所有符号链接），确认解析结果仍落在 `project_root` 之内：

```python
real_src = src.resolve(strict=True)
if not _is_within(real_src, root):
    return err_result("copy_sandbox",
        f"Refusing to stage '{rel}': it resolves outside the project root ...")
```

关键取舍：**项目内部的相对符号链接仍须可用**（如 `x/alias.sv -> ../y/real.sv`），它解析后仍在 root 内，照常放行。三种情形均已实测：

- 文件符号链接逃逸 → 拒绝，错误信息给出解析后的真实路径，便于排查；
- 目录符号链接逃逸 → 拒绝，无泄漏；
- 项目内部符号链接 → 仍 `ok=True`，sha256 正常；
- 普通文件 → 行为不变。

顺带确认 `_clear_dir` 对沙箱内的目录符号链接用 `entry.unlink()`（只删链接、不递归进目标），因此清理阶段不会误删项目外目录——已实测项目外目录在其后完好。

### Tests

新增 `tests/test_regression.py::test_sandbox_symlink_escape`（5 项检查）：文件符号链接逃逸被拒 + 无泄漏、目录符号链接逃逸被拒 + 无泄漏、内部符号链接仍允许。

### 验证

- `tests/test_regression.py` ALL CHECKS PASSED；`tests/test_fusion_and_cross_repo.py` ALL FUSION CHECKS PASSED
- `audit_harness` ok、`docs_check` D1–D13 全通过
- `ruff check fpga_toolbox tests`（CI 同款命令）`All checks passed!`；Python 3.10 语法门禁通过
