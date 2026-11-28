"""多协议 SV 测试台生成器的公共骨架。

本子包由原来的单文件 ``protocol_tb_emitter.py``（2,428 行，全仓最大）拆分而来。
拆分动机：

  * 单文件里 11 个协议模板各自独立（互不调用），却共享同一组常量与 helper；
    任何一处改动都要在 2,400 行里定位；
  * 新增协议（ddr5/spi/ethernet 就是这样加进来的）让体积单调增长，
    而协议之间没有任何复用 —— 这是典型的「按职责分文件」信号。

模块划分：

  common.py        本文件 —— 常量表、端口解析 helper、方向/缺端口校验、编排入口
  apb.py           APB3/4 从机
  axi4.py          AXI4-Full 突发从机
  axi4_stream.py   AXI4-Stream sink
  ahb_lite.py      AHB-Lite 流水线从机
  ddr.py           DDR3 / DDR4 / DDR5 控制器（时序模型相近，共用一组 helper）
  hbm.py           HBM 伪通道
  pcie.py          PCIe TLP 端点（含 Gen3/4/5 × x1/x4/x16 矩阵）
  spi.py           SPI 从设备
  ethernet.py      Ethernet MAC（链路对端）

**向后兼容**：``fpga_toolbox.protocol_tb_emitter`` 仍是完整门面（re-export 全部
公开符号），既有 `from fpga_toolbox.protocol_tb_emitter import emit_protocol_testbench`
与所有测试无需改动。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

SUPPORTED_MULTI_PROTOCOLS = ("apb", "axi4", "axi4_stream", "ahb_lite", "ddr3", "ddr4", "ddr5", "hbm", "pcie", "spi", "ethernet")

# 各协议 testbench 模板**实际硬连**的端口。分类器只需若干个信号就能认出协议
# （APB 只需 4 个），但 TB 模板会连全部 9 个 —— 若 RTL 缺其中任何一个（如 APB3
# 无 pstrb、无 pslverr），生成出的 TB 会 `.pstrb(pstrb)` 连到不存在的端口，
# Verilator 报 PINNOTFOUND 编译失败，而工具却返回 ok=True（静默生成坏产物）。
# 因此生成前按此表校验，缺失即 fail-fast。
REQUIRED_TB_PORTS: Dict[str, Tuple[str, ...]] = {
    # APB 模板硬连全部 9 个信号（含 pstrb/pslverr）——虽然 APB3 允许它们缺失，
    # 但模板会连，所以缺了必须报错，而不是生成 PINNOTFOUND 的坏 TB。
    "apb": ("psel", "penable", "pwrite", "paddr", "pwdata", "pstrb", "pready", "prdata", "pslverr"),
    "axi4": (
        "awaddr", "awvalid", "awready", "wdata", "wstrb", "wvalid", "wready",
        "bresp", "bvalid", "bready", "araddr", "arvalid", "arready",
        "rdata", "rresp", "rvalid", "rready",
    ),
    "ahb_lite": ("hsel", "haddr", "htrans", "hwrite", "hsize", "hwdata", "hrdata", "hresp"),
    # AXI4-Stream 的最小合法实现只有 TDATA/TVALID/TREADY；TLAST/TKEEP/TID/
    # TDEST/TUSER 都是可选的，模板按 port_map 是否出现决定生成与否，所以这里
    # 只列**必需**的三项。把可选信号也列进来会让一个完全合法的精简 DUT 被
    # 判定为「缺端口」而拒绝生成。
    "axi4_stream": ("tdata", "tvalid", "tready"),
    # DDR5：TB 扮演 DRAM，采样控制器的 CK/CS_n/ACT_n/CA。dqs/dq 在模板里接成
    # 高阻或双向，缺失会退化为安装警告而非硬错，因此不列为必需。
    "ddr5": ("ck_t", "ck_c", "cs_n", "act_n", "ca", "ba", "bg", "dq", "rd_valid"),
    # SPI：sclk/cs_n/mosi/miso 缺一不可（双向互换会整体错位）。miso 对
    # master 是输入，但模板必须驱动它，所以同样必需。
    "spi": ("sclk", "cs_n", "mosi", "miso"),
    # Ethernet：模板按 tx_en/tx_data/rx_dv/rx_data 四端口生成。rx_clk/tx_clk
    # 是可选（多数简化模型只用单时钟），出现在 port_map 时才连线。
    "ethernet": ("tx_en", "tx_data", "rx_dv", "rx_data"),
    # ddr3/ddr4/hbm/pcie 的模板不用 port_map，直接按标准端口名连线并用 `.*`
    # 兜底。`.*` 只连接**TB 里已声明**的同名变量，所以 DUT 端口名一旦与模板
    # 声明不一致（如 DUT 用 `inout [15:0] ddr3_dq` 而模板声明的是
    # ddr3_dq_in/ddr3_dq_out），`.*` 会报 "Can't find definition of variable"
    # 编译失败，而工具仍返回 ok=True。这里按模板声明的实际端口名校验。
    # 注意 *_dq_in 必须一起校验：模板对 DUT 是**双向**语义 —— dq_in 由 TB 驱动
    # （写入数据），dq_out 由 DUT 驱动（读回数据）。旧表只列了 dq_out，于是
    # 一个没有 dq_in 端口的 DDR3 DUT 通过了生成前校验，产出的 TB 带
    # `.ddr3_dq_in(...)` 连线，Verilator 报 PINNOTFOUND 编译失败而工具报 ok=True
    # （实测复现）。ddr4 同理。
    "ddr3": (
        "ddr3_ck_p", "ddr3_ck_n", "ddr3_cke", "ddr3_cs_n", "ddr3_ras_n",
        "ddr3_cas_n", "ddr3_we_n", "ddr3_ba", "ddr3_addr",
        "ddr3_dq_in", "ddr3_dq_out", "ddr3_rd_valid",
    ),
    "ddr4": (
        "ddr4_ck_t", "ddr4_ck_c", "ddr4_act_n", "ddr4_bg", "ddr4_ba", "ddr4_adr",
        "ddr4_cs_n", "ddr4_odt", "ddr4_dq_in", "ddr4_dq_out",
        "ddr4_dm_dbi_n", "ddr4_rd_valid",
    ),
    "hbm": (
        "hbm_awid", "hbm_awaddr", "hbm_awvalid", "hbm_awready",
        "hbm_wdata", "hbm_wvalid", "hbm_wready",
        "hbm_bid", "hbm_bvalid", "hbm_bready",
        "hbm_arid", "hbm_araddr", "hbm_arvalid", "hbm_arready",
        "hbm_rid", "hbm_rdata", "hbm_rvalid", "hbm_rready",
    ),
    "pcie": (
        "pcie_tx_tvalid", "pcie_tx_tready", "pcie_tx_tdata", "pcie_tx_tlast",
        "pcie_rx_tvalid", "pcie_rx_tready", "pcie_rx_tdata", "pcie_rx_tlast",
        "pcie_link_up", "pcie_cfg_speed", "pcie_cfg_lanes",
    ),
}


# TB 模板对各端口的**期望方向**：TB 主动驱动的是 DUT 的输入，TB 读取作响应
# 的是 DUT 的输出。方向不匹配时不会编译失败（Verilator 只报 UNDRIVEN 之类），
# 但会在运行期永久挂起——例如 DUT 的 pready 若被写成 input，TB 的
# `do begin ... end while (!pready)` 永远等不到响应，仿真卡死。工具却报
# ok=True。这比编译失败更隐蔽：CI 表现为超时而不是失败。
EXPECTED_TB_DIR: Dict[str, Dict[str, str]] = {
    "apb": {
        "psel": "input", "penable": "input", "pwrite": "input", "paddr": "input",
        "pwdata": "input", "pstrb": "input",
        "pready": "output", "prdata": "output", "pslverr": "output",
    },
    "ahb_lite": {
        "hsel": "input", "haddr": "input", "htrans": "input", "hwrite": "input",
        "hsize": "input", "hwdata": "input",
        # 从机输出 ready 是 HREADYOUT（canon：hreadyout）。
        # 单独 canon "hready" 在标准从机上指向 DUT 的**输入**（TB 驱动），
        # 因此期望方向是 input；旧表把它写成 output，遇到同时暴露
        # HREADY + HREADYOUT 的标准从机就误报方向不符。
        "hreadyout": "output", "hready": "input",
        "hrdata": "output", "hresp": "output",
    },
    "axi4": {
        "awaddr": "input", "awvalid": "input", "wdata": "input", "wstrb": "input",
        "wvalid": "input", "bready": "input", "araddr": "input", "arvalid": "input", "rready": "input",
        "awready": "output", "wready": "output", "bresp": "output", "bvalid": "output",
        "arready": "output", "rdata": "output", "rresp": "output", "rvalid": "output",
    },
    # AXI4-Stream 做 sink：TB 作为数据源驱动 payload 与 TVALID，DUT 给出 TREADY。
    "axi4_stream": {
        "tdata": "input", "tvalid": "input", "tlast": "input", "tkeep": "input",
        "tid": "input", "tdest": "input", "tuser": "input",
        "tready": "output",
    },
    # DDR5：TB 是 DRAM 端，只**观察**控制器的输出；rd_valid 也是控制器输出。
    "ddr5": {
        "ck_t": "output", "ck_c": "output", "cs_n": "output", "act_n": "output",
        "ca": "output", "ba": "output", "bg": "output",
        "dqs_t": "output", "dqs_c": "output", "rd_valid": "output",
    },
    # SPI：TB 是从设备。master 输出 sclk/cs_n/mosi，输入 miso。
    "spi": {
        "sclk": "output", "cs_n": "output", "mosi": "output", "miso": "input",
    },
    # Ethernet：TB 是对端。MAC 发送 tx_*，接收 rx_*。
    "ethernet": {
        "tx_en": "output", "tx_data": "output",
        "rx_dv": "input", "rx_data": "input",
    },
}



def _wrong_dir_ports(
    proto: str, iface: Dict[str, Any], rtl_dir: Dict[str, str],
) -> List[str]:
    """返回期望方向与实际方向不符的端口（仅对有方向表的协议生效）。"""
    expected = EXPECTED_TB_DIR.get(proto)
    if not expected:
        return []
    bad: List[str] = []
    for canon, want in expected.items():
        mapped = _pm(iface, canon, canon)
        actual = rtl_dir.get(mapped)
        if actual is None or actual == "inout":
            continue  # inout 由 DUT 侧三态驱动，TB 端按 input/output 皆可接受
        # logic 输出在 SV 解析里可能记为 output，input 记 input
        if actual != want:
            bad.append(f"{canon}->{mapped} (expected DUT {want}, got {actual})")
    return bad


#: template port name -> canonical signal name, for the protocols whose template
#: still hard-codes a prefixed port spelling (ddr3_ck_p, pcie_tx_tvalid ...).
#: ``REQUIRED_TB_PORTS`` lists template-side spellings; the extractor's port_map is
#: keyed by canonical names, so the check below needs this bridge.
_TEMPLATE_TO_CANON: Dict[str, Dict[str, str]] = {
    "ddr3": {
        "ddr3_ck_p": "ck_p", "ddr3_ck_n": "ck_n", "ddr3_cke": "cke",
        "ddr3_cs_n": "cs_n", "ddr3_ras_n": "ras_n", "ddr3_cas_n": "cas_n",
        "ddr3_we_n": "we_n", "ddr3_ba": "ba", "ddr3_addr": "addr",
        "ddr3_dq_in": "dq", "ddr3_dq_out": "dq", "ddr3_rd_valid": "rd_valid",
    },
    "ddr4": {
        "ddr4_ck_t": "ck_t", "ddr4_ck_c": "ck_c", "ddr4_act_n": "act_n",
        "ddr4_bg": "bg", "ddr4_ba": "ba", "ddr4_adr": "adr",
        "ddr4_cs_n": "cs_n", "ddr4_odt": "odt", "ddr4_dq_in": "dq",
        "ddr4_dq_out": "dq", "ddr4_dm_dbi_n": "dm_dbi_n",
        "ddr4_rd_valid": "rd_valid",
    },
    "hbm": {f"hbm_{n}": n for n in (
        "awid", "awaddr", "awvalid", "awready", "wdata", "wvalid", "wready",
        "bid", "bvalid", "bready", "arid", "araddr", "arvalid", "arready",
        "rid", "rdata", "rvalid", "rready")},
    "pcie": {
        "pcie_tx_tvalid": "tx_tvalid", "pcie_tx_tready": "tx_tready",
        "pcie_tx_tdata": "tx_tdata", "pcie_tx_tlast": "tx_tlast",
        "pcie_rx_tvalid": "rx_tvalid", "pcie_rx_tready": "rx_tready",
        "pcie_rx_tdata": "rx_tdata", "pcie_rx_tlast": "rx_tlast",
        "pcie_link_up": "link_up", "pcie_cfg_speed": "speed",
        "pcie_cfg_lanes": "lanes",
    },
}


def _missing_tb_ports(proto: str, iface: Dict[str, Any], rtl_ports: List[str]) -> List[str]:
    """返回该协议 TB 模板需要、但 DUT 实际没有的端口名。

    优先用 port_map 的映射结果判断（emitter 就是按 port_map 连线），若 port_map
    没给该项则退回 RTL 端口名本身——两者都不存在就是真缺失。

    对 DDR4/HBM/PCIe 这类模板仍硬编码前缀端口名的协议，``REQUIRED_TB_PORTS``
    列的是**模板侧拼写**（``ddr3_ck_p``），而 port_map 以 canonical 名
    （``ck_p``）为键。旧实现直接拿模板名去查 port_map，永远查不到，于是任何
    使用非标准前缀的合法 DUT（如 ``mem_ddr3_*``）都被判成「缺端口」——尽管
    binding 层已经正确绑定了全部信号（缺陷 174）。
    """
    required = REQUIRED_TB_PORTS.get(proto)
    if not required:
        return []
    port_set = {p.lower() for p in rtl_ports}
    bridge = _TEMPLATE_TO_CANON.get(proto) or {}
    split = iface.get("split_port_map") or {}
    missing: List[str] = []
    for canon in required:
        cname = bridge.get(canon, canon)
        # 双向拆分端口：模板对 dq 的两半分别连线（dq_in 由 TB 驱动、dq_out 由
        # DUT 驱动），所以必须分别校验。DUT 只有 dq_out、没有 dq_in 时，
        # `_split_pm` 会回退到默认名 `ddr3_dq_in` —— 该端口并不存在，
        # 生成的 TB 带 `.ddr3_dq_in(...)` 连线，Verilator 报 PINNOTFOUND
        # （缺陷 121）。split 里缺 corresponding role 时直接判缺失。
        pair = split.get(cname)
        role = "in" if canon.endswith("_in") else ("out" if canon.endswith("_out") else "")
        if role:
            if isinstance(pair, dict):
                mapped = pair.get(role)
                if not mapped or mapped.lower() not in port_set:
                    missing.append(f"{canon}->{mapped or canon}")
                continue
            # 没有 split 记录：dq 只绑定了单一方向（例如 DUT 只有 ddr3_dq_out）。
            # 模板对 in/out 两半都会连线，缺的那一半必须按**约定名**校验存在性，
            # 否则生成的 TB 会带一个不存在的 pin（PINNOTFOUND，缺陷 121）。
            # 约定名 = 模板默认拼写（ddr3_dq_in / ddr3_dq_out）
            if canon.lower() not in port_set:
                missing.append(f"{canon}->{canon}")
            continue
        mapped = _pm(iface, cname, canon)
        if mapped.lower() not in port_set:
            missing.append(f"{canon}->{mapped}")
    return missing


def _pm(iface: Dict[str, Any], canon: str, default: str) -> str:
    pm = iface.get("port_map") or {}
    val = pm.get(canon)
    if isinstance(val, dict):
        return str(val.get("port") or default)
    return str(val or default)


def _split_pm(iface: Dict[str, Any], canon: str, role: str, default: str) -> str:
    """Resolve one half of a bidirectional canonical signal.

    DDR controllers commonly expose ``ddr3_dq_in`` / ``ddr3_dq_out`` instead of a
    single ``inout ddr3_dq``.  The extractor reports that pair through
    ``split_port_map``; when it is absent (single inout port) the canonical name
    from ``port_map`` is used for both halves.
    """
    sp = iface.get("split_port_map") or {}
    pair = sp.get(canon)
    if isinstance(pair, dict):
        val = pair.get(role)
        if val:
            return str(val)
    return _pm(iface, canon, default)


def _pw(iface: Dict[str, Any], canon: str, default: int) -> int:
    """取 canon 信号在 **RTL 里真实声明的位宽**（port_details 由扫描 DUT 端口得到）。

    模板必须按真实端口位宽声明 TB 网线：写死位宽时 DUT 稍有不同就是
    WIDTHTRUNC / WIDTHEXPAND，而 Verilator 默认**警告即错误**，构建直接失败
    （实测 DDR3 8 位 dq：8'hBEEF 报 Too many digits；HBM 64 位 wdata 对 256 位
    模板报 WIDTHEXPAND）。工具却仍返回 ok=True —— DUT 一点没验到。
    """
    details = iface.get("port_details") or {}
    d = details.get(canon)
    if isinstance(d, dict):
        w = d.get("width")
        if isinstance(w, int) and w > 0:
            return w
    if canon in ("addr", "adr", "awaddr", "araddr"):
        aw = iface.get("addr_width")
        if isinstance(aw, int) and aw > 0:
            return aw
    if canon in ("dq", "wdata", "rdata"):
        dw = iface.get("data_width")
        if isinstance(dw, int) and dw > 0:
            return dw
    return default
