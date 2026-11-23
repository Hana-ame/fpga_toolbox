`timescale 1ns/1ps
module axi4_64_to_apb_32_bridge #(
  parameter int IDW = 4,
  parameter int AW  = 16
) (
  input  logic           aclk,
  input  logic           aresetn,
  // Interface 1: Upstream AXI4-Full 64-bit Slave
  input  logic [IDW-1:0] s_axi_awid,
  input  logic [AW-1:0]  s_axi_awaddr,
  input  logic [7:0]     s_axi_awlen,
  input  logic [2:0]     s_axi_awsize,
  input  logic [1:0]     s_axi_awburst,
  input  logic           s_axi_awvalid,
  output logic           s_axi_awready,
  input  logic [63:0]    s_axi_wdata,
  input  logic [7:0]     s_axi_wstrb,
  input  logic           s_axi_wlast,
  input  logic           s_axi_wvalid,
  output logic           s_axi_wready,
  output logic [IDW-1:0] s_axi_bid,
  output logic [1:0]     s_axi_bresp,
  output logic           s_axi_bvalid,
  input  logic           s_axi_bready,
  input  logic [IDW-1:0] s_axi_arid,
  input  logic [AW-1:0]  s_axi_araddr,
  input  logic [7:0]     s_axi_arlen,
  input  logic [2:0]     s_axi_arsize,
  input  logic [1:0]     s_axi_arburst,
  input  logic           s_axi_arvalid,
  output logic           s_axi_arready,
  output logic [IDW-1:0] s_axi_rid,
  output logic [63:0]    s_axi_rdata,
  output logic [1:0]     s_axi_rresp,
  output logic           s_axi_rlast,
  output logic           s_axi_rvalid,
  input  logic           s_axi_rready,
  // Interface 2: Downstream APB4 32-bit Master
  output logic           m_apb_psel,
  output logic           m_apb_penable,
  output logic           m_apb_pwrite,
  output logic [AW-1:0]  m_apb_paddr,
  output logic [31:0]    m_apb_pwdata,
  output logic [3:0]     m_apb_pstrb,
  input  logic           m_apb_pready,
  input  logic [31:0]    m_apb_prdata,
  input  logic           m_apb_pslverr
);
  typedef enum logic [3:0] {
    ST_IDLE,
    ST_W_GET,
    ST_W_LO_SETUP, ST_W_LO_ACCESS,
    ST_W_HI_SETUP, ST_W_HI_ACCESS,
    ST_W_RESP,
    ST_R_LO_SETUP, ST_R_LO_ACCESS,
    ST_R_HI_SETUP, ST_R_HI_ACCESS,
    ST_R_PUSH
  } state_t;

  state_t         st;
  logic [IDW-1:0] cur_id;
  logic [AW-1:0]  cur_addr;
  logic [7:0]     cur_rem;
  logic [1:0]     cur_burst;
  logic [63:0]    wbuf_data;
  logic [7:0]     wbuf_strb;
  logic           wbuf_last;
  logic [31:0]    rbuf_lo, rbuf_hi;
  logic           acc_err;
  logic           _u;
  assign _u = (^s_axi_awsize) ^ (^s_axi_arsize);

  assign s_axi_awready = (st == ST_IDLE);
  assign s_axi_arready = (st == ST_IDLE) && !s_axi_awvalid;
  assign s_axi_wready  = (st == ST_W_GET);

  always_ff @(posedge aclk) begin
    if (!aresetn) begin
      st            <= ST_IDLE;
      cur_id        <= '0; cur_addr <= '0; cur_rem <= '0; cur_burst <= '0;
      wbuf_data     <= '0; wbuf_strb <= '0; wbuf_last <= 1'b0;
      rbuf_lo       <= '0; rbuf_hi <= '0; acc_err <= 1'b0;
      m_apb_psel    <= 1'b0; m_apb_penable <= 1'b0; m_apb_pwrite <= 1'b0;
      m_apb_paddr   <= '0;   m_apb_pwdata  <= '0;   m_apb_pstrb  <= '0;
      s_axi_bvalid  <= 1'b0; s_axi_bresp   <= 2'b00; s_axi_bid   <= '0;
      s_axi_rvalid  <= 1'b0; s_axi_rresp   <= 2'b00; s_axi_rid   <= '0;
      s_axi_rdata   <= '0;   s_axi_rlast   <= 1'b0;
    end else begin
      case (st)
        ST_IDLE: begin
          m_apb_psel <= 1'b0; m_apb_penable <= 1'b0;
          if (s_axi_awvalid) begin
            cur_id    <= s_axi_awid;
            cur_addr  <= s_axi_awaddr;
            cur_rem   <= s_axi_awlen;
            cur_burst <= s_axi_awburst;
            acc_err   <= 1'b0;
            st        <= ST_W_GET;
          end else if (s_axi_arvalid) begin
            cur_id    <= s_axi_arid;
            cur_addr  <= s_axi_araddr;
            cur_rem   <= s_axi_arlen;
            cur_burst <= s_axi_arburst;
            acc_err   <= 1'b0;
            st        <= ST_R_LO_SETUP;
          end
        end

        ST_W_GET: begin
          if (s_axi_wvalid) begin
            wbuf_data <= s_axi_wdata;
            wbuf_strb <= s_axi_wstrb;
            wbuf_last <= s_axi_wlast;
            st        <= ST_W_LO_SETUP;
          end
        end

        // Split 64-bit AXI beat -> low 32-bit APB write at cur_addr
        ST_W_LO_SETUP: begin
          m_apb_psel    <= 1'b1;
          m_apb_penable <= 1'b0;
          m_apb_pwrite  <= 1'b1;
          m_apb_paddr   <= {cur_addr[AW-1:3], 3'b000};
          m_apb_pwdata  <= wbuf_data[31:0];
          m_apb_pstrb   <= wbuf_strb[3:0];
          st            <= ST_W_LO_ACCESS;
        end
        ST_W_LO_ACCESS: begin
          m_apb_penable <= 1'b1;
          if (m_apb_penable && m_apb_pready) begin
            acc_err       <= acc_err | m_apb_pslverr;
            m_apb_psel    <= 1'b0;
            m_apb_penable <= 1'b0;
            st            <= ST_W_HI_SETUP;
          end
        end

        // Split 64-bit AXI beat -> high 32-bit APB write at cur_addr + 4
        ST_W_HI_SETUP: begin
          m_apb_psel    <= 1'b1;
          m_apb_penable <= 1'b0;
          m_apb_pwrite  <= 1'b1;
          m_apb_paddr   <= {cur_addr[AW-1:3], 3'b100}; // +4 byte offset
          m_apb_pwdata  <= wbuf_data[63:32];
          m_apb_pstrb   <= wbuf_strb[7:4];
          st            <= ST_W_HI_ACCESS;
        end
        ST_W_HI_ACCESS: begin
          m_apb_penable <= 1'b1;
          if (m_apb_penable && m_apb_pready) begin
            acc_err       <= acc_err | m_apb_pslverr;
            m_apb_psel    <= 1'b0;
            m_apb_penable <= 1'b0;
            if (wbuf_last || cur_rem == 8'd0) begin
              s_axi_bvalid <= 1'b1;
              s_axi_bid    <= cur_id;
              s_axi_bresp  <= (acc_err | m_apb_pslverr) ? 2'b10 : 2'b00;
              st           <= ST_W_RESP;
            end else begin
              cur_rem  <= cur_rem - 8'd1;
              // Support INCR (01) and WRAP4 (10, 32-byte boundary)
              if (cur_burst == 2'b01)
                cur_addr <= cur_addr + 16'd8;
              else if (cur_burst == 2'b10)
                cur_addr <= {cur_addr[AW-1:5], (cur_addr[4:3] + 2'd1), 3'b000};
              st <= ST_W_GET;
            end
          end
        end

        ST_W_RESP: begin
          if (s_axi_bvalid && s_axi_bready) begin
            s_axi_bvalid <= 1'b0;
            st           <= ST_IDLE;
          end
        end

        // Read low 32-bit from APB at cur_addr, then high 32-bit at cur_addr + 4
        ST_R_LO_SETUP: begin
          m_apb_psel    <= 1'b1;
          m_apb_penable <= 1'b0;
          m_apb_pwrite  <= 1'b0;
          m_apb_paddr   <= {cur_addr[AW-1:3], 3'b000};
          m_apb_pstrb   <= 4'h0;
          st            <= ST_R_LO_ACCESS;
        end
        ST_R_LO_ACCESS: begin
          m_apb_penable <= 1'b1;
          if (m_apb_penable && m_apb_pready) begin
            rbuf_lo       <= m_apb_prdata;
            acc_err       <= acc_err | m_apb_pslverr;
            m_apb_psel    <= 1'b0;
            m_apb_penable <= 1'b0;
            st            <= ST_R_HI_SETUP;
          end
        end
        ST_R_HI_SETUP: begin
          m_apb_psel    <= 1'b1;
          m_apb_penable <= 1'b0;
          m_apb_pwrite  <= 1'b0;
          m_apb_paddr   <= {cur_addr[AW-1:3], 3'b100};
          st            <= ST_R_HI_ACCESS;
        end
        ST_R_HI_ACCESS: begin
          m_apb_penable <= 1'b1;
          if (m_apb_penable && m_apb_pready) begin
            rbuf_hi       <= m_apb_prdata;
            acc_err       <= acc_err | m_apb_pslverr;
            m_apb_psel    <= 1'b0;
            m_apb_penable <= 1'b0;
            s_axi_rvalid  <= 1'b1;
            s_axi_rid     <= cur_id;
            s_axi_rdata   <= {m_apb_prdata, rbuf_lo};
            s_axi_rresp   <= (acc_err | m_apb_pslverr) ? 2'b10 : 2'b00;
            s_axi_rlast   <= (cur_rem == 8'd0);
            st            <= ST_R_PUSH;
          end
        end
        ST_R_PUSH: begin
          if (s_axi_rvalid && s_axi_rready) begin
            s_axi_rvalid <= 1'b0;
            s_axi_rlast  <= 1'b0;
            if (cur_rem == 8'd0) begin
              st <= ST_IDLE;
            end else begin
              cur_rem <= cur_rem - 8'd1;
              if (cur_burst == 2'b01)
                cur_addr <= cur_addr + 16'd8;
              else if (cur_burst == 2'b10)
                cur_addr <= {cur_addr[AW-1:5], (cur_addr[4:3] + 2'd1), 3'b000};
              st <= ST_R_LO_SETUP;
            end
          end
        end
        default: st <= ST_IDLE;
      endcase
    end
  end
endmodule
