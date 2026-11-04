#ifndef CYTON_PARSER_H
#define CYTON_PARSER_H

#include <stddef.h>
#include <stdint.h>

#define CYTON_FRAME_SIZE 33
#define CYTON_CHANNELS 8

typedef struct {
    uint8_t seq, footer, aux[6];
    int32_t ch[CYTON_CHANNELS];
    unsigned seq_delta; /* modulo 256; 0 means duplicate, not 255 lost */
} cyton_sample;

typedef struct {
    uint8_t frame[CYTON_FRAME_SIZE];
    size_t used;
    unsigned header; /* 0 = auto (0x41 or 0xA0), otherwise exact header */
    uint8_t previous;
    int have_previous;
    uint64_t bytes, packets, discarded, bad_footer;
    uint64_t sequence_jumps, duplicates, missing_mod256;
} cyton_parser;

typedef int (*cyton_sink)(const cyton_sample *, void *);

void cyton_init(cyton_parser *p, unsigned header);
/* Calls sink for each frame. Returns -1 if the sink fails. */
int cyton_feed(cyton_parser *p, const uint8_t *data, size_t size,
               cyton_sink sink, void *context);

#endif
