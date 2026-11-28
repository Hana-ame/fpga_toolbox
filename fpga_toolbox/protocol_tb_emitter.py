"""多协议 SystemVerilog 测试台 & Verilator 运行脚本生成器（兼容门面）。

本模块原是一个 2,428 行的单文件（全仓最大）。11 个协议模板彼此独立、只共享
一组常量与端口解析 helper，因此已拆分到 :mod:`fpga_toolbox.emitters` 子包：

    常见常量/helper   -> emitters/common.py
    各协议模板        -> emitters/{apb,axi4,ahb_lite,axi4_stream,ddr,hbm,pcie,spi,ethernet}.py
    编排与运行脚本    -> emitters/orchestrator.py

**本文件保留为兼容门面**：全部公开符号与既有私有 helper 在此重新导出，因此
``from fpga_toolbox.protocol_tb_emitter import emit_protocol_testbench``、
``SUPPORTED_MULTI_PROTOCOLS``、以及测试与 audit_harness 使用的
``_emit_run_script`` / ``_emit_*_tb`` 都无需改动。新代码可直接 import 子包。

注意：这里的每个导入都必须列进 ``__all__``。否则 ruff 会把"导入了但本文件
没用"的兼容导出当成 F401 死代码删掉，导致 `cannot import name ...` —— 实测
在拆分后用 `ruff --fix` 就发生过一次。
"""

from __future__ import annotations

from .emitters import (
    EXPECTED_TB_DIR,
    REQUIRED_TB_PORTS,
    SUPPORTED_MULTI_PROTOCOLS,
    add_lint_off,
    add_waveform_dump,
    emit_protocol_testbench,
)
from .emitters.ahb_lite import _emit_ahb_tb
from .emitters.apb import _emit_apb_tb
from .emitters.axi4 import _emit_axi4_tb
from .emitters.axi4_stream import _emit_axi4_stream_tb
from .emitters.common import (
    _missing_tb_ports,
    _pm,
    _pw,
    _split_pm,
    _wrong_dir_ports,
)
from .emitters.ddr import _emit_ddr3_tb, _emit_ddr4_tb, _emit_ddr5_tb
from .emitters.ethernet import _emit_ethernet_tb
from .emitters.hbm import _emit_hbm_tb
from .emitters.orchestrator import _emit_run_script
from .emitters.pcie import _emit_pcie_tb, _pcie_dw_for_lanes
from .emitters.spi import _emit_spi_tb

__all__ = [
    # 公开 API
    "EXPECTED_TB_DIR",
    "REQUIRED_TB_PORTS",
    "SUPPORTED_MULTI_PROTOCOLS",
    "add_lint_off",
    "add_waveform_dump",
    "emit_protocol_testbench",
    # 兼容导出：测试与 audit_harness 直接使用这些私有 helper
    "_missing_tb_ports",
    "_pm",
    "_pw",
    "_split_pm",
    "_wrong_dir_ports",
    "_emit_run_script",
    "_emit_apb_tb",
    "_emit_axi4_tb",
    "_emit_ahb_tb",
    "_emit_axi4_stream_tb",
    "_emit_ddr3_tb",
    "_emit_ddr4_tb",
    "_emit_ddr5_tb",
    "_emit_hbm_tb",
    "_emit_pcie_tb",
    "_pcie_dw_for_lanes",
    "_emit_spi_tb",
    "_emit_ethernet_tb",
]
