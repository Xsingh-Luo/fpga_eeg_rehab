// Included by tb_axis_fullflow after its existing declarations/tasks.
    reg [31:0] axis_data=0;
    reg axis_valid=0, axis_last=0;
    wire axis_ready, axis_dec_valid;
    wire [31:0] axis_errors;
    reg axis_capture_enable=0, axis_feed_start=0, axis_feed_done=0;
    integer uart_actual_count=0, axis_actual_count=0, axis_valid_count=0;
    integer backpressure_cycles=0;
    integer uart_actual_file, axis_actual_file, axis_z_file, axis_sz_file, axis_f_file;
    reg signed [31:0] uart_decisions [0:N_PKT-1];
    reg signed [31:0] axis_decisions [0:N_PKT-1];
    reg uart_labels [0:N_PKT-1], axis_labels [0:N_PKT-1];

    eeg_csp_svm_top #(.CLK_FREQ(CLK_FREQ),.BAUD(BAUD),.N_CH(N_CH),
        .N_BAND(N_BAND),.N_COMP(N_COMP),.FEAT_DIM(FEAT_DIM),.WIN_LEN(WIN_LEN),.INPUT_MODE(1)) axis_dut (
        .clk_in(clk),.rst_n_ext(rst_n_ext),.uart_rx(1'b1),.uart_tx(),
        .s_axis_tdata(axis_data),.s_axis_tvalid(axis_valid),.s_axis_tready(axis_ready),.s_axis_tlast(axis_last),
        .axis_clk(),.axis_aresetn(),.stream_format_errors(axis_errors),
        .s_axi_awaddr(awaddr),.s_axi_awvalid(awvalid),.s_axi_awready(),
        .s_axi_wdata(wdata),.s_axi_wstrb(wstrb),.s_axi_wvalid(wvalid),.s_axi_wready(),
        .s_axi_bresp(),.s_axi_bvalid(),.s_axi_bready(bready),
        .s_axi_araddr(araddr),.s_axi_arvalid(arvalid),.s_axi_arready(),
        .s_axi_rdata(),.s_axi_rresp(),.s_axi_rvalid(),.s_axi_rready(rready),
        .led_pkt(),.led_label(),.dec_valid(axis_dec_valid));

    task axis_beat(input [31:0] value,input end_frame);
        begin
            @(negedge clk); axis_data=value;axis_last=end_frame;axis_valid=1;
            @(posedge clk); while(!axis_ready) @(posedge clk);
            @(negedge clk);axis_valid=0;
        end
    endtask

    task axi_split_write(input [31:0] addr,input [31:0] value,input first_address,
                         input [3:0] mask,input [1:0] expected_response);
        integer ticks;
        begin
            @(negedge clk);
            awaddr=addr;wdata=value;wstrb=mask;bready=0;
            awvalid=first_address;wvalid=!first_address;
            ticks=0;
            @(posedge clk);
            while(!(first_address ? awready : wready)) begin
                ticks=ticks+1;if(ticks>30)$fatal(1,"Independent AXI channel handshake timed out");
                @(posedge clk);
            end
            @(negedge clk);awvalid=0;wvalid=0;
            // Change the already-accepted bus payload to prove the slave latched it.
            if(first_address)awaddr=32'hFC;else wdata=32'hDEADBEEF;
            repeat(5)@(negedge clk);
            if(first_address)wvalid=1;else awvalid=1;
            @(posedge clk);
            while(!(first_address ? wready : awready))@(posedge clk);
            @(negedge clk);awvalid=0;wvalid=0;
            wait(bvalid);
            repeat(4)begin
                @(posedge clk);#1;
                if(!bvalid||bresp!==expected_response)$fatal(1,"AXI write response changed under backpressure");
            end
            @(negedge clk);bready=1;
            @(posedge clk);@(negedge clk);bready=0;wstrb=4'hF;
        end
    endtask

    task axi_stalled_read;
        begin
            @(negedge clk);araddr=32'h08;arvalid=1;rready=0;
            @(posedge clk);while(!arready)@(posedge clk);
            @(negedge clk);arvalid=0;araddr=32'hFC;
            repeat(5)begin
                @(posedge clk);#1;
                if(!rvalid || rdata!==32'd0 || rresp!==2'b00 || arready)
                    $fatal(1,"AXI read response changed or address accepted under backpressure");
            end
            @(negedge clk);rready=1;
            @(posedge clk);@(negedge clk);rready=0;
            if(rvalid)$fatal(1,"AXI read response did not retire");
        end
    endtask

    task axis_prepare;
        integer mode_readback;
        begin
            axi_read(32'h38,mode_readback);
            if(mode_readback!==32'h42434930)$fatal(1,"Legacy core identity mismatch");
            if(axis_dut.u_regs.CORE_ID!==32'h42434931)$fatal(1,"Stream core identity mismatch");
            axi_split_write(32'h08,32'd1,1,4'hF,2'b00);
            axi_read(32'h08,mode_readback);
            if(mode_readback!=1)$fatal(1,"AXI address-first write lost its payload");
            axi_split_write(32'h08,32'd2,0,4'hF,2'b00);
            axi_read(32'h08,mode_readback);
            if(mode_readback!=2)$fatal(1,"AXI data-first write lost its payload");
            axi_split_write(32'h08,32'd3,1,4'h1,2'b10);
            axi_split_write(32'h09,32'd3,0,4'hF,2'b10);
            axi_read(32'h08,mode_readback);
            if(mode_readback!=2)$fatal(1,"Rejected AXI writes modified registers");
            axi_write(32'h08,0);
            axi_stalled_read();
            uart_actual_file=$fopen("uart_actual_dec.txt","w");
            axis_actual_file=$fopen("axis_actual_dec.txt","w");
            axis_z_file=$fopen("axis_z.txt","w");
            axis_sz_file=$fopen("axis_sz.txt","w");
            axis_f_file=$fopen("axis_f.txt","w");
            // Prime the algorithm and then reset it: later equality checks detect stale filter/window state.
            axis_beat(100,0);axis_beat(-200,0);axis_beat(300,1);
            wait(axis_dec_valid); @(negedge clk);
            axis_beat(999,0); // Leave a partial frame before software reset.
            axi_write(32'h00,32'h2);
            repeat(4) @(posedge clk);
            axi_write(32'h00,32'h1);
            repeat(4) @(posedge clk);
            if(axis_dut.cnt_pkt!=0 || axis_dut.acc_cnt!=0 || !axis_dut.params_loaded)
                $fatal(1,"Software reset must flush core/input/counters and retain model parameters");
            // Reject three malformed frames and recover before the real replay.
            axis_beat(1,1);
            axis_beat(2,0);axis_beat(3,0);axis_beat(4,0);axis_beat(5,1);
            axis_beat(6,0);axis_beat(7,0);axis_beat(32'h01000008,1);
            if(axis_errors!=3) $fatal(1,"Wrong malformed-frame count");
            @(negedge clk);axis_capture_enable=1;axis_feed_start=1;
        end
    endtask

    initial begin : axis_producer
        integer ai, ac;
        reg signed [23:0] value;
        wait(axis_feed_start);
        for(ai=0;ai<N_PKT;ai=ai+1) begin
            for(ac=0;ac<N_CH;ac=ac+1) begin
                value={bytes[ai*33+2+ac*3],bytes[ai*33+3+ac*3],bytes[ai*33+4+ac*3]};
                axis_beat({{8{value[23]}},value},ac==N_CH-1);
                if(((ai+ac)%7)==0) repeat(3) @(negedge clk);
            end
        end
        axis_feed_done=1;
    end

    // Capture the actual SVM result after its second registered stage, including the final sample.
    always @(posedge clk) begin
        if(axis_capture_enable && dut.u_svm.en_d) begin
            #1;
            if(uart_actual_count>=N_PKT) $fatal(1,"Unexpected extra UART result");
            uart_decisions[uart_actual_count]=dut.dec_value;
            uart_labels[uart_actual_count]=dut.dec_label;
            $fdisplay(uart_actual_file,"%0d %0d",dut.dec_label,dut.dec_value);
            uart_actual_count=uart_actual_count+1;
        end
    end
    always @(posedge clk) begin
        if(axis_capture_enable && axis_dut.u_svm.en_d) begin
            #1;
            if(axis_actual_count>=N_PKT) $fatal(1,"Unexpected extra AXIS result");
            axis_decisions[axis_actual_count]=axis_dut.dec_value;
            axis_labels[axis_actual_count]=axis_dut.dec_label;
            $fdisplay(axis_actual_file,"%0d %0d",axis_dut.dec_label,axis_dut.dec_value);
            $fdisplay(axis_z_file,"%0d %0d %0d %0d %0d %0d",axis_dut.z[0],axis_dut.z[1],axis_dut.z[2],axis_dut.z[3],axis_dut.z[4],axis_dut.z[5]);
            $fdisplay(axis_sz_file,"%0d %0d %0d %0d %0d %0d",axis_dut.sz[0],axis_dut.sz[1],axis_dut.sz[2],axis_dut.sz[3],axis_dut.sz[4],axis_dut.sz[5]);
            $fdisplay(axis_f_file,"%0d %0d %0d %0d",axis_dut.f_sel[0],axis_dut.f_sel[1],axis_dut.f_sel[2],axis_dut.f_sel[3]);
            axis_actual_count=axis_actual_count+1;
        end
    end
    always @(posedge clk) begin
        if(axis_capture_enable && axis_valid && !axis_ready) backpressure_cycles=backpressure_cycles+1;
        if(axis_capture_enable && axis_dec_valid) begin
            if(axis_valid_count>=axis_actual_count || axis_dut.dec_value!==axis_decisions[axis_valid_count])
                $fatal(1,"AXIS dec_valid advertises stale decision data");
            axis_valid_count=axis_valid_count+1;
        end
    end

    task axis_finish;
        integer j,status_file;
        begin
            if(!axis_feed_done || axis_actual_count!=N_PKT || uart_actual_count!=N_PKT || axis_valid_count!=N_PKT || axis_dut.acc_cnt!=N_PKT || axis_dut.cnt_pkt!=N_PKT)
                $fatal(1,"AXIS replay counts do not match the complete input");
            if(axis_errors!=3 || backpressure_cycles==0) $fatal(1,"Error recovery/backpressure was not exercised");
            for(j=0;j<N_PKT;j=j+1)
                if(axis_decisions[j]!==uart_decisions[j] || axis_labels[j]!==uart_labels[j])
                    $fatal(1,"UART/AXIS decision mismatch at sample %0d",j);
            $fclose(uart_actual_file);$fclose(axis_actual_file);
            $fclose(axis_z_file);$fclose(axis_sz_file);$fclose(axis_f_file);
            status_file=$fopen("axis_status.txt","w");
            $fdisplay(status_file,"PASS samples=%0d malformed_rejected=%0d backpressure_cycles=%0d",N_PKT,axis_errors,backpressure_cycles);
            $fclose(status_file);
            $display("AXIS_FULLFLOW_PASS: %0d exact decisions, %0d malformed frames rejected, backpressure=%0d",N_PKT,axis_errors,backpressure_cycles);
        end
    endtask
