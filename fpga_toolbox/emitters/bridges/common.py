"""桥接联合 TB 的公共 helper：桥接结构识别 + 端口声明构造。

从原 ``emitters/bridge.py``（1,117 行，5 个模板堆一起）拆出。
桥接组合的实现分到同目录的各 ``bridge_*.py``；本文件只放**所有组合共用**的
识别与声明逻辑。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

#: 已实现联合 TB 的桥接组合： (上游协议, 下游协议) -> 说明
SUPPORTED_BRIDGES: Dict[Tuple[str, str], str] = {
    ("axi4", "apb"): "AXI4-Full 上游 → APB 下游（位宽转换桥）",
    ("axi4", "axi4"): "AXI4 上游 → AXI4 下游（位宽转换 / 重排）",
    ("spi", "apb"): "SPI 上游 → APB 下游（串行命令转寄存器访问）",
    ("ethernet", "apb"): "Ethernet 上游 → APB 下游",
    ("axi4_stream", "apb"): "AXI4-Stream 上游 → APB 下游",
}


def classify_bridge(ifaces: List[Dict[str, Any]]) -> Optional[Tuple[str, str, Dict[str, Any], Dict[str, Any]]]:
    """从接口列表里认出桥接结构。

    判定依据是 **role 组合**，不是端口命名：桥接 DUT 必然有一个 `slave` 侧
    （面向上游主设备）和一个 `master` 侧（面向下游从设备）。role 由分类器按
    信号方向推断，比端口名前缀可靠（`s_axi_*` / `m_apb_*` 只是惯例）。

    返回 ``(upstream_proto, downstream_proto, upstream_iface, downstream_iface)``；
    不是桥接结构（或组合未支持）返回 None。
    """
    slaves = [i for i in ifaces if str(i.get("role")) == "slave"]
    masters = [i for i in ifaces if str(i.get("role")) == "master"]
    if not slaves or not masters:
        return None
    # 同协议双向（如 axi4 slave + axi4 master）是位宽转换桥，也算桥接
    for up in slaves:
        for down in masters:
            combo = (str(up.get("protocol")), str(down.get("protocol")))
            if combo in SUPPORTED_BRIDGES:
                return combo[0], combo[1], up, down
    return None


def _axi4_port_decls(p: Dict[str, str], widths: Dict[str, int]) -> str:
    """AXI4 侧 TB 网线声明（TB 作主机，ready 类信号是输入）。"""
    return f"""  logic [{widths['id']-1}:0] {p['awid']} = 0;
  logic [{widths['aw']-1}:0] {p['awaddr']} = 0;
  logic [{widths['len']-1}:0] {p['awlen']} = 0;
  logic [{widths['size']-1}:0] {p['awsize']} = 0;
  logic [{widths['burst']-1}:0] {p['awburst']} = 0;
  logic {p['awvalid']} = 0;
  logic {p['awready']};
  logic [{widths['dw']-1}:0] {p['wdata']} = 0;
  logic [{widths['sw']-1}:0] {p['wstrb']} = 0;
  logic {p['wlast']} = 0;
  logic {p['wvalid']} = 0;
  logic {p['wready']};
  logic [{widths['id']-1}:0] {p['bid']};
  logic [{widths['resp']-1}:0] {p['bresp']};
  logic {p['bvalid']};
  logic {p['bready']} = 1;
  logic [{widths['id']-1}:0] {p['arid']} = 0;
  logic [{widths['aw']-1}:0] {p['araddr']} = 0;
  logic [{widths['len']-1}:0] {p['arlen']} = 0;
  logic [{widths['size']-1}:0] {p['arsize']} = 0;
  logic [{widths['burst']-1}:0] {p['arburst']} = 0;
  logic {p['arvalid']} = 0;
  logic {p['arready']};
  logic [{widths['id']-1}:0] {p['rid']};
  logic [{widths['dw']-1}:0] {p['rdata']};
  logic [{widths['resp']-1}:0] {p['rresp']};
  logic {p['rlast']};
  logic {p['rvalid']};
  logic {p['rready']} = 1;"""


def _apb_model_decls(p: Dict[str, str], widths: Dict[str, int]) -> str:
    """APB 侧 TB 网线声明（TB 作从机，psel/penable/pwrite/paddr/pwdata/pstrb 是输入）。"""
    return f"""  logic {p['psel']};
  logic {p['penable']};
  logic {p['pwrite']};
  logic [{widths['addr']-1}:0] {p['paddr']};
  logic [{widths['data']-1}:0] {p['pwdata']};
  logic [{widths['strb']-1}:0] {p['pstrb']};
  logic {p['pready']} = 0;
  logic [{widths['data']-1}:0] {p['prdata']} = 0;
  logic {p['pslverr']} = 0;"""
