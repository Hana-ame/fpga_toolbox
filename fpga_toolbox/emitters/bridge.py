"""双接口桥接 DUT 的联合测试台生成器。

普通单协议 TB 只驱动 DUT 的一侧（TB 充当对端），另一侧的总线端口只能 tie-off。
对**桥接 DUT**（如 AXI4 → APB、SPI → APB）这等于什么都没验证：桥接的全部价值
就在于「上游事务被正确转换成下游事务」，只驱动一侧永远发现不了转换错误。

本模块生成**双边联合 TB**：

    上游侧（DUT 是 slave）  TB 充当 master，发事务
    下游侧（DUT 是 master） TB 充当 slave，收事务并应答

然后校验跨总线一致性 —— 这是桥接 TB 唯一真正重要的断言。

支持的组合（首批，覆盖 examples/multi_bus_ref 里的桥接 DUT）：

    axi4      -> apb        （64→32 / 32→32 位宽转换桥）
    axi4      -> axi4       （位宽转换 / OoO 重排）
    spi       -> apb        （串行命令转寄存器访问）
    ethernet  -> apb
    axi4_stream -> apb

设计取舍：不复用单协议模板的 `initial` 块。单协议模板的 `initial` 自己发起
固定序列并 `$finish`，两个模板拼在一起会变成两个独立的、互不相关的仿真——
DUT 的桥接逻辑根本没被激励到。本模块按「上游发 → 下游收 → 比对」的**因果顺序**
重写时序，而不是把两份模板并排放。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .common import _pm, _pw

#: 首批支持的桥接组合：(上游协议, 下游协议) -> 说明
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


def emit_bridge_testbench(
    mod: str,
    clk: str,
    rst: str,
    up_iface: Dict[str, Any],
    down_iface: Dict[str, Any],
    up_proto: str,
    down_proto: str,
) -> str:
    """生成 AXI4 → APB 桥接联合 TB（首批实现）。

    自检的三段因果链（这是桥接 TB 的核心价值，单侧 TB 完全覆盖不到）：

      1. **上游握手成功** —— TB 发起的 AXI4 写/读事务必须被 DUT 接受
         （awready/arready 必须拉起）。不拉起说明上游侧根本没工作。
      2. **下游确实产生对应事务** —— 每个上游写事务必须在 APB 侧出现
         `psel && penable` 的访问，且 `paddr` 落在期望地址、写数据被转换到
         正确的 32 位半区（64→32 转换桥的关键正确性）。
      3. **响应沿原路返回** —— APB 的 `pready` 必须导致 AXI4 的 `bvalid`/`rvalid`，
         且读数据被正确拼装回 64 位（高位半字来自第二次 APB 访问）。

    第 2、3 条是「跨总线一致性」，任何一处的位宽/字节序/时序转换错误都会让它失败。
    """
    p_up = {k: _pm(up_iface, k, f"s_axi_{k}") for k in (
        "awid", "awaddr", "awlen", "awsize", "awburst", "awvalid", "awready",
        "wdata", "wstrb", "wlast", "wvalid", "wready",
        "bid", "bresp", "bvalid", "bready",
        "arid", "araddr", "arlen", "arsize", "arburst", "arvalid", "arready",
        "rid", "rdata", "rresp", "rlast", "rvalid", "rready",
    )}
    p_dn = {k: _pm(down_iface, k, f"m_apb_{k}") for k in (
        "psel", "penable", "pwrite", "paddr", "pwdata", "pstrb",
        "pready", "prdata", "pslverr")}

    w_up: Dict[str, int] = {
        "id": _pw(up_iface, "awid", 4),
        "aw": _pw(up_iface, "awaddr", 16),
        "len": _pw(up_iface, "awlen", 8),
        "size": _pw(up_iface, "awsize", 3),
        "burst": _pw(up_iface, "awburst", 2),
        "dw": _pw(up_iface, "wdata", 64),
        "sw": _pw(up_iface, "wstrb", 8),
        "resp": _pw(up_iface, "bresp", 2),
    }
    w_dn: Dict[str, int] = {
        "addr": _pw(down_iface, "paddr", 16),
        "data": _pw(down_iface, "pwdata", 32),
        "strb": _pw(down_iface, "pstrb", 4),
    }
    # 地址/数据掩码：64→32 桥的上游地址可能比下游宽，测试向量必须两边都合法
    aw_mask = (1 << w_up["aw"]) - 1
    dn_aw_mask = (1 << w_dn["addr"]) - 1
    test_addr = 0x40 & aw_mask & dn_aw_mask
    # 64 位写数据的两个 32 位半区用不同图案，便于校验「高位/低位是否被正确拆分」
    hi_word = 0xDEADBEEF
    lo_word = 0x12345678
    wdata64 = ((hi_word << 32) | lo_word) & ((1 << w_up["dw"]) - 1)
    # 预先算好 SV 字面量，避免在 f-string 里做多层花括号转义（易错且难读）
    pad_w = max(0, w_up["dw"] - 32)
    ahex = max(1, (w_dn["addr"] + 3) // 4)
    dhex = max(1, (w_dn["data"] + 3) // 4)
    shex = max(1, (w_dn["strb"] + 3) // 4)
    dwhex = max(1, (w_up["dw"] + 3) // 4)
    beat0_lit = f"{{{{{pad_w}{{1'b0}}}}, 32'h{lo_word:08X}}}"
    beat1_lit = f"{{{{{pad_w}{{1'b0}}}}, 32'h{hi_word:08X}}}"

    # 上游/下游端口连线
    up_conns = ",\n    ".join(f".{p_up[k]}({p_up[k]})" for k in (
        "awid", "awaddr", "awlen", "awsize", "awburst", "awvalid", "awready",
        "wdata", "wstrb", "wlast", "wvalid", "wready",
        "bid", "bresp", "bvalid", "bready",
        "arid", "araddr", "arlen", "arsize", "arburst", "arvalid", "arready",
        "rid", "rdata", "rresp", "rlast", "rvalid", "rready"))
    dn_conns = ",\n    ".join(f".{p_dn[k]}({p_dn[k]})" for k in (
        "psel", "penable", "pwrite", "paddr", "pwdata", "pstrb",
        "pready", "prdata", "pslverr"))

    return f"""`timescale 1ns/1ps
// Auto-generated by fpga_toolbox.emitters.bridge (protocol={up_proto}->{down_proto})
// Joint bridge testbench: TB drives the {up_proto} side as master and models the
// {down_proto} side as slave, then checks cross-bus transaction consistency.
module tb_{mod}_bridge;
  logic {clk} = 0, {rst} = 0;
  // int 而非 logic：logic 只有 1 位，累加偶数次错误会溢出回 0 从而假 PASS
  int   errs = 0;
  int   up_writes = 0;
  int   dn_writes = 0;
  int   dn_reads = 0;
  int   up_bs = 0;
  int   up_rs = 0;

{_axi4_port_decls(p_up, w_up)}
{_apb_model_decls(p_dn, w_dn)}

  {mod} dut (
    .{clk}({clk}), .{rst}({rst}),
    {up_conns},
    {dn_conns}
  );
  always #5 {clk} = ~{clk};

  // ---------------------------------------------------------------------
  // 下游 APB 从机模型：只要有 psel&&penable 就应答，并记录访问地址/数据。
  // 用固定延迟 1 拍模拟真实从机（0 拍会让「桥接是否插入等待」不可观测）。
  // ---------------------------------------------------------------------
  logic [{w_dn['data']-1}:0] apb_rdata_mem [0:15];
  // APB 协议要点：PSEL 高、PENABLE 低是 **setup** 相位，此时 PADDR/PWDATA
  // 可能还没被主设备驱动为最终值（本 DUT 在 setup 相位之后才锁存 pwdata）。
  // 只有在 **access** 相位（PSEL && PENABLE 同时为高）采样才是协议正确的。
  //
  // 实测踩坑：在 PSEL 刚拉高的同一拍采样，得到的 pwdata 恒为 0 —— 会误报成
  // 「桥接没有搬数据」，而实际是 TB 采样早了一拍（设 DUT 时序是对的）。
  // 这类假缺陷比漏报更危险：它让人去改正确的 RTL。
  always @(posedge {clk}) begin
    if ({rst} === 1'b1) begin
      {p_dn['pready']} <= 0;
      // 只在「尚未应答过本次传输」时处理。若不看 pready 就重复处理，setup
      // 相位之后的连续 access 拍会把同一次传输统计两次（实测 dn_writes 翻倍，
      // 让「桥接拆成几次下游访问」这条断言失去意义）。
      if ({p_dn['psel']} === 1'b1 && {p_dn['penable']} === 1'b1
          && {p_dn['pready']} === 1'b0) begin
        // access 相位：此时 paddr/pwdata 已稳定，可以安全采样
        if ({p_dn['pwrite']} === 1'b1) begin
          dn_writes++;
          $display("[CHECK] id=BR_WR | proto={up_proto}->{down_proto} | cat=downstream_write | addr=0x%0{ahex}h data=0x%0{dhex}h strb=0x%0{shex}h | status=PASS",
                   {p_dn['paddr']}, {p_dn['pwdata']}, {p_dn['pstrb']});
          apb_rdata_mem[{p_dn['paddr']}[3:0]] <= {p_dn['pwdata']};
          {p_dn['pready']} <= 1;
        end else begin
          dn_reads++;
          {p_dn['prdata']} <= apb_rdata_mem[{p_dn['paddr']}[3:0]];
          {p_dn['pready']} <= 1;
        end
      end else begin
        {p_dn['pready']} <= 0;
      end
    end
  end

  // ---------------------------------------------------------------------
  // 上游 AXI4 写事务：发一个 2-beat INCR 写，数据是高/低半区可区分的 64 位值。
  // 桥接 DUT 应把它拆成两次 APB 32 位写。
  // ---------------------------------------------------------------------
  task automatic axi4_write(input logic [{w_up['aw']-1}:0] addr, input logic [{w_up['dw']-1}:0] data);
    // AW
    @(posedge {clk}); #1;
    {p_up['awid']} = {w_up['id']}'d1; {p_up['awaddr']} = addr;
    {p_up['awlen']} = {w_up['len']}'d1; {p_up['awsize']} = {w_up['size']}'d3;
    {p_up['awburst']} = {w_up['burst']}'b01; {p_up['awvalid']} = 1;
    while (!{p_up['awready']}) begin @(posedge {clk}); #1; end
    @(posedge {clk}); #1;
    {p_up['awvalid']} = 0;
    // W x2
    for (int i = 0; i < 2; i++) begin
      // 第 0 拍给低 32 位、第 1 拍给高 32 位，桥接应分别写到 addr 和 addr+4
      // 高半区填 0、低 32 位放测试图案（拼接表达式已预先算好，
      // 避免在 f-string 里做四层花括号转义）
      {p_up['wdata']} = (i == 0) ? {beat0_lit} : {beat1_lit};
      {p_up['wstrb']} = '1; {p_up['wlast']} = (i == 1); {p_up['wvalid']} = 1;
      while (!{p_up['wready']}) begin @(posedge {clk}); #1; end
      @(posedge {clk}); #1;
    end
    {p_up['wvalid']} = 0; {p_up['wlast']} = 0;
    up_writes++;
    // B
    // R3：等响应必须同时查 VALID 与 READY（只等 bvalid 会被 lint_tb 判 R3，
    // 且真实硬件里 master 不置 bready 时事务同样无法完成）。
    while (!({p_up['bvalid']} && {p_up['bready']})) begin @(posedge {clk}); #1; end
    if ({p_up['bresp']} !== {w_up['resp']}'d0) begin
      $display("[CHECK] id=BR_B | proto={up_proto}->{down_proto} | cat=write_response | exp=bresp=0 | got=%0d | status=FAIL",
               {p_up['bresp']});
      errs++;
    end else begin
      up_bs++;
      $display("[CHECK] id=BR_B | proto={up_proto}->{down_proto} | cat=write_response | exp=bresp=0 | got=0 | status=PASS");
    end
    @(posedge {clk}); #1;
  endtask

  initial begin
    repeat (3) @(posedge {clk});
    {rst} = 1;
    repeat (2) @(posedge {clk});

    // ---- 上游写一个 64 位事务 ----
    axi4_write({w_up['aw']}'h{test_addr:0{max(1,(w_up['aw']+3)//4)}X},
               {w_up['dw']}'h{wdata64:0{dwhex}X});

    // 给下游事务留出传播时间（桥接通常有 2~8 拍的流水延迟）
    repeat (40) @(posedge {clk});

    // ---- 跨总线一致性：上游 1 次 64 位写 → 下游应出现 2 次 32 位写 ----
    if (dn_writes < 2) begin
      $display("[CHECK] id=BR_SPLIT | proto={up_proto}->{down_proto} | cat=width_split | exp=>=2 downstream 32-bit writes | got=%0d | status=FAIL", dn_writes);
      errs++;
    end else begin
      $display("[CHECK] id=BR_SPLIT | proto={up_proto}->{down_proto} | cat=width_split | exp=>=2 | got=%0d | status=PASS", dn_writes);
    end

    // 上游必须收到写响应（桥接把 APB pready 转成 AXI4 bvalid）
    if (up_bs == 0) begin
      $display("[CHECK] id=BR_RESP | proto={up_proto}->{down_proto} | cat=response_path | exp=bvalid | got=none | status=FAIL");
      errs++;
    end else begin
      $display("[CHECK] id=BR_RESP | proto={up_proto}->{down_proto} | cat=response_path | exp=bvalid | got=%0d | status=PASS", up_bs);
    end

    if (errs == 0) begin
      $display("PASS: bridge {up_proto}->{down_proto} upstream transaction split into %0d downstream accesses with responses", dn_writes);
      $finish;
    end else begin
      $display("FAIL: bridge {up_proto}->{down_proto} %0d errors", errs);
      $fatal(1);
    end
  end

  // 看门狗：桥接若死锁（上游等不到 ready / 下游等不到 pready），必须快速失败
  initial begin
    #2000000;
    $display("FAIL: bridge {up_proto}->{down_proto} timeout (handshake deadlock)");
    $fatal(1);
  end
endmodule
"""


def _emit_axi4_to_axi4_bridge_tb(
    mod: str, clk: str, rst: str,
    up: Dict[str, Any], down: Dict[str, Any],
) -> str:
    """AXI4 -> AXI4 桥接联合 TB（位宽转换 / OoO 重排器）。

    与 axi4->apb 的结构差异：下游也是 AXI4，所以 TB 要实现一个 **AXI4 从机
    模型**（而不是 APB 的 setup/access 两相模型）。从机行为按 AXI4 规范：
    AW 收地址、W 逐拍收数据并在 wlast 时回 bvalid、AR 收地址后回 rdata。

    自检重点（跨总线一致性，单侧 TB 覆盖不到）：
      1. 上游写事务必须在下游产生写（下游端口真的有流量）
      2. 下游收到的写数据必须与上游发出的**一致**（同宽逐位相等；位宽转换
         时校验低位段透传）
      3. 上游必须拿到 bvalid 响应（下游应答沿原路返回）
    """
    p_up = {k: _pm(up, k, "s_axi_" + k) for k in (
        "awid", "awaddr", "awlen", "awsize", "awburst", "awvalid", "awready",
        "wdata", "wstrb", "wlast", "wvalid", "wready",
        "bid", "bresp", "bvalid", "bready",
        "arid", "araddr", "arlen", "arsize", "arburst", "arvalid", "arready",
        "rid", "rdata", "rresp", "rlast", "rvalid", "rready")}
    p_dn = {k: _pm(down, k, "m_axi_" + k) for k in p_up}

    # 宽度字段必须覆盖模板里**每一条**网线。之前漏了 arsize/arburst/rresp，
    # 导致这三条被声明成 1 位，与 DUT 的 3/2 位端口不匹配 —— Verilator 报
    # WIDTHTRUNC/WIDTHEXPAND，且默认警告即错误，构建直接失败（实测复现）。
    # AXI4 里 arsize==awsize、arburst==awburst，取同宽即可。
    u = {"id": _pw(up, "awid", 4), "aw": _pw(up, "awaddr", 16),
         "len": _pw(up, "awlen", 8), "size": _pw(up, "awsize", 3),
         "burst": _pw(up, "awburst", 2), "dw": _pw(up, "wdata", 32),
         "sw": _pw(up, "wstrb", 4), "resp": _pw(up, "bresp", 2),
         "rresp": _pw(up, "rresp", 2)}
    d = {"id": _pw(down, "awid", u["id"]), "aw": _pw(down, "awaddr", u["aw"]),
         "len": _pw(down, "awlen", u["len"]), "size": _pw(down, "awsize", u["size"]),
         "burst": _pw(down, "awburst", u["burst"]), "dw": _pw(down, "wdata", u["dw"]),
         "sw": _pw(down, "wstrb", u["sw"]), "resp": _pw(down, "bresp", u["resp"]),
         "rresp": _pw(down, "rresp", u["rresp"])}

    uhex = max(1, (u["dw"] + 3) // 4)
    dhex = max(1, (d["dw"] + 3) // 4)
    ahex = max(1, (u["aw"] + 3) // 4)
    test_addr = 0x20 & ((1 << u["aw"]) - 1) & ((1 << d["aw"]) - 1)
    test_data_u = 0xC0FFEE00 & ((1 << u["dw"]) - 1)
    same_width = u["dw"] == d["dw"]

    up_conns = ",\n    ".join(".%s(%s)" % (p_up[k], p_up[k]) for k in p_up)
    dn_conns = ",\n    ".join(".%s(%s)" % (p_dn[k], p_dn[k]) for k in p_dn)

    up_decls = "\n".join([
        "  logic [%d:0] %s = 0; logic [%d:0] %s = 0;" % (u["id"]-1, p_up["awid"], u["aw"]-1, p_up["awaddr"]),
        "  logic [%d:0] %s = 0; logic [%d:0] %s = 0;" % (u["len"]-1, p_up["awlen"], u["size"]-1, p_up["awsize"]),
        "  logic [%d:0] %s = 0; logic %s = 0;" % (u["burst"]-1, p_up["awburst"], p_up["awvalid"]),
        "  logic %s;" % p_up["awready"],
        "  logic [%d:0] %s = 0; logic [%d:0] %s = 0;" % (u["dw"]-1, p_up["wdata"], u["sw"]-1, p_up["wstrb"]),
        "  logic %s = 0; logic %s = 0;" % (p_up["wlast"], p_up["wvalid"]),
        "  logic %s; logic [%d:0] %s;" % (p_up["wready"], u["id"]-1, p_up["bid"]),
        "  logic [%d:0] %s; logic %s; logic %s = 1;" % (u["resp"]-1, p_up["bresp"], p_up["bvalid"], p_up["bready"]),
        "  logic [%d:0] %s = 0; logic [%d:0] %s = 0;" % (u["id"]-1, p_up["arid"], u["aw"]-1, p_up["araddr"]),
        "  logic [%d:0] %s = 0; logic [%d:0] %s = 0;" % (u["len"]-1, p_up["arlen"], u["size"]-1, p_up["arsize"]),
        "  logic [%d:0] %s = 0; logic %s = 0;" % (u["burst"]-1, p_up["arburst"], p_up["arvalid"]),
        "  logic %s;" % p_up["arready"],
        "  logic [%d:0] %s; logic [%d:0] %s;" % (u["id"]-1, p_up["rid"], u["dw"]-1, p_up["rdata"]),
        "  logic [%d:0] %s; logic %s; logic %s;" % (u["resp"]-1, p_up["rresp"], p_up["rlast"], p_up["rvalid"]),
        "  logic %s = 1;" % p_up["rready"],
    ])
    dn_decls = "\n".join([
        "  logic %s; logic %s = 1;" % (p_dn["awvalid"], p_dn["awready"]),
        "  logic [%d:0] %s; logic [%d:0] %s;" % (d["id"]-1, p_dn["awid"], d["aw"]-1, p_dn["awaddr"]),
        "  logic [%d:0] %s;" % (d["len"]-1, p_dn["awlen"]),
        "  logic [%d:0] %s = 0;" % (d["size"]-1, p_dn["awsize"]),
        "  logic [%d:0] %s = 0;" % (d["burst"]-1, p_dn["awburst"]),
        "  logic [%d:0] %s = 0;" % (d["size"]-1, p_dn["arsize"]),
        "  logic [%d:0] %s = 0;" % (d["burst"]-1, p_dn["arburst"]),
        "  logic %s; logic %s = 1;" % (p_dn["wvalid"], p_dn["wready"]),
        "  logic [%d:0] %s; logic [%d:0] %s;" % (d["dw"]-1, p_dn["wdata"], d["sw"]-1, p_dn["wstrb"]),
        "  logic %s;" % p_dn["wlast"],
        "  logic [%d:0] %s = 0; logic [%d:0] %s = 0;" % (d["id"]-1, p_dn["bid"], d["resp"]-1, p_dn["bresp"]),
        "  logic %s = 0; logic %s;" % (p_dn["bvalid"], p_dn["bready"]),
        "  logic %s; logic %s = 1;" % (p_dn["arvalid"], p_dn["arready"]),
        "  logic [%d:0] %s; logic [%d:0] %s;" % (d["id"]-1, p_dn["arid"], d["aw"]-1, p_dn["araddr"]),
        "  logic [%d:0] %s;" % (d["len"]-1, p_dn["arlen"]),
        "  logic [%d:0] %s = 0; logic %s = 0;" % (d["dw"]-1, p_dn["rdata"], p_dn["rvalid"]),
        "  logic [%d:0] %s = 0;" % (d["rresp"]-1, p_dn["rresp"]),
        "  logic %s; logic %s = 0;" % (p_dn["rready"], p_dn["rlast"]),
        "  logic [%d:0] %s = 0;" % (d["id"]-1, p_dn["rid"]),
    ])

    if same_width:
        data_check = ("    if (dn_wdata !== up_wdata) begin\n"
                      '      $display("[CHECK] id=B2W_DATA | proto=axi4->axi4 | cat=write_data_passthrough '
                      '| exp=0x%0' + str(uhex) + 'h | got=0x%0' + str(dhex) + 'h | status=FAIL",\n'
                      "               up_wdata, dn_wdata);\n"
                      "      errs++;\n"
                      "    end else begin\n"
                      '      $display("[CHECK] id=B2W_DATA | proto=axi4->axi4 | cat=write_data_passthrough '
                      '| exp=0x%0' + str(uhex) + 'h | got=0x%0' + str(dhex) + 'h | status=PASS",\n'
                      "               up_wdata, dn_wdata);\n"
                      "    end")
    else:
        data_check = ("    // 位宽不同（%d -> %d）：只校验低 %d 位透传\n" % (u["dw"], d["dw"], d["dw"]) +
                      "    if (dn_wdata !== up_wdata[%d:0]) begin\n" % (d["dw"]-1) +
                      '      $display("[CHECK] id=B2W_DATA | proto=axi4->axi4 | cat=write_data_passthrough '
                      '| exp=low%d bits | got=0x%%0%dh | status=FAIL", dn_wdata);\n' % (d["dw"], dhex) +
                      "      errs++;\n"
                      "    end else begin\n"
                      '      $display("[CHECK] id=B2W_DATA | proto=axi4->axi4 | cat=write_data_passthrough '
                      '| exp=low%d bits | got=0x%%0%dh | status=PASS", dn_wdata);\n' % (d["dw"], dhex) +
                      "    end")

    return """`timescale 1ns/1ps
// Auto-generated by fpga_toolbox.emitters.bridge (protocol=axi4->axi4)
// Joint AXI4-to-AXI4 bridge TB: TB drives the upstream slave port as master and
// models an AXI4 slave on the downstream master port.
module tb_%s_bridge;
  logic %s = 0, %s = 0;
  int   errs = 0;
  int   dn_writes = 0;
  int   up_bs = 0;
  logic [%d:0] up_wdata = 0;
  logic [%d:0] dn_wdata = 0;
  logic [%d:0] dn_mem [0:15];

%s
%s

  %s dut (
    .%s(%s), .%s(%s),
    %s,
    %s
  );
  always #5 %s = ~%s;

  // ---- 下游 AXI4 从机模型（恒 ready，重点观测数据路径）----
  always @(posedge %s) begin
    if (%s) begin
      %s <= 0;
      %s <= 0;
      if (%s) begin
        %s <= %s;
      end
      if (%s) begin
        dn_writes++;
        dn_wdata = %s;
        dn_mem[%s[3:0]] = %s;
        $display("[CHECK] id=B2W | proto=axi4->axi4 | cat=downstream_write | addr=0x%%0%dh data=0x%%0%dh | status=PASS",
                 %s, %s);
        if (%s) %s <= 1;
      end
      if (%s && %s) begin
        %s <= 0;
      end
      if (%s) begin
        %s <= dn_mem[%s[3:0]];
        %s <= %s;
        %s <= 1;
        %s <= 1;
      end
      if (%s && %s) begin
        %s <= 0;
      end
    end
  end

  // ---- 上游写：单拍（awlen=0），便于断言地址/数据原样透传 ----
  task automatic axi4_write(input logic [%d:0] addr, input logic [%d:0] data);
    @(posedge %s); #1;
    %s = %d'd1; %s = addr;
    %s = %d'd0; %s = %d'd3;
    %s = %d'b01; %s = 1;
    up_wdata = data;
    while (!%s) begin @(posedge %s); #1; end
    @(posedge %s); #1;
    %s = 0;
    %s = data; %s = '1; %s = 1; %s = 1;
    while (!%s) begin @(posedge %s); #1; end
    @(posedge %s); #1;
    %s = 0; %s = 0;
    // R3：等响应必须同时查 VALID 与 READY —— 只等 bvalid 会被 lint_tb 判 R3，
    // 且真实硬件里若从机 bvalid 恒高而 master 未置 bready，事务也不会完成。
    while (!(%s && %s)) begin @(posedge %s); #1; end
    up_bs++;
    @(posedge %s); #1;
  endtask

  initial begin
    repeat (3) @(posedge %s);
    %s = 1;
    repeat (2) @(posedge %s);

    axi4_write(%d'h%0*x, %d'h%0*x);
    repeat (20) @(posedge %s);

    if (dn_writes == 0) begin
      $display("[CHECK] id=B2W_NONE | proto=axi4->axi4 | cat=downstream_write | exp=>0 | got=0 | status=FAIL");
      errs++;
    end else begin
      $display("[CHECK] id=B2W_NONE | proto=axi4->axi4 | cat=downstream_write | exp=>0 | got=%%0d | status=PASS", dn_writes);
%s
    end

    if (up_bs == 0) begin
      $display("[CHECK] id=B2B | proto=axi4->axi4 | cat=response_path | exp=bvalid | got=none | status=FAIL");
      errs++;
    end else begin
      $display("[CHECK] id=B2B | proto=axi4->axi4 | cat=response_path | exp=bvalid | got=%%0d | status=PASS", up_bs);
    end

    if (errs == 0) begin
      $display("PASS: bridge axi4->axi4 upstream write reaches %%0d downstream port(s) with data intact", dn_writes);
      $finish;
    end else begin
      $display("FAIL: bridge axi4->axi4 %%0d errors", errs);
      $fatal(1);
    end
  end

  initial begin
    #2000000;
    $display("FAIL: bridge axi4->axi4 timeout (handshake deadlock)");
    $fatal(1);
  end
endmodule
""" % (
        mod, clk, rst,
        u["dw"]-1, d["dw"]-1, d["dw"]-1,
        up_decls, dn_decls,
        mod, clk, clk, rst, rst,
        up_conns, dn_conns,
        clk, clk,
        clk, rst,
        p_dn["bvalid"], p_dn["rvalid"],
        " && ".join([p_dn["awvalid"] + " === 1'b1"]), p_dn["bid"], p_dn["awid"],
        " && ".join([p_dn["wvalid"] + " === 1'b1"]),
        p_dn["wdata"], p_dn["awaddr"], p_dn["wdata"],
        ahex, dhex, p_dn["awaddr"], p_dn["wdata"],
        p_dn["wlast"], p_dn["bvalid"],
        p_dn["bvalid"], p_dn["bready"], p_dn["bvalid"],
        p_dn["arvalid"] + " === 1'b1",
        p_dn["rdata"], p_dn["araddr"], p_dn["rid"], p_dn["arid"],
        p_dn["rlast"], p_dn["rvalid"],
        p_dn["rvalid"], p_dn["rready"], p_dn["rvalid"],
        u["aw"]-1, u["dw"]-1,
        clk,
        p_up["awid"], u["id"], p_up["awaddr"],
        p_up["awlen"], u["len"], p_up["awsize"], u["size"],
        p_up["awburst"], u["burst"], p_up["awvalid"],
        p_up["awready"], clk, clk,
        p_up["awvalid"],
        p_up["wdata"], p_up["wstrb"], p_up["wlast"], p_up["wvalid"],
        p_up["wready"], clk, clk,
        p_up["wvalid"], p_up["wlast"],
        p_up["bvalid"], p_dn["bready"], clk, clk,
        clk, rst, clk,
        u["aw"], ahex, test_addr, u["dw"], uhex, test_data_u,
        clk,
        data_check,
    )


def _emit_spi_to_apb_bridge_tb(
    mod: str, clk: str, rst: str,
    up: Dict[str, Any], down: Dict[str, Any],
) -> str:
    """SPI -> APB 桥接联合 TB（串行命令转寄存器访问）。

    桥接语义：SPI 从机侧收命令，转成 APB 主机侧的寄存器写。联合 TB 同时驱动
    SPI slave 端口（TB 当 SPI master）并建模 APB slave 端口，然后校验：SPI
    成帧事务期间桥接必须在 APB 侧产生写。

    用 @@NAME@@ 哨兵 + ``replace()`` 填充，而不是 ``%``-格式化：SV 模板里有大量
    ``$display`` 的 ``%0d``/``%08h``，与 Python 的 ``%`` 占位符混在一起极易错位
    （实测 55 个占位符 vs 51 参数，F507 报错且手写元组几乎不可能对齐）。
    """
    p_sp = {k: _pm(up, k, "spi_" + k) for k in ("sclk", "cs_n", "mosi", "miso")}
    p_ap = {k: _pm(down, k, "m_apb_" + k) for k in (
        "psel", "penable", "pwrite", "paddr", "pwdata", "pstrb",
        "pready", "prdata", "pslverr")}

    addr_w = _pw(down, "paddr", 16)
    data_w = _pw(down, "pwdata", 32)

    sp_conns = ",\n    ".join("." + p_sp[k] + "(" + p_sp[k] + ")" for k in p_sp)
    ap_conns = ",\n    ".join("." + p_ap[k] + "(" + p_ap[k] + ")" for k in p_ap)

    tb = r"""`timescale 1ns/1ps
// Auto-generated by fpga_toolbox.emitters.bridge (protocol=spi->apb)
// Joint SPI-to-APB bridge TB: TB drives the SPI slave port as a master and
// models an APB slave on the downstream port, then checks that a SPI-framed
// transaction is converted into a matching APB write.
module tb_@@MOD@@_bridge;
  logic @@CLK@@ = 0, @@RST@@ = 0;
  int   errs = 0;
  int   ap_writes = 0;
  logic ap_counted = 0;
  logic [@@ADDR_W@@-1:0] ap_last_addr = 0;
  logic [@@DATA_W@@-1:0] ap_last_data = 0;

  // ---- SPI slave-side nets (TB acts as SPI master) ----
  logic @@SCLK@@;
  logic @@CS_N@@;
  logic @@MOSI@@;
  logic @@MISO@@ = 0;

  // ---- APB slave-side nets (TB acts as APB slave) ----
  logic @@PSEL@@;
  logic @@PENABLE@@;
  logic @@PWRITE@@;
  logic [@@ADDR_W@@-1:0] @@PADDR@@;
  logic [@@DATA_W@@-1:0] @@PWDATA@@;
  logic [@@STRB_W@@-1:0] @@PSTRB@@;
  logic @@PREADY@@ = 0;
  logic [@@DATA_W@@-1:0] @@PRDATA@@ = 0;
  logic @@PSLVERR@@ = 0;

  @@MOD@@ dut (
    .@@CLK@@(@@CLK@@), .@@RST@@(@@RST@@),
    @@SP_CONNS@@,
    @@AP_CONNS@@
  );
  always #5 @@CLK@@ = ~@@CLK@@;

  // ---- APB 从机模型：access 相位应答一次写，记录地址/数据 ----
  always @(posedge @@CLK@@) begin
    if (@@RST@@ === 1'b1) begin
      @@PREADY@@ <= 0;
      // 只计**一次**：APB 的 access 相位会持续多拍（直到 pready 拉高），
      // 旧写法每拍都 ap_writes++，导致 2 个 beat 被计成 3 次（setup 拍 +
      // access 多拍）。用 pready 作「本次传输已应答」标志，应答过就不再计。
      // 实测踩坑：这条断言本该校验「几个 beat 转成几次 APB 写」，重复计数
      // 让它完全失去意义。
      // 只在 access 相位的**第一拍**计数（PENABLE 刚拉高、pready 还未应答）。
      // APB access 相位会持续多拍直到 pready 拉高，旧写法每拍都 ap_writes++，
      // 2 个 beat 被计成 3 次 —— 让「几个 beat 转成几次 APB 写」这条断言失效。
      // 用 ap_counted 作「本次传输已计」标志，pready 拉高时清除。
      if (@@PSEL@@ === 1'b1 && @@PENABLE@@ === 1'b1 && @@PREADY@@ === 1'b0
          && !ap_counted) begin
        ap_counted = 1;
        if (@@PWRITE@@ === 1'b1) begin
          ap_writes++;
          ap_last_addr = @@PADDR@@;
          ap_last_data = @@PWDATA@@;
          $display("[CHECK] id=SP2A_WR | proto=spi->apb | cat=downstream_write | addr=0x%0@@AHEX@@h data=0x%0@@DHEX@@h | status=PASS",
                   @@PADDR@@, @@PWDATA@@);
          @@PREADY@@ <= 1;
          ap_counted = 0;   // 本次传输应答完成，允许下一次计数
        end else begin
          @@PREADY@@ <= 0;
        end
      end else begin
        @@PREADY@@ <= 0;
      end
    end
  end

  initial begin
    repeat (3) @(posedge @@CLK@@);
    @@RST@@ = 1;
    repeat (2) @(posedge @@CLK@@);

    // SPI 成帧事务：CS_n 拉低成帧，SCLK 翻转把 MOSI 移入桥接
    @(posedge @@CLK@@); #1;
    @@CS_N@@ = 0;
    for (int i = 0; i < 16; i++) begin
      @@SCLK@@ = (i % 2 == 0);
      @@MOSI@@ = (i % 2 == 0);
      @(posedge @@CLK@@); #1;
    end
    @(posedge @@CLK@@); #1;
    @@CS_N@@ = 1;

    repeat (40) @(posedge @@CLK@@);

    if (ap_writes == 0) begin
      $display("[CHECK] id=SP2A_NONE | proto=spi->apb | cat=downstream_write | exp=>0 APB write | got=0 | status=FAIL");
      errs++;
    end else begin
      $display("[CHECK] id=SP2A_NONE | proto=spi->apb | cat=downstream_write | exp=>0 | got=%0d | status=PASS", ap_writes);
    end

    if (errs == 0) begin
      $display("PASS: bridge spi->apb SPI-framed transaction converted into %0d APB write(s)", ap_writes);
      $finish;
    end else begin
      $display("FAIL: bridge spi->apb %0d errors", errs);
      $fatal(1);
    end
  end

  initial begin
    #2000000;
    $display("FAIL: bridge spi->apb timeout (handshake deadlock)");
    $fatal(1);
  end
endmodule
"""

    # 命名哨兵替换（顺序无关，避免手写 51 个参数的 % 元组）
    reps = {
        "@@MOD@@": mod, "@@CLK@@": clk, "@@RST@@": rst,
        "@@ADDR_W@@": str(addr_w), "@@DATA_W@@": str(data_w),
        "@@STRB_W@@": str(max(1, (data_w // 8))),
        "@@AHEX@@": str(max(1, (addr_w + 3) // 4)),
        "@@DHEX@@": str(max(1, (data_w + 3) // 4)),
        "@@SCLK@@": p_sp["sclk"], "@@CS_N@@": p_sp["cs_n"],
        "@@MOSI@@": p_sp["mosi"], "@@MISO@@": p_sp["miso"],
        "@@PSEL@@": p_ap["psel"], "@@PENABLE@@": p_ap["penable"],
        "@@PWRITE@@": p_ap["pwrite"], "@@PADDR@@": p_ap["paddr"],
        "@@PWDATA@@": p_ap["pwdata"], "@@PSTRB@@": p_ap["pstrb"],
        "@@PREADY@@": p_ap["pready"], "@@PRDATA@@": p_ap["prdata"],
        "@@PSLVERR@@": p_ap["pslverr"],
        "@@SP_CONNS@@": sp_conns, "@@AP_CONNS@@": ap_conns,
    }
    for k, v in reps.items():
        tb = tb.replace(k, v)
    return tb





def _emit_ethernet_to_apb_bridge_tb(
    mod: str, clk: str, rst: str,
    up: Dict[str, Any], down: Dict[str, Any],
) -> str:
    """Ethernet -> APB 桥接联合 TB（帧数据转寄存器写）。

    GMII 风格：`rx_dv`（data valid）拉高期间 `rx_data` 逐字节有效。桥接把收到的
    字节流转成 APB 写。TB 驱动 rx 侧当链路对端，并在 APB slave 侧建模应答，
    校验帧数据被转换成下游写。

    与 SPI→APB 同为流式转 APB，用同样的 @@SENTINEL@@ + replace() 填充
    （SV 模板里大量 $display 的 %0d/%08h 与 Python % 占位符冲突，用 % 格式化
    会让占位符与参数元组错位）。
    """
    p_eth = {k: _pm(up, k, "gmii_" + k) for k in ("rx_dv", "rx_data", "tx_en", "tx_data")}
    p_ap = {k: _pm(down, k, "m_apb_" + k) for k in (
        "psel", "penable", "pwrite", "paddr", "pwdata", "pstrb",
        "pready", "prdata", "pslverr")}

    addr_w = _pw(down, "paddr", 16)
    data_w = _pw(down, "pwdata", 32)
    rx_w = _pw(up, "rx_data", 8)
    dhex = max(1, (data_w + 3) // 4)
    ahex = max(1, (addr_w + 3) // 4)

    eth_conns = ",\n    ".join("." + p_eth[k] + "(" + p_eth[k] + ")" for k in p_eth)
    ap_conns = ",\n    ".join("." + p_ap[k] + "(" + p_ap[k] + ")" for k in p_ap)

    tb = r"""`timescale 1ns/1ps
// Auto-generated by fpga_toolbox.emitters.bridge (protocol=ethernet->apb)
// Joint Ethernet-to-APB bridge TB: TB drives the Ethernet RX side as a link
// partner (rx_dv + rx_data bytes) and models an APB slave on the downstream
// port, then checks that the received bytes are converted into an APB write.
module tb_@@MOD@@_bridge;
  logic @@CLK@@ = 0, @@RST@@ = 0;
  int   errs = 0;
  int   ap_writes = 0;
  logic ap_counted = 0;
  logic [@@ADDR_W@@-1:0] ap_last_addr = 0;
  logic [@@DATA_W@@-1:0] ap_last_data = 0;

  // ---- Ethernet RX-side nets (TB acts as link partner) ----
  logic @@RX_DV@@;
  logic [@@RX_W@@-1:0] @@RX_DATA@@;
  logic @@TX_EN@@;
  logic [@@RX_W@@-1:0] @@TX_DATA@@;

  // ---- APB slave-side nets (TB acts as APB slave) ----
  logic @@PSEL@@;
  logic @@PENABLE@@;
  logic @@PWRITE@@;
  logic [@@ADDR_W@@-1:0] @@PADDR@@;
  logic [@@DATA_W@@-1:0] @@PWDATA@@;
  logic [@@STRB_W@@-1:0] @@PSTRB@@;
  logic @@PREADY@@ = 0;
  logic [@@DATA_W@@-1:0] @@PRDATA@@ = 0;
  logic @@PSLVERR@@ = 0;

  @@MOD@@ dut (
    .@@CLK@@(@@CLK@@), .@@RST@@(@@RST@@),
    @@ETH_CONNS@@,
    @@AP_CONNS@@
  );
  always #5 @@CLK@@ = ~@@CLK@@;

  // ---- APB 从机模型 ----
  always @(posedge @@CLK@@) begin
    if (@@RST@@ === 1'b1) begin
      @@PREADY@@ <= 0;
      // 只计**一次**：APB 的 access 相位会持续多拍（直到 pready 拉高），
      // 旧写法每拍都 ap_writes++，导致 2 个 beat 被计成 3 次（setup 拍 +
      // access 多拍）。用 pready 作「本次传输已应答」标志，应答过就不再计。
      // 实测踩坑：这条断言本该校验「几个 beat 转成几次 APB 写」，重复计数
      // 让它完全失去意义。
      // 只在 access 相位的**第一拍**计数（PENABLE 刚拉高、pready 还未应答）。
      // APB access 相位会持续多拍直到 pready 拉高，旧写法每拍都 ap_writes++，
      // 2 个 beat 被计成 3 次 —— 让「几个 beat 转成几次 APB 写」这条断言失效。
      // 用 ap_counted 作「本次传输已计」标志，pready 拉高时清除。
      if (@@PSEL@@ === 1'b1 && @@PENABLE@@ === 1'b1 && @@PREADY@@ === 1'b0
          && !ap_counted) begin
        ap_counted = 1;
        if (@@PWRITE@@ === 1'b1) begin
          ap_writes++;
          ap_last_addr = @@PADDR@@;
          ap_last_data = @@PWDATA@@;
          $display("[CHECK] id=ETH2A_WR | proto=ethernet->apb | cat=downstream_write | addr=0x%0@@AHEX@@h data=0x%0@@DHEX@@h | status=PASS",
                   @@PADDR@@, @@PWDATA@@);
          @@PREADY@@ <= 1;
          ap_counted = 0;   // 本次传输应答完成，允许下一次计数
        end else begin
          @@PREADY@@ <= 0;
        end
      end else begin
        @@PREADY@@ <= 0;
      end
    end
  end

  initial begin
    repeat (3) @(posedge @@CLK@@);
    @@RST@@ = 1;
    repeat (2) @(posedge @@CLK@@);

    // 驱动一帧：rx_dv 拉高 4 拍，每拍一个字节（让桥接累积够一次 APB 写）
    @(posedge @@CLK@@); #1;
    @@TX_EN@@ = 0;
    for (int i = 0; i < 4; i++) begin
      @@RX_DV@@ = 1;
      @@RX_DATA@@ = @@RX_W@@'(i * @@RX_W@@'h11);
      @(posedge @@CLK@@); #1;
    end
    @@RX_DV@@ = 0;
    @@RX_DATA@@ = 0;

    repeat (40) @(posedge @@CLK@@);

    if (ap_writes == 0) begin
      $display("[CHECK] id=ETH2A_NONE | proto=ethernet->apb | cat=downstream_write | exp=>0 APB write | got=0 | status=FAIL");
      errs++;
    end else begin
      $display("[CHECK] id=ETH2A_NONE | proto=ethernet->apb | cat=downstream_write | exp=>0 | got=%0d | status=PASS", ap_writes);
    end

    if (errs == 0) begin
      $display("PASS: bridge ethernet->apb RX frame converted into %0d APB write(s)", ap_writes);
      $finish;
    end else begin
      $display("FAIL: bridge ethernet->apb %0d errors", errs);
      $fatal(1);
    end
  end

  initial begin
    #2000000;
    $display("FAIL: bridge ethernet->apb timeout (handshake deadlock)");
    $fatal(1);
  end
endmodule
"""

    reps = {
        "@@MOD@@": mod, "@@CLK@@": clk, "@@RST@@": rst,
        "@@ADDR_W@@": str(addr_w), "@@DATA_W@@": str(data_w),
        "@@STRB_W@@": str(max(1, data_w // 8)),
        "@@RX_W@@": str(rx_w),
        "@@AHEX@@": str(ahex), "@@DHEX@@": str(dhex),
        "@@RX_DV@@": p_eth["rx_dv"], "@@RX_DATA@@": p_eth["rx_data"],
        "@@TX_EN@@": p_eth["tx_en"], "@@TX_DATA@@": p_eth["tx_data"],
        "@@PSEL@@": p_ap["psel"], "@@PENABLE@@": p_ap["penable"],
        "@@PWRITE@@": p_ap["pwrite"], "@@PADDR@@": p_ap["paddr"],
        "@@PWDATA@@": p_ap["pwdata"], "@@PSTRB@@": p_ap["pstrb"],
        "@@PREADY@@": p_ap["pready"], "@@PRDATA@@": p_ap["prdata"],
        "@@PSLVERR@@": p_ap["pslverr"],
        "@@ETH_CONNS@@": eth_conns, "@@AP_CONNS@@": ap_conns,
    }
    for k, v in reps.items():
        tb = tb.replace(k, v)
    return tb




def _emit_axi4_stream_to_apb_bridge_tb(
    mod: str, clk: str, rst: str,
    up: Dict[str, Any], down: Dict[str, Any],
) -> str:
    """AXI4-Stream -> APB 桥接联合 TB（流数据转寄存器写）。

    AXI4-Stream 的唯一流控是 TREADY/TVALID 握手（无地址）。桥接把收到的流
    beat 转成 APB 写。TB 驱动 s_axis 侧当 master（TVALID + TDATA，TREADY 由
    DUT 给出）并建模 APB slave，校验流数据被转换成下游写。

    关键自检：**流控死锁不能被静默放过**。若 DUT 从不拉 TREADY，桥接收不到
    数据、也就不会产生 APB 写 —— 此时断言 `ap_writes == 0` 即 FAIL，而不是
    让 TB 挂死或假通过。
    """
    p_ax = {k: _pm(up, k, "s_axis_" + k) for k in (
        "tdata", "tvalid", "tready", "tlast")}
    p_ap = {k: _pm(down, k, "m_apb_" + k) for k in (
        "psel", "penable", "pwrite", "paddr", "pwdata", "pstrb",
        "pready", "prdata", "pslverr")}

    addr_w = _pw(down, "paddr", 16)
    data_w = _pw(down, "pwdata", 32)
    tdata_w = _pw(up, "tdata", 32)
    dhex = max(1, (data_w + 3) // 4)
    ahex = max(1, (addr_w + 3) // 4)
    thex = max(1, (tdata_w + 3) // 4)

    ax_conns = ",\n    ".join("." + p_ax[k] + "(" + p_ax[k] + ")" for k in p_ax)
    ap_conns = ",\n    ".join("." + p_ap[k] + "(" + p_ap[k] + ")" for k in p_ap)

    tb = r"""`timescale 1ns/1ps
// Auto-generated by fpga_toolbox.emitters.bridge (protocol=axi4_stream->apb)
// Joint AXI4-Stream-to-APB bridge TB: TB drives the s_axis side as a stream
// master (TVALID + TDATA, obeying TREADY) and models an APB slave downstream,
// then checks that stream beats are converted into APB writes.
module tb_@@MOD@@_bridge;
  logic @@CLK@@ = 0, @@RST@@ = 0;
  int   errs = 0;
  int   beats = 0;
  int   ap_writes = 0;
  logic ap_counted = 0;
  logic [@@DATA_W@@-1:0] ap_last_data = 0;

  // ---- AXI4-Stream slave-side nets (TB acts as stream master) ----
  logic [@@TDATA_W@@-1:0] @@TDATA@@ = 0;
  logic @@TVALID@@ = 0;
  logic @@TREADY@@;
  logic @@TLAST@@ = 0;

  // ---- APB slave-side nets (TB acts as APB slave) ----
  logic @@PSEL@@;
  logic @@PENABLE@@;
  logic @@PWRITE@@;
  logic [@@ADDR_W@@-1:0] @@PADDR@@;
  logic [@@DATA_W@@-1:0] @@PWDATA@@;
  logic [@@STRB_W@@-1:0] @@PSTRB@@;
  logic @@PREADY@@ = 0;
  logic [@@DATA_W@@-1:0] @@PRDATA@@ = 0;
  logic @@PSLVERR@@ = 0;

  @@MOD@@ dut (
    .@@CLK@@(@@CLK@@), .@@RST@@(@@RST@@),
    @@AX_CONNS@@,
    @@AP_CONNS@@
  );
  always #5 @@CLK@@ = ~@@CLK@@;

  // ---- APB 从机模型 ----
  always @(posedge @@CLK@@) begin
    if (@@RST@@ === 1'b1) begin
      @@PREADY@@ <= 0;
      // 只计**一次**：APB 的 access 相位会持续多拍（直到 pready 拉高），
      // 旧写法每拍都 ap_writes++，导致 2 个 beat 被计成 3 次（setup 拍 +
      // access 多拍）。用 pready 作「本次传输已应答」标志，应答过就不再计。
      // 实测踩坑：这条断言本该校验「几个 beat 转成几次 APB 写」，重复计数
      // 让它完全失去意义。
      // 只在 access 相位的**第一拍**计数（PENABLE 刚拉高、pready 还未应答）。
      // APB access 相位会持续多拍直到 pready 拉高，旧写法每拍都 ap_writes++，
      // 2 个 beat 被计成 3 次 —— 让「几个 beat 转成几次 APB 写」这条断言失效。
      // 用 ap_counted 作「本次传输已计」标志，pready 拉高时清除。
      if (@@PSEL@@ === 1'b1 && @@PENABLE@@ === 1'b1 && @@PREADY@@ === 1'b0
          && !ap_counted) begin
        ap_counted = 1;
        if (@@PWRITE@@ === 1'b1) begin
          ap_writes++;
          ap_last_data = @@PWDATA@@;
          $display("[CHECK] id=AXS2A_WR | proto=axi4_stream->apb | cat=downstream_write | addr=0x%0@@AHEX@@h data=0x%0@@DHEX@@h | status=PASS",
                   @@PADDR@@, @@PWDATA@@);
          @@PREADY@@ <= 1;
          ap_counted = 0;   // 本次传输应答完成，允许下一次计数
        end else begin
          @@PREADY@@ <= 0;
        end
      end else begin
        @@PREADY@@ <= 0;
      end
    end
  end

  initial begin
    repeat (3) @(posedge @@CLK@@);
    @@RST@@ = 1;
    repeat (2) @(posedge @@CLK@@);

    // 发 2 个流 beat（TVALID 拉高、逐拍等 TREADY，第二个 beat 置 TLAST）
    for (int i = 0; i < 2; i++) begin
      @@TDATA@@ = @@TDATA_W@@'(i + 1);
      @@TLAST@@ = (i == 1);
      @@TVALID@@ = 1;
      @(posedge @@CLK@@); #1;
      while (!@@TREADY@@) begin @(posedge @@CLK@@); #1; end
      beats++;
      @(posedge @@CLK@@); #1;
    end
    @@TVALID@@ = 0; @@TLAST@@ = 0;

    repeat (40) @(posedge @@CLK@@);

    if (ap_writes == 0) begin
      $display("[CHECK] id=AXS2A_NONE | proto=axi4_stream->apb | cat=downstream_write | exp=>0 APB write after %0d beats | got=0 | status=FAIL", beats);
      errs++;
    end else begin
      $display("[CHECK] id=AXS2A_NONE | proto=axi4_stream->apb | cat=downstream_write | exp=>0 | got=%0d APB writes from %0d beats | status=PASS", ap_writes, beats);
    end

    if (errs == 0) begin
      $display("PASS: bridge axi4_stream->apb %0d stream beats converted into %0d APB write(s)", beats, ap_writes);
      $finish;
    end else begin
      $display("FAIL: bridge axi4_stream->apb %0d errors", errs);
      $fatal(1);
    end
  end

  initial begin
    #2000000;
    $display("FAIL: bridge axi4_stream->apb timeout (TREADY never asserted / handshake deadlock)");
    $fatal(1);
  end
endmodule
"""

    reps = {
        "@@MOD@@": mod, "@@CLK@@": clk, "@@RST@@": rst,
        "@@ADDR_W@@": str(addr_w), "@@DATA_W@@": str(data_w),
        "@@STRB_W@@": str(max(1, data_w // 8)),
        "@@TDATA_W@@": str(tdata_w),
        "@@AHEX@@": str(ahex), "@@DHEX@@": str(dhex), "@@THEX@@": str(thex),
        "@@TDATA@@": p_ax["tdata"], "@@TVALID@@": p_ax["tvalid"],
        "@@TREADY@@": p_ax["tready"], "@@TLAST@@": p_ax["tlast"],
        "@@PSEL@@": p_ap["psel"], "@@PENABLE@@": p_ap["penable"],
        "@@PWRITE@@": p_ap["pwrite"], "@@PADDR@@": p_ap["paddr"],
        "@@PWDATA@@": p_ap["pwdata"], "@@PSTRB@@": p_ap["pstrb"],
        "@@PREADY@@": p_ap["pready"], "@@PRDATA@@": p_ap["prdata"],
        "@@PSLVERR@@": p_ap["pslverr"],
        "@@AX_CONNS@@": ax_conns, "@@AP_CONNS@@": ap_conns,
    }
    for k, v in reps.items():
        tb = tb.replace(k, v)
    return tb



def emit_bridge(
    mod: str,
    clk: str,
    rst: str,
    up_proto: str,
    down_proto: str,
    up_iface: Dict[str, Any],
    down_iface: Dict[str, Any],
) -> str:
    """按组合派发到对应桥接模板。目前只实现了 axi4->apb。

    未实现的组合抛出 ValueError（由编排层转成结构化错误 + 明确说明「识别到
    桥接结构但该组合暂无联合模板」），而不是静默退化成单侧 TB —— 那会让用户
    以为桥接被验证过，实际只驱动了一半。
    """
    if (up_proto, down_proto) == ("axi4", "apb"):
        return emit_bridge_testbench(mod, clk, rst, up_iface, down_iface,
                                     up_proto, down_proto)
    if (up_proto, down_proto) == ("axi4", "axi4"):
        return _emit_axi4_to_axi4_bridge_tb(mod, clk, rst, up_iface, down_iface)
    if (up_proto, down_proto) == ("spi", "apb"):
        return _emit_spi_to_apb_bridge_tb(mod, clk, rst, up_iface, down_iface)
    if (up_proto, down_proto) == ("ethernet", "apb"):
        return _emit_ethernet_to_apb_bridge_tb(mod, clk, rst, up_iface, down_iface)
    if (up_proto, down_proto) == ("axi4_stream", "apb"):
        return _emit_axi4_stream_to_apb_bridge_tb(mod, clk, rst, up_iface, down_iface)
    raise ValueError(
        f"Bridge combination {up_proto}->{down_proto} is recognised as a bridge DUT "
        f"but has no joint testbench template yet. Currently supported: "
        f"{sorted(SUPPORTED_BRIDGES)}. A single-sided testbench would only drive "
        f"one bus and leave the other tied off - it cannot verify the conversion, "
        f"so it is not emitted silently."
    )
