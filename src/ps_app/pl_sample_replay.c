/* Fixed-sample PS -> AXI FIFO -> PL replay. No serial device is opened.
 * Default --dry-run is portable. Hardware mode requires Linux and explicit bases.
 * FIFO register sequence follows AMD PG080 (axi_fifo_mm_s, AXI4-Lite TX mode).
 */
#define _POSIX_C_SOURCE 200809L
#define _FILE_OFFSET_BITS 64
#include "cyton_parser.h"
#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <signal.h>
#include <time.h>
#ifdef __linux__
#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>
#endif

enum { CTRL=0, STATUS=4, NCH=0x10, FEAT=0x14, DECISION=0x18,
       ACC=0x24, ERRORS=0x28, PADDR=0x2c, PRAM=0x30, COMMIT=0x34, CORE_ID=0x38,
       FIFO_ISR=0, FIFO_IER=4, FIFO_TX_RESET=8, FIFO_VACANCY=12,
       FIFO_DATA=16, FIFO_LENGTH=20, FIFO_TX_DONE=0x08000000,
       FIFO_TX_RESET_DONE=0x01000000 };
typedef struct { cyton_sample *data; size_t n, capacity; } samples;
typedef struct { int32_t sos[30], csp[18], w[4], b[1]; uint32_t *expected; } model;
static volatile sig_atomic_t stopped;
static void stop_signal(int sig) { (void)sig; stopped=1; }

static FILE *bundle_open(const char *dir, const char *name) {
    char path[2048];
    int n=snprintf(path,sizeof path,"%s/%s",dir,name);
    if(n<0 || (size_t)n>=sizeof path) return NULL;
    return fopen(path,"rb");
}
static int collect(const cyton_sample *s, void *ctx) {
    samples *v=ctx;
    if(v->n>=1000000) return -1;
    if(v->n==v->capacity) {
        size_t cap=v->capacity ? v->capacity*2 : 1024;
        void *p=realloc(v->data,cap*sizeof *v->data);
        if(!p) return -1;
        v->data=p;v->capacity=cap;
    }
    v->data[v->n++]=*s;
    return 0;
}
static int read_samples(const char *dir, samples *v) {
    FILE *f=bundle_open(dir,"sample_stream.bin");
    if(!f) return -1;
    cyton_parser p;cyton_init(&p,0);
    unsigned char bytes[4096];size_t n;int failed=0;
    while((n=fread(bytes,1,sizeof bytes,f))!=0)
        if(cyton_feed(&p,bytes,n,collect,v)) { failed=1;break; }
    failed |= ferror(f)!=0;fclose(f);
    if(p.discarded || p.bad_footer || p.used || p.sequence_jumps || p.duplicates || p.missing_mod256 || v->n!=744)
        failed=1;
    if(failed) fprintf(stderr,"Fixed input rejected: samples=%zu, discarded=%" PRIu64 ", tail=%zu\n",v->n,p.discarded,p.used);
    return failed ? -1 : 0;
}
static int read_values(const char *dir, const char *name, int32_t *out, size_t count, unsigned bits) {
    FILE *f=bundle_open(dir,name);int64_t x;size_t i=0;int bad=0;
    if(!f) return -1;
    for(;i<count;i++) {
        if(fscanf(f,"%" SCNd64,&x)!=1 || x<-(INT64_C(1)<<(bits-1)) || x>=(INT64_C(1)<<(bits-1))) {bad=1;break;}
        out[i]=(int32_t)x;
    }
    int c;do {c=fgetc(f);}while(c==' '||c=='\n'||c=='\r'||c=='\t');
    bad |= c!=EOF || ferror(f);fclose(f);
    return bad ? -1 : 0;
}
static int read_model(const char *dir, model *m, size_t n) {
    if(read_values(dir,"ff_sos.txt",m->sos,30,18) || read_values(dir,"ff_csp.txt",m->csp,18,16) ||
       read_values(dir,"ff_svmw.txt",m->w,4,16) || read_values(dir,"ff_svmb.txt",m->b,1,32)) return -1;
    m->expected=calloc(n,sizeof *m->expected);
    FILE *f=bundle_open(dir,"expected_decision_words.txt");
    if(!m->expected || !f) {if(f)fclose(f);return -1;}
    int bad=0;
    for(size_t i=0;i<n;i++) if(fscanf(f,"%" SCNx32,&m->expected[i])!=1) {bad=1;break;}
    int c;do {c=fgetc(f);}while(c==' '||c=='\n'||c=='\r'||c=='\t');
    bad |= c!=EOF || ferror(f);fclose(f);
    return bad ? -1 : 0;
}

#ifdef __linux__
typedef struct { int fd; volatile uint32_t *regs; size_t span; } mapping;
static double millis(void) {
    struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);
    return t.tv_sec*1000.0+t.tv_nsec/1e6;
}
static uint32_t rd(mapping *m,unsigned off) {
    __sync_synchronize();uint32_t v=m->regs[off/4];__sync_synchronize();return v;
}
static void wr(mapping *m,unsigned off,uint32_t value) {
    __sync_synchronize();m->regs[off/4]=value;__sync_synchronize();
}
static int map_regs(mapping *m,uint32_t base) {
    long page=sysconf(_SC_PAGESIZE);
    if(page<=0 || base%(uint32_t)page) return -1;
    m->span=(size_t)page;
    m->fd=open("/dev/mem",O_RDWR|O_SYNC);
    if(m->fd<0) return -1;
    void *p=mmap(NULL,m->span,PROT_READ|PROT_WRITE,MAP_SHARED,m->fd,(off_t)base);
    if(p==MAP_FAILED) {close(m->fd);m->fd=-1;return -1;}
    m->regs=p;return 0;
}
static void unmap_regs(mapping *m) {
    if(m->regs) munmap((void *)m->regs,m->span);
    if(m->fd>=0)close(m->fd);
}
static int wait_value(mapping *m,unsigned off,uint32_t expected,uint32_t mask) {
    double deadline=millis()+1000.0;
    while(!stopped && millis()<deadline) {
        if((rd(m,off)&mask)==expected)return 0;
        struct timespec pause={0,100000};nanosleep(&pause,NULL);
    }
    fprintf(stderr,"Timeout/stop at offset 0x%x, expected 0x%08" PRIx32 "\n",off,expected);
    return -1;
}
static int push_block(mapping *core,unsigned id,const int32_t *v,unsigned n) {
    wr(core,PADDR,(id<<24)|n);
    if(wait_value(core,STATUS,0,12))return -1; /* clear prior commit flags */
    for(unsigned i=0;i<n;i++)wr(core,PRAM,(uint32_t)v[i]);
    wr(core,COMMIT,1);
    return wait_value(core,STATUS,4,12); /* actual RTL word-count acknowledgement, not CRC */
}
static int hardware_replay(const samples *v,const model *m,uint32_t cb,uint32_t fb,FILE *out) {
    mapping core={-1,NULL,0},fifo={-1,NULL,0};int rc=-1,started=0;
    /* Refuse the current factory boot before even reading an unmapped PL address. */
    const char wanted[]="ps-stream-bci1";
    char identity[sizeof wanted];
    FILE *platform=fopen("/proc/device-tree/bci,platform-id","rb");
    if(!platform) {fprintf(stderr,"Hardware replay requires the BCI test device tree; no MMIO accessed\n");goto done;}
    size_t identity_size=fread(identity,1,sizeof identity,platform);
    fclose(platform);
    if(identity_size!=sizeof wanted || memcmp(identity,wanted,sizeof wanted)) {
        fprintf(stderr,"Unexpected test platform identity; no MMIO accessed\n");goto done;
    }
    if(map_regs(&core,cb)||map_regs(&fifo,fb)) {perror("mmap");goto done;}
    if(rd(&core,CORE_ID)!=0x42434931 || rd(&core,NCH)!=3 || rd(&core,FEAT)!=4) {
        fprintf(stderr,"Loaded PL identity/layout/model mismatch (expected BCI1 stream core)\n");goto done;
    }
    wr(&core,CTRL,2);started=1;
    struct timespec delay={0,1000000};nanosleep(&delay,NULL);
    wr(&fifo,FIFO_IER,0);wr(&fifo,FIFO_TX_RESET,0xa5);
    if(wait_value(&fifo,FIFO_ISR,FIFO_TX_RESET_DONE,FIFO_TX_RESET_DONE))goto done;
    wr(&fifo,FIFO_ISR,0xffffffff);
    if(wait_value(&core,ACC,0,0xffffffff))goto done;
    if(push_block(&core,1,m->sos,30)||push_block(&core,3,m->csp,18)||
       push_block(&core,4,m->w,4)||push_block(&core,5,m->b,1))goto done;
    wr(&core,CTRL,1);
    fprintf(out,"sample,seq,window_valid,decision_word,label,expected_word,match\n");
    for(size_t i=0;i<v->n;i++) {
        if(stopped)goto done;
        if(rd(&core,ACC)!=(uint32_t)i || rd(&fifo,FIFO_VACANCY)<3) {
            fprintf(stderr,"Unexpected counter or FIFO vacancy at sample %zu\n",i+1);goto done;
        }
        wr(&fifo,FIFO_ISR,FIFO_TX_DONE);
        for(unsigned ch=0;ch<3;ch++)wr(&fifo,FIFO_DATA,(uint32_t)v->data[i].ch[ch]);
        wr(&fifo,FIFO_LENGTH,12); /* three complete int32 words; TLAST on word 3 */
        if(wait_value(&core,ACC,(uint32_t)i+1,0xffffffff)||
           wait_value(&fifo,FIFO_ISR,FIFO_TX_DONE,FIFO_TX_DONE))goto done;
        uint32_t actual=rd(&core,DECISION),expected=m->expected[i];
        if(fprintf(out,"%zu,%u,%u,%08" PRIx32 ",%u,%08" PRIx32 ",%u\n",i+1,
           (unsigned)v->data[i].seq,(unsigned)(i>=625),actual,(unsigned)(actual&1),expected,
           (unsigned)(actual==expected))<0)goto done;
        if(actual!=expected || rd(&core,ERRORS) || (rd(&fifo,FIFO_ISR)&0xf2000000)) {
            fprintf(stderr,"Decision/error mismatch at sample %zu: %08" PRIx32 " vs %08" PRIx32 "\n",i+1,actual,expected);goto done;
        }
    }
    rc=0;
done:
    if(started)wr(&core,CTRL,0);
    unmap_regs(&fifo);unmap_regs(&core);return rc;
}
#endif

static int parse_base(const char *s,uint32_t *value) {
    char *end;errno=0;unsigned long long n=strtoull(s,&end,0);
    if(errno || !*s || *end || n<0x40000000ULL || n>=0x80000000ULL || n%65536) return -1;
    *value=(uint32_t)n;return 0;
}
int main(int argc,char **argv) {
    const char *dir=NULL,*output=NULL;int hardware=0,cb_set=0,fb_set=0;
    uint32_t core_base=0,fifo_base=0;
    for(int i=1;i<argc;i++) {
        if(!strcmp(argv[i],"--dry-run"))hardware=0;
        else if(!strcmp(argv[i],"--hardware"))hardware=1;
        else if(i+1<argc && !strcmp(argv[i],"--bundle"))dir=argv[++i];
        else if(i+1<argc && !strcmp(argv[i],"--out"))output=argv[++i];
        else if(i+1<argc && !strcmp(argv[i],"--core-base")) {if(parse_base(argv[++i],&core_base))return 2;cb_set=1;}
        else if(i+1<argc && !strcmp(argv[i],"--fifo-base")) {if(parse_base(argv[++i],&fifo_base))return 2;fb_set=1;}
        else {fprintf(stderr,"Unknown/incomplete option: %s\n",argv[i]);return 2;}
    }
    if(!dir || !output || (hardware && (!cb_set||!fb_set||core_base==fifo_base))) {
        fprintf(stderr,"Usage: pl_sample_replay --bundle DIR --out NEW.csv [--dry-run | --hardware --core-base VERIFIED_HEX --fifo-base VERIFIED_HEX]\n");return 2;
    }
    samples v={0};model m={0};int rc=1;
    if(read_samples(dir,&v)||read_model(dir,&m,v.n)) {fprintf(stderr,"Bundle validation failed before MMIO access\n");goto cleanup;}
    FILE *out=fopen(output,"wx");
    if(!out) {perror("Create new output");goto cleanup;}
    signal(SIGINT,stop_signal);signal(SIGTERM,stop_signal);
    if(!hardware) {
        fprintf(out,"sample,seq,ch1_word,ch2_word,ch3_word,tlast_word_index,packet_bytes\n");
        for(size_t i=0;i<v.n;i++)
            if(fprintf(out,"%zu,%u,%08" PRIx32 ",%08" PRIx32 ",%08" PRIx32 ",3,12\n",i+1,
                (unsigned)v.data[i].seq,(uint32_t)v.data[i].ch[0],(uint32_t)v.data[i].ch[1],
                (uint32_t)v.data[i].ch[2])<0)goto close_output;
        printf("DRY_RUN: %zu samples, 53 parameter words validated. No MMIO or inference executed.\n",v.n);rc=0;
    } else {
#ifdef __linux__
        rc=hardware_replay(&v,&m,core_base,fifo_base,out) ? 1 : 0;
#else
        fprintf(stderr,"Hardware replay requires a Linux build. No MMIO executed.\n");
#endif
    }
close_output:
    if(fclose(out))rc=1;
    if(hardware && !rc)printf("PASS hardware replay: %zu encoded decisions; 119 after warmup.\n",v.n);
cleanup:
    free(v.data);free(m.expected);return rc;
}
