// SPDX-License-Identifier: Apache-2.0
// Synthesizable 32-bit SPI Master Controller RTL with shift-register FSM & internal loopback.
// Original clean-room RTL co-simulated via Verilator 5.032 inside Unified_Hetero_MultiPlatform_SoC.
//
// Register Map:
//   0x00 CTRL       (RW: bit0=enable, bit1=loopback, [15:8]=clk_div; W bit2=start_xfer)
//   0x04 STATUS     (RO: bit0=busy, bit1=rx_valid)
//   0x08 TX_DATA    (RW: 32-bit transmit shift word)
//   0x0C RX_DATA    (RO: 32-bit received word)
//   0x10 IRQ_STATUS (R/W1C: bit0=xfer_done_irq)
//   0x14 XFER_COUNT (RO: total completed 32-bit SPI transfers)
module spi_ctrl_rtl (
    input  logic        clk_i,
    input  logic        rst_ni,
    input  logic        req_valid_i,
    input  logic        req_write_i,
    input  logic [7:0]  req_addr_i,
    input  logic [31:0] req_wdata_i,
    output logic [31:0] rsp_rdata_o,
    output logic        rsp_err_o,
    output logic        busy_o,
    output logic        irq_o,
    output logic [63:0] rtl_cycles_o
);
    logic [31:0] ctrl_q, tx_data_q, rx_data_q, xfer_cnt_q;
    logic [31:0] shift_tx_q, shift_rx_q;
    logic [5:0]  bit_cnt_q;
    logic        busy_q, rx_valid_q, irq_q;
    logic [63:0] cycle_cnt_q;

    assign busy_o       = busy_q;
    assign irq_o        = irq_q;
    assign rtl_cycles_o = cycle_cnt_q;

    always_comb begin
        rsp_rdata_o = 32'd0;
        rsp_err_o   = 1'b0;
        if (req_valid_i) begin
            if (req_write_i) begin
                case (req_addr_i)
                    8'h00, 8'h08, 8'h10: rsp_err_o = 1'b0;
                    default: rsp_err_o = 1'b1;
                endcase
            end else begin
                case (req_addr_i)
                    8'h00: rsp_rdata_o = ctrl_q;
                    8'h04: rsp_rdata_o = {30'd0, rx_valid_q, busy_q};
                    8'h08: rsp_rdata_o = tx_data_q;
                    8'h0C: rsp_rdata_o = rx_data_q;
                    8'h10: rsp_rdata_o = {31'd0, irq_q};
                    8'h14: rsp_rdata_o = xfer_cnt_q;
                    default: rsp_err_o = 1'b1;
                endcase
            end
        end
    end

    logic mosi_bit_w, miso_bit_w;
    assign mosi_bit_w = shift_tx_q[31];
    assign miso_bit_w = ctrl_q[1] ? mosi_bit_w : (~mosi_bit_w);

    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            ctrl_q      <= 32'd0;
            tx_data_q   <= 32'd0;
            rx_data_q   <= 32'd0;
            xfer_cnt_q  <= 32'd0;
            shift_tx_q  <= 32'd0;
            shift_rx_q  <= 32'd0;
            bit_cnt_q   <= 6'd0;
            busy_q      <= 1'b0;
            rx_valid_q  <= 1'b0;
            irq_q       <= 1'b0;
            cycle_cnt_q <= 64'd0;
        end else begin
            cycle_cnt_q <= cycle_cnt_q + 64'd1;
            if (busy_q) begin
                shift_tx_q <= {shift_tx_q[30:0], 1'b0};
                shift_rx_q <= {shift_rx_q[30:0], miso_bit_w};
                if (bit_cnt_q == 6'd31) begin
                    rx_data_q  <= {shift_rx_q[30:0], miso_bit_w};
                    busy_q     <= 1'b0;
                    rx_valid_q <= 1'b1;
                    irq_q      <= 1'b1;
                    xfer_cnt_q <= xfer_cnt_q + 32'd1;
                end else begin
                    bit_cnt_q <= bit_cnt_q + 6'd1;
                end
            end else if (req_valid_i && req_write_i && !rsp_err_o) begin
                case (req_addr_i)
                    8'h00: begin
                        ctrl_q <= req_wdata_i & 32'hFFFF_FFFB;
                        if (req_wdata_i[2] && req_wdata_i[0]) begin
                            shift_tx_q <= tx_data_q;
                            shift_rx_q <= 32'd0;
                            bit_cnt_q  <= 6'd0;
                            busy_q     <= 1'b1;
                            rx_valid_q <= 1'b0;
                        end
                    end
                    8'h08: tx_data_q <= req_wdata_i;
                    8'h10: if (req_wdata_i[0]) irq_q <= 1'b0;
                    default: ;
                endcase
            end
        end
    end
endmodule
