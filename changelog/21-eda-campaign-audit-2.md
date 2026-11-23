# Changelog — eda_campaign 审计（第二轮）：命令注入 + 两个「不完整数据判通过」

上一轮审了 `clean_netlist_for_pt`（3 个缺陷）。本轮审完剩余 5 个函数，发现 3 个新问题，
其中一个是**安全缺陷**。

## 缺陷 A（安全）— `generate_remote_run_script` 命令注入

该函数生成一个**会被 scp 到远端执行**的 shell 脚本，但把 `host` / `tag` /
`local_script_name` 直接拼进脚本文本：

```python
HOST="{host}"
```

实测 `host='x"; touch /tmp/PWNED_HOST; "'` 生成：

```bash
HOST="x"; touch /tmp/PWNED_HOST; ""
```

`bash -n` 语法检查**通过**，且实际执行会真的创建文件（已复现：`/tmp/PWNED_HOST`
被创建）。虽然不是 `eval`，但生成物就是要被执行的脚本，等同于在这些参数上
开了一个远程任意命令执行的口子。

修复：三个参数全部白名单校验，非法输入直接 `ValueError`：

| 参数 | 允许字符 |
|---|---|
| `host` | `[A-Za-z0-9_.@:\[\]-]`（user@host / IPv4 / `[IPv6]`）|
| `tag` | `[A-Za-z0-9_.-]`（用作 `/dev/shm/<tag>` 目录名）|
| `local_script_name` | 必须是纯文件名 + `[A-Za-z0-9_.-]`，拒绝路径与元字符 |

7 种注入尝试（`";`、`$()`、反引号、tag 注入、`../../etc/passwd`、`; rm -rf /`）
全部被拒；IPv6、`user@host.example.com`、下划线、连字符等合法边界仍正常。

## 缺陷 B — `aggregate_seed_medians` 传错层级会静默返回空

入参约定是 `parse_uvm_log(...)["data"]`，但误传整条 ToolResult
（`{ok, tool, data, diagnostics, error}`）时，旧实现静默得出：

```
n_passed=0, lat_avg_median=None
```

调用方看到的是「所有种子都失败」或「没有数据」，而不是「参数层级传错了」——
典型的静默失败。修复：探测 ToolResult 外层特征键（`data`+`ok`+`tool`）并抛
`ValueError`，消息直接给出正确写法 `[r['data'] for r in results]`。

## 缺陷 C — 配对基线用不完整数据判「逐位一致」

文档 §2.4/§8.1 要求用 **Leaf Cell Count 与总面积共同**证明可复现性。旧实现：

```python
exact_cells = run_a.get("leaf_cell_count") == run_b.get("leaf_cell_count")
exact_area  = run_a.get("total_cell_area") == run_b.get("total_cell_area")
ok = exact_cells and exact_area and run_a.get("leaf_cell_count") is not None
```

只要求 `leaf` 非 None。当 `total_cell_area` **双方都缺失**时，`None == None`
成立 → `bit_exact=True`。实测 `({'leaf_cell_count': 74265}, {'leaf_cell_count': 74265})`
被判为 bit-exact，而面积数据根本不存在。

修复：两个指标都必须**有值且相等**才算 bit-exact：

```python
exact_cells = ca is not None and cb is not None and ca == cb
exact_area  = aa is not None and ab is not None and aa == ab
```

## 附带修正

`parse_dc_pt_qor` 的失败分支原用 `res = err_result(...); res["data"] = data`
原地改返回字典，改为显式 `data=` 关键字参数（与上一轮 `parse_uvm_log`
的同类修法一致）。至此模块内不再有原地修改 ToolResult 的写法。

## 审计中确认正确的部分（无缺陷）

- `parse_dc_pt_qor` 对真实 K-2015.06 `report_qor` 的解析与文档基准数字完全吻合：
  `Leaf Cell Count 74265`、`Total Cell Area 50656.129`
- `timing_clean` 的 6 种边界（wns=0/-0.5/+0.5、viol=0/3、无 wns）判定全部正确
- `clean_netlist_for_pt` 的两处修复（上一轮）保持有效
- `aggregate_seed_medians` 用 `statistics.median`，偶数样本语义正确
- 生成的远端脚本 `bash -n` 语法通过

## 验证

新增 3 个测试（共 7 个）：`test_aggregate_rejects_toolresult_envelope` /
`test_paired_baseline_requires_both_metrics` /
`test_remote_script_rejects_shell_injection`（含 6 种注入 + 4 种合法边界）。
eda_campaign / regression / fusion / audit 9-9 / docs_check D1–D13 / ruff /
py3.10 全绿。
