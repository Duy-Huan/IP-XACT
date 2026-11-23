// SPDX-License-Identifier: Apache-2.0
// Synthesizable SystemVerilog RTL fixed-point 32-bit MAC accelerator (mac_npu_rtl).
// Co-simulated cycle-by-cycle inside Unified_Hetero_MultiPlatform_SoC via Verilator + SystemC.
// NOTE: Original synthetic MMIO32 MAC RTL, distinct from upstream Chipyard Gemmini RTL.
module mac_npu_rtl (
    input  logic        clk_i,
    input  logic        rst_ni,
    input  logic        req_valid_i,
    input  logic        req_write_i,
    input  logic [7:0]  req_addr_i,
    input  logic [31:0] req_wdata_i,
    output logic [31:0] rsp_rdata_o,
    output logic        rsp_err_o,
    output logic        irq_o,
    output logic [63:0] rtl_cycles_o
);
    logic [31:0] op_a_q;
    logic [31:0] op_b_q;
    logic [31:0] acc_q;
    logic [31:0] ops_done_q;
    logic        irq_q;
    logic [63:0] cycle_cnt_q;
    logic [31:0] prod_w;

    assign prod_w       = op_a_q * op_b_q;
    assign irq_o        = irq_q;
    assign rtl_cycles_o = cycle_cnt_q;

    // Combinational read / error decode
    always_comb begin
        rsp_rdata_o = 32'd0;
        rsp_err_o   = 1'b0;
        if (req_valid_i) begin
            if (req_write_i) begin
                case (req_addr_i)
                    8'h00: begin
                        if (req_wdata_i != 32'd1 && req_wdata_i != 32'd2) begin
                            rsp_err_o = 1'b1;
                        end
                    end
                    8'h04, 8'h08, 8'h10: rsp_err_o = 1'b0;
                    default: rsp_err_o = 1'b1;  // 0x0C acc_result is read-only; others unmapped
                endcase
            end else begin
                case (req_addr_i)
                    8'h00: rsp_rdata_o = ops_done_q;
                    8'h04: rsp_rdata_o = op_a_q;
                    8'h08: rsp_rdata_o = op_b_q;
                    8'h0C: rsp_rdata_o = acc_q;
                    8'h10: rsp_rdata_o = {31'd0, irq_q};
                    default: rsp_err_o = 1'b1;
                endcase
            end
        end
    end

    // Synchronous state update & real RTL cycle counter
    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            op_a_q      <= 32'd0;
            op_b_q      <= 32'd0;
            acc_q       <= 32'd0;
            ops_done_q  <= 32'd0;
            irq_q       <= 1'b0;
            cycle_cnt_q <= 64'd0;
        end else begin
            cycle_cnt_q <= cycle_cnt_q + 64'd1;
            if (req_valid_i && req_write_i && !rsp_err_o) begin
                case (req_addr_i)
                    8'h00: begin
                        if (req_wdata_i == 32'd1) begin
                            acc_q      <= acc_q + prod_w;
                            ops_done_q <= ops_done_q + 32'd1;
                            irq_q      <= 1'b1;
                        end else if (req_wdata_i == 32'd2) begin
                            acc_q      <= 32'd0;
                            ops_done_q <= 32'd0;
                            irq_q      <= 1'b0;
                        end
                    end
                    8'h04: op_a_q <= req_wdata_i;
                    8'h08: op_b_q <= req_wdata_i;
                    8'h10: begin
                        if (req_wdata_i[0]) begin
                            irq_q <= 1'b0;
                        end
                    end
                    default: ;
                endcase
            end
        end
    end
endmodule
