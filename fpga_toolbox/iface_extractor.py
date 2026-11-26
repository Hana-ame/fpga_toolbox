"""Tool 4: Extract AXI4-Lite, AXI4-Stream, APB, SV interface, clk/rst, and sideband ports from RTL AST."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
    __package__ = "fpga_toolbox"

from .canonical_iface import (
    CANONICAL_INTERFACES,
    bind_protocol,
    interface_score,
)
from .core.sv_parser import SVModuleAST, parse_sv_file
from .core.types import REQUIRED_APB_SIGNALS, REQUIRED_AXIL_SIGNALS, err_result, ok_result

_AXIL_PATS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("awaddr",  re.compile(r"(?:^|_)aw_?addr$", re.I)),
    ("awprot",  re.compile(r"(?:^|_)aw_?prot$", re.I)),
    ("awvalid", re.compile(r"(?:^|_)aw_?(?:valid|vld)$", re.I)),
    ("awready", re.compile(r"(?:^|_)aw_?(?:ready|rdy)$", re.I)),
    ("wdata",   re.compile(r"(?:^|_)w_?data$", re.I)),
    ("wstrb",   re.compile(r"(?:^|_)w_?strb$", re.I)),
    ("wvalid",  re.compile(r"(?:^|_)w_?(?:valid|vld)$", re.I)),
    ("wready",  re.compile(r"(?:^|_)w_?(?:ready|rdy)$", re.I)),
    ("bresp",   re.compile(r"(?:^|_)b_?resp$", re.I)),
    ("bvalid",  re.compile(r"(?:^|_)b_?(?:valid|vld)$", re.I)),
    ("bready",  re.compile(r"(?:^|_)b_?(?:ready|rdy)$", re.I)),
    ("araddr",  re.compile(r"(?:^|_)ar_?addr$", re.I)),
    ("arprot",  re.compile(r"(?:^|_)ar_?prot$", re.I)),
    ("arvalid", re.compile(r"(?:^|_)ar_?(?:valid|vld)$", re.I)),
    ("arready", re.compile(r"(?:^|_)ar_?(?:ready|rdy)$", re.I)),
    ("rdata",   re.compile(r"(?:^|_)r_?data$", re.I)),
    ("rresp",   re.compile(r"(?:^|_)r_?resp$", re.I)),
    ("rvalid",  re.compile(r"(?:^|_)r_?(?:valid|vld)$", re.I)),
    ("rready",  re.compile(r"(?:^|_)r_?(?:ready|rdy)$", re.I)),
)

_AXI4_BURST_PATS: Tuple[Tuple[str, re.Pattern[str]], ...] = tuple(
    (s, re.compile(rf"(?:^|_){s}$", re.I))
    for s in ("awid", "awlen", "awsize", "awburst", "wlast", "bid", "arid", "arlen", "arsize", "arburst", "rid", "rlast")
)

_AXIS_PATS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("tdata",  re.compile(r"(?:^|_)t_?data$", re.I)),
    ("tvalid", re.compile(r"(?:^|_)t_?(?:valid|vld)$", re.I)),
    ("tready", re.compile(r"(?:^|_)t_?(?:ready|rdy)$", re.I)),
    ("tlast",  re.compile(r"(?:^|_)t_?last$", re.I)),
    ("tkeep",  re.compile(r"(?:^|_)t_?keep$", re.I)),
)

# 方向后缀：`psel_i` / `pready_o` / `awaddr_in` / `rdata_out` / `din_i` 这类
# 命名在真实 RTL 里极常见。各 `_*_PATS` 都以 `(?:^|_)sig$` 形态匹配（信号名
# 必须在末尾），因此这些名字全部匹配不到 —— 一个标准 APB 从机用 `_i`/`_o`
# 后缀就会得到 0 个接口。匹配前先剥掉这一段。
_DIR_SUFFIX_RE = re.compile(r"_(?:i|o|in|out|ib|ob)$", re.I)

_APB_PATS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("psel",    re.compile(r"(?:^|_)p_?sel\d*$", re.I)),
    ("penable", re.compile(r"(?:^|_)p_?enable$", re.I)),
    ("pwrite",  re.compile(r"(?:^|_)p_?write$", re.I)),
    ("paddr",   re.compile(r"(?:^|_)p_?addr$", re.I)),
    ("pwdata",  re.compile(r"(?:^|_)p_?wdata$", re.I)),
    ("pstrb",   re.compile(r"(?:^|_)p_?strb$", re.I)),
    ("pprot",   re.compile(r"(?:^|_)p_?prot$", re.I)),
    ("pready",  re.compile(r"(?:^|_)p_?ready$", re.I)),
    ("prdata",  re.compile(r"(?:^|_)p_?rdata$", re.I)),
    ("pslverr", re.compile(r"(?:^|_)p_?slverr$", re.I)),
)

_AHB_PATS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("hsel",      re.compile(r"(?:^|_)h_?sel\d*$", re.I)),
    ("haddr",     re.compile(r"(?:^|_)h_?addr$", re.I)),
    ("htrans",    re.compile(r"(?:^|_)h_?trans$", re.I)),
    ("hwrite",    re.compile(r"(?:^|_)h_?write$", re.I)),
    ("hsize",     re.compile(r"(?:^|_)h_?size$", re.I)),
    ("hwdata",    re.compile(r"(?:^|_)h_?wdata$", re.I)),
    # `hready` 与 `hreadyout` 在 AHB-Lite 从机上是**两个不同端口**：HREADY 是
    # TB 驱动的输入、HREADYOUT 是从机驱动的输出。旧的单一模式
    # `h_?ready(?:out|in)?$` 会把两者都归到 canon `hready`，后遍历到的覆盖前者：
    # 实测同时存在时 port_map["hready"] = "HREADYOUT"，而真正的输入 HREADY 落进
    # out_of_scope_ports 并被 tie_off 到 1'b0 —— TB 端永远等不到 ready，仿真
    # 永久挂起。拆成两个 canon 后各归各位。
    ("hreadyout", re.compile(r"(?:^|_)h_?ready_?out$", re.I)),
    ("hready",    re.compile(r"(?:^|_)h_?ready(?:in)?$", re.I)),
    ("hrdata",    re.compile(r"(?:^|_)h_?rdata$", re.I)),
    ("hresp",     re.compile(r"(?:^|_)h_?resp$", re.I)),
)

_DDR3_PATS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("ck_p",  re.compile(r"ddr3_ck_?p$", re.I)),
    ("ck_n",  re.compile(r"ddr3_ck_?n$", re.I)),
    ("cke",   re.compile(r"ddr3_cke$", re.I)),
    ("cs_n",  re.compile(r"ddr3_cs_?n$", re.I)),
    ("ras_n", re.compile(r"ddr3_ras_?n$", re.I)),
    ("cas_n", re.compile(r"ddr3_cas_?n$", re.I)),
    ("we_n",  re.compile(r"ddr3_we_?n$", re.I)),
    ("ba",    re.compile(r"ddr3_ba$", re.I)),
    ("addr",  re.compile(r"ddr3_addr$", re.I)),
    ("dq",    re.compile(r"ddr3_dq(?:_in|_out)?$", re.I)),
)

_DDR4_PATS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("ck_t",     re.compile(r"ddr4_ck_?t$", re.I)),
    ("ck_c",     re.compile(r"ddr4_ck_?c$", re.I)),
    ("act_n",    re.compile(r"ddr4_act_?n$", re.I)),
    ("bg",       re.compile(r"ddr4_bg$", re.I)),
    ("ba",       re.compile(r"ddr4_ba$", re.I)),
    ("adr",      re.compile(r"ddr4_adr$", re.I)),
    ("cs_n",     re.compile(r"ddr4_cs_?n$", re.I)),
    ("odt",      re.compile(r"ddr4_odt$", re.I)),
    ("dq",       re.compile(r"ddr4_dq(?:_in|_out)?$", re.I)),
    ("dm_dbi_n", re.compile(r"ddr4_dm_?dbi_?n$", re.I)),
)

_HBM_PATS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("awid",    re.compile(r"hbm_.*aw_?id$", re.I)),
    ("awaddr",  re.compile(r"hbm_.*aw_?addr$", re.I)),
    ("awvalid", re.compile(r"hbm_.*aw_?valid$", re.I)),
    ("awready", re.compile(r"hbm_.*aw_?ready$", re.I)),
    ("wdata",   re.compile(r"hbm_.*w_?data$", re.I)),
    ("wvalid",  re.compile(r"hbm_.*w_?valid$", re.I)),
    ("wready",  re.compile(r"hbm_.*w_?ready$", re.I)),
    ("bid",     re.compile(r"hbm_.*b_?id$", re.I)),
    ("bvalid",  re.compile(r"hbm_.*b_?valid$", re.I)),
    ("bready",  re.compile(r"hbm_.*b_?ready$", re.I)),
    ("arid",    re.compile(r"hbm_.*ar_?id$", re.I)),
    ("araddr",  re.compile(r"hbm_.*ar_?addr$", re.I)),
    ("arvalid", re.compile(r"hbm_.*ar_?valid$", re.I)),
    ("arready", re.compile(r"hbm_.*ar_?ready$", re.I)),
    ("rid",     re.compile(r"hbm_.*r_?id$", re.I)),
    ("rdata",   re.compile(r"hbm_.*r_?data$", re.I)),
    ("rvalid",  re.compile(r"hbm_.*r_?valid$", re.I)),
    ("rready",  re.compile(r"hbm_.*r_?ready$", re.I)),
)


_SPI_PATS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("sclk", re.compile(r"(?:^|_)spi_?sclk$", re.I)),
    ("cs_n", re.compile(r"(?:^|_)spi_?(?:cs_n|ss_n)$", re.I)),
    ("mosi", re.compile(r"(?:^|_)spi_?mosi$", re.I)),
    ("miso", re.compile(r"(?:^|_)spi_?miso$", re.I)),
)

_ETH_PATS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("tx_en",   re.compile(r"(?:^|_)(?:eth|gmii|rgmii)_tx_(?:en|valid|ctl)$", re.I)),
    ("tx_data", re.compile(r"(?:^|_)(?:eth|gmii|rgmii)_tx_?d(?:ata)?$", re.I)),
    ("rx_dv",   re.compile(r"(?:^|_)(?:eth|gmii|rgmii)_rx_(?:dv|valid|ctl)$", re.I)),
    ("rx_data", re.compile(r"(?:^|_)(?:eth|gmii|rgmii)_rx_?d(?:ata)?$", re.I)),
    ("link_up", re.compile(r"(?:^|_)eth_link_up$", re.I)),
    ("an_done", re.compile(r"(?:^|_)eth_an_done$", re.I)),
    ("an_fail", re.compile(r"(?:^|_)eth_an_fail$", re.I)),
)

_PCIE_PATS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("tx_valid", re.compile(r"pcie_.*tx_?t?valid$", re.I)),
    ("tx_ready", re.compile(r"pcie_.*tx_?t?ready$", re.I)),
    ("tx_data",  re.compile(r"pcie_.*tx_?t?data$", re.I)),
    ("tx_last",  re.compile(r"pcie_.*tx_?t?last$", re.I)),
    ("rx_valid", re.compile(r"pcie_.*rx_?t?valid$", re.I)),
    ("rx_ready", re.compile(r"pcie_.*rx_?t?ready$", re.I)),
    ("rx_data",  re.compile(r"pcie_.*rx_?t?data$", re.I)),
    ("rx_last",  re.compile(r"pcie_.*rx_?t?last$", re.I)),
    ("link_up",  re.compile(r"pcie_.*link_?up$", re.I)),
    ("speed",    re.compile(r"pcie_.*(?:cfg_)?speed$", re.I)),
    ("lanes",    re.compile(r"pcie_.*(?:cfg_)?(?:width|lanes)$", re.I)),
)

# Clock / reset naming. Anchored, so ``reset_done`` or ``clk_div2`` never match,
# while every common prefix style (``s_axi_aresetn``, ``i_rst_n``, ``core_clk``) does.
# 时钟名识别。旧正则 `^(?:[a-z0-9]+_)*(?:[ahps]?clk|clock)$` 要求时钟词出现在
# **末尾**，于是 `clk_extra`（第二时钟）、`clk2`、`clk_i`、`clk_in` 这些极常见
# 的命名全都识别不到：它们会落进 out_of_scope_ports 被 tie 到 1'b0，而
# `_is_clock()` 本该给这类端口加 "clock/reset-like port left out of scope"
# 警告 —— 识别不到就连警告都没有（实测 clk_extra 静默 tie-off，零诊断）。
# 多时钟设计里把第二个时钟 tie 到 0 是灾难性的，必须能识别并警告。
_CLK_RE = re.compile(
    r"^(?:[a-z0-9]+_)*"
    r"(?:[ahps]?clk|clock)"          # 时钟词
    # 可选后缀/序号，允许叠加两层：clk_i、clk2、clk_extra、clk_extra_i
    r"(?:_(?:in|i|o|extra|aux|2nd|b|p|n))*"
    r"(?:\d+)?"
    r"$",
    re.I,
)
# reset 名：除了 rst/reset 家族，还要认 power-on-reset 家族（por / poreset /
# power_on_reset / pon）。`por_n` 是工业界非常标准的 POR 端口名（实测仓库自带的
# soc_omni_bus_hub.sv 就用 `if (!por_n)`，且它是唯一复位），漏检的后果不是少报
# 一条 warning：emit_testbench 的每条协议都硬连复位脚，检测不到就直接拒绝生成
# 该 DUT 的 TB —— 一个 6 协议 DUT 因此 6 条路径全被挡掉。
# 复位名识别。与时钟（缺陷 164）同构：旧式样要求复位词出现在名称**末尾**
# （只允许一个 `_n`/`_b` 后缀），于是 `rst_extra`（第二复位域）、`rst2`、
# `rst_i`、`reset_extra`、`rst_async`、`rst_sync_i` 全都识别不到。这类端口会
# 落进 out_of_scope_ports 被 tie 到固定电平，而 `_is_reset()` 返回 False 时连
# "clock/reset-like port left out of scope" 警告都不会出现 —— 复位被拉死是
# 灾难性的（整块逻辑永不出复位态，仿真可能照样报 PASS）。
_RST_RE = re.compile(
    r"^(?:[a-z0-9]+_)*(?:[ahps]?reset|[ahps]?rst|nreset|nrst"
    r"|n?por|n?poreset|n?power_?on_?reset|n?pon)"
    # 极性后缀（_n/_b）在前，随后允许叠加的修附属后缀与序号：
    # rst_n / rst_i / rst_extra / rst_async / rst_sync_i / rst2 / rst_b
    r"(?:_?[nb])?"
    r"(?:_(?:in|i|o|extra|aux|2nd|b|p|n|async|sync|a|hard|soft))*"
    r"(?:\d+)?"
    r"$",
    re.I,
)


def _norm_prefix(raw: str) -> str:
    """把端口名前缀归一成「接口组名」。

    方向前缀 `i_` / `o_` / `in_` / `out_` 必须剥掉 —— 否则同一总线会被拆成
    两组：`i_psel` 组名 "i"、`o_pready` 组名 "o"，于是输出口全部落进
    out_of_scope_ports，识别出的接口只有一半信号（实测 i_psel/i_penable/...
    + o_pready/o_prdata/o_pslverr 的 APB：只认出 group="i" 的 6 个信号，
    3 个输出口被当成本总线之外的端口）。

    注意 `_norm_prefix` 已在调用点按「pat 匹配位置之前的子串」传入（见
    classify_module），所以这里只需要处理缀在组名首尾的方向词。
    """
    # 归一前先剥方向词：`i_psel` 的 _APB_PATS 是 `(?:^|_)psel`，于是
    # m.start() 落在下划线**之后**，传进来的 raw 就是裸 "i"（不含下划线）。
    # 因此这里要同时接受 `i` / `i_` / `in` / `in_` 等形态。
    norm = re.sub(r"^(?:in|out|i|o)(?:_|$)", "", raw.strip(), flags=re.I)
    return norm.strip("_").lower() or "default"


def _is_clock(name: str) -> bool:
    return bool(_CLK_RE.match(name))


def _is_reset(name: str) -> bool:
    return bool(_RST_RE.match(name))


def _reset_active_low(name: str) -> bool:
    n = name.lower()
    if re.search(r"(?:^|_)n(?:rst|reset)$", n):
        return True
    if n.endswith(("_n", "_b")):
        return True
    # 后缀式低有效：`rst_n` / `resetb` / `aresetn`（`[ahps]?` 覆盖 aresetn/hresetn）。
    # 必须同时覆盖 POR 家族，否则 `por_n` 会被判成高有效。
    if re.match(r"^(?:[a-z0-9]+_)*[ahps]?(?:reset|rst|por|poreset|power_?on_?reset|pon)[nb]$", n):
        return True
    # 前缀式低有效（`nrst` / `npor` / `nporeset`，实测见于工业 RTL）。
    return bool(re.match(
        r"^(?:[a-z0-9]+_)*n(?:reset|rst|por|poreset|power_?on_?reset|pon)$", n))


def _infer_sv_iface_protocol(iface_type: str, port_name: str) -> str:
    probe = f"{iface_type} {port_name}".lower()
    if "axi" in probe and ("stream" in probe or "axis" in probe):
        return "axi4_stream"
    if "axi" in probe:
        return "axi4_lite"
    if "apb" in probe:
        return "apb"
    return "sv_interface"


def classify_module(mod: SVModuleAST) -> Dict[str, Any]:
    claimed, clk, rst = set(), None, None
    for p in mod.ports:
        if p.is_interface or p.direction != "input" or p.width != 1:
            continue
        if clk is None and _is_clock(p.name):
            clk = p
            claimed.add(p.name)
        elif rst is None and _is_reset(p.name):
            rst = p
            claimed.add(p.name)

    ifaces: List[Dict[str, Any]] = []
    # First classify explicit SystemVerilog interface/modport ports so they are never misclassified as 1-bit tie-offs.
    for p in mod.ports:
        if not p.is_interface:
            continue
        claimed.add(p.name)
        proto = _infer_sv_iface_protocol(p.interface_type, p.name)
        mp = p.modport.lower()
        role = "slave" if "slave" in mp or "slv" in mp else ("master" if "master" in mp or "mst" in mp else "unknown")
        ifaces.append({
            "protocol": proto,
            "group_prefix": p.name,
            "role": role,
            "addr_width": mod.parameters.get("AW"),
            "data_width": mod.parameters.get("DW"),
            "port_map": {},
            "sv_interface": {"port": p.name, "interface_type": p.interface_type, "modport": p.modport},
            "missing_signals": [],
        })

    # ------------------------------------------------------------------
    # 标准接口 -> DUT 端口绑定
    #
    # 旧实现为每个协议维护一张「canonical 信号 -> 名字正则」表，并逐个
    # `pat.search(port)`。命名风格空间是无界的（psel / p_sel / psel_i /
    # s0_p_sel / s_axi_psel / PSel ...），每遇到一种就要打一条补丁 ——
    # 缺陷 131（方向前缀拆组）、166（方向后缀全失效）、170（burst 信号漏
    # 绑定）都是同一个病根的三次发作。
    #
    # 现在改为：标准接口在 canonical_iface 里**声明一次**（名称、方向、位宽
    # 语义、是否必需），绑定引擎把任意 DUT 端口名 token 化后按「token 后缀
    # 拼接 == canonical 名」匹配，剩余前导 token 作为通道/实例前缀分组。
    # TB 仍只面向 canonical 信号生成，再经 port_map 连到真实端口名。
    # ------------------------------------------------------------------
    _BIND_ORDER = ("hbm", "pcie", "ddr4", "ddr3", "ahb_lite", "axi4_lite",
                   "axi4_stream", "apb", "spi", "ethernet")
    _REQ_FALLBACK: Dict[str, Tuple[str, ...]] = {
        "apb": REQUIRED_APB_SIGNALS,
        "axi4_lite": REQUIRED_AXIL_SIGNALS,
        "ahb_lite": ("hsel", "haddr", "htrans", "hwrite", "hwdata",
                     "hready", "hrdata", "hresp"),
        "ddr3": ("cs_n", "ba", "addr", "dq"),
        "ddr4": ("cs_n", "act_n", "bg", "ba", "adr", "dq"),
        "hbm": ("awaddr", "awvalid", "awready", "wdata", "wvalid", "wready",
                "araddr", "arvalid", "arready", "rdata", "rvalid", "rready"),
        "spi": ("sclk", "cs_n", "mosi", "miso"),
        "ethernet": ("tx_en", "tx_data", "rx_dv", "rx_data"),
        "pcie": ("tx_tvalid", "tx_tready", "tx_tdata",
                 "rx_tvalid", "rx_tready", "rx_tdata"),
        "axi4_stream": ("tdata", "tvalid", "tready"),
    }

    for proto in _BIND_ORDER:
        ci = CANONICAL_INTERFACES.get(proto)
        if ci is None:
            continue
        for bi in bind_protocol(ci, mod.ports, claimed):
            req_n, tot_n = interface_score(bi, ci)
            if tot_n < ci.min_signals:
                continue
            sigs = {k: v.port for k, v in bi.signals.items()}
            # 认同一个接口的判据：
            #   * 绑定信号数达到 min_signals（协议自身的最低特征数），且
            #   * 至少有一个「协议特征信号」绑上 —— 否则 AXI4 的 awaddr 之类
            #     可能在别的协议里凑够数量造成误认。
            # 注意不能用「required 信号的绑定数」，因为 SPI/ethernet 的
            # 全部信号在标准接口里都是可选（不同实现只暴露子集），
            # required 计数恒为 0 会把它们全部拒掉。
            req = _REQ_FALLBACK.get(proto) or tuple(
                s.name for s in ci.signals if s.required)
            # 特征信号命中数必须按 `req`（协议特征清单）统计，不能用
            # `interface_score` 的 required 计数 —— SPI/ethernet 的所有信号在
            # 标准接口里都标了 required=False（不同实现只暴露子集），于是
            # required 计数恒为 0，任何非空 `req` 都会把整个协议拒掉。
            if req and not any(x in sigs for x in req):
                continue
            claimed.update(v.port.name for v in bi.signals.values())
            # 双向拆分的「另一半」也属于这个接口：不加进 claimed 的话它会被
            # 当成 out_of_scope 再 tie 一次，与 TB 里的驱动/观察冲突。
            for _v in bi.signals.values():
                if _v.twin is not None:
                    claimed.add(_v.twin.name)

            aw_name = next((c for c in ("paddr", "awaddr", "araddr", "haddr",
                                        "addr", "adr") if c in sigs), None)
            dw_name = next((c for c in ("pwdata", "prdata", "wdata", "rdata",
                                        "hwdata", "hrdata", "dq",
                                        "tx_data", "rx_data", "tdata")
                            if c in sigs), None)
            aw_p = sigs.get(aw_name) if aw_name else None
            dw_p = sigs.get(dw_name) if dw_name else None
            probe = next((sigs[c] for c in ci.role_probes if c in sigs), None)
            role = ("slave" if probe is not None and probe.direction == "input"
                    else ("master" if probe is not None else "unknown"))
            eff_proto = proto
            if proto == "axi4_lite" and any(b in sigs for b in ci.burst_probes):
                eff_proto = "axi4"

            entry: Dict[str, Any] = {
                "protocol": eff_proto,
                "group_prefix": bi.group,
                "role": role,
                "addr_width": aw_p.width if aw_p else None,
                "data_width": dw_p.width if dw_p else None,
                "port_map": bi.port_map(),
                # 双向 canonical（DDR 的 dq）在真实 RTL 里常被拆成 in/out 两个
                # 端口，`port_map` 只能表达其中一个。`split_port_map` 给出
                # {canonical: {"in": 端口, "out": 端口}}，下游 TB 模板据此同时
                # 驱动写数据、观察读数据。
                "split_port_map": bi.split_port_map(),
                "port_details": bi.port_details(),
                "missing_signals": [x for x in req if x not in sigs],
                # 绑定证据：命名风格不再决定识别成功率，但调用方仍应能看到
                # 每个 canonical 信号被哪个真实端口、以什么拼写匹配上的。
                "binding": {
                    "matched": {k: v.matched for k, v in bi.signals.items()},
                    "ambiguous": bi.ambiguous,
                },
            }
            if bi.ambiguous:
                entry["binding"]["ambiguity_warning"] = (
                    "multiple DUT ports bind to the same canonical signal; "
                    "the highest-preference one was chosen - verify the binding"
                )
            if proto == "pcie":
                if "GEN" in mod.parameters:
                    entry["pcie_gen"] = mod.parameters["GEN"]
                if "LANES" in mod.parameters:
                    entry["pcie_lanes"] = mod.parameters["LANES"]
            ifaces.append(entry)

    oos = []
    for p in mod.ports:
        if p.name in claimed:
            continue
        if p.direction == "input":
            # width 可能为 0：typedef（enum/struct）或 interface 端口的位宽在
            # 纯文本解析下无法确定（实测 `input st_e state_in` 与
            # `input req_t req` 都是 width=0）。此时**不能**生成
            # f"{width}'b0" —— 那会产出非法字面量 `0'b0`（零位宽），下游
            # tb_emitter 直接把它写进 `.port(0'b0)`，生成物必然编译失败。
            # 位宽未知时用 '0（SV 的「全零、宽度由上下文决定」字面量）兜底，
            # 并由 diagnostics 提示调用方该端口位宽未解析。
            if p.width and p.width > 0:
                item = {"port": p.name, "dir": "input", "width": p.width,
                        "tie_off": f"{p.width}'b0"}
            else:
                item = {"port": p.name, "dir": "input", "width": p.width,
                        "tie_off": "'0",
                        "warning": f"width of port '{p.name}' could not be resolved "
                                   f"(typedef/interface type); tie_off uses unsized '0"}
            if _is_clock(p.name) or _is_reset(p.name):
                item["warning"] = "clock/reset-like port left out of scope; verify tie-off is intended"
            oos.append(item)
        else:
            oos.append({"port": p.name, "dir": p.direction, "width": p.width, "observe_only": True})
    return {
        "module_name": mod.name,
        "rtl_file": mod.file_path,
        "parser_backend": mod.parser_backend,
        "parameters": mod.parameters,
        "total_ports": len(mod.ports),
        "clock": {"port": clk.name, "freq_mhz": 100.0} if clk else None,
        "reset": {"port": rst.name, "active_low": _reset_active_low(rst.name), "sync": True} if rst else None,
        "interfaces": ifaces,
        # interface 打包的总线端口（SV `interface` + modport）无法用扁平
        # port_map 表达：期望的 9/18 个信号在 interface 内部，DUT 顶层只有
        # `apb_if apb` 一个端口。旧实现照样报 protocol="apb" + port_map={} +
        # missing_signals=[]，**没有任何诊断**，调用方会以为抽到了可用接口；
        # 下游 emit_testbench 则报「DUT 缺少 psel->psel, ...」——把「端口是
        # interface 类型」误述成「DUT 漏了端口」，排查方向完全错。
        # 这里如实标记展开状态，并给出可定位的诊断。
        "interfaces_expandable": any(bool(i.get("port_map")) for i in ifaces)
                                 if ifaces else True,
        "unexpanded_sv_interfaces": [
            {"port": i["sv_interface"]["port"],
             "interface_type": i["sv_interface"]["interface_type"],
             "modport": i["sv_interface"].get("modport") or "",
             "protocol": i["protocol"]}
            for i in ifaces
            if i.get("sv_interface") and not i.get("port_map")
        ],
        "out_of_scope_ports": oos,
    }


def tool_extract_interfaces(
    rtl_file: str,
    module_name: Optional[str] = None,
    emit_draft_yaml: Optional[str] = None,
    staged_rtl_files: Optional[List[str]] = None,
    parser_backend: str = "auto",
) -> Dict[str, Any]:
    p = Path(rtl_file)
    if not p.is_file():
        return err_result("extract_interfaces", f"RTL file not found: {rtl_file}")
    # `staged_rtl_files` 的语义是「与目标文件同批编译」，所以每个条目都必须是
    # 真实存在的**文件**。缺陷 161 前该参数只被写进返回值的 rtl_files 字段，
    # 从未传给解析器 —— 不存在的路径、目录都能通过，且对解析结果零影响。
    if staged_rtl_files:
        # 相对路径的解析基准（按顺序尝试，命中即用）：
        #   1. rtl_file 所在目录；
        #   2. rtl_file 的父目录（= project root 的常见布局，如
        #      `staged_rtl_files=["rtl/reg_slave.sv"]` 配 `rtl_file=proj/rtl/x.sv`）；
        #   3. 当前工作目录（测试/脚本常直接传 cwd 相对路径）。
        # 三种约定在真实调用里都出现过，只支持一种会把合法路径误判成「不存在」。
        _rtl_dir = p.resolve().parent
        _rroots = [_rtl_dir]
        if _rtl_dir.parent != _rtl_dir:
            _rroots.append(_rtl_dir.parent)
        _bad: List[str] = []
        _resolved_staged: List[str] = []
        for _s in staged_rtl_files:
            _sp = Path(_s)
            if not _sp.is_absolute():
                _hit = next((_r / _sp for _r in _rroots if (_r / _sp).is_file()), None)
                _sp = _hit if _hit is not None else _sp.resolve()
            if not _sp.is_file():
                _bad.append(f"{_s} ({'directory' if _sp.is_dir() else 'not found'})")
            else:
                _resolved_staged.append(str(_sp.resolve()))
        staged_rtl_files = _resolved_staged
        if _bad:
            return err_result(
                "extract_interfaces",
                "Invalid staged_rtl_files entries: "
                + ", ".join(_bad)
                + ". Each entry must be an existing RTL/header file compiled together "
                "with rtl_file (package or interface definitions imported by it).",
            )
    # 收集 slang 后端的回退原因：slang 装了却没生效时对调用方可见，
    # 而不是静默退回 builtin（否则「可选 slang AST 后端」就是虚报）。
    parse_diags: List[str] = []
    mods = parse_sv_file(
        p, backend=parser_backend, diagnostics=parse_diags,
        staged_rtl_files=staged_rtl_files,
    )
    if not mods:
        return err_result("extract_interfaces", f"No modules found in {rtl_file}")
    selected = [m for m in mods if m.name == module_name] if module_name else mods
    if not selected:
        return err_result("extract_interfaces", f"Module '{module_name}' not found.")

    extracted = [classify_module(m) for m in selected]
    data: Dict[str, Any] = {"rtl_file": str(p.resolve()), "modules": extracted}

    diagnostics: List[Dict[str, Any]] = []
    # 把 slang 回退/解析诊断透传（W013），避免静默降级
    for pd in parse_diags:
        diagnostics.append({
            "severity": "WARNING",
            "code": "W013",
            "path": p.name,
            "message": f"Parser backend: {pd}",
        })
    for m in extracted:
        if m["clock"] is None:
            diagnostics.append({"severity": "WARNING", "code": "W010", "path": m["module_name"], "message": "No clock port detected; defaulting to aclk@100MHz."})
        if m["reset"] is None:
            diagnostics.append({"severity": "WARNING", "code": "W011", "path": m["module_name"], "message": "No reset port detected; defaulting to aresetn active-low."})
        for iface in m["interfaces"]:
            if "sv_interface" in iface and m.get("parser_backend") != "slang":
                diagnostics.append({
                    "severity": "WARNING",
                    "code": "W012",
                    "path": f"{m['module_name']}.{iface['sv_interface']['port']}",
                    "message": f"SV interface port '{iface['sv_interface']['interface_type']}.{iface['sv_interface']['modport']}' detected by builtin parser; install slang to expand member signals.",
                })

    if emit_draft_yaml:
        m0 = extracted[0]
        # AXI4-Full 也归到这里：契约的 boundary.axi4_lite 是各类 AXI 端口的
        # 统一出口，AXI4-Full 设备若被排除会得到空 axi4_lite 段。
        axil = next((i for i in m0["interfaces"] if i["protocol"] in ("axi4_lite", "axi4")), None)
        apb = next((i for i in m0["interfaces"] if i["protocol"] == "apb"), None)
        boundary: Dict[str, Any] = {
            "module_name": m0["module_name"],
            "rtl_files": staged_rtl_files or [m0["rtl_file"]],
            "parameters": m0["parameters"],
            "clock": m0["clock"] or {"port": "aclk", "freq_mhz": 100.0},
            "reset": m0["reset"] or {"port": "aresetn", "active_low": True, "sync": True},
            "out_of_scope_ports": m0["out_of_scope_ports"],
        }
        # 只在 RTL 真的存在 AXI 接口（或两种接口都没有，保持旧行为）时写
        # axi4_lite 段。纯 APB 设备若写入空的 axi4_lite.port_map，validator 会把
        # 空 dict 当成「有 AXI 边界」并报 17 条 E001，让合法的纯 APB 契约无法
        # 通过自家校验（回归 33）。
        if axil or not apb:
            boundary["axi4_lite"] = {
                "addr_width": (axil["addr_width"] or 12) if axil else 12,
                "data_width": (axil["data_width"] or 32) if axil else 32,
                "role": (axil["role"] if axil and axil["role"] != "unknown" else "slave"),
                "port_map": axil["port_map"] if axil else {},
            }
        if apb:
            boundary["apb"] = {
                "addr_width": apb["addr_width"] or 8,
                "data_width": apb["data_width"] or 32,
                "role": apb["role"] if apb["role"] != "unknown" else "slave",
                "port_map": apb["port_map"],
            }
        if m0.get("instance_hierarchy"):
            boundary["instance_hierarchy"] = m0["instance_hierarchy"]
        draft = {
            "boundary": boundary,
            "test_plan": {
                "protocol_rules": {"check_valid_stability": True, "check_reset_outputs": True, "max_resp_cycles": 16, "allow_unaligned_addr": False},
                "register_rules": [{"rule_id": "REG_001", "name": "CTRL_REG", "offset": 0, "reset_value": 0, "access": "RW", "write_mask": 0xFFFFFFFF, "read_mask": 0xFFFFFFFF}],
                "illegal_addr_rules": [{"rule_id": "ILL_001", "offset": 0xFC, "expected_resp": "SLVERR"}],
                "stress_profile": {"random_backpressure": True, "iterations": 32, "seed": 20261120},
            },
        }
        out_p = Path(emit_draft_yaml)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(yaml.safe_dump(draft, sort_keys=False, allow_unicode=True), encoding="utf-8")
        data["draft_yaml_path"] = str(out_p.resolve())

    return ok_result("extract_interfaces", data, diagnostics=diagnostics)


if __name__ == "__main__":
    import sys

    from .cli import run_single_tool

    sys.exit(run_single_tool("extract_interfaces"))
