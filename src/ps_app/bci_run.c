/* Production mode worker: serial or labelled file -> existing PL -> UI + audit CSV.
 * No CPU prediction is substituted for hardware inference.
 */
#define main bci_fixed_replay_compatibility_entry
#include "pl_sample_replay.c"
#undef main
#include "bci_ui_status.h"
#include <math.h>
#include "bci_notch.h"
#ifdef __linux__
#include <poll.h>
#include <termios.h>
#include <sys/file.h>
#include <sys/stat.h>

typedef struct {
    mapping core,fifo;model parameters;const char *status,*mode;FILE *csv;
    unsigned long sent;int trial,total,truth,correct,scored,last_label,live;
    double begin,last_ui,last_sample;bci_notch notch;
    int32_t ring[626][2];int rails[626];unsigned wp,filled,rail_count;
    int64_t sums[2],squares[2];int touched;
} runtime;
static void pause_ms(unsigned ms){struct timespec p={ms/1000,(long)(ms%1000)*1000000};while(!stopped&&nanosleep(&p,&p)&&errno==EINTR){} }
static int status(runtime *r,const char *state){
    r->last_ui=millis();return ui_status(r->status,r->mode,state,r->sent,
       r->live&&r->last_ui>r->begin?r->sent*1000.0/(r->last_ui-r->begin):250.,
       r->trial,r->total,r->truth,r->correct,r->scored);
}
static int hold_status(runtime *r,const char *state,unsigned ms){
    double end=millis()+ms;
    do{if(status(r,state))return -1;pause_ms(100);}while(!stopped&&millis()<end);
    return stopped?-1:0;
}
static int setup(runtime *r,const char *dir){
    if(read_values(dir,"ff_sos.txt",r->parameters.sos,30,18)||read_values(dir,"ff_csp.txt",r->parameters.csp,18,16)||
       read_values(dir,"ff_svmw.txt",r->parameters.w,4,16)||read_values(dir,"ff_svmb.txt",r->parameters.b,1,32))return -1;
    const char expected[]="ps-stream-bci1";char actual[sizeof expected];
    FILE *f=fopen("/proc/device-tree/bci,platform-id","rb");if(!f)return -1;
    size_t n=fread(actual,1,sizeof actual,f);fclose(f);
    if(n!=sizeof expected||memcmp(actual,expected,sizeof expected))return -1;
    if(map_regs(&r->core,0x43d00000)||map_regs(&r->fifo,0x43d10000))return -1;
    if(rd(&r->core,CORE_ID)!=0x42434931||rd(&r->core,NCH)!=3||rd(&r->core,FEAT)!=4)return -1;
    return 0;
}
static int reset_core(runtime *r){
    wr(&r->core,CTRL,2);r->touched=1;pause_ms(2);
    wr(&r->fifo,FIFO_IER,0);wr(&r->fifo,FIFO_TX_RESET,0xa5);
    if(wait_value(&r->fifo,FIFO_ISR,FIFO_TX_RESET_DONE,FIFO_TX_RESET_DONE))return -1;
    wr(&r->fifo,FIFO_ISR,0xffffffff);
    if(wait_value(&r->core,ACC,0,0xffffffff)||push_block(&r->core,1,r->parameters.sos,30)||
       push_block(&r->core,3,r->parameters.csp,18)||push_block(&r->core,4,r->parameters.w,4)||push_block(&r->core,5,r->parameters.b,1))return -1;
    wr(&r->core,CTRL,1);r->sent=0;return 0;
}
static int push(runtime *r,const int32_t x[3],uint32_t *decision){
    if(stopped||rd(&r->core,ACC)!=r->sent||rd(&r->fifo,FIFO_VACANCY)<3)return -1;
    wr(&r->fifo,FIFO_ISR,FIFO_TX_DONE);
    for(unsigned c=0;c<3;c++)wr(&r->fifo,FIFO_DATA,(uint32_t)x[c]);
    wr(&r->fifo,FIFO_LENGTH,12);
    if(wait_value(&r->core,ACC,(uint32_t)r->sent+1,0xffffffff)||wait_value(&r->fifo,FIFO_ISR,FIFO_TX_DONE,FIFO_TX_DONE))return -1;
    if(rd(&r->core,ERRORS)||(rd(&r->fifo,FIFO_ISR)&0xf2000000))return -1;
    *decision=rd(&r->core,DECISION);r->sent++;r->last_label=*decision&1;return 0;
}
static int live_sample_run(const cyton_sample *s,void *context){
    runtime *r=context;if(r->sent&&s->seq_delta!=1){fprintf(stderr,"Sequence gap; stopping live output\n");return -1;}
    int32_t selected[2]={s->ch[2],s->ch[3]},input[3]={0};int rail=0;
    for(unsigned c=0;c<2;c++){
        double y=bci_notch_apply(&r->notch,c,selected[c]);
        if(!isfinite(y))return -1;
        input[c?2:0]=(int32_t)llround(fmax(-8388608.,fmin(8388607.,y)));
        if(selected[c]==-8388608||selected[c]==8388607)rail=1;
        int64_t old=r->filled==626?r->ring[r->wp][c]:0;
        r->sums[c]+=selected[c]-old;r->squares[c]+=(int64_t)selected[c]*selected[c]-old*old;
        r->ring[r->wp][c]=selected[c];
    }
    if(r->filled==626)r->rail_count-=r->rails[r->wp];else r->filled++;
    r->rails[r->wp]=rail;r->rail_count+=rail;r->wp=(r->wp+1)%626;
    uint32_t d;if(push(r,input,&d))return -1;
    int bad=r->rail_count!=0;
    for(unsigned c=0;c<2;c++)if(r->filled==626&&(long double)r->squares[c]*626-(long double)r->sums[c]*r->sums[c]<4*626*626)bad=1;
    if(fprintf(r->csv,"%lu,%u,%d,%d,%d,%d,%08" PRIx32 ",%d\n",r->sent,s->seq,selected[0],selected[1],input[0],input[2],d,bad)<0)return -1;
    r->last_sample=millis();
    if(r->last_sample-r->last_ui>=100&&status(r,r->sent<750?"warmup":bad?"bad":r->last_label?"right":"left"))return -1;
    return 0;
}
static int registers_ok(const char *s){
    unsigned addr,v;const char *a=strstr(s,"CH3SET,"),*b=strstr(s,"CH4SET,");
    return a&&b&&sscanf(a,"CH3SET, %x, %x",&addr,&v)==2&&v==0x68&&sscanf(b,"CH4SET, %x, %x",&addr,&v)==2&&v==0x68;
}
static int serial_configure(runtime *r,int fd,const char *out){
    if(write(fd,"s",1)!=1)return -1;
    pause_ms(300);tcflush(fd,TCIFLUSH);
    const char config[]="x3060110Xx4060110X";
    if(write(fd,config,sizeof config-1)!=(ssize_t)(sizeof config-1))return -1;
    pause_ms(300);tcflush(fd,TCIFLUSH);
    if(write(fd,"?",1)!=1)return -1;
    char data[8192];size_t used=0;double end=millis()+2500;
    while(!stopped&&millis()<end&&used<sizeof data-1){
        if(millis()-r->last_ui>=300&&status(r,"prepare"))return -1;
        struct pollfd p={fd,POLLIN,0};int ready=poll(&p,1,100);
        if(ready<0&&errno!=EINTR)return -1;
        if(ready>0&&(p.revents&POLLIN)){ssize_t n=read(fd,data+used,sizeof data-1-used);if(n>0)used+=(size_t)n;}
    }
    data[used]=0;char path[2048];snprintf(path,sizeof path,"%s/cyton_registers.txt",out);
    FILE *f=fopen(path,"wx");if(!f)return -1;int failed=fwrite(data,1,used,f)!=used;if(fclose(f))failed=1;
    if(failed||!registers_ok(data)){fprintf(stderr,"CH3/CH4 Normal 0x68 verification failed\n");return -1;}
    tcflush(fd,TCIFLUSH);return 0;
}
static int run_live(runtime *r,const char *out){
    int fd=open("/dev/ttyUSB0",O_RDWR|O_NOCTTY|O_NONBLOCK),rc=-1;struct termios saved,t;int have_saved=0;
    if(fd<0)return -1;
    if(flock(fd,LOCK_EX|LOCK_NB))goto done;
    if(tcgetattr(fd,&saved))goto done;
    have_saved=1;t=saved;
    t.c_iflag=0;t.c_oflag=0;t.c_lflag=0;t.c_cflag=(t.c_cflag&~(CSIZE|PARENB|CSTOPB))|CS8|CLOCAL|CREAD;
#ifdef CRTSCTS
    t.c_cflag&=~CRTSCTS;
#endif
    t.c_cc[VMIN]=0;t.c_cc[VTIME]=0;cfsetispeed(&t,B115200);cfsetospeed(&t,B115200);
    if(tcsetattr(fd,TCSANOW,&t)||serial_configure(r,fd,out)||reset_core(r))goto done;
    /* Same causal iirnotch(50,30,fs=250) used by the frozen training code. */
    bci_notch_init(&r->notch);
    r->begin=r->last_sample=millis();
    if(write(fd,"b",1)!=1)goto done;
    cyton_parser parser;cyton_init(&parser,0);unsigned char buf[4096];
    while(!stopped){
        struct pollfd p={fd,POLLIN,0};int ready=poll(&p,1,100);
        if(ready<0){if(errno==EINTR)continue;goto done;}
        if(ready>0){
            if(p.revents&(POLLERR|POLLHUP|POLLNVAL))goto done;
            ssize_t n=read(fd,buf,sizeof buf);
            if(n>0&&cyton_feed(&parser,buf,(size_t)n,live_sample_run,r))goto done;
            if(n<0&&errno!=EAGAIN&&errno!=EINTR)goto done;
        }
        if(parser.bad_footer||parser.sequence_jumps||parser.duplicates)goto done;
        if(millis()-r->last_sample>1500){fprintf(stderr,"Serial timeout\n");goto done;}
        if(millis()-r->last_ui>=300&&r->sent<750)if(status(r,"warmup"))goto done;
    }
    rc=0;
done:
    if(write(fd,"s",1)!=1)rc=-1;
    if(have_saved)tcsetattr(fd,TCSANOW,&saved);
    close(fd);return rc;
}
static int run_dataset(runtime *r,const char *root){
    char path[2048];snprintf(path,sizeof path,"%s/dataset/index.csv",root);FILE *index=fopen(path,"r");if(!index)return -1;
    char line[128];if(!fgets(line,sizeof line,index)){fclose(index);return -1;}
    while(fgets(line,sizeof line,index))r->total++;
    if(r->total<1||r->total>10000){fclose(index);return -1;}rewind(index);(void)fgets(line,sizeof line,index);
    int rc=-1;
    while(!stopped&&fgets(line,sizeof line,index)){
        unsigned id,expected;int truth;char file[64],extra;
        if(sscanf(line,"%u,%d,%63[^,],%x %c",&id,&truth,file,&expected,&extra)!=4||truth<0||truth>1||strchr(file,'/')||strchr(file,'\\')||strstr(file,".."))goto done;
        r->trial++;r->truth=truth;
        if(hold_status(r,"prepare",1000)||reset_core(r))goto done;
        snprintf(path,sizeof path,"%s/dataset/%s",root,file);FILE *f=fopen(path,"rb");if(!f)goto done;
        samples input={0};cyton_parser parser;cyton_init(&parser,0);unsigned char buf[4096];size_t n;int bad=0;
        while((n=fread(buf,1,sizeof buf,f)))if(cyton_feed(&parser,buf,n,collect,&input)){bad=1;break;}
        bad|=ferror(f)||parser.used||parser.discarded||parser.bad_footer||parser.sequence_jumps||parser.duplicates||input.n<626;
        fclose(f);if(bad){free(input.data);goto done;}
        double start=millis();uint32_t decision=0;
        for(size_t i=0;i<input.n&&!stopped;i++){
            double due=start+i*4.;while(!stopped&&millis()<due)pause_ms(1);
            if(push(r,input.data[i].ch,&decision)){bad=1;break;}
            if(fprintf(r->csv,"%u,%zu,%d,%u,%08" PRIx32 "\n",id,i+1,truth,(unsigned)(decision&1),decision)<0){bad=1;break;}
            if(millis()-r->last_ui>=100&&status(r,r->sent<626?"warmup":"processing")){bad=1;break;}
        }
        free(input.data);if(bad||stopped)goto done;
        if(decision!=expected){fprintf(stderr,"Trial%u PL/reference mismatch: %08x vs %08x\n",id,decision,expected);goto done;}
        r->scored++;r->correct+=r->last_label==truth;
        if(fflush(r->csv)||hold_status(r,r->last_label?"right":"left",2000))goto done;
    }
    rc=stopped?0:hold_status(r,"complete",300);
done:fclose(index);return rc;
}
#endif
int main(int argc,char **argv){
    if(argc==2&&!strcmp(argv[1],"--version")){puts("BCI_RUN_20261103 PL_ONLY C3_C4_NOTCH50 LABELLED_REPLAY");return 0;}
    const char *root=NULL,*mode=NULL,*output=NULL,*ui=NULL;
    for(int i=1;i<argc;i++){
        if(i+1<argc&&!strcmp(argv[i],"--root"))root=argv[++i];
        else if(i+1<argc&&!strcmp(argv[i],"--mode"))mode=argv[++i];
        else if(i+1<argc&&!strcmp(argv[i],"--out"))output=argv[++i];
        else if(i+1<argc&&!strcmp(argv[i],"--status"))ui=argv[++i];
        else return 2;
    }
    if(!root||!mode||!output||!ui||(strcmp(mode,"live")&&strcmp(mode,"dataset")))return 2;
#ifndef __linux__
    fprintf(stderr,"Actual PL inference requires ARM Linux; no prerecorded prediction substitute\n");return 2;
#else
    signal(SIGINT,stop_signal);signal(SIGTERM,stop_signal);
    int lock=open("/run/bci_engine.lock",O_RDWR|O_CREAT,0600);if(lock<0||flock(lock,LOCK_EX|LOCK_NB))return 1;
    runtime r={.core={-1,NULL,0},.fifo={-1,NULL,0},.status=ui,.mode=mode,.truth=-1,.live=!strcmp(mode,"live")};
    int rc=1;char path[2048];
    if(mkdir(output,0700))goto done;
    snprintf(path,sizeof path,"%s/inference.csv",output);r.csv=fopen(path,"wx");if(!r.csv)goto done;
    fprintf(r.csv,r.live?"sample,seq,c3_raw,c4_raw,c3_notch,c4_notch,decision_word,bad_signal\n":"trial,sample,truth,prediction,decision_word\n");
    snprintf(path,sizeof path,"%s/models/%s",root,mode);
    if(status(&r,"prepare")||setup(&r,path))goto done;
    rc=(r.live?run_live(&r,output):run_dataset(&r,root))?1:0;
done:
    if(rc&&!stopped)(void)status(&r,"error");
    if(r.touched)wr(&r.core,CTRL,0);
    unmap_regs(&r.fifo);unmap_regs(&r.core);
    if(r.csv&&fclose(r.csv))rc=1;
    close(lock);return stopped?0:rc;
#endif
}
