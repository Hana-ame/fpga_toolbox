# 重构 · 桥接子包再拆分（`bridge.py` 1,117 → 最大 271 行）

## 背景

上一轮把 `protocol_tb_emitter.py`（2,428 行）拆成 `emitters/` 子包后，
`emitters/bridge.py` 成了新的最大文件（1,117 行）——5 个桥接模板堆在一起。
同样的"按职责分文件"信号再次出现：5 个组合彼此独立，只共享 `classify_bridge`
与端口声明 helper。

## 拆分

`emitters/bridge.py` 保留为兼容门面，实际内容移到 `emitters/bridges/` 子包：

| 文件 | 行数 | 内容 |
|---|---|---|
| `common.py` | 88 | `classify_bridge`（按 role 组合识别桥接）+ 端口声明 helper + `SUPPORTED_BRIDGES` 表 |
| `axi4_to_apb.py` | 238 | AXI4 → APB 联合 TB |
| `axi4_to_axi4.py` | 271 | AXI4 → AXI4 联合 TB |
| `spi_to_apb.py` | 169 | SPI → APB 联合 TB |
| `ethernet_to_apb.py` | 171 | Ethernet → APB 联合 TB |
| `axi4_stream_to_apb.py` | 173 | AXI4-Stream → APB 联合 TB |
| `__init__.py` | 55 | 派发器 `emit_bridge` |

最大文件从 1,117 降到 271 行。

## 首次拆分踩到的真实缺陷

**`SUPPORTED_BRIDGES` 循环导入**：该常量原放在 `bridges/__init__.py`，而
`classify_bridge`（在 `common.py`）要查它来判定组合是否支持。`__init__` 导入
`common` 时 `SUPPORTED_BRIDGES` 尚未定义 → `NameError: name
'SUPPORTED_BRIDGES' is not defined`。修复：把常量移到 `common.py`（它是被
`__init__` 导入的一方）。

另一个坑（上轮已踩过、本轮复现）：门面的每个导入都必须列进 `__all__`，否则
ruff 把"导入了但本文件没用"的兼容导出当成 F401 删掉。

## 验证

| 项目 | 结果 |
|---|---|
| 5 个桥接组合 | 全部仍能生成 TB（axi4→apb、axi4→axi4 用 examples 真 DUT 验证） |
| 六层门禁（完整 EDA） | **6/6**，Layer 4 `217/217` 函数 / 45 模块（+7 新模块） |
| ruff | 0 errors |
| 测试 | `tests/test_bridge_coverage.py` 8 用例全通过 |
