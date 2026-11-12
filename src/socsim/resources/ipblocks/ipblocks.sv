// SPDX-License-Identifier: Apache-2.0
// Technology selection only: preserve the real OpenTitan generic buffer.
module prim_buf #(parameter int Width=1)(input [Width-1:0] in_i, output [Width-1:0] out_o);
  prim_generic_buf #(.Width(Width)) impl (.*);
endmodule

module usp_ipblocks(
  input logic clk_i, rst_ni,
  input logic cy_valid_i, cy_write_i,
  input logic [3:0] cy_addr_i,
  input logic [31:0] data_i,
  output logic cy_error_o, cy_irq_o,
  output logic [31:0] cy_data_o,
  input logic ot_valid_i, ot_write_i, ot_corrupt_i, ot_ready_i,
  input logic [31:0] ot_addr_i,
  output logic ot_accept_o, ot_valid_o, ot_error_o, ot_irq_o, ot_alert_o, ot_integrity_o,
  output logic [31:0] ot_data_o
);
  // Original MMIO shim around the actual Chipyard GCD Chisel core. Its status
  // and four offsets follow GCD.scala; completion IRQ is explicitly shim-owned.
  logic [31:0] x_q, gcd;
  logic input_ready, output_valid, busy;
  assign cy_irq_o = output_valid;
  always_comb begin
    cy_error_o = 1'b0;
    cy_data_o = 0;
    case (cy_addr_i)
      0: begin
        cy_data_o = {30'b0, input_ready, output_valid};
        cy_error_o = cy_write_i;
      end
      4: cy_error_o = !cy_write_i || !input_ready;
      8: cy_error_o = !cy_write_i || !input_ready || x_q == 0 || data_i == 0;
      12: begin cy_data_o = gcd; cy_error_o = cy_write_i || !output_valid; end
      default: cy_error_o = 1'b1;
    endcase
  end
  always_ff @(posedge clk_i) begin
    if (!rst_ni) x_q <= 0;
    else if (cy_valid_i && cy_write_i && !cy_error_o && cy_addr_i == 4) x_q <= data_i;
  end
  GCDMMIOChiselModule cy (
    .clock(clk_i), .reset(!rst_ni), .io_clock(clk_i), .io_reset(!rst_ni),
    .io_x(x_q), .io_y(data_i), .io_input_ready(input_ready),
    .io_input_valid(cy_valid_i && cy_write_i && !cy_error_o && cy_addr_i == 8),
    .io_output_ready(cy_valid_i && !cy_write_i && !cy_error_o && cy_addr_i == 12),
    .io_output_valid(output_valid), .io_gcd(gcd), .io_busy(busy)
  );

  // Actual TL-UL bus and register bank; integrity generation/checking stays on.
  import tlul_pkg::*;
  tl_h2d_t req;
  tl_d2h_t rsp;
  logic [31:0] pins, enables, interrupts;
  prim_alert_pkg::alert_tx_t [0:0] alerts;
  logic [63:0] response_code, data_code;
  always_comb begin
    req = TL_H2D_DEFAULT;
    req.a_valid = ot_valid_i;
    req.a_opcode = ot_write_i ? PutFullData : Get;
    req.a_size = 2;
    req.a_mask = 4'hf;
    req.a_address = ot_addr_i;
    req.a_data = data_i;
    req.a_user.tl_type = DataType;
    req.a_user.cmd_intg = get_cmd_intg(req) ^ (ot_corrupt_i ? 7'h1 : 7'h0);
    req.a_user.data_intg = get_data_intg(data_i);
    req.d_ready = ot_ready_i;
  end
  gpio #(.AlertAsyncOn('0)) ot (
    .clk_i, .rst_ni, .tl_i(req), .tl_o(rsp), .intr_gpio_o(interrupts),
    .alert_rx_i('{prim_alert_pkg::ALERT_RX_DEFAULT}), .alert_tx_o(alerts),
    .cio_gpio_i(pins & enables), .cio_gpio_o(pins), .cio_gpio_en_o(enables)
  );
  assign response_code = prim_secded_pkg::prim_secded_64_57_enc(
      57'(extract_d2h_rsp_intg(rsp)));
  assign data_code = prim_secded_pkg::prim_secded_64_57_enc(57'(rsp.d_data));
  assign ot_integrity_o = rsp.d_user.rsp_intg == response_code[63:57] &&
                          rsp.d_user.data_intg == data_code[63:57];
  assign ot_accept_o = rsp.a_ready;
  assign ot_valid_o = rsp.d_valid;
  assign ot_error_o = rsp.d_error;
  assign ot_data_o = rsp.d_data;
  assign ot_irq_o = |interrupts;
  assign ot_alert_o = alerts[0].alert_p;
endmodule
