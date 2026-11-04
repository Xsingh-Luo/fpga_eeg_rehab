#ifndef BCI_UI_STATUS_H
#define BCI_UI_STATUS_H
#include <stdio.h>
#include <time.h>
#include <string.h>
/* Atomic replacement: renderer never reads a partially written status line. */
static inline int ui_status(const char *path,const char *mode,const char *state,unsigned long frames,double hz,
                     int trial,int total,int truth,int correct,int scored){
    char tmp[2048];if(snprintf(tmp,sizeof tmp,"%s.pending",path)>=(int)sizeof tmp)return -1;
    FILE *f=fopen(tmp,"w");if(!f)return -1;
    int bad=fprintf(f,"BCI_UI_V2 %lld %s %s 0 %lu %.2f %d %d %d %d %d\n",
        (long long)time(NULL),mode,state,frames,hz,trial,total,truth,correct,scored)<0;
    if(fclose(f))bad=1;
    return bad?-1:rename(tmp,path);
}
#endif
