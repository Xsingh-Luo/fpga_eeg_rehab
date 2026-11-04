`timescale 1ns/1ps
module tb_ps_stream_subsystem;
    reg clk=0,resetn=0;
    always #5 clk=~clk;
    reg [31:0] awaddr=0,wdata=0,araddr=0;
    reg awvalid=0,wvalid=0,bready=0,arvalid=0,rready=0;
    wire awready,wready,bvalid,arready,rvalid;
    wire [1:0] bresp,rresp;
    wire [31:0] rdata;
    ps_stream_wrapper dut(
        .ps_clk(clk),.ps_resetn(resetn),
        .S_AXI_awaddr(awaddr),.S_AXI_awprot(3'b0),.S_AXI_awvalid(awvalid),.S_AXI_awready(awready),
        .S_AXI_wdata(wdata),.S_AXI_wstrb(4'hf),.S_AXI_wvalid(wvalid),.S_AXI_wready(wready),
        .S_AXI_bresp(bresp),.S_AXI_bvalid(bvalid),.S_AXI_bready(bready),
        .S_AXI_araddr(araddr),.S_AXI_arprot(3'b0),.S_AXI_arvalid(arvalid),.S_AXI_arready(arready),
        .S_AXI_rdata(rdata),.S_AXI_rresp(rresp),.S_AXI_rvalid(rvalid),.S_AXI_rready(rready));
    localparam [31:0] CORE=32'h43d00000,FIFO=32'h43d10000;
    task write32(input [31:0] address,input [31:0] value);
        begin
            @(negedge clk);awaddr=address;wdata=value;awvalid=1;wvalid=1;bready=0;
            fork
                begin @(posedge clk);while(!awready)@(posedge clk);@(negedge clk);awvalid=0;end
                begin @(posedge clk);while(!wready)@(posedge clk);@(negedge clk);wvalid=0;end
            join
            bready=1;
            @(posedge clk);while(!bvalid)@(posedge clk);
            if(bresp!==0)$fatal(1,"AXI write error addr=%h resp=%h",address,bresp);
            @(negedge clk);bready=0;
        end
    endtask
    task read32(input [31:0] address,output [31:0] value);
        begin
            @(negedge clk);araddr=address;arvalid=1;rready=0;
            @(posedge clk);while(!arready)@(posedge clk);
            @(negedge clk);arvalid=0;rready=1;
            @(posedge clk);while(!rvalid)@(posedge clk);
            if(rresp!==0)$fatal(1,"AXI read error addr=%h resp=%h",address,rresp);
            value=rdata;
            @(negedge clk);rready=0;
        end
    endtask
    task wait32(input [31:0] address,input [31:0] mask,input [31:0] expected);
        reg [31:0] value;integer ticks;
        begin
            read32(address,value);ticks=0;
            while((value&mask)!==expected) begin
                ticks=ticks+1;if(ticks>1000)$fatal(1,"Poll timeout at %h got=%h expected=%h",address,value,expected);
                read32(address,value);
            end
        end
    endtask
    reg [7:0] bytes[0:24551];
    reg signed [31:0] sos[0:29],csp[0:17],sw[0:3],sb;
    reg [31:0] expected[0:743];
    integer fd,code,i,ch,outfile,status;
    reg [31:0] value;
    reg signed [23:0] count;
    initial begin
        fd=$fopen("ff_bytes.txt","r");if(!fd)$fatal(1,"Missing bytes");
        for(i=0;i<24552;i=i+1)code=$fscanf(fd,"%d",bytes[i]);$fclose(fd);
        fd=$fopen("ff_sos.txt","r");for(i=0;i<30;i=i+1)code=$fscanf(fd,"%d",sos[i]);$fclose(fd);
        fd=$fopen("ff_csp.txt","r");for(i=0;i<18;i=i+1)code=$fscanf(fd,"%d",csp[i]);$fclose(fd);
        fd=$fopen("ff_svmw.txt","r");for(i=0;i<4;i=i+1)code=$fscanf(fd,"%d",sw[i]);$fclose(fd);
        fd=$fopen("ff_svmb.txt","r");code=$fscanf(fd,"%d",sb);$fclose(fd);
        $readmemh("expected_decision_words.txt",expected);
        #200;resetn=1;#20000;
        read32(CORE+'h38,value);if(value!==32'h42434931)$fatal(1,"Stream core identity %h",value);
        write32(CORE,2);write32(FIFO+8,'ha5);
        wait32(FIFO,32'h01000000,32'h01000000);write32(FIFO,32'hffffffff);
        write32(CORE+'h2c,32'h0100001e);for(i=0;i<30;i=i+1)write32(CORE+'h30,sos[i]);
        write32(CORE+'h34,1);wait32(CORE+4,12,4);
        write32(CORE+'h2c,32'h03000012);for(i=0;i<18;i=i+1)write32(CORE+'h30,csp[i]);
        write32(CORE+'h34,1);wait32(CORE+4,12,4);
        write32(CORE+'h2c,32'h04000004);for(i=0;i<4;i=i+1)write32(CORE+'h30,sw[i]);
        write32(CORE+'h34,1);wait32(CORE+4,12,4);
        write32(CORE+'h2c,32'h05000001);write32(CORE+'h30,sb);
        write32(CORE+'h34,1);wait32(CORE+4,12,4);write32(CORE,1);
        outfile=$fopen("subsystem_decisions.txt","w");
        for(i=0;i<744;i=i+1) begin
            read32(FIFO+12,value);if(value<3)$fatal(1,"FIFO unexpectedly full");
            write32(FIFO,32'h08000000);
            for(ch=0;ch<3;ch=ch+1) begin
                count={bytes[i*33+2+ch*3],bytes[i*33+3+ch*3],bytes[i*33+4+ch*3]};
                write32(FIFO+16,{{8{count[23]}},count});
            end
            write32(FIFO+20,12);
            wait32(CORE+'h24,32'hffffffff,i+1);
            wait32(FIFO,32'h08000000,32'h08000000);
            read32(CORE+'h18,value);$fdisplay(outfile,"%08h",value);
            if(value!==expected[i])$fatal(1,"Decision mismatch sample=%0d got=%h expected=%h",i+1,value,expected[i]);
            read32(CORE+'h28,value);if(value!==0)$fatal(1,"Core errors %h",value);
            read32(FIFO,value);if(value&32'hf2000000)$fatal(1,"FIFO errors %h",value);
        end
        $fclose(outfile);write32(CORE,0);
        status=$fopen("subsystem_status.txt","w");
        $fdisplay(status,"PASS 744 encoded decisions through actual FIFO and CDC; 119 post-warmup");$fclose(status);
        $display("PS_STREAM_MMIO_PASS: 744 exact encoded decisions");$finish;
    end
    initial begin #50000000;$fatal(1,"Subsystem watchdog");end
endmodule
