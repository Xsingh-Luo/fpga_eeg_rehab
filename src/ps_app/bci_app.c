/* EC11 mode supervisor. Input polling never waits for a full LCD redraw. */
#define _POSIX_C_SOURCE 200809L
#define _FILE_OFFSET_BITS 64
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <signal.h>
#include "bci_rotary.h"
#include "bci_ui_status.h"
#include "bci_menu.h"
#ifdef __linux__
#include <fcntl.h>
#include <unistd.h>
#include <sys/mman.h>
#include <sys/file.h>
#include <sys/wait.h>
#include <sys/stat.h>
static volatile sig_atomic_t exiting;
static void terminate(int sig){(void)sig;exiting=1;}
static uint64_t now_ms(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (uint64_t)t.tv_sec*1000+t.tv_nsec/1000000;}
static void pause_ms(unsigned ms){struct timespec t={ms/1000,(long)(ms%1000)*1000000};while(nanosleep(&t,&t)&&errno==EINTR&&!exiting){} }
static void stop_child(pid_t *pid){
    if(*pid<=0)return;
    (void)kill(*pid,SIGTERM);uint64_t deadline=now_ms()+3000;
    while(now_ms()<deadline){pid_t done=waitpid(*pid,NULL,WNOHANG);if(done==*pid||done<0){*pid=-1;return;}pause_ms(10);}
    (void)kill(*pid,SIGKILL);while(waitpid(*pid,NULL,0)<0&&errno==EINTR){}*pid=-1;
}
static pid_t launch(const char *root,const char *program,const char *mode,const char *ui,const char *out){
    char path[2048];if(snprintf(path,sizeof path,"%s/%s",root,program)>=(int)sizeof path)return -1;
    pid_t pid=fork();
    if(pid==0){
        if(!strcmp(program,"st7789_display"))execl(path,path,"--emio","--state-file",ui,"--watch",(char *)NULL);
        else execl(path,path,"--root",root,"--mode",mode,"--status",ui,"--out",out,(char *)NULL);
        perror(path);_exit(127);
    }
    return pid;
}
static pid_t launch_poweroff(const char *root){
    char path[2048];if(snprintf(path,sizeof path,"%s/poweroff.sh",root)>=(int)sizeof path)return -1;
    pid_t pid=fork();
    if(pid==0){execl("/bin/sh","sh",path,(char *)NULL);perror(path);_exit(127);}
    return pid;
}
static int refresh_finished(const char *ui){
    char version[32],mode[16],state[24],extra;long long stamp;int valid,trial,total,truth,correct,scored;unsigned long frames;double rate;
    FILE *f=fopen(ui,"r");if(!f)return -1;
    int n=fscanf(f,"%31s %lld %15s %23s %d %lu %lf %d %d %d %d %d %c",version,&stamp,mode,state,&valid,&frames,&rate,&trial,&total,&truth,&correct,&scored,&extra);fclose(f);
    if(n!=12||strcmp(version,"BCI_UI_V2"))return -1;
    return ui_status(ui,mode,state,frames,rate,trial,total,truth,correct,scored);
}
#endif
int main(int argc,char **argv){
    if(argc==2&&!strcmp(argv[1],"--version")){puts("BCI_APP_SHUTDOWN_20261103 EC11_V1 UI_V2");return 0;}
    const char *root=NULL,*ui="/run/bci_ui_state",*runs="/root/bci/ui_runs";
    for(int i=1;i<argc;i++){
        if(i+1<argc&&!strcmp(argv[i],"--root"))root=argv[++i];
        else if(i+1<argc&&!strcmp(argv[i],"--status"))ui=argv[++i];
        else if(i+1<argc&&!strcmp(argv[i],"--runs"))runs=argv[++i];
        else return 2;
    }
    if(!root||root[0]!='/')return 2;
#ifndef __linux__
    (void)ui;(void)runs;fprintf(stderr,"EC11 supervisor requires ARM Linux\n");return 2;
#else
    int rc=1,lock=-1,fd=-1;volatile uint32_t *gpio=NULL;uint32_t old_dir=0,old_oen=0;
    pid_t display=-1,worker=-1,power=-1;int active=0,powering=0;unsigned serial=0;
    bci_menu menu={0};uint64_t power_started=0;const char *power_state="powering-off";
    /* Reject the old output-only bitstream before touching any GPIO registers. */
    const char marker[]="emio-ec11-v1";char actual[sizeof marker];
    FILE *f=fopen("/proc/device-tree/bci,knob-id","rb");if(!f){fprintf(stderr,"EC11 platform image required\n");goto done;}
    size_t n=fread(actual,1,sizeof actual,f);fclose(f);
    if(n!=sizeof marker||memcmp(actual,marker,sizeof marker))goto done;
    lock=open("/run/bci_app.lock",O_RDWR|O_CREAT|O_CLOEXEC,0600);if(lock<0||flock(lock,LOCK_EX|LOCK_NB))goto done;
    if(mkdir(runs,0700)&&errno!=EEXIST)goto done;
    fd=open("/dev/mem",O_RDWR|O_SYNC|O_CLOEXEC);if(fd<0)goto done;
    void *map=mmap(NULL,4096,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0xe000a000);if(map==MAP_FAILED)goto done;
    gpio=map;old_dir=gpio[0x284/4];old_oen=gpio[0x288/4];
    gpio[0x288/4]=old_oen&~0x1c0u;gpio[0x284/4]=old_dir&~0x1c0u;__sync_synchronize();
    signal(SIGTERM,terminate);signal(SIGINT,terminate);
    if(ui_status(ui,"menu","menu-live",0,0,0,0,-1,0,0))goto done;
    display=launch(root,"st7789_display",NULL,ui,NULL);if(display<0)goto done;
    knob_decoder knob={0};uint64_t heartbeat=now_ms();
    while(!exiting){
        int child_status;pid_t ended=waitpid(display,&child_status,WNOHANG);
        if(ended==display){display=-1;fprintf(stderr,"Display process stopped; stopping inference\n");goto done;}
        if(worker>0){ended=waitpid(worker,&child_status,WNOHANG);if(ended==worker){worker=-1;
            if(!WIFEXITED(child_status)||WEXITSTATUS(child_status)!=0)(void)ui_status(ui,menu.selected?"dataset":"live","error",0,0,0,0,-1,0,0);
        }}
        if(power>0&&waitpid(power,&child_status,WNOHANG)==power){
            power=-1;
            if(!WIFEXITED(child_status)||WEXITSTATUS(child_status)!=0){powering=0;power_state="power-error";}
        }
        __sync_synchronize();uint32_t bits=(gpio[0x68/4]>>6)&7u;uint64_t tick=now_ms();
        int event=knob_update(&knob,bits&3u,!(bits&4u),tick);
        if(powering){
            if(tick-heartbeat>=500){
                if(tick-power_started>=20000)power_state="power-wait";
                if(ui_status(ui,"menu",power_state,0,0,0,0,-1,0,0))goto done;
                heartbeat=tick;
            }
            pause_ms(1);continue;
        }
        if(!strcmp(power_state,"power-error")){
            if(event==KNOB_BACK){power_state="powering-off";active=0;}
            else {if(tick-heartbeat>=500){if(ui_status(ui,"menu","power-error",0,0,0,0,-1,0,0))goto done;heartbeat=tick;}pause_ms(1);continue;}
        }
        if(active&&(event==KNOB_BACK||(worker<0&&event==KNOB_PRESS))){
            stop_child(&worker);active=0;
        }else if(!active){
            int action=menu_event(&menu,event);
            if(action==MENU_POWER_OFF){
                stop_child(&worker);
                if(ui_status(ui,"menu","powering-off",0,0,0,0,-1,0,0))goto done;
                power=launch_poweroff(root);power_started=tick;powering=power>0;
                if(power<0)power_state="power-error";
            }else if(action==MENU_START_LIVE||action==MENU_START_DATASET){
                const char *mode=action==MENU_START_DATASET?"dataset":"live";
                char out[2048];snprintf(out,sizeof out,"%s/run_%lld_%u_%s",runs,(long long)time(NULL),serial++,mode);
                if(ui_status(ui,mode,"prepare",0,0,0,0,-1,0,0))goto done;
                worker=launch(root,"bci_run",mode,ui,out);
                if(worker<0)goto done;
                active=1;
            }
        }
        if(tick-heartbeat>=500||event!=KNOB_NONE){
            if(!active){if(ui_status(ui,"menu",powering?"powering-off":!strcmp(power_state,"power-error")?power_state:menu_state(&menu),0,0,0,0,-1,0,0))goto done;}
            else if(worker<0&&refresh_finished(ui))goto done;
            heartbeat=tick;
        }
        pause_ms(1);
    }
    rc=0;
done:
    stop_child(&worker);stop_child(&display);
    if(gpio){
        gpio[0x284/4]=(gpio[0x284/4]&~0x1c0u)|(old_dir&0x1c0u);
        gpio[0x288/4]=(gpio[0x288/4]&~0x1c0u)|(old_oen&0x1c0u);
        __sync_synchronize();munmap((void *)gpio,4096);
    }
    if(fd>=0)close(fd);
    if(lock>=0)close(lock);
    if(rc)fprintf(stderr,"BCI application stopped with error\n");
    return rc;
#endif
}
