#ifndef BCI_NOTCH_H
#define BCI_NOTCH_H
#include <math.h>
typedef struct {double b0,b1,b2,a1,a2,x1[2],x2[2],y1[2],y2[2];} bci_notch;
static inline void bci_notch_init(bci_notch *n){
    *n=(bci_notch){0};double g=1/(1+tan(3.14159265358979323846*50/(250*30)));
    n->b0=n->b2=g;n->b1=n->a1=-2*g*cos(2*3.14159265358979323846*50/250);n->a2=2*g-1;
}
static inline double bci_notch_apply(bci_notch *n,unsigned c,double x){
    double y=n->b0*x+n->b1*n->x1[c]+n->b2*n->x2[c]-n->a1*n->y1[c]-n->a2*n->y2[c];
    n->x2[c]=n->x1[c];n->x1[c]=x;n->y2[c]=n->y1[c];n->y1[c]=y;return y;
}
#endif
