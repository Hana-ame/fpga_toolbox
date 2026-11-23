`timescale 1ns/1ps
module pcie_tlp_repack_loopback #(
  parameter int GEN   = 4,
  parameter int LANES = 4,
  parameter int DW    = 128
) (
  input  logic          clk,
  input  logic          rst_n,
  // Interface 1: RX TLP Ingress (128-bit packed: [127:96]=H0, [95:64]=H1, [63:32]=H2/AddrLo, [31:0]=Data0 or AddrLo)
  input  logic          pcie_rx_tvalid,
  output logic          pcie_rx_tready,
  input  logic [DW-1:0] pcie_rx_tdata,
  input  logic          pcie_rx_tlast,
  // Interface 2: TX TLP Egress (Split CplD / Loopback completions)
  output logic          pcie_tx_tvalid,
  input  logic          pcie_tx_tready,
  output logic [DW-1:0] pcie_tx_tdata,
  output logic          pcie_tx_tlast,
  output logic          pcie_link_up,
  output logic [3:0]    pcie_cfg_speed,
  output logic [5:0]    pcie_cfg_lanes
);
  // 256-byte BAR memory (64 x 32-bit DW)
  logic [31:0] bar_mem [0:63];

  // Header fields in H0=[127:96]: [127:125]=fmt, [124:120]=type, [105:96]=dw_len
  // H1=[95:64]: [87:80]=tag, [71:68]=last_be, [67:64]=first_be
  // 3DW (fmt[0]==0): H2=[63:32]=addr32, Payload0=[31:0] (if 2-DW write, next beat [31:0]=Payload1)
  // 4DW (fmt[0]==1): H2=[63:32]=addr_hi, H3=[31:0]=addr_lo (payload follows on next beat: [63:32]=DW0, [31:0]=DW1)
  typedef enum logic [2:0] { S_IDLE, S_4DW_PAY, S_3DW_PAY2, S_CPL_DW0, S_CPL_DW1 } st_t;
  st_t         st;
  logic [7:0]  lat_tag;
  logic [3:0]  lat_fbe, lat_lbe;
  logic [9:0]  lat_len;
  logic [31:0] lat_addr;

  assign pcie_link_up   = rst_n;
  assign pcie_cfg_speed = 4'(GEN);
  assign pcie_cfg_lanes = 6'(LANES);
  assign pcie_rx_tready = (st == S_IDLE || st == S_4DW_PAY || st == S_3DW_PAY2) && !pcie_tx_tvalid;

  wire [2:0]  rx_fmt  = pcie_rx_tdata[127:125];
  wire [9:0]  rx_len  = pcie_rx_tdata[105:96];
  wire [7:0]  rx_tag  = pcie_rx_tdata[87:80];
  wire [3:0]  rx_lbe  = pcie_rx_tdata[71:68];
  wire [3:0]  rx_fbe  = pcie_rx_tdata[67:64];
  wire [31:0] rx_h2   = pcie_rx_tdata[63:32];
  wire [31:0] rx_h3   = pcie_rx_tdata[31:0];
  logic       _ulast;
  assign _ulast = pcie_rx_tlast;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      for (int i = 0; i < 64; i++) bar_mem[i] <= 32'h0;
      st             <= S_IDLE;
      lat_tag <= '0; lat_fbe <= '0; lat_lbe <= '0; lat_len <= '0; lat_addr <= '0;
      pcie_tx_tvalid <= 1'b0;
      pcie_tx_tdata  <= '0;
      pcie_tx_tlast  <= 1'b0;
    end else begin
      if (pcie_tx_tvalid && pcie_tx_tready) begin
        pcie_tx_tvalid <= 1'b0;
        pcie_tx_tlast  <= 1'b0;
      end

      case (st)
        S_IDLE: begin
          if (pcie_rx_tvalid && pcie_rx_tready) begin
            lat_tag <= rx_tag;
            lat_fbe <= rx_fbe;
            lat_lbe <= rx_lbe;
            lat_len <= rx_len;
            if (rx_fmt == 3'b010) begin
              // 3DW MWr: addr=rx_h2, DW0=rx_h3
              lat_addr <= rx_h2;
              for (int b = 0; b < 4; b++)
                if (rx_fbe[b]) bar_mem[rx_h2[7:2]][b*8 +: 8] <= rx_h3[b*8 +: 8];
              if (rx_len == 10'd2) st <= S_3DW_PAY2;
            end else if (rx_fmt == 3'b011) begin
              // 4DW MWr: addr_lo=rx_h3, payload arrives on next beat
              lat_addr <= rx_h3;
              st       <= S_4DW_PAY;
            end else if (rx_fmt == 3'b000 || rx_fmt == 3'b001) begin
              // 3DW MRd (addr=rx_h2) or 4DW MRd (addr=rx_h3) -> emit CplD (split if len==2!)
              lat_addr <= (rx_fmt == 3'b001) ? rx_h3 : rx_h2;
              st       <= S_CPL_DW0;
            end
          end
        end

        S_3DW_PAY2: begin
          if (pcie_rx_tvalid && pcie_rx_tready) begin
            for (int b = 0; b < 4; b++)
              if (lat_lbe[b]) bar_mem[lat_addr[7:2] + 6'd1][b*8 +: 8] <= pcie_rx_tdata[b*8 +: 8];
            st <= S_IDLE;
          end
        end

        S_4DW_PAY: begin
          if (pcie_rx_tvalid && pcie_rx_tready) begin
            // Beat carries [63:32]=DW0 (masked by lat_fbe), [31:0]=DW1 (masked by lat_lbe if len==2)
            for (int b = 0; b < 4; b++)
              if (lat_fbe[b]) bar_mem[lat_addr[7:2]][b*8 +: 8] <= pcie_rx_tdata[32 + b*8 +: 8];
            if (lat_len == 10'd2) begin
              for (int b = 0; b < 4; b++)
                if (lat_lbe[b]) bar_mem[lat_addr[7:2] + 6'd1][b*8 +: 8] <= pcie_rx_tdata[b*8 +: 8];
            end
            st <= S_IDLE;
          end
        end

        // Emit CplD packet 0: H0={8'h4A, 14'd0, 10'd1}, H1={8'd0, lat_tag, 16'(lat_addr)}, H2=lat_addr, DW0=bar_mem[addr]
        S_CPL_DW0: begin
          if (!pcie_tx_tvalid) begin
            pcie_tx_tvalid <= 1'b1;
            pcie_tx_tlast  <= 1'b1;
            pcie_tx_tdata  <= {{8'h4A, 14'd0, 10'd1}, {8'd0, lat_tag, lat_addr[15:0]}, lat_addr, bar_mem[lat_addr[7:2]]};
            st             <= (lat_len == 10'd2) ? S_CPL_DW1 : S_IDLE;
          end
        end

        // Split Completion packet 1 (for 2-DW MRd): lower_addr = lat_addr + 4
        S_CPL_DW1: begin
          if (!pcie_tx_tvalid) begin
            pcie_tx_tvalid <= 1'b1;
            pcie_tx_tlast  <= 1'b1;
            pcie_tx_tdata  <= {{8'h4A, 14'd0, 10'd1}, {8'd0, lat_tag, (lat_addr[15:0] + 16'd4)}, (lat_addr + 32'd4), bar_mem[lat_addr[7:2] + 6'd1]};
            st             <= S_IDLE;
          end
        end

        default: st <= S_IDLE;
      endcase
    end
  end
endmodule
