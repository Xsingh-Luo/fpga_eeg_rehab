// Plain Verilog module-reference shell for Vivado BD; algorithm remains SystemVerilog.
`timescale 1ns/1ps
module eeg_stream_ip #(
    parameter integer INPUT_MODE=1,
    parameter integer CLK_FREQ=80000000,
    parameter LUT_FILE="log2_lut.hex"
) (
    (* X_INTERFACE_INFO="xilinx.com:signal:clock:1.0 clk_in CLK",
       X_INTERFACE_PARAMETER="FREQ_HZ 100000000" *)
    input wire clk_in,
    input wire rst_n_ext,
    input wire uart_rx,
    output wire uart_tx,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 S_AXIS TDATA" *)
    input wire [31:0] s_axis_tdata,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 S_AXIS TVALID" *)
    input wire s_axis_tvalid,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 S_AXIS TREADY" *)
    output wire s_axis_tready,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 S_AXIS TLAST" *)
    input wire s_axis_tlast,
    (* X_INTERFACE_INFO="xilinx.com:signal:clock:1.0 axis_clk CLK",
       X_INTERFACE_PARAMETER="FREQ_HZ 80000000, ASSOCIATED_BUSIF S_AXIS:S_AXI, ASSOCIATED_RESET axis_aresetn" *)
    output wire axis_clk,
    (* X_INTERFACE_INFO="xilinx.com:signal:reset:1.0 axis_aresetn RST",
       X_INTERFACE_PARAMETER="POLARITY ACTIVE_LOW" *)
    output wire axis_aresetn,
    output wire [31:0] stream_format_errors,
    input wire [31:0] s_axi_awaddr,
    input wire s_axi_awvalid,
    output wire s_axi_awready,
    input wire [31:0] s_axi_wdata,
    input wire [3:0] s_axi_wstrb,
    input wire s_axi_wvalid,
    output wire s_axi_wready,
    output wire [1:0] s_axi_bresp,
    output wire s_axi_bvalid,
    input wire s_axi_bready,
    input wire [31:0] s_axi_araddr,
    input wire s_axi_arvalid,
    output wire s_axi_arready,
    output wire [31:0] s_axi_rdata,
    output wire [1:0] s_axi_rresp,
    output wire s_axi_rvalid,
    input wire s_axi_rready,
    output wire led_pkt,led_label,dec_valid
);
    eeg_csp_svm_top #(.INPUT_MODE(INPUT_MODE),.CLK_FREQ(CLK_FREQ),.LUT_FILE(LUT_FILE)) core (
        .clk_in(clk_in),.rst_n_ext(rst_n_ext),.uart_rx(uart_rx),.uart_tx(uart_tx),
        .s_axis_tdata(s_axis_tdata),.s_axis_tvalid(s_axis_tvalid),.s_axis_tready(s_axis_tready),.s_axis_tlast(s_axis_tlast),
        .axis_clk(axis_clk),.axis_aresetn(axis_aresetn),.stream_format_errors(stream_format_errors),
        .s_axi_awaddr(s_axi_awaddr),.s_axi_awvalid(s_axi_awvalid),.s_axi_awready(s_axi_awready),
        .s_axi_wdata(s_axi_wdata),.s_axi_wstrb(s_axi_wstrb),.s_axi_wvalid(s_axi_wvalid),.s_axi_wready(s_axi_wready),
        .s_axi_bresp(s_axi_bresp),.s_axi_bvalid(s_axi_bvalid),.s_axi_bready(s_axi_bready),
        .s_axi_araddr(s_axi_araddr),.s_axi_arvalid(s_axi_arvalid),.s_axi_arready(s_axi_arready),
        .s_axi_rdata(s_axi_rdata),.s_axi_rresp(s_axi_rresp),.s_axi_rvalid(s_axi_rvalid),.s_axi_rready(s_axi_rready),
        .led_pkt(led_pkt),.led_label(led_label),.dec_valid(dec_valid));
endmodule
