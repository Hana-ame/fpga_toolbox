# 缺陷修复 · `copy_sandbox` 失败时留下半成品沙箱（非原子）

## 现象

```python
copy_sandbox(
    project_root="/proj",
    relative_files=["good.sv", "evil.sv"],   # evil.sv -> /etc/passwd
    sandbox_dir="/sb",
)
→ ok=False
   err="Refusing to stage 'evil.sv': it resolves outside the project root ..."

# 但沙箱里已经有内容：
/sb/good.sv
/sb/.fpga_sandbox
```

调用方看到 `ok=False` 不会想到目标目录里已经有文件；而 `.fpga_sandbox`
标记存在还会让**下一次**调用可以无提示地清空它（标记即"可安全清理"）。
如果 next run 的 `relative_files` 不同，残留文件会混进新沙箱、被一起编译。

同源问题：已有沙箱里放着用户文件时，失败路径也会先把它们 `_clear_dir` 清掉
—— `ok=False` 但内容已经没了。

## 根因

旧实现是「边校验边写」：

```
mkdir(dst)          ← 先创建
写 .fpga_sandbox    ← 先打标记
for rel in relative_files:
    校验路径 / 存在性 / 符号链接逃逸   ← 中途失败会 return
    shutil.copy2(...)                  ← 但前面几项已写盘
```

`_clear_dir(dst_root)` 也在校验之前执行。

## 修复

改为**两阶段**：

1. **阶段 1（纯只读预校验）**：把所有校验前置，不碰文件系统
   - 相对路径可移植性（绝对路径 / `..`）
   - 源文件存在性，区分「不存在」与「是目录」
   - 符号链接逃逸（`resolve()` 后必须仍在 project root 内）
   - 重复项去重
   - 「是否允许清理已有沙箱」的判定（只判定，不执行）
   产出 `plan: [(relative_key, src, dst), ...]`。

2. **阶段 2（写盘）**：预校验全部通过后才
   - 需要时 `_clear_dir`
   - `mkdir` + 写 `.fpga_sandbox`
   - 按 plan 逐个 `copy2`

失败时目标目录**完全不被触碰**：原本不存在则仍不存在；原本有内容则原封不动。

## 验证

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 预校验失败（符号链接逃逸），目标目录不存在 | 留下 `good.sv` + `.fpga_sandbox` | 目录**不存在** |
| 预校验失败，目标已有沙箱（含 `keep.txt`） | `keep.txt` 已被 `_clear_dir` 删除 | `keep.txt` 保留 |
| 项目源目录 | 不受影响 | 不受影响 |
| 成功路径（含同名不同目录文件） | 正常 | 正常（`file_count` 一致） |
| 重复调用 + `clean=True` | 正常清理重建 | 正常清理重建 |

四套测试全绿；六层门禁 6/6，函数覆盖 **170/170**。

回归测试：`test_copy_sandbox_failure_leaves_no_partial_output`。
