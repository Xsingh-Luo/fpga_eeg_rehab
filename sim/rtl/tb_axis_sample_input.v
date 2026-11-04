`timescale 1ns/1ps
module tb_axis_sample_input;
    reg clk=0, rst_n=0, enable=0, valid=0, last=0, ready=0;
    reg [31:0] data=0;
    wire input_ready, output_valid;
    wire signed [23:0] sample [0:2];
    wire [31:0] errors;
    always #5 clk=~clk;
    axis_sample_input #(.N_CH(3)) dut (
        .clk(clk),.rst_n(rst_n),.enable(enable),.s_axis_tdata(data),
        .s_axis_tvalid(valid),.s_axis_tready(input_ready),.s_axis_tlast(last),
        .sample(sample),.sample_valid(output_valid),.sample_ready(ready),.format_errors(errors));

    task beat(input [31:0] value, input end_frame);
        begin
            @(negedge clk); data=value; last=end_frame; valid=1;
            @(posedge clk); while (!input_ready) @(posedge clk);
            @(negedge clk); valid=0;
        end
    endtask
    task take(input integer a, input integer b, input integer c);
        begin
            if (!output_valid || $signed(sample[0])!=a || $signed(sample[1])!=b || $signed(sample[2])!=c)
                $fatal(1,"Assembled sample mismatch");
            @(negedge clk); ready=1;
            @(posedge clk); @(negedge clk); ready=0;
        end
    endtask
    initial begin
        repeat(3) @(negedge clk); rst_n=1;
        repeat(2) @(posedge clk); #1;
        if(input_ready) $fatal(1,"Input accepted while disabled");
        @(negedge clk); enable=1;
        beat(32'hFF800000,0); beat(32'hFFFFFFFF,0); beat(32'h007FFFFF,1);
        // Backpressure must preserve the complete sample and deassert TREADY.
        repeat(12) begin
            @(posedge clk); #1;
            if(input_ready || !output_valid || sample[0]!==24'h800000 || sample[1]!==24'hFFFFFF || sample[2]!==24'h7FFFFF)
                $fatal(1,"Backpressured output changed");
        end
        take(-8388608,-1,8388607);
        beat(1,1); // early TLAST
        beat(2,0); beat(3,0); beat(4,0); beat(5,1); // missing TLAST, then resync
        beat(6,0); beat(7,0); beat(32'h01000008,1); // noncanonical sign extension
        #1;
        if(errors!=3 || output_valid) $fatal(1,"Malformed input was not rejected");
        beat(100,0); // partial frame must be flushed by disable
        @(negedge clk); enable=0;
        repeat(2) @(negedge clk); enable=1;
        beat(-10,0); beat(0,0); beat(10,1); take(-10,0,10);
        // Reset a pending complete sample and partial input state.
        beat(20,0); beat(21,0); beat(22,1);
        @(negedge clk); rst_n=0;
        repeat(2) @(negedge clk); rst_n=1;
        #1; if(output_valid || errors!=0) $fatal(1,"Reset did not flush state");
        beat(30,0); beat(31,0); beat(32,1); take(30,31,32);
        $display("AXIS_UNIT_PASS: signed boundaries, backpressure, malformed TLAST/padding, disable and reset");
        $finish;
    end
    initial begin #100000; $fatal(1,"AXIS unit test timed out"); end
endmodule
