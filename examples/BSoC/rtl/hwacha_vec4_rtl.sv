// SPDX-License-Identifier: Apache-2.0
// Synthesizable SystemVerilog RTL 4-Lane Hwacha-Style Vector Accelerator (hwacha_vec4_rtl).
// Implements 4 parallel INT32 vector lanes (Lane 0..3) with Master Sequencer control:
//   - Cmd 1: 4-lane parallel signed vector product & dot-product accumulation (c[i] = a[i]*b[i])
//   - Cmd 2: Reset / clear accumulator and lanes
//   - Cmd 3: 4-lane parallel complex magnitude-squared energy (c[i] = a[i]^2 + b[i]^2)
// NOTE: Clean-room synthesizable RTL for BSoC pre-silicon co-verification, distinct from
// upstream Berkeley Chisel Hwacha RTL.
module hwacha_vec4_rtl (
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
    logic signed [31:0] va_q [0:3];
    logic signed [31:0] vb_q [0:3];
    logic signed [31:0] vc_q [0:3];
    logic signed [31:0] acc_q;
    logic        [31:0] vlen_q;
    logic        [31:0] ops_done_q;
    logic        [31:0] power_ops_q;
    logic               irq_q;
    logic        [63:0] cycle_cnt_q;

    // 4 parallel vector lanes (Lane 0..3)
    logic signed [31:0] mul0_w, mul1_w, mul2_w, mul3_w, dot_sum_w;
    logic signed [31:0] pwr0_w, pwr1_w, pwr2_w, pwr3_w, pwr_sum_w;

    assign mul0_w    = va_q[0] * vb_q[0];
    assign mul1_w    = va_q[1] * vb_q[1];
    assign mul2_w    = va_q[2] * vb_q[2];
    assign mul3_w    = va_q[3] * vb_q[3];
    assign dot_sum_w = mul0_w + mul1_w + mul2_w + mul3_w;

    assign pwr0_w    = (va_q[0] * va_q[0]) + (vb_q[0] * vb_q[0]);
    assign pwr1_w    = (va_q[1] * va_q[1]) + (vb_q[1] * vb_q[1]);
    assign pwr2_w    = (va_q[2] * va_q[2]) + (vb_q[2] * vb_q[2]);
    assign pwr3_w    = (va_q[3] * va_q[3]) + (vb_q[3] * vb_q[3]);
    assign pwr_sum_w = pwr0_w + pwr1_w + pwr2_w + pwr3_w;

    assign irq_o        = irq_q;
    assign rtl_cycles_o = cycle_cnt_q;

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
                    8'h0C, 8'h10,
                    8'h20, 8'h24, 8'h28, 8'h2C,
                    8'h30, 8'h34, 8'h38, 8'h3C: rsp_err_o = 1'b0;
                    default: rsp_err_o = 1'b1;
                endcase
            end else begin
                case (req_addr_i)
                    8'h00: rsp_rdata_o = ops_done_q;
                    8'h04: rsp_rdata_o = {30'd0, irq_q, 1'b1};
                    8'h08: rsp_rdata_o = unsigned'(acc_q);
                    8'h0C: rsp_rdata_o = vlen_q;
                    8'h10: rsp_rdata_o = {31'd0, irq_q};
                    8'h14: rsp_rdata_o = 32'd4;
                    8'h20: rsp_rdata_o = unsigned'(va_q[0]);
                    8'h24: rsp_rdata_o = unsigned'(va_q[1]);
                    8'h28: rsp_rdata_o = unsigned'(va_q[2]);
                    8'h2C: rsp_rdata_o = unsigned'(va_q[3]);
                    8'h30: rsp_rdata_o = unsigned'(vb_q[0]);
                    8'h34: rsp_rdata_o = unsigned'(vb_q[1]);
                    8'h38: rsp_rdata_o = unsigned'(vb_q[2]);
                    8'h3C: rsp_rdata_o = unsigned'(vb_q[3]);
                    8'h40: rsp_rdata_o = unsigned'(vc_q[0]);
                    8'h44: rsp_rdata_o = unsigned'(vc_q[1]);
                    8'h48: rsp_rdata_o = unsigned'(vc_q[2]);
                    8'h4C: rsp_rdata_o = unsigned'(vc_q[3]);
                    8'h50: rsp_rdata_o = power_ops_q;
                    default: rsp_err_o = 1'b1;
                endcase
            end
        end
    end

    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            va_q[0]     <= 32'sd0;
            va_q[1]     <= 32'sd0;
            va_q[2]     <= 32'sd0;
            va_q[3]     <= 32'sd0;
            vb_q[0]     <= 32'sd0;
            vb_q[1]     <= 32'sd0;
            vb_q[2]     <= 32'sd0;
            vb_q[3]     <= 32'sd0;
            vc_q[0]     <= 32'sd0;
            vc_q[1]     <= 32'sd0;
            vc_q[2]     <= 32'sd0;
            vc_q[3]     <= 32'sd0;
            acc_q       <= 32'sd0;
            vlen_q      <= 32'd4;
            ops_done_q  <= 32'd0;
            power_ops_q <= 32'd0;
            irq_q       <= 1'b0;
            cycle_cnt_q <= 64'd0;
        end else begin
            cycle_cnt_q <= cycle_cnt_q + 64'd1;
            if (req_valid_i && req_write_i && !rsp_err_o) begin
                case (req_addr_i)
                    8'h00: begin
                        if (req_wdata_i == 32'd1) begin
                            vc_q[0]    <= mul0_w;
                            vc_q[1]    <= mul1_w;
                            vc_q[2]    <= mul2_w;
                            vc_q[3]    <= mul3_w;
                            acc_q      <= acc_q + dot_sum_w;
                            ops_done_q <= ops_done_q + 32'd1;
                            irq_q      <= 1'b1;
                        end else if (req_wdata_i == 32'd2) begin
                            vc_q[0]     <= 32'sd0;
                            vc_q[1]     <= 32'sd0;
                            vc_q[2]     <= 32'sd0;
                            vc_q[3]     <= 32'sd0;
                            acc_q       <= 32'sd0;
                            ops_done_q  <= 32'd0;
                            power_ops_q <= 32'd0;
                            irq_q       <= 1'b0;
                        end else if (req_wdata_i == 32'd3) begin
                            vc_q[0]     <= pwr0_w;
                            vc_q[1]     <= pwr1_w;
                            vc_q[2]     <= pwr2_w;
                            vc_q[3]     <= pwr3_w;
                            acc_q       <= acc_q + pwr_sum_w;
                            ops_done_q  <= ops_done_q + 32'd1;
                            power_ops_q <= power_ops_q + 32'd1;
                            irq_q       <= 1'b1;
                        end
                    end
                    8'h0C: vlen_q  <= req_wdata_i;
                    8'h10: if (req_wdata_i[0]) irq_q <= 1'b0;
                    8'h20: va_q[0] <= signed'(req_wdata_i);
                    8'h24: va_q[1] <= signed'(req_wdata_i);
                    8'h28: va_q[2] <= signed'(req_wdata_i);
                    8'h2C: va_q[3] <= signed'(req_wdata_i);
                    8'h30: vb_q[0] <= signed'(req_wdata_i);
                    8'h34: vb_q[1] <= signed'(req_wdata_i);
                    8'h38: vb_q[2] <= signed'(req_wdata_i);
                    8'h3C: vb_q[3] <= signed'(req_wdata_i);
                    default: ;
                endcase
            end
        end
    end
endmodule
