// SPDX-License-Identifier: Apache-2.0
// Standard AMBA AXI4-Lite (32-bit) IEEE 802.3 CRC-32 Hardware Accelerator RTL.
// Directly loaded into USP via automatic pin-level VerilatorAxi4LiteAdapter (no MMIO32 shim).
module axi4_lite_crc32_rtl (
    input  logic        aclk,
    input  logic        aresetn,
    // AXI4-Lite Write Address Channel
    input  logic        s_axi_awvalid,
    output logic        s_axi_awready,
    input  logic [11:0] s_axi_awaddr,
    input  logic [2:0]  s_axi_awprot,
    // AXI4-Lite Write Data Channel
    input  logic        s_axi_wvalid,
    output logic        s_axi_wready,
    input  logic [31:0] s_axi_wdata,
    input  logic [3:0]  s_axi_wstrb,
    // AXI4-Lite Write Response Channel
    output logic        s_axi_bvalid,
    input  logic        s_axi_bready,
    output logic [1:0]  s_axi_bresp,
    // AXI4-Lite Read Address Channel
    input  logic        s_axi_arvalid,
    output logic        s_axi_arready,
    input  logic [11:0] s_axi_araddr,
    input  logic [2:0]  s_axi_arprot,
    // AXI4-Lite Read Data Channel
    output logic        s_axi_rvalid,
    input  logic        s_axi_rready,
    output logic [31:0] s_axi_rdata,
    output logic [1:0]  s_axi_rresp,
    // Interrupt output
    output logic        irq_o
);
    localparam logic [1:0] RESP_OKAY   = 2'b00;
    localparam logic [1:0] RESP_SLVERR = 2'b10;

    logic [31:0] crc_q;
    logic [31:0] byte_cnt_q;
    logic        irq_q;

    typedef enum logic [1:0] {W_IDLE, W_WAIT, W_RESP} w_state_t;
    typedef enum logic [1:0] {R_IDLE, R_WAIT, R_RESP} r_state_t;
    w_state_t    w_state_q;
    r_state_t    r_state_q;
    logic [11:0] aw_addr_q;
    logic        aw_seen_q, w_seen_q;
    logic [31:0] w_data_q;
    logic [1:0]  b_resp_q;
    logic [31:0] r_data_q;
    logic [1:0]  r_resp_q;

    assign irq_o         = irq_q;
    assign s_axi_awready = (w_state_q == W_WAIT) && !aw_seen_q;
    assign s_axi_wready  = (w_state_q == W_WAIT) && !w_seen_q;
    assign s_axi_bvalid  = (w_state_q == W_RESP);
    assign s_axi_bresp   = b_resp_q;

    assign s_axi_arready = (r_state_q == R_WAIT);
    assign s_axi_rvalid  = (r_state_q == R_RESP);
    assign s_axi_rdata   = r_data_q;
    assign s_axi_rresp   = r_resp_q;

    // Bit-reflected IEEE 802.3 CRC-32 update for 1 byte (poly 0xEDB88320)
    function automatic logic [31:0] crc32_byte(input logic [31:0] c_in, input logic [7:0] d_byte);
        logic [31:0] c;
        c = c_in ^ {24'd0, d_byte};
        for (int i = 0; i < 8; i++) begin
            if (c[0]) c = (c >> 1) ^ 32'hEDB88320;
            else      c = (c >> 1);
        end
        return c;
    endfunction

    always_ff @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            crc_q      <= 32'hFFFFFFFF;
            byte_cnt_q <= 32'd0;
            irq_q      <= 1'b0;
            w_state_q  <= W_IDLE;
            r_state_q  <= R_IDLE;
            aw_addr_q  <= 12'd0;
            aw_seen_q  <= 1'b0;
            w_seen_q   <= 1'b0;
            w_data_q   <= 32'd0;
            b_resp_q   <= RESP_OKAY;
            r_data_q   <= 32'd0;
            r_resp_q   <= RESP_OKAY;
        end else begin
            // Write channel FSM (1-cycle wait-state, independent AW/W capture)
            case (w_state_q)
                W_IDLE: begin
                    if (s_axi_awvalid || s_axi_wvalid) begin
                        aw_seen_q <= 1'b0;
                        w_seen_q  <= 1'b0;
                        w_state_q <= W_WAIT;
                    end
                end
                W_WAIT: begin
                    logic        got_aw, got_w;
                    logic [11:0] eff_addr;
                    logic [31:0] eff_data;
                    got_aw   = aw_seen_q || (s_axi_awvalid && s_axi_awready);
                    got_w    = w_seen_q  || (s_axi_wvalid  && s_axi_wready);
                    eff_addr = aw_seen_q ? aw_addr_q : s_axi_awaddr;
                    eff_data = w_seen_q  ? w_data_q  : s_axi_wdata;
                    if (s_axi_awvalid && s_axi_awready) begin
                        aw_addr_q <= s_axi_awaddr;
                        aw_seen_q <= 1'b1;
                    end
                    if (s_axi_wvalid && s_axi_wready) begin
                        w_data_q <= s_axi_wdata;
                        w_seen_q <= 1'b1;
                    end
                    if (got_aw && got_w) begin
                        b_resp_q <= RESP_OKAY;
                        case (eff_addr[7:0])
                            8'h00: begin
                                if (eff_data[0]) begin
                                    crc_q      <= 32'hFFFFFFFF;
                                    byte_cnt_q <= 32'd0;
                                    irq_q      <= 1'b0;
                                end
                                if (eff_data[1]) irq_q <= 1'b1;
                                if (eff_data[2]) irq_q <= 1'b0;
                            end
                            8'h04: begin
                                crc_q      <= crc32_byte(crc_q, eff_data[7:0]);
                                byte_cnt_q <= byte_cnt_q + 32'd1;
                            end
                            8'h10: begin
                                logic [31:0] c0, c1, c2, c3;
                                c0 = crc32_byte(crc_q, eff_data[7:0]);
                                c1 = crc32_byte(c0,    eff_data[15:8]);
                                c2 = crc32_byte(c1,    eff_data[23:16]);
                                c3 = crc32_byte(c2,    eff_data[31:24]);
                                crc_q      <= c3;
                                byte_cnt_q <= byte_cnt_q + 32'd4;
                            end
                            default: b_resp_q <= RESP_SLVERR;
                        endcase
                        w_state_q <= W_RESP;
                    end
                end
                W_RESP: begin
                    if (s_axi_bready) begin
                        w_state_q <= W_IDLE;
                        aw_seen_q <= 1'b0;
                        w_seen_q  <= 1'b0;
                    end
                end
                default: w_state_q <= W_IDLE;
            endcase

            // Read channel FSM (1-cycle AR wait-state -> R)
            case (r_state_q)
                R_IDLE: begin
                    if (s_axi_arvalid) begin
                        r_state_q <= R_WAIT;
                    end
                end
                R_WAIT: begin
                    if (s_axi_arvalid) begin
                        r_resp_q <= RESP_OKAY;
                        case (s_axi_araddr[7:0])
                            8'h00: r_data_q <= {30'd0, irq_q, 1'b0};
                            8'h08: r_data_q <= crc_q ^ 32'hFFFFFFFF;
                            8'h0C: r_data_q <= byte_cnt_q;
                            default: begin
                                r_data_q <= 32'd0;
                                r_resp_q <= RESP_SLVERR;
                            end
                        endcase
                        r_state_q <= R_RESP;
                    end
                end
                R_RESP: begin
                    if (s_axi_rready) begin
                        r_state_q <= R_IDLE;
                    end
                end
                default: r_state_q <= R_IDLE;
            endcase
        end
    end
endmodule
