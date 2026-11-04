# Navigator baseboard V3.9 page5 J4 net names -> core V2.5 page6 package balls.
# Do not use the old package-ball annotations beside the baseboard core connector.
# EMIO0..5 = SCK MOSI CSn DC RESETn BLK; all BANK13 3.3V.
set_property -dict {PACKAGE_PIN U5 IOSTANDARD LVCMOS33 DRIVE 4 SLEW SLOW} [get_ports {lcd_gpio[0]}]
set_property -dict {PACKAGE_PIN T5 IOSTANDARD LVCMOS33 DRIVE 4 SLEW SLOW} [get_ports {lcd_gpio[1]}]
set_property -dict {PACKAGE_PIN V7 IOSTANDARD LVCMOS33 DRIVE 4 SLEW SLOW} [get_ports {lcd_gpio[2]}]
set_property -dict {PACKAGE_PIN U7 IOSTANDARD LVCMOS33 DRIVE 4 SLEW SLOW} [get_ports {lcd_gpio[3]}]
set_property -dict {PACKAGE_PIN W8 IOSTANDARD LVCMOS33 DRIVE 4 SLEW SLOW} [get_ports {lcd_gpio[4]}]
set_property -dict {PACKAGE_PIN V8 IOSTANDARD LVCMOS33 DRIVE 4 SLEW SLOW} [get_ports {lcd_gpio[5]}]
