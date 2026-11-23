// SPDX-License-Identifier: Apache-2.0
// Synthesizable I2C Master Controller + Internal Loopback Slave FSM RTL.
// Original clean-room RTL co-simulated via Verilator 5.032 inside Unified_Hetero_MultiPlatform_SoC.
// NOTE: Distinct from upstream OpenTitan hw/ip/i2c RTL.
//
// Register Map:
//   0x00 CTRL        (RW: bit0=enable, bit1=loopback_slave; W bit2=start_xfer)
//   0x04 STATUS      (RO: bit0=busy, bit1=ack_ok, bit2=done)
//   0x08 TARGET_ADDR (RW: 7-bit I2C slave address [6:0])
//   0x0C TX_DATA     (RW: 8-bit transmit byte)
//   0x10 RX_DATA     (RO: 8-bit received loopback byte)
//   0x14 IRQ_STATUS  (R/W1C: bit0=xfer_done_irq)
//   0x18 XFER_COUNT  (RO: total completed I2C transfers)
module i2c_ctrl_rtl (
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
    localparam logic [2:0] ST_IDLE     = 3'd0;
    localparam logic [2:0] ST_START    = 3'd1;
    localparam logic [2:0] ST_ADDR     = 3'd2;
    localparam logic [2:0] ST_ACK_ADDR = 3'd3;
    localparam logic [2:0] ST_DATA     = 3'd4;
    localparam logic [2:0] ST_ACK_DATA = 3'd5;
    localparam logic [2:0] ST_STOP     = 3'd6;

    logic [31:0] ctrl_q, target_addr_q, tx_data_q, rx_data_q, xfer_cnt_q;
    logic [2:0]  state_q;
    logic [3:0]  bit_cnt_q;
    logic [7:0]  shift_addr_q, shift_data_q, slave_rx_q;
    logic        ack_ok_q, done_q, irq_q;
    logic [63:0] cycle_cnt_q;

    assign busy_o       = (state_q != ST_IDLE);
    assign irq_o        = irq_q;
    assign rtl_cycles_o = cycle_cnt_q;

    always_comb begin
        rsp_rdata_o = 32'd0;
        rsp_err_o   = 1'b0;
        if (req_valid_i) begin
            if (req_write_i) begin
                case (req_addr_i)
                    8'h00, 8'h08, 8'h0C, 8'h14: rsp_err_o = 1'b0;
                    default: rsp_err_o = 1'b1;
                endcase
            end else begin
                case (req_addr_i)
                    8'h00: rsp_rdata_o = ctrl_q;
                    8'h04: rsp_rdata_o = {29'd0, done_q, ack_ok_q, busy_o};
                    8'h08: rsp_rdata_o = target_addr_q;
                    8'h0C: rsp_rdata_o = tx_data_q;
                    8'h10: rsp_rdata_o = rx_data_q;
                    8'h14: rsp_rdata_o = {31'd0, irq_q};
                    8'h18: rsp_rdata_o = xfer_cnt_q;
                    default: rsp_err_o = 1'b1;
                endcase
            end
        end
    end

    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            ctrl_q        <= 32'd0;
            target_addr_q <= 32'd0;
            tx_data_q     <= 32'd0;
            rx_data_q     <= 32'd0;
            xfer_cnt_q    <= 32'd0;
            state_q       <= ST_IDLE;
            bit_cnt_q     <= 4'd0;
            shift_addr_q  <= 8'd0;
            shift_data_q  <= 8'd0;
            slave_rx_q    <= 8'd0;
            ack_ok_q      <= 1'b0;
            done_q        <= 1'b0;
            irq_q         <= 1'b0;
            cycle_cnt_q   <= 64'd0;
        end else begin
            cycle_cnt_q <= cycle_cnt_q + 64'd1;
            case (state_q)
                ST_IDLE: begin
                    if (req_valid_i && req_write_i && !rsp_err_o) begin
                        case (req_addr_i)
                            8'h00: begin
                                ctrl_q <= req_wdata_i & 32'hFFFF_FFFB;
                                if (req_wdata_i[2] && req_wdata_i[0]) begin
                                    shift_addr_q <= {target_addr_q[6:0], 1'b0};
                                    shift_data_q <= tx_data_q[7:0];
                                    slave_rx_q   <= 8'd0;
                                    bit_cnt_q    <= 4'd0;
                                    ack_ok_q     <= 1'b0;
                                    done_q       <= 1'b0;
                                    state_q      <= ST_START;
                                end
                            end
                            8'h08: target_addr_q <= {25'd0, req_wdata_i[6:0]};
                            8'h0C: tx_data_q     <= {24'd0, req_wdata_i[7:0]};
                            8'h14: if (req_wdata_i[0]) irq_q <= 1'b0;
                            default: ;
                        endcase
                    end
                end
                ST_START: begin
                    bit_cnt_q <= 4'd0;
                    state_q   <= ST_ADDR;
                end
                ST_ADDR: begin
                    shift_addr_q <= {shift_addr_q[6:0], 1'b0};
                    if (bit_cnt_q == 4'd7) begin
                        state_q <= ST_ACK_ADDR;
                    end else begin
                        bit_cnt_q <= bit_cnt_q + 4'd1;
                    end
                end
                ST_ACK_ADDR: begin
                    if (ctrl_q[1] && (target_addr_q[6:0] == 7'h50)) begin
                        ack_ok_q  <= 1'b1;
                        bit_cnt_q <= 4'd0;
                        state_q   <= ST_DATA;
                    end else begin
                        ack_ok_q <= 1'b0;
                        state_q  <= ST_STOP;
                    end
                end
                ST_DATA: begin
                    slave_rx_q   <= {slave_rx_q[6:0], shift_data_q[7]};
                    shift_data_q <= {shift_data_q[6:0], 1'b0};
                    if (bit_cnt_q == 4'd7) begin
                        state_q <= ST_ACK_DATA;
                    end else begin
                        bit_cnt_q <= bit_cnt_q + 4'd1;
                    end
                end
                ST_ACK_DATA: begin
                    rx_data_q <= {24'd0, slave_rx_q};
                    state_q   <= ST_STOP;
                end
                ST_STOP: begin
                    done_q     <= 1'b1;
                    irq_q      <= 1'b1;
                    xfer_cnt_q <= xfer_cnt_q + 32'd1;
                    state_q    <= ST_IDLE;
                end
                default: state_q <= ST_IDLE;
            endcase
        end
    end
endmodule
