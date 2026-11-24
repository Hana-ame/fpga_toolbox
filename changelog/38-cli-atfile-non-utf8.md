# Changelog — CLI `@file.json` 非 UTF-8：堆栈直接冒泡给用户

## 背景

上一轮（36）的 changelog 把剩余无容错 `read_text` 标为"都处理工具自产产物、
安全"。这轮逐处核实 `cli.py:24` 时发现分类又错了：`@file.json` 走的是
**用户提供的文件**（`render_visual_report --report @x.json` 等 object/array
参数），不是工具自产产物。

## 缺陷 86 — `@file.json` 非 UTF-8：堆栈直接冒泡

实测（`render_visual_report` 用 `@bad.json`，文件含 latin-1 字节 `0xE9`）：

```
  File "fpga_toolbox/cli.py", line 96, in _extract_params
    params[name] = _parse_json_or_file(val)
  File "fpga_toolbox/cli.py", line 24, in _parse_json_or_file
    return json.loads(Path(raw[1:]).read_text(encoding="utf-8"))
UnicodeDecodeError: 'utf-8' codec can't decode byte 0xe9 in position 11: ...
```

CLI 是用户直接接触的入口。这是唯一一处把**完整 Python 堆栈**直接甩给用户的
路径——用户拿到的是 `UnicodeDecodeError` + 8 行 traceback，没有一句指向
"文件编码不对、请重新导出为 UTF-8"。

## 修复（两层）

1. `_parse_json_or_file`：捕获 `UnicodeDecodeError`，转成 `ValueError` 并在
   消息里写清"文件不是合法 UTF-8、请重新导出为 UTF-8"（`ValueError` 是
   `json.JSONDecodeError` 的父类，与既有 JSON 语法错误路径一致）。

2. `main()`：把 `_extract_params`（含 `@file` 读取/JSON 解析）包进
   `try/except ValueError`，输出**与工具信封同构**的结构化错误 + 退出码 1，
   而不是堆栈冒泡。

## 修复后

```
$ python3 -m fpga_toolbox render_visual_report --report @bad.json ... 
{
  "ok": false,
  "tool": "render_visual_report",
  "error": "Invalid argument: @bad.json is not valid UTF-8 ('utf-8' codec
           can't decode byte 0xe9 ...); re-export it as UTF-8 before passing
           it via @file."
}
退出码: 1
```

## 验证

```
tests/test_regression.py  ALL CHECKS PASSED（新增 3 项）
tests/test_fusion_and_cross_repo.py / test_eda_campaign.py  全绿
audit_harness                         13 / 13
ruff check                            All checks passed!
docs_check                            D1–D13 全过
```

新增 3 项 CLI 回归断言（`test_cli` 内，走真实子进程）：
- `@file non-UTF-8 exits 1 (not a traceback)`
- `@file non-UTF-8 error points at encoding`
- `@file non-UTF-8 error is structured (no traceback)`

## 用户提供的输入：编码路径已全部收口

| 用户输入 | 路径 | 状态 |
|---|---|---|
| RTL（`--rtl-file`） | `sv_parser._read_rtl` | 83-A 已修 |
| RTL（变异目标） | `mutation_runner` 按字节读写 | 85-B 已修 |
| 契约 YAML | `load_raw_spec` | 84 已修 |
| 变异 spec | `load_mutations` | 85-A 已修 |
| `@file.json`（CLI object/array 参数） | `_parse_json_or_file` + `main` | 86 本轮已修 |

剩余无容错 `read_text` 只处理**工具自己写出的产物**（必为 UTF-8）。

## 过程踩坑

新增 CLI 断言时，`render_visual_report` 的 `--dep-graph` / `--extracted-interfaces`
都是必填，我漏传导致 rc=2（argparse 用法错误），改了 2 次才补齐——这与被测
缺陷无关，是我的测试写错。
