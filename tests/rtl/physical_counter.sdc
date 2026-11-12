# SPDX-License-Identifier: Apache-2.0
create_clock -name counter_clk -period 10.0 [get_ports clock]
set_input_delay 0.2 -clock counter_clk [get_ports {reset enable}]
set_output_delay 0.2 -clock counter_clk [all_outputs]
