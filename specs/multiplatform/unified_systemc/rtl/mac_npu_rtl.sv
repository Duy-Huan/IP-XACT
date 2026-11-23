// SPDX-License-Identifier: Apache-2.0
// Synthesizable SystemVerilog RTL fixed-point MAC + 2x2 Matrix Multiply accelerator (mac_npu_rtl).
// Co-simulated cycle-by-cycle via Verilator 5.032 inside Unified_Hetero_MultiPlatform_SoC.
// NOTE: Original clean-room synthesizable RTL, distinct from upstream Chipyard Gemmini RTL.
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
    logic [31:0] op_a_q, op_b_q, acc_q, ops_done_q;
    logic [31:0] mat_a_q [0:3];
    logic [31:0] mat_b_q [0:3];
    logic [31:0] mat_c_q [0:3];
    logic [31:0] matmul_ops_q;
    logic        irq_q;
    logic [63:0] cycle_cnt_q;

    logic [31:0] prod_w;
    logic [31:0] c00_w, c01_w, c10_w, c11_w;

    assign prod_w       = op_a_q * op_b_q;
    assign c00_w        = (mat_a_q[0] * mat_b_q[0]) + (mat_a_q[1] * mat_b_q[2]);
    assign c01_w        = (mat_a_q[0] * mat_b_q[1]) + (mat_a_q[1] * mat_b_q[3]);
    assign c10_w        = (mat_a_q[2] * mat_b_q[0]) + (mat_a_q[3] * mat_b_q[2]);
    assign c11_w        = (mat_a_q[2] * mat_b_q[1]) + (mat_a_q[3] * mat_b_q[3]);
    assign irq_o        = irq_q;
    assign rtl_cycles_o = cycle_cnt_q;

    // Combinational MMIO decode
    always_comb begin
        rsp_rdata_o = 32'd0;
        rsp_err_o   = 1'b0;
        if (req_valid_i) begin
            if (req_write_i) begin
                case (req_addr_i)
                    8'h00: begin
                        if (req_wdata_i != 32'd1 && req_wdata_i != 32'd2 && req_wdata_i != 32'd3) begin
                            rsp_err_o = 1'b1;
                        end
                    end
                    8'h04, 8'h08, 8'h10,
                    8'h20, 8'h24, 8'h28, 8'h2C,
                    8'h30, 8'h34, 8'h38, 8'h3C: rsp_err_o = 1'b0;
                    default: rsp_err_o = 1'b1;
                endcase
            end else begin
                case (req_addr_i)
                    8'h00: rsp_rdata_o = ops_done_q;
                    8'h04: rsp_rdata_o = op_a_q;
                    8'h08: rsp_rdata_o = op_b_q;
                    8'h0C: rsp_rdata_o = acc_q;
                    8'h10: rsp_rdata_o = {31'd0, irq_q};
                    8'h14: rsp_rdata_o = matmul_ops_q;
                    8'h20: rsp_rdata_o = mat_a_q[0];
                    8'h24: rsp_rdata_o = mat_a_q[1];
                    8'h28: rsp_rdata_o = mat_a_q[2];
                    8'h2C: rsp_rdata_o = mat_a_q[3];
                    8'h30: rsp_rdata_o = mat_b_q[0];
                    8'h34: rsp_rdata_o = mat_b_q[1];
                    8'h38: rsp_rdata_o = mat_b_q[2];
                    8'h3C: rsp_rdata_o = mat_b_q[3];
                    8'h40: rsp_rdata_o = mat_c_q[0];
                    8'h44: rsp_rdata_o = mat_c_q[1];
                    8'h48: rsp_rdata_o = mat_c_q[2];
                    8'h4C: rsp_rdata_o = mat_c_q[3];
                    default: rsp_err_o = 1'b1;
                endcase
            end
        end
    end

    integer k;
    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            op_a_q       <= 32'd0;
            op_b_q       <= 32'd0;
            acc_q        <= 32'd0;
            ops_done_q   <= 32'd0;
            matmul_ops_q <= 32'd0;
            irq_q        <= 1'b0;
            cycle_cnt_q  <= 64'd0;
            for (k = 0; k < 4; k = k + 1) begin
                mat_a_q[k] <= 32'd0;
                mat_b_q[k] <= 32'd0;
                mat_c_q[k] <= 32'd0;
            end
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
                        end else if (req_wdata_i == 32'd3) begin
                            mat_c_q[0]   <= c00_w;
                            mat_c_q[1]   <= c01_w;
                            mat_c_q[2]   <= c10_w;
                            mat_c_q[3]   <= c11_w;
                            matmul_ops_q <= matmul_ops_q + 32'd1;
                            irq_q        <= 1'b1;
                        end
                    end
                    8'h04: op_a_q <= req_wdata_i;
                    8'h08: op_b_q <= req_wdata_i;
                    8'h10: if (req_wdata_i[0]) irq_q <= 1'b0;
                    8'h20: mat_a_q[0] <= req_wdata_i;
                    8'h24: mat_a_q[1] <= req_wdata_i;
                    8'h28: mat_a_q[2] <= req_wdata_i;
                    8'h2C: mat_a_q[3] <= req_wdata_i;
                    8'h30: mat_b_q[0] <= req_wdata_i;
                    8'h34: mat_b_q[1] <= req_wdata_i;
                    8'h38: mat_b_q[2] <= req_wdata_i;
                    8'h3C: mat_b_q[3] <= req_wdata_i;
                    default: ;
                endcase
            end
        end
    end
endmodule
