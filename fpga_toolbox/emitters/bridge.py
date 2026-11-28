"""桥接联合 TB（兼容门面）。

本模块原是一个 1,117 行的单文件（5 个桥接模板堆一起）。已拆分到
:mod:`fpga_toolbox.emitters.bridges` 子包：

    桥接识别 + 端口声明 helper -> bridges/common.py
    各组合模板               -> bridges/{axi4_to_apb,axi4_to_axi4,spi_to_apb,
                                          ethernet_to_apb,axi4_stream_to_apb}.py
    派发器 emit_bridge        -> bridges/__init__.py

**本文件保留为兼容门面**：``classify_bridge`` / ``emit_bridge`` /
``SUPPORTED_BRIDGES`` 在此重新导出，既有 import 无需改动。

注意：每个导入都必须列进 ``__all__``，否则 ruff 会把"导入了但本文件没用"的
兼容导出当成 F401 死代码删掉（实测踩过）。
"""

from __future__ import annotations

from .bridges import (
    SUPPORTED_BRIDGES,
    classify_bridge,
    emit_bridge,
)
from .bridges.axi4_stream_to_apb import _emit_axi4_stream_to_apb_bridge_tb
from .bridges.axi4_to_apb import emit_bridge_testbench
from .bridges.axi4_to_axi4 import _emit_axi4_to_axi4_bridge_tb
from .bridges.common import _apb_model_decls, _axi4_port_decls
from .bridges.ethernet_to_apb import _emit_ethernet_to_apb_bridge_tb
from .bridges.spi_to_apb import _emit_spi_to_apb_bridge_tb

__all__ = [
    "SUPPORTED_BRIDGES",
    "classify_bridge",
    "emit_bridge",
    "emit_bridge_testbench",
    "_emit_axi4_to_axi4_bridge_tb",
    "_emit_axi4_stream_to_apb_bridge_tb",
    "_emit_spi_to_apb_bridge_tb",
    "_emit_ethernet_to_apb_bridge_tb",
    "_apb_model_decls",
    "_axi4_port_decls",
]
