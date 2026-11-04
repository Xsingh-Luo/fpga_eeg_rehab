#include <assert.h>
#include <stdio.h>
#include "../../src/ps_app/bci_menu.h"
#include "../../src/ps_app/bci_storage_guard.h"
static int safe(const char *mounts){FILE *f=tmpfile();assert(f);fputs(mounts,f);rewind(f);int r=storage_mounts_safe(f);fclose(f);return r;}
static int swap_safe(const char *swaps,const char *mem){
    FILE *s=NULL,*m=NULL;
    if(swaps){s=tmpfile();assert(s);fputs(swaps,s);rewind(s);}
    if(mem){m=tmpfile();assert(m);fputs(mem,m);rewind(m);}
    int result=storage_swap_safe(s,m);if(s)fclose(s);if(m)fclose(m);return result;
}
int main(void){
    bci_menu m={0};assert(menu_event(&m,KNOB_PRESS)==MENU_START_LIVE);
    menu_event(&m,KNOB_NEXT);assert(menu_event(&m,KNOB_PRESS)==MENU_START_DATASET);
    menu_event(&m,KNOB_NEXT);assert(m.selected==2);
    assert(menu_event(&m,KNOB_PRESS)==MENU_NO_ACTION&&m.confirming&&!m.confirm_yes);
    assert(menu_event(&m,KNOB_PRESS)==MENU_NO_ACTION&&!m.confirming);
    menu_event(&m,KNOB_PRESS);menu_event(&m,KNOB_NEXT);assert(m.confirm_yes);
    assert(menu_event(&m,KNOB_BACK)==MENU_NO_ACTION&&!m.confirming);
    menu_event(&m,KNOB_PRESS);menu_event(&m,KNOB_PREVIOUS);
    assert(menu_event(&m,KNOB_PRESS)==MENU_POWER_OFF);
    menu_event(&m,KNOB_NEXT);assert(m.selected==0);menu_event(&m,KNOB_PREVIOUS);assert(m.selected==2);
    assert(safe("/dev/root / ext4 ro,relatime 0 0\ntmpfs /run tmpfs rw,nosuid 0 0\n"));
    assert(!safe("/dev/root / ext4 rw,relatime 0 0\n"));
    assert(!safe("/dev/root / ext4 ro,relatime 0 0\n/dev/sda1 /usb exfat rw 0 0\n"));
    assert(!safe("tmpfs /run tmpfs rw 0 0\n"));
    assert(!safe("/dev/root / ext4 ro 0 0\nbroken"));
    assert(!safe("/dev/root / ext4 ro 0 0\nfoo /other mysteryfs rw 0 0\n"));
    assert(safe("/dev/root / ext4 ro 0 0\n/dev/mmcblk0p1 /boot vfat ro 0 0\n"));
    assert(swap_safe("Filename Type Size Used Priority\n",NULL));
    assert(!swap_safe("Filename Type Size Used Priority\n/dev/sda2 partition 100 1 -2\n",NULL));
    assert(swap_safe(NULL,"MemTotal: 512000 kB\nSwapTotal: 0 kB\n"));
    assert(!swap_safe(NULL,"SwapTotal: 1000 kB\n"));
    assert(!swap_safe(NULL,NULL));
    puts("PASS menu confirmation/cancel/wrap, live/dataset routes, and fail-closed storage guard");return 0;
}
