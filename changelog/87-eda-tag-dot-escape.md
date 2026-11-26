# 145 · `eda_campaign` 的 tag='..' 让「清理临时目录」变成删除 `/dev`

## 现象

`generate_remote_run_script(host, tag, script, remote_work_dir)` 把 `tag` 白名单
校验成 `^[A-Za-z0-9_.-]+$`，`.` 是允许字符，于是 `tag=".."` 与 `tag="."` 都
通过。生成脚本：

```bash
TAG=".."
WORK="/dev/shm/.."
mkdir -p /dev/shm/.. && cd /dev/shm/..
...
  $SSH_CMD "bash -c 'rm -rf $WORK'"
```

- `tag=".."` → `rm -rf /dev/shm/..` 等于 `rm -rf /dev`
- `tag="."` → `rm -rf /dev/shm/.` 等于清空 `/dev/shm`

这是**远端**主机上的破坏性删除，且脚本是工具「生成给用户照着跑」的产物。

## 根因

与缺陷 127 的 `remote_work_dir='/'` 同一类：**字符白名单挡不住路径语义**。
当时只给 `remote_work_dir` 加了 `..`/根路径检查，**忘了 `tag` 也参与路径拼接**。
`tag` 的白名单注释只提到「用作 /dev/shm 目录名」，没考虑「目录名可以是 `.`/`..`」。

## 修复

`tag` 字符校验之后再校验语义：

```python
if tag in (".", "..") or ".." in tag.split("."):
    raise ValueError(...)
```

`a..b` / `...` / `.hidden` 这类在文件系统里是**普通目录名**、不产生越界语义，
仍然放行（实测生成 `WORK="/dev/shm/a..b"`、`"/dev/shm/..."`，都是安全路径）。

## 验证

| tag | 修复前 | 修复后 |
|---|---|---|
| `..` | 生成 `rm -rf /dev/shm/..` | `ValueError` |
| `.` | 生成 `rm -rf /dev/shm/.` | `ValueError` |
| `../..` | 生成越界路径 | `ValueError` |
| `a..b` / `...` / `.hidden` | 可用 | 仍可用（普通目录名） |
| `ver1` / `v1_2-3` / `a.b` | 可用 | 仍可用 |

其他参数的原防护未受影响：host 换行注入、script 带路径、workdir 为根/带 `..`
全部仍被拒绝。

回归测试：`test_eda_remote_tag_cannot_escape_staging_dir`。
