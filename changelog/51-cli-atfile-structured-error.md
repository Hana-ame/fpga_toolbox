# Changelog — CLI `@file` 路径错误归一成结构化错误（防堆栈冒泡）

## 背景

CLI 的 object/array 参数支持 `@path.json` 从文件读取 JSON。`main()` 已经把
`_extract_params` 包在 `try/except ValueError` 里，让「非法 UTF-8」「JSON 语法错」
这类用户输入错误输出与工具信封同构的结构化错误 + 退出码 1，而不是堆栈冒泡。

但 `@file` 指向的路径**不存在**时，`Path(path).read_text()` 抛的是
`FileNotFoundError`——它是 `OSError` 的子类，**不是** `ValueError`。于是
`except ValueError` 接不住，Python 堆栈直接冒泡给用户。

## 实测复现

```
python3 -m fpga_toolbox normalize_sim_result --module-name m \
    --coverage-summary @/no/such.json
```

修复前：

```
Traceback (most recent call last):
  ...
FileNotFoundError: [Errno 2] No such file or directory: '/no/such.json'
```

这类「路径打错 / 文件被删 / 相对路径不对」与「非法 UTF-8」「JSON 语法错」是
**同一类用户输入错误**，理应得到同构的可读错误，而不是一个裸堆栈。

## 修复

`fpga_toolbox/cli.py` 的 `_parse_json_or_file`：在既有 `except UnicodeDecodeError`
之外，再加 `except OSError` 分支，把「读不到文件」也归一成 `ValueError`（带可读
的 `check the path exists and is a readable file` 提示），让 `main()` 的
`except ValueError` 能接住。

## 测试

`tests/test_full_coverage.py::test_cli_atfile_errors_structured`：

- object 参数 `@missing`（`normalize_sim_result --coverage-summary`）→ 返回 1，无堆栈
- 行内非法 JSON → 返回 1，无堆栈
- array 参数单个 `@missing`（`clean_pt_netlist --blackbox-modules`，提供真实必填
  文件避免 argparse 先 `SystemExit`）→ 返回 1，无堆栈

## 验证

- `ruff check fpga_toolbox tests scripts` 0 错
- 4 个测试套件（regression / full_coverage / fusion / eda_campaign）全过
