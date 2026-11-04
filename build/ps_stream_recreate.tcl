################################################################
# This is a generated script based on design: ps_stream
#
# Though there are limitations about the generated script,
# the main purpose of this utility is to make learning
# IP Integrator Tcl commands easier.
################################################################

namespace eval _tcl {
proc get_script_folder {} {
   set script_path [file normalize [info script]]
   set script_folder [file dirname $script_path]
   return $script_folder
}
}
variable script_folder
set script_folder [_tcl::get_script_folder]

################################################################
# Check if script is running in correct Vivado version.
################################################################
set scripts_vivado_version 2025.2
set current_vivado_version [version -short]

if { [string first $scripts_vivado_version $current_vivado_version] == -1 } {
   puts ""
   if { [string compare $scripts_vivado_version $current_vivado_version] > 0 } {
      catch {common::send_gid_msg -ssname BD::TCL -id 2042 -severity "ERROR" " This script was generated using Vivado <$scripts_vivado_version> and is being run in <$current_vivado_version> of Vivado. Sourcing the script failed since it was created with a future version of Vivado."}

   } else {
     catch {common::send_gid_msg -ssname BD::TCL -id 2041 -severity "ERROR" "This script was generated using Vivado <$scripts_vivado_version> and is being run in <$current_vivado_version> of Vivado. Please run the script in Vivado <$scripts_vivado_version> then open the design in Vivado <$current_vivado_version>. Upgrade the design by running \"Tools => Report => Report IP Status...\", then run write_bd_tcl to create an updated script."}

   }

   return 1
}

################################################################
# START
################################################################

# To test this script, run the following commands from Vivado Tcl console:
# source ps_stream_script.tcl


# The design that will be created by this Tcl script contains the following 
# module references:
# eeg_stream_ip

# Please add the sources of those modules before sourcing this Tcl script.

# If there is no project opened, this script will create a
# project, but make sure you do not have an existing project
# <./myproj/project_1.xpr> in the current working folder.

set list_projs [get_projects -quiet]
if { $list_projs eq "" } {
   create_project project_1 myproj -part xc7z020clg400-2
}


# CHANGE DESIGN NAME HERE
variable design_name
set design_name ps_stream

# If you do not already have an existing IP Integrator design open,
# you can create a design using the following command:
#    create_bd_design $design_name

# Creating design if needed
set errMsg ""
set nRet 0

set cur_design [current_bd_design -quiet]
set list_cells [get_bd_cells -quiet]

if { ${design_name} eq "" } {
   # USE CASES:
   #    1) Design_name not set

   set errMsg "Please set the variable <design_name> to a non-empty value."
   set nRet 1

} elseif { ${cur_design} ne "" && ${list_cells} eq "" } {
   # USE CASES:
   #    2): Current design opened AND is empty AND names same.
   #    3): Current design opened AND is empty AND names diff; design_name NOT in project.
   #    4): Current design opened AND is empty AND names diff; design_name exists in project.

   if { $cur_design ne $design_name } {
      common::send_gid_msg -ssname BD::TCL -id 2001 -severity "INFO" "Changing value of <design_name> from <$design_name> to <$cur_design> since current design is empty."
      set design_name [get_property NAME $cur_design]
   }
   common::send_gid_msg -ssname BD::TCL -id 2002 -severity "INFO" "Constructing design in IPI design <$cur_design>..."

} elseif { ${cur_design} ne "" && $list_cells ne "" && $cur_design eq $design_name } {
   # USE CASES:
   #    5) Current design opened AND has components AND same names.

   set errMsg "Design <$design_name> already exists in your project, please set the variable <design_name> to another value."
   set nRet 1
} elseif { [get_files -quiet ${design_name}.bd] ne "" } {
   # USE CASES: 
   #    6) Current opened design, has components, but diff names, design_name exists in project.
   #    7) No opened design, design_name exists in project.

   set errMsg "Design <$design_name> already exists in your project, please set the variable <design_name> to another value."
   set nRet 2

} else {
   # USE CASES:
   #    8) No opened design, design_name not in project.
   #    9) Current opened design, has components, but diff names, design_name not in project.

   common::send_gid_msg -ssname BD::TCL -id 2003 -severity "INFO" "Currently there is no design <$design_name> in project, so creating one..."

   create_bd_design $design_name

   common::send_gid_msg -ssname BD::TCL -id 2004 -severity "INFO" "Making design <$design_name> as current_bd_design."
   current_bd_design $design_name

}

common::send_gid_msg -ssname BD::TCL -id 2005 -severity "INFO" "Currently the variable <design_name> is equal to \"$design_name\"."

if { $nRet != 0 } {
   catch {common::send_gid_msg -ssname BD::TCL -id 2006 -severity "ERROR" $errMsg}
   return $nRet
}

set bCheckIPsPassed 1
##################################################################
# CHECK IPs
##################################################################
set bCheckIPs 1
if { $bCheckIPs == 1 } {
   set list_check_ips "\ 
xilinx.com:ip:processing_system7:5.5\
xilinx.com:ip:xlconstant:1.1\
xilinx.com:ip:xlslice:1.0\
xilinx.com:ip:xlconcat:2.1\
xilinx.com:ip:axi_protocol_converter:2.1\
xilinx.com:ip:axi_clock_converter:2.1\
xilinx.com:ip:smartconnect:1.0\
xilinx.com:ip:axi_fifo_mm_s:4.3\
"

   set list_ips_missing ""
   common::send_gid_msg -ssname BD::TCL -id 2011 -severity "INFO" "Checking if the following IPs exist in the project's IP catalog: $list_check_ips ."

   foreach ip_vlnv $list_check_ips {
      set ip_obj [get_ipdefs -all $ip_vlnv]
      if { $ip_obj eq "" } {
         lappend list_ips_missing $ip_vlnv
      }
   }

   if { $list_ips_missing ne "" } {
      catch {common::send_gid_msg -ssname BD::TCL -id 2012 -severity "ERROR" "The following IPs are not found in the IP Catalog:\n  $list_ips_missing\n\nResolution: Please add the repository containing the IP(s) to the project." }
      set bCheckIPsPassed 0
   }

}

##################################################################
# CHECK Modules
##################################################################
set bCheckModules 1
if { $bCheckModules == 1 } {
   set list_check_mods "\ 
eeg_stream_ip\
"

   set list_mods_missing ""
   common::send_gid_msg -ssname BD::TCL -id 2020 -severity "INFO" "Checking if the following modules exist in the project's sources: $list_check_mods ."

   foreach mod_vlnv $list_check_mods {
      if { [can_resolve_reference $mod_vlnv] == 0 } {
         lappend list_mods_missing $mod_vlnv
      }
   }

   if { $list_mods_missing ne "" } {
      catch {common::send_gid_msg -ssname BD::TCL -id 2021 -severity "ERROR" "The following module(s) are not found in the project: $list_mods_missing" }
      common::send_gid_msg -ssname BD::TCL -id 2022 -severity "INFO" "Please add source files for the missing module(s) above."
      set bCheckIPsPassed 0
   }
}

if { $bCheckIPsPassed != 1 } {
  common::send_gid_msg -ssname BD::TCL -id 2023 -severity "WARNING" "Will not continue with creation of design due to the error(s) above."
  return 3
}

##################################################################
# DESIGN PROCs
##################################################################



# Procedure to create entire design; Provide argument to make
# procedure reusable. If parentCell is "", will use root.
proc create_root_design { parentCell } {

  variable script_folder
  variable design_name

  if { $parentCell eq "" } {
     set parentCell [get_bd_cells /]
  }

  # Get object for parentCell
  set parentObj [get_bd_cells $parentCell]
  if { $parentObj == "" } {
     catch {common::send_gid_msg -ssname BD::TCL -id 2090 -severity "ERROR" "Unable to find parent cell <$parentCell>!"}
     return
  }

  # Make sure parentObj is hier blk
  set parentType [get_property TYPE $parentObj]
  if { $parentType ne "hier" } {
     catch {common::send_gid_msg -ssname BD::TCL -id 2091 -severity "ERROR" "Parent <$parentObj> has TYPE = <$parentType>. Expected to be <hier>."}
     return
  }

  # Save current instance; Restore later
  set oldCurInst [current_bd_instance .]

  # Set parent object as current
  current_bd_instance $parentObj


  # Create interface ports
  set DDR_0 [ create_bd_intf_port -mode Master -vlnv xilinx.com:interface:ddrx_rtl:1.0 DDR_0 ]

  set FIXED_IO_0 [ create_bd_intf_port -mode Master -vlnv xilinx.com:display_processing_system7:fixedio_rtl:1.0 FIXED_IO_0 ]


  # Create ports
  set lcd_gpio [ create_bd_port -dir O -from 5 -to 0 lcd_gpio ]
  set knob_gpio [ create_bd_port -dir I -from 2 -to 0 knob_gpio ]

  # Create instance: ps7, and set properties
  set ps7 [ create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 ps7 ]
  set_property -dict [list \
    CONFIG.PCW_ACT_APU_PERIPHERAL_FREQMHZ {666.666687} \
    CONFIG.PCW_ACT_CAN_PERIPHERAL_FREQMHZ {10.000000} \
    CONFIG.PCW_ACT_DCI_PERIPHERAL_FREQMHZ {10.158730} \
    CONFIG.PCW_ACT_ENET0_PERIPHERAL_FREQMHZ {10.000000} \
    CONFIG.PCW_ACT_ENET1_PERIPHERAL_FREQMHZ {10.000000} \
    CONFIG.PCW_ACT_FPGA0_PERIPHERAL_FREQMHZ {100.000000} \
    CONFIG.PCW_ACT_FPGA1_PERIPHERAL_FREQMHZ {10.000000} \
    CONFIG.PCW_ACT_FPGA2_PERIPHERAL_FREQMHZ {10.000000} \
    CONFIG.PCW_ACT_FPGA3_PERIPHERAL_FREQMHZ {10.000000} \
    CONFIG.PCW_ACT_PCAP_PERIPHERAL_FREQMHZ {200.000000} \
    CONFIG.PCW_ACT_QSPI_PERIPHERAL_FREQMHZ {10.000000} \
    CONFIG.PCW_ACT_SDIO_PERIPHERAL_FREQMHZ {10.000000} \
    CONFIG.PCW_ACT_SMC_PERIPHERAL_FREQMHZ {10.000000} \
    CONFIG.PCW_ACT_SPI_PERIPHERAL_FREQMHZ {10.000000} \
    CONFIG.PCW_ACT_TPIU_PERIPHERAL_FREQMHZ {200.000000} \
    CONFIG.PCW_ACT_TTC0_CLK0_PERIPHERAL_FREQMHZ {111.111115} \
    CONFIG.PCW_ACT_TTC0_CLK1_PERIPHERAL_FREQMHZ {111.111115} \
    CONFIG.PCW_ACT_TTC0_CLK2_PERIPHERAL_FREQMHZ {111.111115} \
    CONFIG.PCW_ACT_TTC1_CLK0_PERIPHERAL_FREQMHZ {111.111115} \
    CONFIG.PCW_ACT_TTC1_CLK1_PERIPHERAL_FREQMHZ {111.111115} \
    CONFIG.PCW_ACT_TTC1_CLK2_PERIPHERAL_FREQMHZ {111.111115} \
    CONFIG.PCW_ACT_UART_PERIPHERAL_FREQMHZ {10.000000} \
    CONFIG.PCW_ACT_WDT_PERIPHERAL_FREQMHZ {111.111115} \
    CONFIG.PCW_CLK0_FREQ {100000000} \
    CONFIG.PCW_CLK1_FREQ {10000000} \
    CONFIG.PCW_CLK2_FREQ {10000000} \
    CONFIG.PCW_CLK3_FREQ {10000000} \
    CONFIG.PCW_DDR_RAM_HIGHADDR {0x1FFFFFFF} \
    CONFIG.PCW_EN_CLK0_PORT {1} \
    CONFIG.PCW_EN_EMIO_GPIO {1} \
    CONFIG.PCW_EN_RST0_PORT {1} \
    CONFIG.PCW_FCLK_CLK0_BUF {TRUE} \
    CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ {100} \
    CONFIG.PCW_FPGA_FCLK0_ENABLE {1} \
    CONFIG.PCW_GPIO_EMIO_GPIO_ENABLE {1} \
    CONFIG.PCW_GPIO_EMIO_GPIO_IO {9} \
    CONFIG.PCW_GPIO_EMIO_GPIO_WIDTH {9} \
    CONFIG.PCW_UIPARAM_ACT_DDR_FREQ_MHZ {533.333374} \
    CONFIG.PCW_USE_M_AXI_GP0 {1} \
  ] $ps7


  # Create instance: lcd_inputs_zero, and set properties
  set lcd_inputs_zero [ create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 lcd_inputs_zero ]
  set_property -dict [list \
    CONFIG.CONST_VAL {0} \
    CONFIG.CONST_WIDTH {6} \
  ] $lcd_inputs_zero


  # Create instance: lcd_outputs, and set properties
  set lcd_outputs [ create_bd_cell -type ip -vlnv xilinx.com:ip:xlslice:1.0 lcd_outputs ]
  set_property -dict [list \
    CONFIG.DIN_FROM {5} \
    CONFIG.DIN_TO {0} \
    CONFIG.DIN_WIDTH {9} \
    CONFIG.DOUT_WIDTH {6} \
  ] $lcd_outputs


  # Create instance: gpio_inputs, and set properties
  set gpio_inputs [ create_bd_cell -type ip -vlnv xilinx.com:ip:xlconcat:2.1 gpio_inputs ]
  set_property CONFIG.NUM_PORTS {2} $gpio_inputs


  # Create instance: ps_protocol, and set properties
  set ps_protocol [ create_bd_cell -type ip -vlnv xilinx.com:ip:axi_protocol_converter:2.1 ps_protocol ]
  set_property -dict [list \
    CONFIG.ADDR_WIDTH {32} \
    CONFIG.DATA_WIDTH {32} \
    CONFIG.ID_WIDTH {12} \
    CONFIG.MI_PROTOCOL {AXI4LITE} \
    CONFIG.SI_PROTOCOL {AXI3} \
  ] $ps_protocol


  # Create instance: eeg_core, and set properties
  set block_name eeg_stream_ip
  set block_cell_name eeg_core
  if { [catch {set eeg_core [create_bd_cell -type module -reference $block_name $block_cell_name] } errmsg] } {
     catch {common::send_gid_msg -ssname BD::TCL -id 2095 -severity "ERROR" "Unable to add referenced block <$block_name>. Please add the files for ${block_name}'s definition into the project."}
     return 1
   } elseif { $eeg_core eq "" } {
     catch {common::send_gid_msg -ssname BD::TCL -id 2096 -severity "ERROR" "Unable to referenced block <$block_name>. Please add the files for ${block_name}'s definition into the project."}
     return 1
   }
    set_property -dict [list \
    CONFIG.CLK_FREQ {80000000} \
    CONFIG.INPUT_MODE {1} \
    CONFIG.LUT_FILE [file normalize [file join $script_folder .. src pl log2_lut.hex]] \
  ] $eeg_core


  # Create instance: control_cdc, and set properties
  set control_cdc [ create_bd_cell -type ip -vlnv xilinx.com:ip:axi_clock_converter:2.1 control_cdc ]
  set_property -dict [list \
    CONFIG.ACLK_ASYNC {1} \
    CONFIG.ADDR_WIDTH {32} \
    CONFIG.DATA_WIDTH {32} \
    CONFIG.PROTOCOL {AXI4LITE} \
    CONFIG.SYNCHRONIZATION_STAGES {3} \
  ] $control_cdc


  # Create instance: fabric, and set properties
  set fabric [ create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 fabric ]
  set_property -dict [list \
    CONFIG.NUM_MI {2} \
    CONFIG.NUM_SI {1} \
  ] $fabric


  # Create instance: sample_fifo, and set properties
  set sample_fifo [ create_bd_cell -type ip -vlnv xilinx.com:ip:axi_fifo_mm_s:4.3 sample_fifo ]
  set_property -dict [list \
    CONFIG.C_AXI_STR_TXD_TDATA_WIDTH {32} \
    CONFIG.C_DATA_INTERFACE_TYPE {0} \
    CONFIG.C_S_AXI_DATA_WIDTH {32} \
    CONFIG.C_TX_FIFO_DEPTH {512} \
    CONFIG.C_USE_RX_DATA {0} \
    CONFIG.C_USE_TX_CTRL {0} \
    CONFIG.C_USE_TX_CUT_THROUGH {0} \
    CONFIG.C_USE_TX_DATA {1} \
  ] $sample_fifo


  # Create instance: uart_idle, and set properties
  set uart_idle [ create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 uart_idle ]
  set_property -dict [list \
    CONFIG.CONST_VAL {1} \
    CONFIG.CONST_WIDTH {1} \
  ] $uart_idle


  # Create interface connections
  connect_bd_intf_net -intf_net control_cdc_M_AXI [get_bd_intf_pins control_cdc/M_AXI] [get_bd_intf_pins fabric/S00_AXI]
  connect_bd_intf_net -intf_net fabric_M00_AXI [get_bd_intf_pins fabric/M00_AXI] [get_bd_intf_pins eeg_core/s_axi]
  connect_bd_intf_net -intf_net fabric_M01_AXI [get_bd_intf_pins fabric/M01_AXI] [get_bd_intf_pins sample_fifo/S_AXI]
  connect_bd_intf_net -intf_net ps7_DDR [get_bd_intf_ports DDR_0] [get_bd_intf_pins ps7/DDR]
  connect_bd_intf_net -intf_net ps7_FIXED_IO [get_bd_intf_ports FIXED_IO_0] [get_bd_intf_pins ps7/FIXED_IO]
  connect_bd_intf_net -intf_net ps7_M_AXI_GP0 [get_bd_intf_pins ps7/M_AXI_GP0] [get_bd_intf_pins ps_protocol/S_AXI]
  connect_bd_intf_net -intf_net ps_protocol_M_AXI [get_bd_intf_pins ps_protocol/M_AXI] [get_bd_intf_pins control_cdc/S_AXI]
  connect_bd_intf_net -intf_net sample_fifo_AXI_STR_TXD [get_bd_intf_pins sample_fifo/AXI_STR_TXD] [get_bd_intf_pins eeg_core/S_AXIS]

  # Create port connections
  connect_bd_net -net eeg_core_axis_aresetn  [get_bd_pins eeg_core/axis_aresetn] \
  [get_bd_pins control_cdc/m_axi_aresetn] \
  [get_bd_pins fabric/aresetn] \
  [get_bd_pins sample_fifo/s_axi_aresetn]
  connect_bd_net -net eeg_core_axis_clk  [get_bd_pins eeg_core/axis_clk] \
  [get_bd_pins control_cdc/m_axi_aclk] \
  [get_bd_pins fabric/aclk] \
  [get_bd_pins sample_fifo/s_axi_aclk]
  connect_bd_net -net gpio_inputs_dout  [get_bd_pins gpio_inputs/dout] \
  [get_bd_pins ps7/GPIO_I]
  connect_bd_net -net knob_gpio_1  [get_bd_ports knob_gpio] \
  [get_bd_pins gpio_inputs/In1]
  connect_bd_net -net lcd_inputs_zero_dout  [get_bd_pins lcd_inputs_zero/dout] \
  [get_bd_pins gpio_inputs/In0]
  connect_bd_net -net lcd_outputs_Dout  [get_bd_pins lcd_outputs/Dout] \
  [get_bd_ports lcd_gpio]
  connect_bd_net -net ps7_FCLK_CLK0  [get_bd_pins ps7/FCLK_CLK0] \
  [get_bd_pins ps7/M_AXI_GP0_ACLK] \
  [get_bd_pins ps_protocol/aclk] \
  [get_bd_pins eeg_core/clk_in] \
  [get_bd_pins control_cdc/s_axi_aclk]
  connect_bd_net -net ps7_FCLK_RESET0_N  [get_bd_pins ps7/FCLK_RESET0_N] \
  [get_bd_pins ps_protocol/aresetn] \
  [get_bd_pins eeg_core/rst_n_ext] \
  [get_bd_pins control_cdc/s_axi_aresetn]
  connect_bd_net -net ps7_GPIO_O  [get_bd_pins ps7/GPIO_O] \
  [get_bd_pins lcd_outputs/Din]
  connect_bd_net -net uart_idle_dout  [get_bd_pins uart_idle/dout] \
  [get_bd_pins eeg_core/uart_rx]

  # Create address segments
  assign_bd_address -offset 0x43D00000 -range 0x00010000 -target_address_space [get_bd_addr_spaces ps7/Data] [get_bd_addr_segs eeg_core/s_axi/reg0] -force
  assign_bd_address -offset 0x43D10000 -range 0x00010000 -target_address_space [get_bd_addr_spaces ps7/Data] [get_bd_addr_segs sample_fifo/S_AXI/Mem0] -force


  # Restore current instance
  current_bd_instance $oldCurInst

  validate_bd_design
  save_bd_design
}
# End of create_root_design()


##################################################################
# MAIN FLOW
##################################################################

create_root_design ""
