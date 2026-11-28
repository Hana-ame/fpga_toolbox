"""多协议测试台生成器子包（按协议分文件 + 公共骨架）。

从原单文件 ``protocol_tb_emitter.py``（2,428 行）拆出。对外仍通过
``fpga_toolbox.protocol_tb_emitter`` 暴露全部公开符号，拆分对调用方透明。
"""

from __future__ import annotations

from .common import (
    EXPECTED_TB_DIR,
    REQUIRED_TB_PORTS,
    SUPPORTED_MULTI_PROTOCOLS,
)
from .orchestrator import (
    add_lint_off,
    add_waveform_dump,
    emit_protocol_testbench,
)

__all__ = [
    "EXPECTED_TB_DIR",
    "REQUIRED_TB_PORTS",
    "SUPPORTED_MULTI_PROTOCOLS",
    "add_lint_off",
    "add_waveform_dump",
    "emit_protocol_testbench",
]
