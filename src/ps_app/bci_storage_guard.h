#ifndef BCI_STORAGE_GUARD_H
#define BCI_STORAGE_GUARD_H
#include <stdio.h>
#include <string.h>
/* Conservative allowlist: anything persistent or unknown must be read-only. */
static inline int storage_mounts_safe(FILE *f){
    char source[256],target[256],type[64],options[1024],tail[128];int root_ro=0,fields;
    while((fields=fscanf(f,"%255s %255s %63s %1023s",source,target,type,options))==4){
        int rw=!strncmp(options,"rw,",3)||!strcmp(options,"rw");
        int ro=!strncmp(options,"ro,",3)||!strcmp(options,"ro");
        if(!strcmp(target,"/")){if(!ro)return 0;root_ro=1;}
        int volatile_fs=!strcmp(type,"tmpfs")||!strcmp(type,"devtmpfs")||!strcmp(type,"proc")||
            !strcmp(type,"sysfs")||!strcmp(type,"devpts")||!strcmp(type,"cgroup")||!strcmp(type,"cgroup2")||
            !strcmp(type,"debugfs")||!strcmp(type,"securityfs")||!strcmp(type,"configfs")||!strcmp(type,"pstore")||
            !strcmp(type,"mqueue")||!strcmp(type,"tracefs")||!strcmp(type,"ramfs")||!strcmp(type,"rootfs");
        if(rw&&!volatile_fs)return 0;
        if(!fgets(tail,sizeof tail,f)||!strchr(tail,'\n'))return 0;
    }
    return root_ro&&!ferror(f)&&fields==EOF;
}
static inline int storage_swap_safe(FILE *swaps,FILE *meminfo){
    char line[512];
    if(swaps)return fgets(line,sizeof line,swaps)&&!fgets(line,sizeof line,swaps)&&!ferror(swaps);
    int zero_swap=0;
    if(meminfo){
        while(fgets(line,sizeof line,meminfo)){unsigned long kb;if(sscanf(line,"SwapTotal: %lu kB",&kb)==1)zero_swap=kb==0;}
        if(ferror(meminfo))zero_swap=0;
    }
    return zero_swap;
}
#endif
