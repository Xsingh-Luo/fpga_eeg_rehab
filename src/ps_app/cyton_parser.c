#include "cyton_parser.h"
#include <string.h>

static int is_header(const cyton_parser *p, uint8_t b)
{
    return p->header ? b == p->header : b == 0x41 || b == 0xA0;
}

void cyton_init(cyton_parser *p, unsigned header)
{
    memset(p, 0, sizeof(*p));
    p->header = header;
}

int cyton_feed(cyton_parser *p, const uint8_t *data, size_t size,
               cyton_sink sink, void *context)
{
    size_t i;
    for (i = 0; i < size; ++i) {
        cyton_sample s;
        unsigned c;
        ++p->bytes;
        if (!p->used && !is_header(p, data[i])) {
            ++p->discarded;
            continue;
        }
        p->frame[p->used++] = data[i];
        if (p->used < CYTON_FRAME_SIZE) continue;
        /* Match the supplied Python reference's supported packet types. */
        if (p->frame[32] < 0xC0 || p->frame[32] > 0xC6) {
            size_t next = 1;
            ++p->bad_footer;
            while (next < CYTON_FRAME_SIZE && !is_header(p, p->frame[next]))
                ++next;
            p->discarded += next;
            p->used -= next;
            memmove(p->frame, p->frame + next, p->used);
            continue;
        }
        memset(&s, 0, sizeof(s));
        s.seq = p->frame[1];
        s.footer = p->frame[32];
        memcpy(s.aux, p->frame + 26, 6);
        for (c = 0; c < CYTON_CHANNELS; ++c) {
            const uint8_t *b = p->frame + 2 + c * 3;
            uint32_t v = ((uint32_t)b[0] << 16) | ((uint32_t)b[1] << 8) | b[2];
            s.ch[c] = (int32_t)v - ((v & 0x800000u) ? 16777216 : 0);
        }
        if (p->have_previous) {
            s.seq_delta = ((unsigned)s.seq + 256u - p->previous) % 256u;
            if (!s.seq_delta) ++p->duplicates;
            else if (s.seq_delta != 1) {
                ++p->sequence_jumps;
                p->missing_mod256 += s.seq_delta - 1;
            }
        }
        p->previous = s.seq;
        p->have_previous = 1;
        p->used = 0;
        ++p->packets;
        if (sink(&s, context)) return -1;
    }
    return 0;
}
