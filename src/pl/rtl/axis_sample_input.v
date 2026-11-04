// AXI4-Stream signed int32 counts -> one complete, parallel EEG sample.
// TDATA must be the sign extension of a signed 24-bit ADC count.
// Exactly N_CH beats per sample; TLAST is required on the last channel only.
// One complete sample is buffered. A malformed sample is discarded through TLAST.
`timescale 1ns/1ps
`default_nettype none
module axis_sample_input #(
    parameter integer N_CH = 3
) (
    input wire clk,
    input wire rst_n,
    input wire enable,
    input wire [31:0] s_axis_tdata,
    input wire s_axis_tvalid,
    output wire s_axis_tready,
    input wire s_axis_tlast,
    output reg signed [23:0] sample [0:N_CH-1],
    output reg sample_valid,
    input wire sample_ready,
    output reg [31:0] format_errors
);
    localparam integer IW = (N_CH > 1) ? $clog2(N_CH) : 1;
    reg [IW-1:0] index;
    reg dropping;
    reg signed [23:0] staging [0:N_CH-1];
    wire last_channel = (index == N_CH-1);
    wire canonical_count = (s_axis_tdata[31:24] == {8{s_axis_tdata[23]}});
    integer c;
    assign s_axis_tready = enable && (!sample_valid || sample_ready);

    always @(posedge clk) begin
        if (!rst_n) begin
            index <= 0;
            dropping <= 0;
            sample_valid <= 0;
            format_errors <= 0;
            for (c=0; c<N_CH; c=c+1) begin
                staging[c] <= 0;
                sample[c] <= 0;
            end
        end else if (!enable) begin
            // Stopping discards partial/pending input; counters persist until reset.
            index <= 0;
            dropping <= 0;
            sample_valid <= 0;
        end else begin
            if (sample_valid && sample_ready) sample_valid <= 0;
            if (s_axis_tvalid && s_axis_tready) begin
                if (dropping) begin
                    if (s_axis_tlast) begin
                        dropping <= 0;
                        index <= 0;
                    end
                end else if (!canonical_count || (s_axis_tlast != last_channel)) begin
                    format_errors <= format_errors + 1'b1;
                    index <= 0;
                    dropping <= !s_axis_tlast;
                end else if (last_channel) begin
                    for (c=0; c<N_CH; c=c+1)
                        sample[c] <= (c == N_CH-1) ? $signed(s_axis_tdata[23:0]) : staging[c];
                    sample_valid <= 1;
                    index <= 0;
                end else begin
                    staging[index] <= s_axis_tdata[23:0];
                    index <= index + 1'b1;
                end
            end
        end
    end
endmodule
`default_nettype wire
