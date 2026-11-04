#ifndef ST7789_EMIO_H
#define ST7789_EMIO_H
#include <stdint.h>
/* EMIO[0..5] = SCK, MOSI, CSn, DC, RESETn, BLK. Mode0, MSB first.
 * Three ordered output writes per bit: data/clock-low, clock-high, clock-low.
 */
static inline void st7789_encode_byte(uint8_t byte,uint8_t control,uint8_t out[24]){
    control&=0x3c;
    for(unsigned bit=0;bit<8;bit++){
        uint8_t low=(uint8_t)(control|((byte&(0x80u>>bit))?2:0));
        out[bit*3]=low;out[bit*3+1]=(uint8_t)(low|1);out[bit*3+2]=low;
    }
}
#endif
