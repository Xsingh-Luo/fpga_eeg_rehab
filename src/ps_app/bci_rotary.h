#ifndef BCI_ROTARY_H
#define BCI_ROTARY_H
#include <stdint.h>
enum {KNOB_NONE=0,KNOB_NEXT=1,KNOB_PREVIOUS=-1,KNOB_PRESS=2,KNOB_BACK=3};
typedef struct {unsigned ab;int partial,raw,stable,held,initialized;uint64_t changed,pressed;} knob_decoder;
static inline int knob_update(knob_decoder *k,unsigned ab,int pressed,uint64_t ms){
    static const int8_t steps[16]={0,-1,1,0,1,0,0,-1,-1,0,0,1,0,1,-1,0};
    int event=KNOB_NONE;
    if(!k->initialized){k->ab=ab;k->raw=k->stable=pressed;k->held=pressed;k->changed=k->pressed=ms;k->initialized=1;return event;}
    if(ab!=k->ab){
        if((ab^k->ab)==3)k->partial=0;
        else k->partial+=steps[(k->ab<<2)|ab];
        k->ab=ab;
        if(k->partial>=4){event=KNOB_NEXT;k->partial=0;}
        if(k->partial<=-4){event=KNOB_PREVIOUS;k->partial=0;}
    }
    if(pressed!=k->raw){k->raw=pressed;k->changed=ms;}
    if(k->raw!=k->stable&&ms-k->changed>=25){
        k->stable=k->raw;
        if(k->stable){k->pressed=ms;k->held=0;}
        else if(!k->held){event=KNOB_PRESS;}
    }
    if(k->stable&&!k->held&&ms-k->pressed>=1000){k->held=1;event=KNOB_BACK;}
    return event;
}
#endif
