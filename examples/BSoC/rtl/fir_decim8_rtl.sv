// SPDX-License-Identifier: Apache-2.0
// Synthesizable SystemVerilog RTL 136-Tap Programmable FIR Filter & /8 Decimator (fir_decim8_rtl).
// Supports all 136 programmable signed 16-bit tap coefficients via indirect SCR access
// (COEFF_IDX / COEFF_DATA), a 136-sample circular delay buffer, and sequential folded MAC
// execution on every decimation tick (default /8 decimation) with real RTL cycle tracking.
// NOTE: Clean-room synthesizable RTL for BSoC DSP Chain Stage 3.
module fir_decim8_rtl (
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
    localparam int MAX_TAPS = 136;

    logic signed [15:0] coeff_mem [0:MAX_TAPS-1];
    logic signed [15:0] delay_buf [0:MAX_TAPS-1];

    logic [7:0]  num_taps_q;
    logic [7:0]  decim_ratio_q;
    logic [3:0]  shift_right_q;
    logic [7:0]  coeff_idx_q;
    logic [7:0]  wr_ptr_q;
    logic [7:0]  decim_cnt_q;
    logic signed [15:0] in_sample_q;
    logic signed [31:0] out_sample_q;
    logic [31:0] in_count_q;
    logic [31:0] out_count_q;
    logic        irq_q;
    logic [63:0] cycle_cnt_q;

    // Sequential folded 136-tap MAC FSM
    logic        busy_q;
    logic [7:0]  mac_idx_q;
    logic [7:0]  rd_ptr_q;
    logic signed [47:0] mac_acc_q;

    logic signed [31:0] cur_sample32_w, cur_coeff32_w, prod32_w;
    logic signed [47:0] next_acc_w;
    logic signed [47:0] shifted_acc_w;
    logic [7:0]  next_wr_ptr_w, next_rd_ptr_w;

    assign cur_sample32_w = {{16{delay_buf[rd_ptr_q][15]}}, delay_buf[rd_ptr_q]};
    assign cur_coeff32_w  = {{16{coeff_mem[mac_idx_q][15]}}, coeff_mem[mac_idx_q]};
    assign prod32_w       = cur_sample32_w * cur_coeff32_w;
    assign next_acc_w     = mac_acc_q + {{16{prod32_w[31]}}, prod32_w};
    assign shifted_acc_w  = next_acc_w >>> shift_right_q;
    assign next_wr_ptr_w  = (wr_ptr_q == 8'd135) ? 8'd0 : (wr_ptr_q + 8'd1);
    assign next_rd_ptr_w  = (rd_ptr_q == 8'd0) ? 8'd135 : (rd_ptr_q - 8'd1);

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
                    8'h04, 8'h08, 8'h0C, 8'h10, 8'h1C, 8'h20: rsp_err_o = 1'b0;
                    default: rsp_err_o = 1'b1;
                endcase
            end else begin
                case (req_addr_i)
                    8'h00: rsp_rdata_o = out_count_q;
                    8'h04: rsp_rdata_o = {20'd0, shift_right_q, decim_ratio_q};
                    8'h08: rsp_rdata_o = {24'd0, num_taps_q};
                    8'h0C: rsp_rdata_o = {{16{in_sample_q[15]}}, unsigned'(in_sample_q)};
                    8'h10: rsp_rdata_o = {31'd0, irq_q};
                    8'h14: rsp_rdata_o = unsigned'(out_sample_q);
                    8'h18: rsp_rdata_o = in_count_q;
                    8'h1C: rsp_rdata_o = {24'd0, coeff_idx_q};
                    8'h20: rsp_rdata_o = (coeff_idx_q < 8'd136)
                        ? {{16{coeff_mem[coeff_idx_q][15]}}, unsigned'(coeff_mem[coeff_idx_q])}
                        : 32'd0;
                    8'h24: rsp_rdata_o = {31'd0, busy_q};
                    default: rsp_err_o = 1'b1;
                endcase
            end
        end
    end

    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            num_taps_q    <= 8'd136;
            decim_ratio_q <= 8'd8;
            shift_right_q <= 4'd10;
            coeff_idx_q   <= 8'd0;
            wr_ptr_q      <= 8'd0;
            decim_cnt_q   <= 8'd0;
            in_sample_q   <= 16'sd0;
            out_sample_q  <= 32'sd0;
            in_count_q    <= 32'd0;
            out_count_q   <= 32'd0;
            irq_q         <= 1'b0;
            cycle_cnt_q   <= 64'd0;
            busy_q        <= 1'b0;
            mac_idx_q     <= 8'd0;
            rd_ptr_q      <= 8'd0;
            mac_acc_q     <= 48'sd0;
            for (int i = 0; i < MAX_TAPS; i = i + 1) begin
                coeff_mem[i] <= 16'sd0;
                delay_buf[i] <= 16'sd0;
            end
        end else begin
            cycle_cnt_q <= cycle_cnt_q + 64'd1;

            if (busy_q) begin
                mac_acc_q <= next_acc_w;
                if (mac_idx_q + 8'd1 >= num_taps_q) begin
                    busy_q       <= 1'b0;
                    out_sample_q <= signed'(shifted_acc_w[31:0]);
                    out_count_q  <= out_count_q + 32'd1;
                    irq_q        <= 1'b1;
                end else begin
                    mac_idx_q <= mac_idx_q + 8'd1;
                    rd_ptr_q  <= next_rd_ptr_w;
                end
            end else if (req_valid_i && req_write_i && !rsp_err_o) begin
                case (req_addr_i)
                    8'h00: begin
                        if (req_wdata_i == 32'd1) begin
                            delay_buf[wr_ptr_q] <= in_sample_q;
                            in_count_q          <= in_count_q + 32'd1;
                            if (decim_cnt_q + 8'd1 >= decim_ratio_q) begin
                                decim_cnt_q <= 8'd0;
                                busy_q      <= 1'b1;
                                mac_idx_q   <= 8'd0;
                                rd_ptr_q    <= wr_ptr_q;
                                mac_acc_q   <= 48'sd0;
                            end else begin
                                decim_cnt_q <= decim_cnt_q + 8'd1;
                            end
                            wr_ptr_q <= next_wr_ptr_w;
                        end else if (req_wdata_i == 32'd2) begin
                            wr_ptr_q     <= 8'd0;
                            decim_cnt_q  <= 8'd0;
                            out_sample_q <= 32'sd0;
                            in_count_q   <= 32'd0;
                            out_count_q  <= 32'd0;
                            irq_q        <= 1'b0;
                            busy_q       <= 1'b0;
                            for (int j = 0; j < MAX_TAPS; j = j + 1) begin
                                delay_buf[j] <= 16'sd0;
                            end
                        end else if (req_wdata_i == 32'd3) begin
                            busy_q    <= 1'b1;
                            mac_idx_q <= 8'd0;
                            rd_ptr_q  <= (wr_ptr_q == 8'd0) ? 8'd135 : (wr_ptr_q - 8'd1);
                            mac_acc_q <= 48'sd0;
                        end
                    end
                    8'h04: begin
                        decim_ratio_q <= (req_wdata_i[7:0] == 8'd0) ? 8'd1 : req_wdata_i[7:0];
                        shift_right_q <= req_wdata_i[11:8];
                    end
                    8'h08: begin
                        if (req_wdata_i[7:0] >= 8'd1 && req_wdata_i[7:0] <= 8'd136) begin
                            num_taps_q <= req_wdata_i[7:0];
                        end
                    end
                    8'h0C: in_sample_q <= signed'(req_wdata_i[15:0]);
                    8'h10: if (req_wdata_i[0]) irq_q <= 1'b0;
                    8'h1C: begin
                        if (req_wdata_i[7:0] < 8'd136) coeff_idx_q <= req_wdata_i[7:0];
                    end
                    8'h20: coeff_mem[coeff_idx_q] <= signed'(req_wdata_i[15:0]);
                    default: ;
                endcase
            end
        end
    end
endmodule
