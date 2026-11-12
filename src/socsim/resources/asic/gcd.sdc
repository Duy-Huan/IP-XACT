# SPDX-License-Identifier: Apache-2.0
# Reference GCD leaf constraints; no complete-SoC timing assumption is implied.
create_clock -name core_clk -period 10.0 [get_ports clock]
set_input_delay 0.2 -clock core_clk [get_ports {reset io_clock io_reset io_input_valid io_x* io_y* io_output_ready}]
set_output_delay 0.2 -clock core_clk [all_outputs]
