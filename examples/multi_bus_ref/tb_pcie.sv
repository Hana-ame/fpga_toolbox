`timescale 1ns/1ps
module tb_pcie #(
  parameter int GEN   = 4,
  parameter int LANES = 4
);
  localparam int DW = (LANES == 16) ? 512 : ((LANES == 4) ? 128 : 64);
  logic          clk = 0, rst_n = 0;
  logic          pcie_rx_tvalid = 0, pcie_rx_tready, pcie_rx_tlast = 0;
  logic [DW-1:0] pcie_rx_tdata = '0, pcie_tx_tdata;
  logic          pcie_tx_tvalid, pcie_tx_tready = 1, pcie_tx_tlast;
  logic          pcie_link_up;
  logic [3:0]    pcie_cfg_speed;
  logic [5:0]    pcie_cfg_lanes;
  int            errs = 0;

  pcie_tlp_ep #(.GEN(GEN), .LANES(LANES), .DW(DW)) dut (.*);
  always #5 clk = ~clk;

  initial begin
    repeat (3) @(posedge clk);
    rst_n = 1;
    @(posedge clk); #1;
    if (!pcie_link_up || pcie_cfg_speed !== 4'(GEN) || pcie_cfg_lanes !== 6'(LANES)) begin
      $display("[CHECK] id=PCIE_G%0dX%0d_001 | proto=PCIe-Gen%0d-x%0d | cat=link_cfg | tx=LTSSM link_up check (DW=%0d) | exp=up=1,Gen%0d,x%0d | got=up=%0b,Gen%0d,x%0d | status=FAIL",
               GEN, LANES, GEN, LANES, DW, GEN, LANES, pcie_link_up, pcie_cfg_speed, pcie_cfg_lanes);
      errs++;
    end else begin
      $display("[CHECK] id=PCIE_G%0dX%0d_001 | proto=PCIe-Gen%0d-x%0d | cat=link_cfg | tx=LTSSM link_up check (DW=%0d) | exp=up=1,Gen%0d,x%0d | got=up=%0b,Gen%0d,x%0d | status=PASS",
               GEN, LANES, GEN, LANES, DW, GEN, LANES, pcie_link_up, pcie_cfg_speed, pcie_cfg_lanes);
    end
    // Send MWr TLP (cmd=0x40, tag=0x07, addr=0x0010, data=0x55AA1234)
    pcie_rx_tvalid = 1; pcie_rx_tlast = 1;
    pcie_rx_tdata = '0; pcie_rx_tdata[63:0] = {8'h40, 8'h07, 16'h0010, 32'h55AA1234};
    @(posedge clk); #1;
    // Send MRd TLP (cmd=0x00, tag=0x3C, addr=0x0010)
    pcie_rx_tdata[63:0] = {8'h00, 8'h3C, 16'h0010, 32'h0};
    @(posedge clk); #1;
    pcie_rx_tvalid = 0; pcie_rx_tlast = 0;
    if (!pcie_tx_tvalid || !pcie_tx_tlast || pcie_tx_tdata[63:0] !== {8'h4A, 8'h3C, 16'h0010, 32'h55AA1234}) begin
      $display("[CHECK] id=PCIE_G%0dX%0d_002 | proto=PCIe-Gen%0d-x%0d | cat=tlp_mwr_mrd_cpld | tx=MWr(0x0010,0x55AA1234)+MRd(tag=0x3C) | exp=CplD(0x4A3C001055AA1234) | got=0x%016h(v=%0b,last=%0b) | status=FAIL",
               GEN, LANES, GEN, LANES, pcie_tx_tdata[63:0], pcie_tx_tvalid, pcie_tx_tlast);
      errs++;
    end else begin
      $display("[CHECK] id=PCIE_G%0dX%0d_002 | proto=PCIe-Gen%0d-x%0d | cat=tlp_mwr_mrd_cpld | tx=MWr(0x0010,0x55AA1234)+MRd(tag=0x3C) | exp=CplD(0x4A3C001055AA1234) | got=0x%016h(v=%0b,last=%0b) | status=PASS",
               GEN, LANES, GEN, LANES, pcie_tx_tdata[63:0], pcie_tx_tvalid, pcie_tx_tlast);
    end
    if (errs == 0) begin $display("PASS: pcie Gen%0d x%0d (DW=%0d) MWr/MRd/CplD passed", GEN, LANES, DW); $finish; end
    else begin $display("FAIL: pcie Gen%0d x%0d %0d errors", GEN, LANES, errs); $fatal(1); end
  end
endmodule
