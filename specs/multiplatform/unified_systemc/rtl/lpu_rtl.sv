// SPDX-License-Identifier: Apache-2.0
// Synthesizable SystemVerilog RTL Language / Linear Processing Unit (lpu_rtl).
// Executes 4-lane signed INT32 Linear Projection + Bias + ReLU activation AND
// 4-token quantized Attention Logit Argmax decoding with real RTL cycle counter & IRQ.
// Co-simulated cycle-by-cycle via Verilator 5.032 inside ASoC / Unified SystemC top-level.
// NOTE: Original clean-room synthesizable RTL for pre-silicon HW/SW co-verification.
module lpu_rtl (
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
    logic signed [31:0] x_q     [0:3];
    logic signed [31:0] w_q     [0:3];
    logic signed [31:0] logit_q [0:3];
    logic signed [31:0] bias_q;
    logic signed [31:0] raw_out_q;
    logic        [31:0] relu_out_q;
    logic signed [31:0] max_logit_q;
    logic        [31:0] argmax_idx_q;
    logic        [31:0] ops_done_q;
    logic        [31:0] linear_ops_q;
    logic        [31:0] tokens_decoded_q;
    logic               irq_q;
    logic        [63:0] cycle_cnt_q;

    // 4-lane parallel signed MAC + Bias + ReLU datapath
    logic signed [31:0] p0_w, p1_w, p2_w, p3_w, sum_w;
    logic        [31:0] relu_w;

    assign p0_w   = x_q[0] * w_q[0];
    assign p1_w   = x_q[1] * w_q[1];
    assign p2_w   = x_q[2] * w_q[2];
    assign p3_w   = x_q[3] * w_q[3];
    assign sum_w  = p0_w + p1_w + p2_w + p3_w + bias_q;
    assign relu_w = sum_w[31] ? 32'd0 : sum_w;

    // 4-token signed comparator tree for Attention / Vocabulary Argmax
    logic signed [31:0] max01_w, max23_w, max_all_w;
    logic        [31:0] idx01_w, idx23_w, idx_all_w;

    assign max01_w   = (logit_q[1] > logit_q[0]) ? logit_q[1] : logit_q[0];
    assign idx01_w   = (logit_q[1] > logit_q[0]) ? 32'd1 : 32'd0;
    assign max23_w   = (logit_q[3] > logit_q[2]) ? logit_q[3] : logit_q[2];
    assign idx23_w   = (logit_q[3] > logit_q[2]) ? 32'd3 : 32'd2;
    assign max_all_w = (max23_w > max01_w) ? max23_w : max01_w;
    assign idx_all_w = (max23_w > max01_w) ? idx23_w : idx01_w;

    assign irq_o        = irq_q;
    assign rtl_cycles_o = cycle_cnt_q;

    // Combinational MMIO32 address decode
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
                    8'h04, 8'h10,
                    8'h20, 8'h24, 8'h28, 8'h2C,
                    8'h30, 8'h34, 8'h38, 8'h3C,
                    8'h40, 8'h44, 8'h48, 8'h4C: rsp_err_o = 1'b0;
                    default: rsp_err_o = 1'b1;
                endcase
            end else begin
                case (req_addr_i)
                    8'h00: rsp_rdata_o = ops_done_q;
                    8'h04: rsp_rdata_o = bias_q;
                    8'h08: rsp_rdata_o = raw_out_q;
                    8'h0C: rsp_rdata_o = relu_out_q;
                    8'h10: rsp_rdata_o = {31'd0, irq_q};
                    8'h14: rsp_rdata_o = linear_ops_q;
                    8'h18: rsp_rdata_o = tokens_decoded_q;
                    8'h1C: rsp_rdata_o = argmax_idx_q;
                    8'h20: rsp_rdata_o = x_q[0];
                    8'h24: rsp_rdata_o = x_q[1];
                    8'h28: rsp_rdata_o = x_q[2];
                    8'h2C: rsp_rdata_o = x_q[3];
                    8'h30: rsp_rdata_o = w_q[0];
                    8'h34: rsp_rdata_o = w_q[1];
                    8'h38: rsp_rdata_o = w_q[2];
                    8'h3C: rsp_rdata_o = w_q[3];
                    8'h40: rsp_rdata_o = logit_q[0];
                    8'h44: rsp_rdata_o = logit_q[1];
                    8'h48: rsp_rdata_o = logit_q[2];
                    8'h4C: rsp_rdata_o = logit_q[3];
                    8'h50: rsp_rdata_o = max_logit_q;
                    default: rsp_err_o = 1'b1;
                endcase
            end
        end
    end

    integer k;
    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            bias_q           <= 32'sd0;
            raw_out_q        <= 32'sd0;
            relu_out_q       <= 32'd0;
            max_logit_q      <= 32'sd0;
            argmax_idx_q     <= 32'd0;
            ops_done_q       <= 32'd0;
            linear_ops_q     <= 32'd0;
            tokens_decoded_q <= 32'd0;
            irq_q            <= 1'b0;
            cycle_cnt_q      <= 64'd0;
            for (k = 0; k < 4; k = k + 1) begin
                x_q[k]     <= 32'sd0;
                w_q[k]     <= 32'sd0;
                logit_q[k] <= 32'sd0;
            end
        end else begin
            cycle_cnt_q <= cycle_cnt_q + 64'd1;
            if (req_valid_i && req_write_i && !rsp_err_o) begin
                case (req_addr_i)
                    8'h00: begin
                        if (req_wdata_i == 32'd1) begin
                            raw_out_q    <= sum_w;
                            relu_out_q   <= relu_w;
                            linear_ops_q <= linear_ops_q + 32'd1;
                            ops_done_q   <= ops_done_q + 32'd1;
                            irq_q        <= 1'b1;
                        end else if (req_wdata_i == 32'd2) begin
                            raw_out_q        <= 32'sd0;
                            relu_out_q       <= 32'd0;
                            max_logit_q      <= 32'sd0;
                            argmax_idx_q     <= 32'd0;
                            ops_done_q       <= 32'd0;
                            linear_ops_q     <= 32'd0;
                            tokens_decoded_q <= 32'd0;
                            irq_q            <= 1'b0;
                        end else if (req_wdata_i == 32'd3) begin
                            max_logit_q      <= max_all_w;
                            argmax_idx_q     <= idx_all_w;
                            tokens_decoded_q <= tokens_decoded_q + 32'd1;
                            ops_done_q       <= ops_done_q + 32'd1;
                            irq_q            <= 1'b1;
                        end
                    end
                    8'h04: bias_q     <= signed'(req_wdata_i);
                    8'h10: if (req_wdata_i[0]) irq_q <= 1'b0;
                    8'h20: x_q[0]     <= signed'(req_wdata_i);
                    8'h24: x_q[1]     <= signed'(req_wdata_i);
                    8'h28: x_q[2]     <= signed'(req_wdata_i);
                    8'h2C: x_q[3]     <= signed'(req_wdata_i);
                    8'h30: w_q[0]     <= signed'(req_wdata_i);
                    8'h34: w_q[1]     <= signed'(req_wdata_i);
                    8'h38: w_q[2]     <= signed'(req_wdata_i);
                    8'h3C: w_q[3]     <= signed'(req_wdata_i);
                    8'h40: logit_q[0] <= signed'(req_wdata_i);
                    8'h44: logit_q[1] <= signed'(req_wdata_i);
                    8'h48: logit_q[2] <= signed'(req_wdata_i);
                    8'h4C: logit_q[3] <= signed'(req_wdata_i);
                    default: ;
                endcase
            end
        end
    end
endmodule
