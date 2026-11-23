// SPDX-License-Identifier: Apache-2.0
// Synthesizable SystemVerilog RTL 32-Phase Digital Quadrature Tuner / NCO Mixer (tuner_32ph_rtl).
// Performs complex baseband frequency mixing using a 32-phase Q10 (scale=1024) cos/sin LUT:
//   I_out = (I_in * cos(phi) + Q_in * sin(phi)) >>> 10
//   Q_out = (Q_in * cos(phi) - I_in * sin(phi)) >>> 10
// NOTE: Clean-room synthesizable RTL for BSoC DSP Chain Stage 2.
module tuner_32ph_rtl (
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
    logic [4:0]  phase_idx_q;
    logic [4:0]  phase_step_q;
    logic [31:0] in_iq_q;
    logic [31:0] out_iq_q;
    logic [31:0] samples_done_q;
    logic        irq_q;
    logic [63:0] cycle_cnt_q;

    logic signed [15:0] cos_w, sin_w;
    always_comb begin
        case (phase_idx_q)
            5'd0:  begin cos_w =  16'sd1024; sin_w =  16'sd0;     end
            5'd1:  begin cos_w =  16'sd1004; sin_w =  16'sd200;   end
            5'd2:  begin cos_w =  16'sd946;  sin_w =  16'sd392;   end
            5'd3:  begin cos_w =  16'sd851;  sin_w =  16'sd569;   end
            5'd4:  begin cos_w =  16'sd724;  sin_w =  16'sd724;   end
            5'd5:  begin cos_w =  16'sd569;  sin_w =  16'sd851;   end
            5'd6:  begin cos_w =  16'sd392;  sin_w =  16'sd946;   end
            5'd7:  begin cos_w =  16'sd200;  sin_w =  16'sd1004;  end
            5'd8:  begin cos_w =  16'sd0;    sin_w =  16'sd1024;  end
            5'd9:  begin cos_w = -16'sd200;  sin_w =  16'sd1004;  end
            5'd10: begin cos_w = -16'sd392;  sin_w =  16'sd946;   end
            5'd11: begin cos_w = -16'sd569;  sin_w =  16'sd851;   end
            5'd12: begin cos_w = -16'sd724;  sin_w =  16'sd724;   end
            5'd13: begin cos_w = -16'sd851;  sin_w =  16'sd569;   end
            5'd14: begin cos_w = -16'sd946;  sin_w =  16'sd392;   end
            5'd15: begin cos_w = -16'sd1004; sin_w =  16'sd200;   end
            5'd16: begin cos_w = -16'sd1024; sin_w =  16'sd0;     end
            5'd17: begin cos_w = -16'sd1004; sin_w = -16'sd200;   end
            5'd18: begin cos_w = -16'sd946;  sin_w = -16'sd392;   end
            5'd19: begin cos_w = -16'sd851;  sin_w = -16'sd569;   end
            5'd20: begin cos_w = -16'sd724;  sin_w = -16'sd724;   end
            5'd21: begin cos_w = -16'sd569;  sin_w = -16'sd851;   end
            5'd22: begin cos_w = -16'sd392;  sin_w = -16'sd946;   end
            5'd23: begin cos_w = -16'sd200;  sin_w = -16'sd1004;  end
            5'd24: begin cos_w =  16'sd0;    sin_w = -16'sd1024;  end
            5'd25: begin cos_w =  16'sd200;  sin_w = -16'sd1004;  end
            5'd26: begin cos_w =  16'sd392;  sin_w = -16'sd946;   end
            5'd27: begin cos_w =  16'sd569;  sin_w = -16'sd851;   end
            5'd28: begin cos_w =  16'sd724;  sin_w = -16'sd724;   end
            5'd29: begin cos_w =  16'sd851;  sin_w = -16'sd569;   end
            5'd30: begin cos_w =  16'sd946;  sin_w = -16'sd392;   end
            5'd31: begin cos_w =  16'sd1004; sin_w = -16'sd200;   end
        endcase
    end

    logic signed [31:0] in_i_w, in_q_w, cos32_w, sin32_w;
    logic signed [31:0] mix_i_w, mix_q_w;
    logic [15:0] out_i_w, out_q_w;

    assign in_i_w  = {{16{in_iq_q[15]}}, in_iq_q[15:0]};
    assign in_q_w  = {{16{in_iq_q[31]}}, in_iq_q[31:16]};
    assign cos32_w = {{16{cos_w[15]}}, cos_w};
    assign sin32_w = {{16{sin_w[15]}}, sin_w};
    assign mix_i_w = ((in_i_w * cos32_w) + (in_q_w * sin32_w)) >>> 10;
    assign mix_q_w = ((in_q_w * cos32_w) - (in_i_w * sin32_w)) >>> 10;
    assign out_i_w = mix_i_w[15:0];
    assign out_q_w = mix_q_w[15:0];

    assign irq_o        = irq_q;
    assign rtl_cycles_o = cycle_cnt_q;

    always_comb begin
        rsp_rdata_o = 32'd0;
        rsp_err_o   = 1'b0;
        if (req_valid_i) begin
            if (req_write_i) begin
                case (req_addr_i)
                    8'h00: begin
                        if (req_wdata_i != 32'd1 && req_wdata_i != 32'd2) rsp_err_o = 1'b1;
                    end
                    8'h04, 8'h08, 8'h10, 8'h14: rsp_err_o = 1'b0;
                    default: rsp_err_o = 1'b1;
                endcase
            end else begin
                case (req_addr_i)
                    8'h00: rsp_rdata_o = samples_done_q;
                    8'h04: rsp_rdata_o = {27'd0, phase_step_q};
                    8'h08: rsp_rdata_o = in_iq_q;
                    8'h0C: rsp_rdata_o = out_iq_q;
                    8'h10: rsp_rdata_o = {31'd0, irq_q};
                    8'h14: rsp_rdata_o = {27'd0, phase_idx_q};
                    8'h18: rsp_rdata_o = samples_done_q;
                    default: rsp_err_o = 1'b1;
                endcase
            end
        end
    end

    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            phase_idx_q    <= 5'd0;
            phase_step_q   <= 5'd0;
            in_iq_q        <= 32'd0;
            out_iq_q       <= 32'd0;
            samples_done_q <= 32'd0;
            irq_q          <= 1'b0;
            cycle_cnt_q    <= 64'd0;
        end else begin
            cycle_cnt_q <= cycle_cnt_q + 64'd1;
            if (req_valid_i && req_write_i && !rsp_err_o) begin
                case (req_addr_i)
                    8'h00: begin
                        if (req_wdata_i == 32'd1) begin
                            out_iq_q       <= {out_q_w, out_i_w};
                            phase_idx_q    <= phase_idx_q + phase_step_q;
                            samples_done_q <= samples_done_q + 32'd1;
                            irq_q          <= 1'b1;
                        end else if (req_wdata_i == 32'd2) begin
                            phase_idx_q    <= 5'd0;
                            out_iq_q       <= 32'd0;
                            samples_done_q <= 32'd0;
                            irq_q          <= 1'b0;
                        end
                    end
                    8'h04: phase_step_q <= req_wdata_i[4:0];
                    8'h08: in_iq_q      <= req_wdata_i;
                    8'h10: if (req_wdata_i[0]) irq_q <= 1'b0;
                    8'h14: phase_idx_q  <= req_wdata_i[4:0];
                    default: ;
                endcase
            end
        end
    end
endmodule
