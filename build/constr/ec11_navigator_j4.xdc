# Baseboard V3.9 J4 nets -> core V2.5 page6 BANK13 balls.
# EMIO6 A=J4-9 B13_L17_P U9; EMIO7 B=J4-10 B13_L17_N U8;
# EMIO8 PUSH=J4-11 B13_L12_N U10. Module powered at3.3V; common ground.
set_property -dict {PACKAGE_PIN U9 IOSTANDARD LVCMOS33 PULLUP true} [get_ports {knob_gpio[0]}]
set_property -dict {PACKAGE_PIN U8 IOSTANDARD LVCMOS33 PULLUP true} [get_ports {knob_gpio[1]}]
set_property -dict {PACKAGE_PIN U10 IOSTANDARD LVCMOS33 PULLUP true} [get_ports {knob_gpio[2]}]
