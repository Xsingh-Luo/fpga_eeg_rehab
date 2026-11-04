/* 320x240 ST7789 dashboard. Preview is portable; spidev requires Linux.
 * No inferred GPIO numbers, no MMIO access, no fabricated confidence score.
 * State file: BCI_DISPLAY_V1 UNIX_SECONDS STATE VALIDATED FRAMES RATE_HZ
 * STATE: idle/warmup/bad/left/right. Stale or invalid records show WAITING.
 */
#define _POSIX_C_SOURCE 200809L
#define _FILE_OFFSET_BITS 64
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <signal.h>
#include <math.h>
#include "st7789_emio.h"
#include "bci_zh_font.h"
#include "bci_storage_guard.h"
#ifdef __linux__
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <linux/spi/spidev.h>
#include <sys/mman.h>
#include <sys/file.h>
#endif

#define WIDTH 320
#define HEIGHT 240
static uint16_t canvas[WIDTH*HEIGHT];
static volatile sig_atomic_t stopped;
static void stop_handler(int sig){(void)sig;stopped=1;}
typedef enum {IDLE,WARMUP,BAD,LEFT,RIGHT,MENU_LIVE,MENU_DATASET,PREPARE,COMPLETE,ERROR_STATE,STOPPING,PROCESSING,MENU_POWER,POWER_CANCEL,POWER_CONFIRM,POWERING_OFF,POWER_READY,POWER_ERROR,POWER_WAIT} screen_state;
typedef struct {screen_state state;int validated,demo;unsigned long frames;double rate;int v2,mode,trial,total,truth,correct,scored;} dashboard;
static uint16_t color(unsigned r,unsigned g,unsigned b){return (uint16_t)(((r>>3)<<11)|((g>>2)<<5)|(b>>3));}
static void rect(int x,int y,int w,int h,uint16_t c){
    for(int j=y;j<y+h;j++)for(int i=x;i<x+w;i++)
        if(i>=0&&i<WIDTH&&j>=0&&j<HEIGHT)canvas[j*WIDTH+i]=c;
}
/* Original compact 5x7 uppercase font; rows encoded with bit4 at left. */
static const unsigned char glyphs[][7]={
 {14,17,19,21,25,17,14},{4,12,4,4,4,4,14},{14,17,1,2,4,8,31},
 {30,1,1,14,1,1,30},{2,6,10,18,31,2,2},{31,16,16,30,1,1,30},
 {14,16,16,30,17,17,14},{31,1,2,4,8,8,8},{14,17,17,14,17,17,14},{14,17,17,15,1,1,14},
 {14,17,17,31,17,17,17},{30,17,17,30,17,17,30},{14,17,16,16,16,17,14},
 {30,17,17,17,17,17,30},{31,16,16,30,16,16,31},{31,16,16,30,16,16,16},
 {14,17,16,23,17,17,15},{17,17,17,31,17,17,17},{14,4,4,4,4,4,14},
 {7,2,2,2,18,18,12},{17,18,20,24,20,18,17},{16,16,16,16,16,16,31},
 {17,27,21,21,17,17,17},{17,25,21,19,17,17,17},{14,17,17,17,17,17,14},
 {30,17,17,30,16,16,16},{14,17,17,17,21,18,13},{30,17,17,30,20,18,17},
 {15,16,16,14,1,1,30},{31,4,4,4,4,4,4},{17,17,17,17,17,17,14},
 {17,17,17,17,17,10,4},{17,17,17,21,21,21,10},{17,17,10,4,10,17,17},
 {17,17,10,4,4,4,4},{31,1,2,4,8,16,31}
};
static void text(int x,int y,const char *s,int scale,uint16_t c){
    for(;*s;s++,x+=6*scale){
        int idx=*s>='0'&&*s<='9'?*s-'0':*s>='A'&&*s<='Z'?*s-'A'+10:-1;
        if(idx>=0)for(int row=0;row<7;row++)for(int col=0;col<5;col++)
            if(glyphs[idx][row]&(1u<<(4-col)))rect(x+col*scale,y+row*scale,scale,scale,c);
        if(*s=='+'){rect(x+2*scale,y+scale,scale,5*scale,c);rect(x,y+3*scale,5*scale,scale,c);}
        if(*s=='-')rect(x,y+3*scale,5*scale,scale,c);
    }
}
static uint32_t utf8_next(const char **value){
    const unsigned char *p=(const unsigned char *)*value;uint32_t code;
    if(*p<0x80){*value+=1;return *p;}
    if((*p&0xe0)==0xc0&&p[1]&&(p[1]&0xc0)==0x80){code=((*p&31u)<<6)|(p[1]&63u);*value+=2;return code;}
    if((*p&0xf0)==0xe0&&p[1]&&(p[1]&0xc0)==0x80&&p[2]&&(p[2]&0xc0)==0x80){
        code=((*p&15u)<<12)|((p[1]&63u)<<6)|(p[2]&63u);*value+=3;return code;
    }
    *value+=1;return 0x25a1;
}
static const zh_glyph *zh_lookup(uint32_t code){
    size_t lo=0,hi=sizeof zh_font/sizeof zh_font[0];
    while(lo<hi){size_t mid=lo+(hi-lo)/2;if(zh_font[mid].code<code)lo=mid+1;else hi=mid;}
    return lo<sizeof zh_font/sizeof zh_font[0]&&zh_font[lo].code==code?&zh_font[lo]:NULL;
}
static int zh_width(const char *s,int scale){
    int width=0;while(*s){const zh_glyph *g=zh_lookup(utf8_next(&s));width+=(g?(int)g->advance:16)*scale;}return width;
}
static void zh_text(int x,int y,const char *s,int scale,uint16_t c){
    while(*s){
        const zh_glyph *g=zh_lookup(utf8_next(&s));
        if(g)for(int row=0;row<16;row++)for(unsigned col=0;col<g->advance;col++)
            if(g->rows[row]&(1u<<(15-col)))rect(x+(int)col*scale,y+row*scale,scale,scale,c);
        x+=(g?(int)g->advance:16)*scale;
    }
}
static void zh_centered(int y,const char *s,int scale,uint16_t c){zh_text((WIDTH-zh_width(s,scale))/2,y,s,scale,c);}
static void draw_menu(screen_state selected){
    uint16_t bg=color(12,20,33),card=color(22,36,54),fg=color(231,239,247),muted=color(144,166,188),blue=color(83,179,255);
    rect(0,0,WIDTH,HEIGHT,bg);zh_text(14,12,"脑机接口系统",1,fg);
    zh_centered(34,"转动旋钮选择模式",1,muted);
    const screen_state states[]={MENU_LIVE,MENU_DATASET,MENU_POWER};
    const char *labels[]={"实时采集","数据集回放","安全关机"};
    for(int i=0;i<3;i++){
        int y=54+i*54,chosen=selected==states[i];
        rect(12,y,296,48,chosen?color(28,66,97):card);
        if(chosen)rect(12,y,5,48,blue);
        zh_text(29,y+8,labels[i],2,chosen?blue:fg);
    }
    zh_centered(214,"短按进入  长按返回",1,fg);
}
static void draw(dashboard d){
    if(d.state==MENU_LIVE||d.state==MENU_DATASET||d.state==MENU_POWER){draw_menu(d.state);return;}
    if(d.state>=POWER_CANCEL&&d.state<=POWER_WAIT){
        uint16_t bg=color(12,20,33),fg=color(231,239,247),muted=color(144,166,188),blue=color(83,179,255);
        rect(0,0,WIDTH,HEIGHT,bg);zh_text(14,12,"安全关机",1,fg);
        if(d.state==POWER_CANCEL||d.state==POWER_CONFIRM){
            zh_centered(50,"停止采集并保存记录",1,fg);zh_centered(76,"确认后关闭系统",1,muted);
            for(int i=0;i<2;i++){
                int y=110+48*i,chosen=(d.state==POWER_CONFIRM)==i;
                rect(28,y,264,40,chosen?color(28,66,97):color(22,36,54));
                if(chosen)rect(28,y,5,40,blue);
                zh_centered(y+11,i?"确认关机":"取消返回",1,chosen?blue:fg);
            }
            zh_centered(216,"转动选择  短按确认",1,muted);
        }else{
            const char *main=d.state==POWER_READY?"可以断电":d.state==POWER_ERROR?"关机失败":d.state==POWER_WAIT?"仍在关机":"正在关机";
            zh_centered(88,main,2,d.state==POWER_READY?color(74,220,167):blue);
            zh_centered(140,d.state==POWER_READY?"存储已保护":d.state==POWER_ERROR?"请保留供电并检查终端":"正在保存记录，请勿断电",1,fg);
            zh_centered(207,d.state==POWER_READY?"请关闭开发板电源":d.state==POWER_ERROR?"长按旋钮返回菜单":"请等待可以断电提示",1,muted);
        }
        return;
    }
    if(d.v2){
        uint16_t bg=color(12,20,33),card=color(22,36,54),fg=color(231,239,247),muted=color(144,166,188),accent=color(83,179,255);
        const char *heading=d.mode==2?"数据集回放":"实时采集";
        const char *result="等待数据";
        if(d.state==WARMUP)result="正在预热";
        if(d.state==PREPARE)result="准备开始";
        if(d.state==LEFT)result="左手想象";
        if(d.state==RIGHT)result="右手想象";
        if(d.state==BAD){result="信号异常";accent=color(255,103,103);}
        if(d.state==ERROR_STATE){result="系统错误";accent=color(255,103,103);}
        if(d.state==STOPPING)result="正在停止";
        if(d.state==PROCESSING)result="正在处理";
        if(d.state==COMPLETE)result="回放完成";
        rect(0,0,WIDTH,HEIGHT,bg);zh_text(12,10,heading,1,fg);
        zh_text(246,10,d.mode==2?"回放":"实时",1,accent);
        zh_text(12,34,d.mode==2?"B04 左右手运动想象数据":"C3 / C4 双通道实时脑电",1,muted);
        rect(12,54,296,111,card);rect(12,54,5,111,accent);
        char line[64];
        if(d.mode==2){snprintf(line,sizeof line,"第 %d / %d 次",d.trial,d.total);zh_centered(63,line,1,muted);}
        else {snprintf(line,sizeof line,"样本 %lu  采样 %u 赫兹",d.frames,(unsigned)d.rate);zh_centered(63,line,1,muted);}
        zh_centered(91,result,2,accent);
        if(d.mode==2&&d.truth>=0){snprintf(line,sizeof line,"提示：%s",d.truth?"右手":"左手");zh_centered(139,line,1,fg);}
        else zh_centered(141,d.state==BAD?"已暂停方向输出":"运动想象识别结果",1,muted);
        if(d.mode==2&&d.scored){snprintf(line,sizeof line,"正确 %d/%d  命中率 %d%%",d.correct,d.scored,100*d.correct/d.scored);zh_centered(175,line,1,fg);}
        else zh_centered(175,d.mode==2?"等待试次判定":d.state==BAD?"请检查头帽和耳夹":d.state==ERROR_STATE?"请检查设备连接":d.state==WARMUP?"正在建立分析窗口":"请保持身体放松",1,muted);
        zh_centered(199,d.mode==2?"B04 公开数据集":"左右手运动想象",1,color(255,194,79));
        zh_centered(221,"长按旋钮返回菜单",1,muted);return;
    }
    uint16_t bg=color(12,20,33),card=color(22,36,54),fg=color(231,239,247),muted=color(144,166,188);
    uint16_t accent=color(83,179,255);const char *main="等待数据",*sub="暂无实时数据";
    if(d.state==WARMUP){main="正在预热";sub="正在建立窗口";accent=color(255,194,79);}
    if(d.state==BAD){main="检查信号";sub="请检查电极接触";accent=color(255,103,103);}
    if(d.state==LEFT||d.state==RIGHT){
        if(d.validated||d.demo){main=d.state==LEFT?"左手想象":"右手想象";sub="运动想象";accent=color(74,220,167);}
        else{main="识别结果";sub="运动想象";accent=color(255,194,79);}
    }
    rect(0,0,WIDTH,HEIGHT,bg);zh_text(14,12,"脑机接口",1,fg);zh_text(176,14,"C3 + C4",1,muted);
    if(d.demo)zh_text(274,14,"演示",1,color(255,194,79));
    rect(12,42,296,142,card);rect(12,42,5,142,accent);
    zh_centered(66,sub,1,muted);zh_centered(98,main,2,accent);
    zh_centered(155,d.state==BAD?"已暂停方向输出":d.demo?"仅显示演示":"运动想象",1,muted);
    char line[64];snprintf(line,sizeof line,"样本 %lu",d.frames);zh_text(14,198,line,1,muted);
    snprintf(line,sizeof line,"%u 赫兹",(unsigned)(d.rate<0?0:d.rate));zh_text(235,198,line,1,muted);
    zh_centered(222,d.demo?"显示演示，并非脑电识别":"左右手运动想象",1,muted);
}
static int parse_state(const char *s,screen_state *state){
    const char *names[]={"idle","warmup","bad","left","right","menu-live","menu-dataset","prepare","complete","error","stopping","processing","menu-power","power-cancel","power-confirm","powering-off","power-ready","power-error","power-wait"};
    for(size_t i=0;i<sizeof names/sizeof names[0];i++)if(!strcmp(s,names[i])){*state=(screen_state)i;return 0;}
    return -1;
}
static void draw_color_test(void){
    rect(0,0,160,120,color(255,0,0));rect(160,0,160,120,color(0,255,0));
    rect(0,120,160,120,color(0,0,255));rect(160,120,160,120,color(255,255,255));
    text(176,170,"RGB TEST",2,color(0,0,0));
    text(176,200,"NO EEG DATA",1,color(0,0,0));
}
static int ppm(const char *path){
    FILE *f=fopen(path,"wbx");if(!f){perror(path);return -1;}
    int bad=fprintf(f,"P6\n%d %d\n255\n",WIDTH,HEIGHT)<0;
    for(size_t i=0;i<WIDTH*HEIGHT;i++){
        unsigned v=canvas[i];unsigned char b[3]={(unsigned char)(((v>>11)&31)*255/31),
            (unsigned char)(((v>>5)&63)*255/63),(unsigned char)((v&31)*255/31)};
        if(fwrite(b,1,3,f)!=3){bad=1;break;}
    }
    if(fclose(f))bad=1;
    return bad?-1:0;
}
static dashboard read_dashboard(const char *path){
    dashboard d={.state=IDLE};FILE *f=fopen(path,"r");if(!f)return d;
    char record[256];if(!fgets(record,sizeof record,f)){fclose(f);return d;}fclose(f);
    char version[32],name[24],extra;long long stamp;int valid;unsigned long frames;double rate;
    if(!strncmp(record,"BCI_UI_V2 ",10)){
        char mode[16];int trial,total,truth,correct,scored;
        int n=sscanf(record,"%31s %lld %15s %23s %d %lu %lf %d %d %d %d %d %c",version,&stamp,mode,name,&valid,&frames,&rate,&trial,&total,&truth,&correct,&scored,&extra);
        time_t now=time(NULL);
        if(n!=12||stamp<0||stamp>now+1||now-stamp>3||valid<0||valid>1||!isfinite(rate)||rate<0||rate>1000||
           trial<0||total<0||total>100000||trial>total||truth< -1||truth>1||correct<0||scored<correct||scored>total||parse_state(name,&d.state))return (dashboard){.state=IDLE};
        if(strcmp(mode,"menu")&&strcmp(mode,"live")&&strcmp(mode,"dataset"))return (dashboard){.state=IDLE};
        d.v2=1;d.mode=!strcmp(mode,"dataset")?2:!strcmp(mode,"live")?1:0;
        d.validated=valid;d.frames=frames;d.rate=rate;d.trial=trial;d.total=total;d.truth=truth;d.correct=correct;d.scored=scored;return d;
    }
    int n=sscanf(record,"%31s %lld %23s %d %lu %lf %c",version,&stamp,name,&valid,&frames,&rate,&extra);
    time_t now=time(NULL);
    if(n!=6||strcmp(version,"BCI_DISPLAY_V1")||valid<0||valid>1||!isfinite(rate)||rate<0||rate>1000||stamp<0||stamp>now+1||now-stamp>3)return d;
    if(parse_state(name,&d.state))return d;
    d.validated=valid;d.frames=frames;d.rate=rate;return d;
}
#ifdef __linux__
typedef struct {int spi,dc,res,emio_fd,lock_fd,backlight,keep_pins;uint32_t speed,state;volatile uint32_t *gpio;uint32_t old_dir,old_oen,old_data;} panel;
static void emio_write(panel *p,uint32_t value){
    p->state=value&0x3f;
    p->gpio[0x10/4]=0xffc00000u|p->state; /* bank2 low16: only EMIO0..5 */
    __sync_synchronize();
}
static int emio_open(panel *p){
    const char expected[]="emio-st7789-v1";char marker[sizeof expected];
    FILE *f=fopen("/proc/device-tree/bci,lcd-id","rb");
    if(!f){fprintf(stderr,"LCD boot marker missing; no GPIO accessed\n");return -1;}
    size_t n=fread(marker,1,sizeof marker,f);fclose(f);
    if(n!=sizeof expected||memcmp(marker,expected,sizeof expected)){errno=EINVAL;return -1;}
    if(!p->keep_pins){
        p->lock_fd=open("/run/bci_st7789.lock",O_RDWR|O_CREAT,0600);
        if(p->lock_fd<0||flock(p->lock_fd,LOCK_EX|LOCK_NB))return -1;
    }
    p->emio_fd=open("/dev/mem",O_RDWR|O_SYNC);if(p->emio_fd<0)return -1;
    void *map=mmap(NULL,4096,PROT_READ|PROT_WRITE,MAP_SHARED,p->emio_fd,0xe000a000);
    if(map==MAP_FAILED)return -1;
    p->gpio=map;p->old_data=p->gpio[0x48/4];p->old_dir=p->gpio[0x284/4];p->old_oen=p->gpio[0x288/4];
    emio_write(p,0x14); /* clock0, CS high, RESET high, backlight low */
    p->gpio[0x284/4]=p->old_dir|0x3f;
    p->gpio[0x288/4]=p->old_oen|0x3f;
    __sync_synchronize();return 0;
}
static void emio_close(panel *p){
    if(p->gpio){
        if(!p->keep_pins){
            emio_write(p,p->old_data);
            p->gpio[0x284/4]=(p->gpio[0x284/4]&~0x3fu)|(p->old_dir&0x3f);
            p->gpio[0x288/4]=(p->gpio[0x288/4]&~0x3fu)|(p->old_oen&0x3f);
        }
        __sync_synchronize();munmap((void *)p->gpio,4096);
    }
    if(p->emio_fd>=0)close(p->emio_fd);
    if(p->lock_fd>=0)close(p->lock_fd);
}
static int gpio_open_value(int number){
    char path[128],direction[16];snprintf(path,sizeof path,"/sys/class/gpio/gpio%d/direction",number);
    FILE *f=fopen(path,"r");if(!f)return -1;
    int ok=fgets(direction,sizeof direction,f)!=NULL;fclose(f);
    if(!ok||strncmp(direction,"out",3)){errno=EINVAL;return -1;}
    snprintf(path,sizeof path,"/sys/class/gpio/gpio%d/value",number);
    /* GPIO must already be exported as output by the reviewed board setup. */
    return open(path,O_WRONLY);
}
static int gpio_set(int fd,int value){if(lseek(fd,0,SEEK_SET)<0)return -1;return write(fd,value?"1":"0",1)==1?0:-1;}
static void delay_ms(unsigned ms){struct timespec t={ms/1000,(long)(ms%1000)*1000000};while(nanosleep(&t,&t)&&errno==EINTR&&!stopped){} }
static int spi_bytes(panel *p,int data,const void *bytes,size_t count){
    if(p->gpio){
        const unsigned char *b=bytes;uint8_t levels[24];
        uint8_t ctrl=(uint8_t)((p->state&0x30)|(data?8:0)); /* CS low */
        emio_write(p,ctrl);
        for(size_t j=0;j<count&&!stopped;j++){
            st7789_encode_byte(b[j],ctrl,levels);
            for(unsigned k=0;k<24;k++)emio_write(p,levels[k]);
        }
        emio_write(p,(uint32_t)(ctrl|4)); /* deselect after complete bytes */
        return stopped?-1:0;
    }
    if(gpio_set(p->dc,data))return -1;
    const unsigned char *b=bytes;
    while(count){size_t n=count>1024?1024:count;struct spi_ioc_transfer tr={0};
        tr.tx_buf=(uintptr_t)b;tr.len=(uint32_t)n;tr.speed_hz=p->speed;tr.bits_per_word=8;
        int done;do{done=ioctl(p->spi,SPI_IOC_MESSAGE(1),&tr);}while(done<0&&errno==EINTR&&!stopped);
        if(done!=(int)n)return -1;
        b+=n;count-=n;
    }return 0;
}
static int cmd(panel *p,unsigned char command,const unsigned char *data,size_t size){
    return spi_bytes(p,0,&command,1)||(size&&spi_bytes(p,1,data,size));
}
static int initialize(panel *p,unsigned char madctl,int invert){
    if(p->gpio)emio_write(p,p->state|0x10);else if(gpio_set(p->res,1))return -1;
    delay_ms(10);
    if(p->gpio)emio_write(p,p->state&~0x10u);else if(gpio_set(p->res,0))return -1;
    delay_ms(20);
    if(p->gpio)emio_write(p,p->state|0x10);else if(gpio_set(p->res,1))return -1;
    delay_ms(120);
    if(cmd(p,0x01,NULL,0))return -1;
    delay_ms(150);
    if(cmd(p,0x11,NULL,0))return -1;
    delay_ms(120);
    unsigned char fmt=0x55;
    if(cmd(p,0x3a,&fmt,1)||cmd(p,0x36,&madctl,1)||cmd(p,invert?0x21:0x20,NULL,0)||cmd(p,0x13,NULL,0)||cmd(p,0x29,NULL,0))return -1;
    if(p->gpio&&p->backlight)emio_write(p,p->state|0x20);
    delay_ms(20);return 0;
}
static int present(panel *p,unsigned xoffset,unsigned yoffset){
    unsigned xe=xoffset+WIDTH-1,ye=yoffset+HEIGHT-1;
    unsigned char xs[4]={(unsigned char)(xoffset>>8),(unsigned char)xoffset,(unsigned char)(xe>>8),(unsigned char)xe};
    unsigned char ys[4]={(unsigned char)(yoffset>>8),(unsigned char)yoffset,(unsigned char)(ye>>8),(unsigned char)ye};
    if(cmd(p,0x2a,xs,4)||cmd(p,0x2b,ys,4)||cmd(p,0x2c,NULL,0))return -1;
    unsigned char line[WIDTH*2];
    for(int y=0;y<HEIGHT;y++){
        for(int x=0;x<WIDTH;x++){uint16_t v=canvas[y*WIDTH+x];line[x*2]=(unsigned char)(v>>8);line[x*2+1]=(unsigned char)v;}
        if(spi_bytes(p,1,line,sizeof line))return -1;
    }return 0;
}
#endif
static int number(const char *s,unsigned long max,unsigned long *v){char *end;errno=0;*v=strtoul(s,&end,0);return errno||!*s||*end||*v>max?-1:0;}
int main(int argc,char **argv){
    dashboard d={.state=IDLE};const char *preview=NULL,*spi=NULL,*state_file=NULL;
    int dc=-1,res=-1,watch=0,invert=1,manual=0,color_test=0,emio=0,backlight=0,shutdown_ready=0;unsigned long speed=8000000,madctl=0x60,xoff=0,yoff=0,v;
    for(int i=1;i<argc;i++){
        const char *arg=argv[i];
        if(!strcmp(arg,"--demo"))d.demo=1;
        else if(!strcmp(arg,"--color-test")){color_test=1;d.demo=1;}
        else if(!strcmp(arg,"--emio"))emio=1;
        else if(!strcmp(arg,"--shutdown-ready"))shutdown_ready=1;
        else if(!strcmp(arg,"--backlight"))backlight=1;
        else if(!strcmp(arg,"--watch"))watch=1;
        else if(!strcmp(arg,"--no-invert"))invert=0;
        else if(i+1<argc&&!strcmp(arg,"--state")){if(parse_state(argv[++i],&d.state))return 2;manual=1;}
        else if(i+1<argc&&!strcmp(arg,"--preview"))preview=argv[++i];
        else if(i+1<argc&&!strcmp(arg,"--state-file"))state_file=argv[++i];
        else if(i+1<argc&&!strcmp(arg,"--spi"))spi=argv[++i];
        else if(i+1<argc&&!strcmp(arg,"--dc-gpio")){if(number(argv[++i],4095,&v))return 2;dc=(int)v;}
        else if(i+1<argc&&!strcmp(arg,"--reset-gpio")){if(number(argv[++i],4095,&v))return 2;res=(int)v;}
        else if(i+1<argc&&!strcmp(arg,"--speed")){if(number(argv[++i],32000000,&speed)||speed<100000)return 2;}
        else if(i+1<argc&&!strcmp(arg,"--madctl")){if(number(argv[++i],255,&madctl))return 2;}
        else if(i+1<argc&&!strcmp(arg,"--x-offset")){if(number(argv[++i],65535-WIDTH,&xoff))return 2;}
        else if(i+1<argc&&!strcmp(arg,"--y-offset")){if(number(argv[++i],65535-HEIGHT,&yoff))return 2;}
        else{fprintf(stderr,"Invalid option %s\n",arg);return 2;}
    }
    if((!preview&&!spi&&!emio)||(preview&&(spi||emio))||(spi&&emio)||(state_file&&manual)||(watch&&!spi&&!emio)||
       (manual&&!d.demo)||(color_test&&manual)||(d.demo&&state_file)||(spi&&(dc<0||res<0||dc==res))||
       (shutdown_ready&&(!emio||watch||preview||state_file||manual||d.demo||color_test))||
       (!preview&&manual&&d.state==POWER_READY)){
        fprintf(stderr,"Preview: --preview NEW.ppm [--state idle|warmup|bad|left|right --demo]\n"
          "Linux: --emio [--backlight] OR --spi /dev/spidevB.C --dc-gpio N --reset-gpio N; [--state-file FILE --watch | --state left --demo]\n");return 2;
    }
#ifndef __linux__
    (void)dc;(void)res;(void)invert;(void)speed;(void)madctl;(void)xoff;(void)yoff;(void)backlight;
    if(spi||emio){fprintf(stderr,"Hardware backend requires Linux\n");return 2;}
#endif
#ifdef __linux__
    if(shutdown_ready){
        FILE *mounts=fopen("/proc/mounts","r");if(!mounts)return 1;
        int safe=storage_mounts_safe(mounts);fclose(mounts);
        FILE *swaps=fopen("/proc/swaps","r"),*mem=swaps?NULL:fopen("/proc/meminfo","r");
        if(!storage_swap_safe(swaps,mem))safe=0;
        if(swaps)fclose(swaps);
        if(mem)fclose(mem);
        if(!safe){fprintf(stderr,"Storage still writable or swap active; no safe-to-cut indication\n");return 1;}
        d.state=POWER_READY;
    }
#endif
    signal(SIGINT,stop_handler);signal(SIGTERM,stop_handler);
    if(state_file)d=read_dashboard(state_file);
    if(color_test)draw_color_test();else draw(d);
    if(preview)return ppm(preview)?1:0;
#ifdef __linux__
    panel p={.spi=-1,.dc=-1,.res=-1,.emio_fd=-1,.lock_fd=-1,.backlight=backlight,.keep_pins=shutdown_ready,.speed=(uint32_t)speed};int rc=1;
    if(emio){if(emio_open(&p))goto done;}
    else{
      p.spi=open(spi,O_RDWR);if(p.spi<0){perror(spi);goto done;}
      p.dc=gpio_open_value(dc);p.res=gpio_open_value(res);
      if(p.dc<0||p.res<0){perror("GPIO must be exported as output first");goto done;}
    uint8_t mode=SPI_MODE_0,bits=8,lsb=0;
    if(ioctl(p.spi,SPI_IOC_WR_MODE,&mode)<0||ioctl(p.spi,SPI_IOC_WR_BITS_PER_WORD,&bits)<0||
       ioctl(p.spi,SPI_IOC_WR_LSB_FIRST,&lsb)<0||ioctl(p.spi,SPI_IOC_WR_MAX_SPEED_HZ,&p.speed)<0)goto done;
    }
    if(initialize(&p,(unsigned char)madctl,invert))goto done;
    do{
        if(state_file)d=read_dashboard(state_file);
        if(color_test)draw_color_test();else draw(d);
        if(present(&p,(unsigned)xoff,(unsigned)yoff))goto done;
        if(watch)delay_ms(200);
    }while(watch&&!stopped);
    rc=0;
done:
    if(stopped)rc=0;
    if(rc)perror("ST7789 transport");
    if(p.res>=0)close(p.res);
    if(p.dc>=0)close(p.dc);
    if(p.spi>=0)close(p.spi);
    emio_close(&p);
    return rc;
#else
    return 2;
#endif
}
