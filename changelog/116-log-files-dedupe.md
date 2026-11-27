# 193 · `log_files` 出现重复条目

## 现象

```python
run_sim(sim_script="/tmp/x/s.sh", log_dir="/tmp/x", ...)
→ {"log_files": [
      {"path": "/tmp/x/sim_...log", ...},
      {"path": "/tmp/x/sim_...log", ...}   # 完全相同的第二条
   ]}
```

同一个日志文件被记录两次。

## 根因

`run_sim` 的搜索目录列表是

```python
search_dirs = [script_p.parent.resolve()] + ([Path(log_dir).resolve()] if log_dir else [])
```

而 `_collect_logs` 逐个目录 `rglob("*")` 直接 append，**没有去重**。当
`log_dir` 与脚本目录相同（很常见：把日志和脚本放在一起），或一个目录是另一个
的父目录时，同一个文件被遍历多次、记录多次。

## 修复

按 **realpath** 去重（`str(p.resolve())`），既覆盖目录重叠，也覆盖「符号链接
或相对路径写法不同但指向同一文件」的情形：

```python
key = str(p.resolve())
if key in exclude or key in seen:
    continue
...
seen.add(key)
```

## 验证

| 场景 | 修复前 | 修复后 |
|---|---|---|
| `log_dir` == 脚本目录 | 同一文件 2 条 | 1 条 |
| 目录互相嵌套 | 重复 | 去重 |
| 真有两个不同日志文件 | 2 条 | 2 条（不误删） |

四套测试全绿；六层门禁 6/6，函数覆盖 **170/170**。

回归测试：`test_log_files_are_deduplicated`。
