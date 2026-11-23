// SPDX-License-Identifier: Apache-2.0
// Synthesizable SystemVerilog RTL 128-Point Radix-2 Cooley-Tukey Complex FFT Engine (fft_128pt_rtl).
// Executes a full 7-stage, 64-butterfly/stage (704+ RTL cycles) in-place fixed-point 128-point FFT
// with bit-reversal permutation, 64-entry Q10 twiddle ROM, per-stage /2 scaling, peak-tone bin
// detector, and PLIC completion IRQ.
// NOTE: Clean-room synthesizable RTL for BSoC DSP Chain Stage 5.
module fft_128pt_rtl (
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
    localparam int N = 128;

    typedef enum logic [2:0] {
        S_IDLE   = 3'd0,
        S_BITREV = 3'd1,
        S_BFLY   = 3'd2,
        S_PEAK   = 3'd3
    } fft_state_e;

    fft_state_e state_q;

    logic signed [15:0] in_re  [0:N-1];
    logic signed [15:0] in_im  [0:N-1];
    logic signed [15:0] buf_re [0:N-1];
    logic signed [15:0] buf_im [0:N-1];

    logic [6:0]  wr_idx_q;
    logic [6:0]  rd_idx_q;
    logic [6:0]  cnt_q;
    logic [2:0]  stage_q;
    logic [5:0]  bfly_q;

    logic [31:0] ffts_done_q;
    logic [31:0] peak_bin_q;
    logic [31:0] peak_mag2_q;
    logic        irq_q;
    logic [63:0] cycle_cnt_q;

    // 7-bit bit-reversal function for N=128
    function automatic logic [6:0] bitrev7(input logic [6:0] x);
        return {x[0], x[1], x[2], x[3], x[4], x[5], x[6]};
    endfunction

    // 64-entry Q10 twiddle factor ROM: W_128^k = cos(2*pi*k/128) - j*sin(2*pi*k/128), k=0..63
    function automatic logic signed [15:0] tw_cos(input logic [5:0] k);
        case (k)
            6'd0:  return  16'sd1024; 6'd1:  return  16'sd1023; 6'd2:  return  16'sd1019; 6'd3:  return  16'sd1013;
            6'd4:  return  16'sd1004; 6'd5:  return  16'sd993;  6'd6:  return  16'sd980;  6'd7:  return  16'sd964;
            6'd8:  return  16'sd946;  6'd9:  return  16'sd926;  6'd10: return  16'sd903;  6'd11: return  16'sd878;
            6'd12: return  16'sd851;  6'd13: return  16'sd822;  6'd14: return  16'sd792;  6'd15: return  16'sd759;
            6'd16: return  16'sd724;  6'd17: return  16'sd688;  6'd18: return  16'sd650;  6'd19: return  16'sd610;
            6'd20: return  16'sd569;  6'd21: return  16'sd526;  6'd22: return  16'sd483;  6'd23: return  16'sd438;
            6'd24: return  16'sd392;  6'd25: return  16'sd345;  6'd26: return  16'sd297;  6'd27: return  16'sd249;
            6'd28: return  16'sd200;  6'd29: return  16'sd150;  6'd30: return  16'sd100;  6'd31: return  16'sd50;
            6'd32: return  16'sd0;    6'd33: return -16'sd50;   6'd34: return -16'sd100;  6'd35: return -16'sd150;
            6'd36: return -16'sd200;  6'd37: return -16'sd249;  6'd38: return -16'sd297;  6'd39: return -16'sd345;
            6'd40: return -16'sd392;  6'd41: return -16'sd438;  6'd42: return -16'sd483;  6'd43: return -16'sd526;
            6'd44: return -16'sd569;  6'd45: return -16'sd610;  6'd46: return -16'sd650;  6'd47: return -16'sd688;
            6'd48: return -16'sd724;  6'd49: return -16'sd759;  6'd50: return -16'sd792;  6'd51: return -16'sd822;
            6'd52: return -16'sd851;  6'd53: return -16'sd878;  6'd54: return -16'sd903;  6'd55: return -16'sd926;
            6'd56: return -16'sd946;  6'd57: return -16'sd964;  6'd58: return -16'sd980;  6'd59: return -16'sd993;
            6'd60: return -16'sd1004; 6'd61: return -16'sd1013; 6'd62: return -16'sd1019; 6'd63: return -16'sd1023;
        endcase
    endfunction

    function automatic logic signed [15:0] tw_sin(input logic [5:0] k);
        case (k)
            6'd0:  return 16'sd0;    6'd1:  return 16'sd50;   6'd2:  return 16'sd100;  6'd3:  return 16'sd150;
            6'd4:  return 16'sd200;  6'd5:  return 16'sd249;  6'd6:  return 16'sd297;  6'd7:  return 16'sd345;
            6'd8:  return 16'sd392;  6'd9:  return 16'sd438;  6'd10: return 16'sd483;  6'd11: return 16'sd526;
            6'd12: return 16'sd569;  6'd13: return 16'sd610;  6'd14: return 16'sd650;  6'd15: return 16'sd688;
            6'd16: return 16'sd724;  6'd17: return 16'sd759;  6'd18: return 16'sd792;  6'd19: return 16'sd822;
            6'd20: return 16'sd851;  6'd21: return 16'sd878;  6'd22: return 16'sd903;  6'd23: return 16'sd926;
            6'd24: return 16'sd946;  6'd25: return 16'sd964;  6'd26: return 16'sd980;  6'd27: return 16'sd993;
            6'd28: return 16'sd1004; 6'd29: return 16'sd1013; 6'd30: return 16'sd1019; 6'd31: return 16'sd1023;
            6'd32: return 16'sd1024; 6'd33: return 16'sd1023; 6'd34: return 16'sd1019; 6'd35: return 16'sd1013;
            6'd36: return 16'sd1004; 6'd37: return 16'sd993;  6'd38: return 16'sd980;  6'd39: return 16'sd964;
            6'd40: return 16'sd946;  6'd41: return 16'sd926;  6'd42: return 16'sd903;  6'd43: return 16'sd878;
            6'd44: return 16'sd851;  6'd45: return 16'sd822;  6'd46: return 16'sd792;  6'd47: return 16'sd759;
            6'd48: return 16'sd724;  6'd49: return 16'sd688;  6'd50: return 16'sd650;  6'd51: return 16'sd610;
            6'd52: return 16'sd569;  6'd53: return 16'sd526;  6'd54: return 16'sd483;  6'd55: return 16'sd438;
            6'd56: return 16'sd392;  6'd57: return 16'sd345;  6'd58: return 16'sd297;  6'd59: return 16'sd249;
            6'd60: return 16'sd200;  6'd61: return 16'sd150;  6'd62: return 16'sd100;  6'd63: return 16'sd50;
        endcase
    endfunction

    // Butterfly address & arithmetic combinational network
    logic [6:0] half_span_w, grp_idx_w, sub_idx_w, idx0_w, idx1_w;
    logic [5:0] tw_idx_w;
    logic signed [15:0] tw_c_w, tw_s_w;
    logic signed [31:0] u_re_w, u_im_w, v_re_w, v_im_w, wc_w, ws_w;
    logic signed [31:0] t_re_w, t_im_w, y0_re_w, y0_im_w, y1_re_w, y1_im_w;
    logic signed [31:0] pk_re_w, pk_im_w, pk_mag2_s_w;
    logic        [31:0] pk_mag2_w;

    always_comb begin
        half_span_w = 7'd1 << stage_q;
        sub_idx_w   = {1'b0, bfly_q} & (half_span_w - 7'd1);
        grp_idx_w   = {1'b0, bfly_q} >> stage_q;
        idx0_w      = (grp_idx_w << (stage_q + 3'd1)) | sub_idx_w;
        idx1_w      = idx0_w | half_span_w;
        tw_idx_w    = sub_idx_w[5:0] << (3'd6 - stage_q);

        u_re_w = {{16{buf_re[idx0_w][15]}}, buf_re[idx0_w]};
        u_im_w = {{16{buf_im[idx0_w][15]}}, buf_im[idx0_w]};
        v_re_w = {{16{buf_re[idx1_w][15]}}, buf_re[idx1_w]};
        v_im_w = {{16{buf_im[idx1_w][15]}}, buf_im[idx1_w]};
        tw_c_w = tw_cos(tw_idx_w);
        tw_s_w = tw_sin(tw_idx_w);
        wc_w   = {{16{tw_c_w[15]}}, tw_c_w};
        ws_w   = {{16{tw_s_w[15]}}, tw_s_w};

        // t = v * (cos - j*sin)
        t_re_w  = ((v_re_w * wc_w) + (v_im_w * ws_w)) >>> 10;
        t_im_w  = ((v_im_w * wc_w) - (v_re_w * ws_w)) >>> 10;
        // Scale by 1/2 per stage to keep Q15 headroom across all 7 stages
        y0_re_w = (u_re_w + t_re_w) >>> 1;
        y0_im_w = (u_im_w + t_im_w) >>> 1;
        y1_re_w = (u_re_w - t_re_w) >>> 1;
        y1_im_w = (u_im_w - t_im_w) >>> 1;

        pk_re_w     = {{16{buf_re[cnt_q][15]}}, buf_re[cnt_q]};
        pk_im_w     = {{16{buf_im[cnt_q][15]}}, buf_im[cnt_q]};
        pk_mag2_s_w = (pk_re_w * pk_re_w) + (pk_im_w * pk_im_w);
        pk_mag2_w   = unsigned'(pk_mag2_s_w);
    end

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
                    8'h04, 8'h08, 8'h0C, 8'h10: rsp_err_o = 1'b0;
                    default: rsp_err_o = 1'b1;
                endcase
            end else begin
                case (req_addr_i)
                    8'h00: rsp_rdata_o = ffts_done_q;
                    8'h04: rsp_rdata_o = {25'd0, wr_idx_q};
                    8'h08: rsp_rdata_o = {unsigned'(in_im[wr_idx_q]), unsigned'(in_re[wr_idx_q])};
                    8'h0C: rsp_rdata_o = {25'd0, rd_idx_q};
                    8'h10: rsp_rdata_o = {31'd0, irq_q};
                    8'h14: rsp_rdata_o = {unsigned'(buf_im[rd_idx_q]), unsigned'(buf_re[rd_idx_q])};
                    8'h18: rsp_rdata_o = peak_bin_q;
                    8'h1C: rsp_rdata_o = peak_mag2_q;
                    8'h20: rsp_rdata_o = {29'd0, 3'(state_q)};
                    default: rsp_err_o = 1'b1;
                endcase
            end
        end
    end

    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            state_q     <= S_IDLE;
            wr_idx_q    <= 7'd0;
            rd_idx_q    <= 7'd0;
            cnt_q       <= 7'd0;
            stage_q     <= 3'd0;
            bfly_q      <= 6'd0;
            ffts_done_q <= 32'd0;
            peak_bin_q  <= 32'd0;
            peak_mag2_q <= 32'd0;
            irq_q       <= 1'b0;
            cycle_cnt_q <= 64'd0;
            for (int i = 0; i < N; i = i + 1) begin
                in_re[i]  <= 16'sd0;
                in_im[i]  <= 16'sd0;
                buf_re[i] <= 16'sd0;
                buf_im[i] <= 16'sd0;
            end
        end else begin
            cycle_cnt_q <= cycle_cnt_q + 64'd1;

            case (state_q)
                S_BITREV: begin
                    buf_re[cnt_q] <= in_re[bitrev7(cnt_q)];
                    buf_im[cnt_q] <= in_im[bitrev7(cnt_q)];
                    if (cnt_q == 7'd127) begin
                        state_q <= S_BFLY;
                        stage_q <= 3'd0;
                        bfly_q  <= 6'd0;
                    end else begin
                        cnt_q <= cnt_q + 7'd1;
                    end
                end
                S_BFLY: begin
                    buf_re[idx0_w] <= signed'(y0_re_w[15:0]);
                    buf_im[idx0_w] <= signed'(y0_im_w[15:0]);
                    buf_re[idx1_w] <= signed'(y1_re_w[15:0]);
                    buf_im[idx1_w] <= signed'(y1_im_w[15:0]);
                    if (bfly_q == 6'd63) begin
                        bfly_q <= 6'd0;
                        if (stage_q == 3'd6) begin
                            state_q     <= S_PEAK;
                            cnt_q       <= 7'd0;
                            peak_bin_q  <= 32'd0;
                            peak_mag2_q <= 32'd0;
                        end else begin
                            stage_q <= stage_q + 3'd1;
                        end
                    end else begin
                        bfly_q <= bfly_q + 6'd1;
                    end
                end
                S_PEAK: begin
                    if (pk_mag2_w > peak_mag2_q) begin
                        peak_mag2_q <= pk_mag2_w;
                        peak_bin_q  <= {25'd0, cnt_q};
                    end
                    if (cnt_q == 7'd127) begin
                        state_q     <= S_IDLE;
                        ffts_done_q <= ffts_done_q + 32'd1;
                        irq_q       <= 1'b1;
                    end else begin
                        cnt_q <= cnt_q + 7'd1;
                    end
                end
                default: begin
                    if (req_valid_i && req_write_i && !rsp_err_o) begin
                        case (req_addr_i)
                            8'h00: begin
                                if (req_wdata_i == 32'd1) begin
                                    state_q <= S_BITREV;
                                    cnt_q   <= 7'd0;
                                end else if (req_wdata_i == 32'd2) begin
                                    wr_idx_q    <= 7'd0;
                                    rd_idx_q    <= 7'd0;
                                    ffts_done_q <= 32'd0;
                                    peak_bin_q  <= 32'd0;
                                    peak_mag2_q <= 32'd0;
                                    irq_q       <= 1'b0;
                                end
                            end
                            8'h04: wr_idx_q <= req_wdata_i[6:0];
                            8'h08: begin
                                in_re[wr_idx_q] <= signed'(req_wdata_i[15:0]);
                                in_im[wr_idx_q] <= signed'(req_wdata_i[31:16]);
                                wr_idx_q        <= wr_idx_q + 7'd1;
                            end
                            8'h0C: rd_idx_q <= req_wdata_i[6:0];
                            8'h10: if (req_wdata_i[0]) irq_q <= 1'b0;
                            default: ;
                        endcase
                    end
                end
            endcase
        end
    end
endmodule
