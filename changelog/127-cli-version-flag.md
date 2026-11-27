# 127 · CLI 补全 `--version` / `-V` 标准版本查询参数

## 现象

`python3 -m fpga_toolbox --version` 与 `fpga-toolbox -V` 此前报错 `unrecognized arguments`，用户和脚本无法通过标准 CLI 选项快速确认已安装的 `fpga_toolbox` 版本。

## 改进

1. **`fpga_toolbox/cli.py`**：在主 `ArgumentParser` 中注册 `-V` / `--version`（`action="version"`, `version=f"%(prog)s {__version__}"`），复用包顶层 `__version__`。
2. **`tests/test_full_coverage.py`**：新增 `test_cli_version_flag()`，校验 `--version` 与 `-V` 均以退出码 `0` 输出包含当前版本号的版本字符串。

## 验证

- `python3 -m fpga_toolbox --version` → `fpga-toolbox 0.5.0`（exit 0）。
- `python3 tests/test_full_coverage.py`：`test_cli_version_flag` 通过。
