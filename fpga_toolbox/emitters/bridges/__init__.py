"""桥接联合 TB 子包：每个组合一个文件 + 公共识别/声明 helper。

从原 ``emitters/bridge.py``（1,117 行，5 个模板堆一起）拆出。对外仍通过
``emitters.bridge`` 暴露 ``classify_bridge`` / ``emit_bridge`` / ``SUPPORTED_BRIDGES``。
"""

from __future__ import annotations

from typing import Any, Dict

from .common import SUPPORTED_BRIDGES, classify_bridge


def emit_bridge(
    mod: str,
    clk: str,
    rst: str,
    up_proto: str,
    down_proto: str,
    up_iface: Dict[str, Any],
    down_iface: Dict[str, Any],
) -> str:
    """按组合派发到对应桥接联合 TB 模板。

    未实现的组合抛出 ValueError（由编排层转成结构化错误 + 明确说明「识别到
    桥接结构但该组合暂无联合模板」），而不是静默退化成单侧 TB —— 那会让用户
    以为桥接被验证过，实际只驱动了一半。
    """
    if (up_proto, down_proto) == ("axi4", "apb"):
        from .axi4_to_apb import emit_bridge_testbench
        return emit_bridge_testbench(mod, clk, rst, up_iface, down_iface,
                                     up_proto, down_proto)
    if (up_proto, down_proto) == ("axi4", "axi4"):
        from .axi4_to_axi4 import _emit_axi4_to_axi4_bridge_tb
        return _emit_axi4_to_axi4_bridge_tb(mod, clk, rst, up_iface, down_iface)
    if (up_proto, down_proto) == ("spi", "apb"):
        from .spi_to_apb import _emit_spi_to_apb_bridge_tb
        return _emit_spi_to_apb_bridge_tb(mod, clk, rst, up_iface, down_iface)
    if (up_proto, down_proto) == ("ethernet", "apb"):
        from .ethernet_to_apb import _emit_ethernet_to_apb_bridge_tb
        return _emit_ethernet_to_apb_bridge_tb(mod, clk, rst, up_iface, down_iface)
    if (up_proto, down_proto) == ("axi4_stream", "apb"):
        from .axi4_stream_to_apb import _emit_axi4_stream_to_apb_bridge_tb
        return _emit_axi4_stream_to_apb_bridge_tb(mod, clk, rst, up_iface, down_iface)

    raise ValueError(
        f"Bridge combination {up_proto}->{down_proto} is recognised as a bridge DUT "
        f"but has no joint testbench template yet. Currently supported: "
        f"{sorted(SUPPORTED_BRIDGES)}. A single-sided testbench would only drive "
        f"one bus and leave the other tied off - it cannot verify the conversion, "
        f"so it is not emitted silently."
    )


__all__ = ["classify_bridge", "emit_bridge"]
