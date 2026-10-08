#ifndef FPSUP_QS_LAYER_H
#define FPSUP_QS_LAYER_H
#include <stdint.h>
#include "qs_apply.h"

/* One sup's Quick Set layers (QS_SHARE.md §2, §4.1, §4.2, §4.4).
 *
 * Record layer (C05E6400). A layer recognises a record by where it sits in
 * the stock NBU (the reader's position, which no layer changes) -- whether
 * the reader still points at the stock bytes or at a copy an outer layer
 * made. A copy carries, in the 16 bytes before the record, the name of the
 * layer that made it, the version and the stock base; it counts only if that
 * name is one the loader's books list as holding C05E6400 (the rule the
 * string layers use). The copy buffer holds the record (QS_BUFFER) and, after
 * it, QS_BUFFER of scratch inner layers use, so only the layer that copies
 * has a buffer on the stack.
 *
 * Layout layer (C05D90F0). Quick Set layouts are parsed at boot and held by
 * UicResourceCache; one parsed before the record layers were hung stays stock
 * for the power cycle. On the first layout load after hanging, record the
 * trees the cache's three QS layouts have. Later, only while ANOTHER layout
 * -- not one of the three -- is loading, look at the QS layout the cache
 * holds: if it still has the recorded tree, held only by the cache, the
 * cache not busy, not the current or held screen (QS closed), release it with
 * the cache's own v3 so the next QS open re-parses it through the record
 * layers. Never the tree of the layout being loaded, inside its own load
 * (v5c did that at the first QSON and froze the camera, QS_SHARE.md §4.1).
 * Several layers each do it; the first release changes the tree, so the
 * others forget it -- one release, any order. v3 takes the CACHE in r0 (not
 * the tree): with the tree's refcount at 1 it frees it and sets [layout+0xC]
 * = 0, so the next load re-parses (C05E82F0); §4.1 has the addresses.
 * Switch rule (QS_FLAG_SWITCH; for the case that image names are fixed when
 * a layout is parsed): when the resolution value becomes this layer's enum,
 * the cache's QS layout is released once, at the next such load.
 *
 * Pack layer (C05E84D8). After the stock NBR (C0D22400) is loaded into a
 * resource context, register this sup's private pack in the same context,
 * once per context (og3k_ui.S ensure_ui_pack). The record layer does the same
 * for the context of the first stock record it sees, in case the stock NBR
 * was loaded before the layers were hung. A failed registration: status
 * QS_ST_PACK and the record layer passes every record through (QS stays
 * stock, no half screen).
 *
 * Row <-> enum table (§4.4): the firmware's converter C06BDA30 returns a table
 * of (enum, row) pairs -- movw/movt r2 at C06BDA3C/40, the count at C06BDA48
 * (stock: C2E4020C, 3 pairs (3,0) (2,1) (4,2)). The first sup copies the
 * stock UHD/FHD pairs into cave memory with a 16-byte header {its name,
 * version, capacity} and points the converter at it; each sup appends (its
 * enum, its row k). The enum is the sup's, fixed; k is only a position.
 *
 * Entry (the sup, in this order, before anything else is written):
 *   qs_check(header, svc, &opt)   claims its resolution value (QS_RES_ENUM_BASE +
 *                                 value, EXCL: a second sup with the same value
 *                                 is refused, LOAD.LOG "QS FAIL ENUM n") and
 *                                 the five ranges below (SHARED_UI, with
 *                                 their stock words: the loader repairs a stale
 *                                 hook on the first claim), then: the sites hold
 *                                 stock or layers the books know,
 *                                 N supported, the enum not taken, pack valid;
 *                                 reserves the cave it will need (veneers, and
 *                                 the pair table if none exists) in the header
 *   (claims: C05E6400, C05D90F0, C05E84D8, C06BDA3C+8, C06BDA48 -- qs_check's)
 * commit (cannot fail; qs_check has seen every way it could):
 *   qs_hang(header, svc, &opt)
 */
#define QS_SITE        0xC05E6400u
#define QS_SITE_STOCK  0x4FF0E92Du      /* push.w {r4-r11, lr} */
#define QS_LAY_SITE    0xC05D90F0u
#define QS_LAY_STOCK   0x4CF0E92Du      /* push.w {r4-r7, r10, r11, lr} */
#define QS_LOAD_SITE   0xC05E84D8u
#define QS_LOAD_STOCK  0xB086B500u      /* push {lr}; sub sp, #24 */
#define QS_CONV_MOVW   0xC06BDA3Cu
#define QS_CONV_MOVT   0xC06BDA40u
#define QS_CONV_COUNT  0xC06BDA48u
#define QS_CONV_STOCK_TABLE 0xC2E4020Cu
#define QS_NBU_BASE    0xC18C0460u
#define QS_STOCK_NBR   0xC0D22400u
#define QS_LAYER_VERSION 2u      /* v2: self-describing (QS_H_LEN, QS_H_OFF_*, back words) */
#define QS_COPY_HEAD   16u
#define QS_COPY_BYTES  (QS_COPY_HEAD + 2u * QS_BUFFER)
#define QS_PAIRS_MAX   8u
#define QS_PAIRS_HEAD  16u
#define QS_PAIRS_BYTES (QS_PAIRS_HEAD + 8u * QS_PAIRS_MAX)

/* The header (qs_layer.S, 40 words). */
#define QS_H_NAME       0u
#define QS_H_VERSION    8u
#define QS_H_NEXT       12u     /* record: next layer's entry, 0 = replay */
#define QS_H_TABLE      16u
#define QS_H_K          20u
#define QS_H_STATE      24u
#define QS_H_IDS        28u     /* four words */
#define QS_H_N          44u
#define QS_H_RECORD     48u     /* the C halves (Thumb), set by qs_hang */
#define QS_H_REPLAY     52u
#define QS_H_SVC        56u
#define QS_H_LAY_NEXT   60u
#define QS_H_LAYOUT     64u
#define QS_H_LAY_REPLAY 68u
#define QS_H_LOAD_NEXT  72u
#define QS_H_LOAD       76u
#define QS_H_LOAD_REPLAY 80u
#define QS_H_ENUM       84u
#define QS_H_FLAGS      88u
#define QS_H_LAST_ENUM  92u
#define QS_H_PACK       96u
#define QS_H_PACK_LEN   100u
#define QS_H_PACK_CTX   104u
#define QS_H_PACK_STATE 108u    /* 0 new, 1 ready, 2 loading, 3 failed */
#define QS_H_LAY_TAKEN  112u
#define QS_H_SNAP       116u    /* three {layout, tree} */
#define QS_H_RELEASES   140u
#define QS_H_PENDING    144u
#define QS_H_STATUS     148u
#define QS_H_CAVE       152u    /* three veneers, reserved by qs_check */
#define QS_H_PAIRS_NEW  156u    /* a pair table, reserved by qs_check (0: one exists) */
#define QS_H_BYTES      160u    /* the v1 words; v2 adds: */
#define QS_H_LEN        160u    /* header length */
#define QS_H_OFF_REC    164u    /* each entry's offset from the header */
#define QS_H_OFF_LAY    168u
#define QS_H_OFF_LOAD   172u
#define QS_H_MIN_LEN    176u
/* This build's own entry and replay offsets: qs_build.py passes them from
 * qs_layer.S's symbols (-D), so changing the assembly needs no edit here.
 * Readers of OTHER layers never use these: they use the header's offsets. */
#ifndef QS_ENTRY_RECORD
#define QS_ENTRY_RECORD 180u
#define QS_ENTRY_LAYOUT 220u
#define QS_ENTRY_LOAD   260u
#define QS_REPLAY_RECORD 300u   /* Thumb: +1 */
#define QS_REPLAY_LAYOUT 316u
#define QS_REPLAY_LOAD   332u
#endif

#define QS_FLAG_SWITCH  1u
/* ui/enums.py UIS_RES_ENUM_BASE: each sup claims base + its value (EXCL) */
#define QS_RES_ENUM_BASE 0x4D4E4500u

enum qs_state { QS_NOT_READY = 0, QS_READY = 1 };
/* qs_check */
enum qs_check_result { QS_OK = 0, QS_HOOK = 1, QS_NMAX = 2, QS_NAME = 3, QS_ROOM = 4,
                       QS_ENUM_TAKEN = 5, QS_TABLE = 6, QS_PACK_BAD = 7 };
/* status word (QS_SHARE.md §9.5) */
#define QS_ST_PACK      0x80000001u

/* What a sup hands its layers. pack: its private NBR pack (0 = none), already
 * where it will live (qs_pack_place); k: its row; ids: the shared QS/SET/FONT
 * images and its own SET image; enum: the resolution value it stores. */
struct qs_option { uint32_t table, k, ids[4], enum_value, flags, pack, pack_len,
                   n; /* the resolution rows with this sup's (uia_apply knows them before
                         its CSV is committed); 0 = read the shared CSV now */ };

uint32_t qs_rows_now(void);
uint32_t qs_check(uintptr_t header, uintptr_t svc, const struct qs_option *opt);
void qs_hang(uintptr_t header, uintptr_t svc, const struct qs_option *opt);
uint32_t qs_record(uintptr_t reader, uintptr_t header);
uint32_t qs_layout(uintptr_t layout, uintptr_t header);
uint32_t qs_load(uint32_t *args, uintptr_t header);
/* Move a pack to the first 64-aligned address in [area, area+room); its
 * address there, or 0 if it does not fit or is not an NBR pack. */
uintptr_t qs_pack_place(uintptr_t pack, uint32_t len, uintptr_t area, uint32_t room);
#endif
