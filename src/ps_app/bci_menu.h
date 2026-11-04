#ifndef BCI_MENU_H
#define BCI_MENU_H
#include "bci_rotary.h"
enum {MENU_NO_ACTION, MENU_START_LIVE, MENU_START_DATASET, MENU_POWER_OFF};
typedef struct {int selected,confirming,confirm_yes;} bci_menu;
static inline int menu_event(bci_menu *m,int event){
    if(event==KNOB_BACK){m->confirming=0;m->confirm_yes=0;return MENU_NO_ACTION;}
    if(m->confirming){
        if(event==KNOB_NEXT||event==KNOB_PREVIOUS)m->confirm_yes=!m->confirm_yes;
        if(event==KNOB_PRESS){
            int yes=m->confirm_yes;m->confirming=0;m->confirm_yes=0;
            if(yes)return MENU_POWER_OFF;
        }
        return MENU_NO_ACTION;
    }
    if(event==KNOB_NEXT)m->selected=(m->selected+1)%3;
    if(event==KNOB_PREVIOUS)m->selected=(m->selected+2)%3;
    if(event==KNOB_PRESS){
        if(m->selected==2){m->confirming=1;m->confirm_yes=0;}
        else return m->selected?MENU_START_DATASET:MENU_START_LIVE;
    }
    return MENU_NO_ACTION;
}
static inline const char *menu_state(const bci_menu *m){
    if(m->confirming)return m->confirm_yes?"power-confirm":"power-cancel";
    return m->selected==2?"menu-power":m->selected?"menu-dataset":"menu-live";
}
#endif
