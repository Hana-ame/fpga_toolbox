# Changelog — `remote_work_dir` shell 注入面封堵

## 背景

`eda_campaign.generate_remote_run_script` 生成的脚本会被 `scp` 到远端执行，
参数若含 shell 元字符就能注入任意命令（changelog 21 已实测 `host='x"; touch
/tmp/PWNED; "'` 会真的执行 `touch`）。为此工具做了**三道白名单**：

- `host` → `_HOST_RE`（`[A-Za-z0-9_.@:\[\]-]`）
- `tag` → `_TAG_RE`（`[A-Za-z0-9_.-]`）
- `local_script_name` → 必须纯文件名 + `[A-Za-z0-9_.-]`

但 `remote_work_dir` 参数被**原文插进远端执行的脚本**里，却**没有任何校验**：

```
# use_dev_shm=False 路径
mkdir -p {remote_work_dir}/{tag} && cd {remote_work_dir}/{tag}
```

## 实测复现

```
generate_remote_run_script("h","t","run.sh",
    remote_work_dir='x; touch /tmp/PWNED_RWD; #', use_dev_shm=False)
```

生成的脚本里出现：

```bash
mkdir -p x; touch /tmp/PWNED_RWD; #/t && cd x; touch /tmp/PWNED_RWD; #/t
```

远端 `bash` 执行时 `;` 后的 `touch /tmp/PWNED_RWD` 会真的运行。这使 host/tag/
script 三道白名单**形同虚设**——攻击面从另一条参数绕进来。

## 修复

`fpga_toolbox/eda_campaign.py`：

- 新增 `_REMOTE_WORK_DIR_RE = ^[A-Za-z0-9_./~\-]+$`：允许目录分隔符与 `~`（合法
  的 `~/work`、`/tmp/eda` 等），但拒绝 `; & | ` ` $ ( )` 等 shell 元字符。
- `generate_remote_run_script` 对 `remote_work_dir` 走校验，非法输入抛
  `ValueError`（与其他三个参数一致，而不是生成带注入的脚本）。
- docstring 补登 `remote_work_dir` 也受校验。

## 为什么 dev_shm=True 也要校验

`use_dev_shm=True` 时脚本走 `mkdir -p /dev/shm/{tag}`，**不引用**
`remote_work_dir`。但校验仍生效——保持「无论哪条分支，参数都过白名单」的一致性，
避免将来有人改动分支逻辑时把未校验的参数接回去。

## 测试

`tests/test_full_coverage.py::test_remote_work_dir_injection_blocked`：

- 5 个注入向量（`;`、`$()`、反引号、`&&`、`|`）全部 `ValueError` 拒绝
- 3 个合法目录（`~/work`、`/tmp/eda`、`/home/user/proj_run`）原样出现在脚本里
- `use_dev_shm=True` 路径不受影响，且同样拒绝非法 `remote_work_dir`

## 验证

- `ruff check fpga_toolbox tests scripts` 0 错
- 4 个测试套件（regression / full_coverage / fusion / eda_campaign）全过
- `verify_invariants.py --fast` 5/5
