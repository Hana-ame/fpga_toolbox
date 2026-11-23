`timescale 1ns/1ps
module tb_apb_pcie_axi4_ltssm;
  logic        clk = 0, por_n = 0;
  logic        phy_rx_detect = 1, phy_deskew_ok = 0, pm_l1_req = 0, pm_l1_ack;
  logic [2:0]  ltssm_state;
  logic        pcie_link_up, tlp_reject_nol0;
  logic [3:0]  pcie_cfg_speed;
  logic [5:0]  pcie_cfg_lanes;
  logic        pcie_rx_tvalid = 0, pcie_rx_tready, pcie_rx_tlast = 0;
  logic [63:0] pcie_rx_tdata = 0, pcie_tx_tdata;
  logic        pcie_tx_tvalid, pcie_tx_tready = 1, pcie_tx_tlast;
  logic        s_apb_psel = 0, s_apb_penable = 0, s_apb_pwrite = 0, s_apb_pready, s_apb_pslverr;
  logic [15:0] s_apb_paddr = 0;
  logic [31:0] s_apb_pwdata = 0, s_apb_prdata;
  logic [3:0]  s_apb_pstrb = 4'hF;
  int          errs = 0;

  apb_pcie_axi4_ltssm_fabric #(.GEN(4), .LANES(4)) dut (.*);
  always #5 clk = ~clk;

  task automatic apb_op(input logic wr, input logic [15:0] addr, input logic [31:0] wdata, output logic [31:0] rdata);
    @(posedge clk); #1;
    s_apb_psel = 1; s_apb_penable = 0; s_apb_pwrite = wr; s_apb_paddr = addr; s_apb_pwdata = wdata; s_apb_pstrb = 4'hF;
    @(posedge clk); #1; s_apb_penable = 1;
    do begin @(posedge clk); #1; end while (!s_apb_pready);
    rdata = s_apb_prdata;
    s_apb_psel = 0; s_apb_penable = 0;
  endtask

  initial begin
    logic [31:0] rd;
    repeat (2) @(posedge clk); #1;
    por_n = 1;

    // 1. Power-On LTSSM Negotiation FAILURE (phy_deskew_ok=0 -> DETECT->POLLING->CONFIG->FAIL)
    repeat (10) @(posedge clk); #1;
    pcie_rx_tvalid = 1; pcie_rx_tlast = 1; pcie_rx_tdata = {8'h40, 8'h01, 16'h0010, 32'hBAD0_0001};
    @(posedge clk); #1;
    pcie_rx_tvalid = 0;
    if (ltssm_state !== 3'd5 || pcie_link_up || !tlp_reject_nol0) begin
      $display("[CHECK] id=LTSSM_NEG_ERR_001 | proto=PCIe-APB-MEM | cat=ltssm_negotiation_fail | tx=POR deskew=0 -> TLP | exp=NEG_ERR(5),reject=1 | got=st=%0d,reject=%0b | status=FAIL", ltssm_state, tlp_reject_nol0); errs++;
    end else begin
      $display("[CHECK] id=LTSSM_NEG_ERR_001 | proto=PCIe-APB-MEM | cat=ltssm_negotiation_fail | tx=POR deskew=0 -> TLP | exp=NEG_ERR(5),reject=1 | got=st=%0d,reject=%0b | status=PASS", ltssm_state, tlp_reject_nol0);
    end

    // 2. Recover to L0 (phy_deskew_ok=1 -> DETECT->POLLING->CONFIG->L0)
    phy_deskew_ok = 1;
    repeat (10) @(posedge clk); #1;
    if (ltssm_state !== 3'd3 || !pcie_link_up || pcie_cfg_speed !== 4'd4) begin
      $display("[CHECK] id=LTSSM_L0_002 | proto=PCIe-APB-MEM | cat=ltssm_por_to_l0 | tx=Recovery->L0 | exp=L0(3),Gen4 | got=st=%0d,Gen%0d | status=FAIL", ltssm_state, pcie_cfg_speed); errs++;
    end else begin
      $display("[CHECK] id=LTSSM_L0_002 | proto=PCIe-APB-MEM | cat=ltssm_por_to_l0 | tx=Recovery->L0 | exp=L0(3),Gen4 | got=st=%0d,Gen%0d | status=PASS", ltssm_state, pcie_cfg_speed);
    end

    // 3. Cross-Protocol Built-In MEM: PCIe MWr 0x0010 -> APB RD; APB WR 0x0020 -> PCIe MRd CplD
    pcie_rx_tvalid = 1; pcie_rx_tlast = 1; pcie_rx_tdata = {8'h40, 8'h11, 16'h0010, 32'hCAFE_1234};
    @(posedge clk); #1; pcie_rx_tvalid = 0;
    apb_op(1'b0, 16'h0010, 32'h0, rd);
    apb_op(1'b1, 16'h0020, 32'h55AA_7788, rd);
    pcie_rx_tvalid = 1; pcie_rx_tlast = 1; pcie_rx_tdata = {8'h00, 8'h22, 16'h0020, 32'h0};
    @(posedge clk); #1; pcie_rx_tvalid = 0;
    if (pcie_tx_tdata[31:0] !== 32'h55AA_7788) begin
      $display("[CHECK] id=PCIE_APB_XMEM_003 | proto=PCIe-APB-MEM | cat=cross_proto_shared_mem | tx=PCIe<->APB shared MEM | exp=0x55AA7788 | got=0x%08h | status=FAIL", pcie_tx_tdata[31:0]); errs++;
    end else begin
      $display("[CHECK] id=PCIE_APB_XMEM_003 | proto=PCIe-APB-MEM | cat=cross_proto_shared_mem | tx=PCIe<->APB shared MEM | exp=0x55AA7788 | got=0x%08h | status=PASS", pcie_tx_tdata[31:0]);
    end

    // 4. L0 <-> L1 Sleep Handshake + Reject in L1 + Wake Retention
    pm_l1_req = 1;
    repeat (3) @(posedge clk); #1;
    pcie_rx_tvalid = 1; pcie_rx_tdata = {8'h40, 8'h33, 16'h0010, 32'h0};
    @(posedge clk); #1; pcie_rx_tvalid = 0;
    if (!pm_l1_ack || ltssm_state !== 3'd4 || !tlp_reject_nol0) begin
      $display("[CHECK] id=PCIE_L1_PM_004 | proto=PCIe-APB-MEM | cat=l0_l1_sleep_wake | tx=L1 entry + TLP reject | exp=L1(4),ack=1,rej=1 | got=st=%0d,ack=%0b,rej=%0b | status=FAIL", ltssm_state, pm_l1_ack, tlp_reject_nol0); errs++;
    end else begin
      pm_l1_req = 0; repeat (3) @(posedge clk); #1;
      apb_op(1'b0, 16'h0010, 32'h0, rd);
      if (ltssm_state !== 3'd3 || rd !== 32'hCAFE_1234) begin
        $display("[CHECK] id=PCIE_L1_PM_004 | proto=PCIe-APB-MEM | cat=l0_l1_sleep_wake | tx=L1 wake retention | exp=L0(3),0xCAFE1234 | got=st=%0d,0x%08h | status=FAIL", ltssm_state, rd); errs++;
      end else begin
        $display("[CHECK] id=PCIE_L1_PM_004 | proto=PCIe-APB-MEM | cat=l0_l1_sleep_wake | tx=L1(rej)->L0 wake->MEM | exp=L0(3),0xCAFE1234 | got=st=%0d,0x%08h | status=PASS", ltssm_state, rd);
      end
    end

    if (errs == 0) begin $display("PASS: apb_pcie_axi4_ltssm all 4 LTSSM/fail/XMEM/L1 checks passed"); $finish; end
    else begin $display("FAIL: apb_pcie_axi4_ltssm %0d errors", errs); $fatal(1); end
  end
endmodule
