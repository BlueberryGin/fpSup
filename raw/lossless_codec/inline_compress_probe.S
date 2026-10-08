/* The whole chain, in one place, at the flush site.
 *
 * Every piece below has been measured separately and live on 2026-09-29. This
 * is the first time they run as one.
 *
 *   the codec completes when driven by us      polls 1, pattern 1, 26 ms
 *   its output is a real lossless JPEG         ff d8 ff c3, 12 bit, 512x368
 *   the per-tile sizes come back through F_SIZE  141,805 bytes
 *   the camera's own frame buffer is writable  8 files, each its own content
 *   a shortened node is honoured               files exactly 1,396,736 bytes
 *   the engine needs OPEN before it can signal 0xC37CF878 was 0
 *   the engine needs START after the submit    [0x300D0000] |= 2
 *   the allocator gives 16 MiB in total        so no pool, hence write-back
 *
 * WHY THIS ONE IS SYNCHRONOUS
 *
 * Not because synchronous is right -- it is not, and the asynchronous encode is
 * already proven. It is because this run is about the JOINS. Waiting here means
 * one frame's work is one frame's call, so a failure has one place to be, and
 * the pipeline's own timing cannot be confused with a join that does not hold.
 * Turning it asynchronous afterwards changes the scheduling and touches none of
 * the joins: the encode moves to frame N and its result is written at frame
 * N+1, which is the operator's own late-frame model.
 *
 * G5 measured 13.65 ms per FHD frame. That is 33 percent of a 24p period and
 * 82 percent of a 59.94p one, so a stutter at 59.94p is an expected outcome of
 * THIS build and not a failure of the design.
 *
 * WHAT THE FILE ON THE CARD IS
 *
 * The frame's own header is kept -- the first 0x13400 bytes of the camera's
 * buffer are left exactly as they are -- and the compressed tiles are written
 * after it, with the node shortened to fit. The header still describes an
 * uncompressed frame, so the file does NOT decode. That is deliberate: the
 * trailing IFD that makes it decode is a separate, already-written tool, and
 * folding it in here would put two untested things in one run. What this run
 * proves is the chain; what it produces is a file of exactly the right length
 * whose payload starts with a lossless-JPEG SOI.
 *
 * NOTHING IS ALLOCATED HERE. Both blocks are the codec chain's, acquired and
 * recorded by the camera beforehand, and their bases are seeded into the state.
 */

#ifndef STATE
#define STATE 0xC072F700
#endif
#ifndef HOOK_ARMED
#define HOOK_ARMED 0xEB000000
#endif
#ifndef FRAMES
#define FRAMES 8
#endif

.equ HOOK_SITE,       0xC03A5490
.equ HOOK_ORIG,       0xEB0BD652
.equ REAL_FLUSH,      0xC069ADE0

/* GEOMETRY IS NOT COMPILED IN. The host works it out from one reference frame
 * of whatever is being recorded and seeds it, and this probe checks the frame
 * in front of it against that seed before touching anything. A frame of any
 * other shape goes to the card exactly as the camera made it.
 *
 * The five tags that describe the shape sit at FIXED offsets in the header --
 * measured 2026-09-30, identical for FHD and UHD, because the entries are
 * sorted by tag number and the ones that differ come after. The offsets are
 * used directly and the tag id at each is verified, so a layout that ever
 * changes fails safe instead of reading the wrong word. */
.equ T_WIDTH,         0x0016           /* entry for tag 256 */
.equ T_HEIGHT,        0x0022           /*           tag 257 */
.equ T_BITS,          0x002E           /*           tag 258 */
.equ T_STRIP_OFF,     0x0076           /*           tag 273 */
.equ T_STRIP_LEN,     0x00A6           /*           tag 279 */
.equ TAG_VALUE,       8                /* a 12-byte entry's value is its last
                                        * four bytes */
.equ TILE_W,          512              /* the engine's own limits: 32..512, a
                                        * multiple of 32 */
.equ TILE_H,          368              /* 2..512, even */

.equ UNCACHED,        0x40000000
/* The trailing IFD. A TIFF's root pointer can point anywhere, so the new IFD
 * goes AFTER the payload and the frame's own header stays byte for byte as the
 * camera wrote it -- which is what keeps the per-frame data alive: three
 * consecutive frames measured 2026-09-29 differ in six header bytes, and all
 * six are outside the 12-byte entries, reached through offsets into the region
 * we leave alone. The 58 entries themselves were byte-identical, which is why
 * one template serves the whole clip.
 *
 * tools/trailing_ifd.py builds it and hands back the two places the device has
 * to patch, because ifd_at depends on the payload size and tags 324 and 325
 * carry file offsets. The layout decodes to the identical 2,110,240 samples as
 * the leading-IFD one, max difference 0, verified 2026-09-26. */
.equ TEMPLATE_OFF,    0x310000         /* in block A, clear of source, tables,
                                        * records, mark and every guard */
.equ STOCK_ENTRIES,   58               /* what the camera's own root IFD has */
.equ TEMPLATE_MAX,    4096             /* copy_ifd counts down from the
                                        * seeded size; bound it */
.equ SRC_OFF,         0x000000         /* in block A */
.equ DST_OFF,         0x000000         /* in block B */
.if DST_OFF != 0
.error "DST_OFF is no longer zero: F_INIT's destination is passed as block B itself"
.endif
.equ TABLE_OFF,       0x3F1000         /* in block A, where the scratch chain
                                        * put it and where its guard watches */

.equ F_INIT,          0xC05A6890
.equ F_SIZE,          0xC05A6990
.equ ENGINE_BLOCK,    0xC302CDD4
/* The device, as its own driver uses it. Only six registers are touched in the
 * whole of C062F800..C0630700, and two of them carry the answer to "why was
 * this frame refused":
 *   0x300D0004  the interrupt status. The IRQ handler C0630430 reads it,
 *               writes it straight back to clear it, and hands the WORD it
 *               read to whatever callback the submit installed. Bit 2 is the
 *               error bit -- that is the pattern bit we see -- and every other
 *               bit in that word is thrown away by the firmware's own
 *               callback, one instruction after the only copy of it exists.
 *   0x300D00F8  where the engine says the stream ended. C062F6C0 clears its
 *               low ten bits and uses the rest as a byte offset into the
 *               destination, to patch a truncated EOI; the firmware reads it
 *               only on a SUCCESSFUL job, so a refused job's value has never
 *               been looked at. If a refusal really produces nothing, this
 *               reads 0.
 * Also learned there: on an engine error the firmware's SYNCHRONOUS path
 * (C062F90A -> C062FE80) CLOSES the device, while the asynchronous callback
 * (C062FCC2 -> C062FE90 -> C062FFC0) only pulses 0x300D03FC bit 0, a soft
 * reset. This probe takes the asynchronous path and carries on without a
 * re-open, which is unproven -- if it is wrong, every frame after the first
 * error fails, and S_ENG_ERRS against S_DONE says so. */
.equ DEV_ENDPOS,      0x300D00F8
.equ TILE_COUNT_VAR,  0xC302CDEC       /* F_SIZE's loop bound, via C062FA48 */
.equ ENG_OPEN,        0xC062FEE9       /* power, clock, install+enable IRQ 0x29 */
.equ ENG_SUBMIT,      0xC062FFF9       /* programs the registers only */
.equ ENG_START,       0xC062FEB1       /* [0x300D0000] |= 2, the GO bit */
.equ ENG_CALLBACK,    0xC062FCB1       /* the firmware's own completion hook */
.equ FLAG_CREATE,     0xC062F441       /* idempotent */
.equ TWAI_FLG,        0xC0016C08       /* (flgid, waiptn, wfmode, &ptn, tmout) */
.equ CLR_FLG,         0xC00169C8       /* (flgid, clrptn) */
.equ WAIT_PATTERN,    5                /* bit 0 done, bit 2 error */
.equ WAIT_MODE,       1                /* TWF_ORW */
.equ WAIT_TMOUT,      1                /* A POLL, not a wait. The encode is
                                        * about 14 ms and a frame is 33.4 ms
                                        * at 29.97p, so a job submitted last
                                        * frame is long finished by now. If it
                                        * is not, this frame goes through
                                        * untouched and the next one collects
                                        * it -- which is the dynamic part of
                                        * dynamic compression. The old value
                                        * was 100 and that was a WAIT: it held
                                        * the writer thread and on 2026-09-30
                                        * it aborted the clip. It used to say:
                                        * driver's own loop floors its timeout
                                        * at 100. At 1000, a device that had
                                        * been closed made EVERY frame pay a
                                        * full timeout, which on 2026-09-29
                                        * looked exactly like a hang. */
.equ MARK_MAGIC,      0x344C534D       /* "MSL4" */

.equ S_COUNT,         0x00
.equ S_WRITER,        0x04
.equ S_LR,            0x08
.equ S_RESTORED,      0x0C
.equ S_ERROR,         0x10
.equ S_STAGE,         0x14
.equ S_DONE,          0x18             /* frames actually compressed */
.equ S_BLOCK_A,       0x1C             /* seeded */
.equ S_BLOCK_B,       0x20             /* seeded */
.equ S_CAM_BUF,       0x24
.equ S_OPEN_RET,      0x28
.equ S_FLGID,         0x2C
.equ S_INIT_RET,      0x30
.equ S_SUBMIT_RET,    0x34
.equ S_START_RET,     0x38
.equ S_WAIT_RET,      0x3C
.equ S_PATTERN,       0x40
.equ S_SIZE_RET,      0x44
.equ S_SUM,           0x48
.equ S_NEW_LEN,       0x4C
.equ S_LEN_BACK,      0x50
.equ S_SHAPE_BAD,     0x54
.equ S_SKIPPED,       0x58             /* frames left stock after a failure */
.equ S_IFD_SIZE,      0x5C             /* seeded, from trailing_ifd.build */
.equ S_PATCH_OFF,     0x60             /* seeded: where TileOffsets' value is */
.equ S_PATCH_CNT,     0x64             /* seeded: where TileByteCounts' is */
.equ S_IFD_AT,        0x68
.equ S_FILE_BYTES,    0x6C
.equ S_ENTRIES,       0x70             /* what the frame's own IFD said */
.equ S_GIVE_UP,       0x74             /* set once; stops the whole run */
.equ S_INFLIGHT,      0x78             /* a job is running; its output is B's */
.equ S_NOT_READY,     0x7C             /* frames that found it still running */
.equ S_LATE,          0x80             /* frames written from an EARLIER frame */
/* Seeded geometry. Everything the shape decides, worked out on the host. */
.equ S_WIDTH,         0x84
.equ S_HEIGHT,        0x88
.equ S_BITS,          0x8C             /* bits per sample, as the tag reads */
.equ S_DEPTH,         0x90             /* the engine's code for those bits */
.equ S_PIXEL_OFF,     0x94             /* where the raster starts */
.equ S_RAW_BYTES,     0x98             /* the raster, rounded DOWN to 16 */
.equ S_DECLARED,      0x9C             /* round_up(raster, 1024) */
.equ S_DNG_LEN,       0xA0             /* the node length this shape produces */
.equ S_PAYLOAD_CAP,   0xA4             /* DNG_LEN - PIXEL_OFF */
.equ S_TILES,         0xA8             /* ceil(w/512) * ceil(h/368) */
.equ S_DST_CAP,       0xAC             /* block B's size, 1 KiB aligned */
.equ S_WRONG_SHAPE,   0xB0             /* frames of some other format */
.equ S_PROOF,         0xB4             /* "MSL4", seeded */
.equ S_TILE0,         0xB8             /* S_TILES words, up to TILE_MAX */

/* Instrumentation, placed past the tile array so that adding it moved no
 * offset the host and the probe already agreed on. Nothing here is on the
 * write-back path: every field is written either by our completion callback or
 * by the refusal path, and a frame that reaches the card is unaffected. */
.equ S_AFTER_TILES,   (S_TILE0 + TILE_MAX * 4)
.equ S_CB_ADDR,       (S_AFTER_TILES + 0x00)   /* seeded: our own callback */
.equ S_CB_HITS,       (S_AFTER_TILES + 0x04)   /* times it ran */
.equ S_GOOD_STATUS,   (S_AFTER_TILES + 0x08)   /* the last WITHOUT bit 2 set */
.equ S_ENG_STATUS,    (S_AFTER_TILES + 0x0C)   /* the FIRST one with bit 2 */
.equ S_ENG_ERRS,      (S_AFTER_TILES + 0x10)   /* how many frames were refused */
.equ S_ENG_AT,        (S_AFTER_TILES + 0x14)   /* S_COUNT at the first refusal */
.equ S_ENG_ENDPOS,    (S_AFTER_TILES + 0x18)   /* 0x300D00F8 at the first one */
.equ S_GOOD_ENDPOS,   (S_AFTER_TILES + 0x1C)   /* and on a frame that worked */
.equ S_STATE_END,     (S_AFTER_TILES + 0x20)

.equ ERR_SHAPE,       2
.equ ERR_OPEN,        3
.equ ERR_FLAG,        4
.equ ERR_INIT,        5
.equ ERR_SUBMIT,      6
.equ ERR_START,       7
.equ ERR_WAIT,        8
.equ ERR_ENGINE,      9                /* the error bit came back set */
.equ ERR_SIZE,        10
.equ ERR_TOO_BIG,     11               /* would not fit the camera's buffer */
.equ TILE_MAX,        160            /* the state block's own bound.
                                      * 6064x4042 needs 132 tiles of
                                      * 512x368; 160 leaves room without
                                      * the state leaving block A. */
.equ ERR_TEMPLATE,    12               /* the frame's IFD is not the one
                                        * the template was built from */

.macro LDA reg, value
    movw    \reg, #:lower16:\value
    movt    \reg, #:upper16:\value
.endm

/* Record an error, count the frame as skipped, and leave it entirely stock. */
.macro FAIL code
    mov     r0, #\code
    str     r0, [r4, #S_ERROR]
    b       skip_frame
.endm

/* The same, but STOP THE RUN. For every failure that means the codec itself is
 * not behaving: one such frame is reason enough to stop, and letting the rest
 * of the clip pay the same cost is how a stutter becomes a hang. On 2026-09-29
 * a 1000-tick wait on a device that had been closed did exactly that. */
.macro FAIL_STOP code
    mov     r0, #\code
    str     r0, [r4, #S_ERROR]
    mov     r0, #1
    str     r0, [r4, #S_GIVE_UP]
    b       skip_frame
.endm

.global probe_entry
probe_entry:
    push    {r0, r1, r2, r3, r4, r5, r6, r7, r8, r9, r10, r11, r12, lr}
    LDA     r4, STATE
    ldr     r3, [r4, #S_COUNT]
    add     r3, r3, #1
    str     r3, [r4, #S_COUNT]
    str     r0, [r4, #S_WRITER]
    str     lr, [r4, #S_LR]

    /* Restore first, always. A fault anywhere below then costs this frame and
     * not every frame after it. Re-arming happens at the very end. */
    LDA     r5, HOOK_SITE
    LDA     r6, HOOK_ORIG
    str     r6, [r5]
    dsb     sy
    isb
    ldr     r7, [r5]
    str     r7, [r4, #S_RESTORED]
    cmp     r7, r6
    bne     call_real

    ldr     r0, [r4, #S_PROOF]
    LDA     r1, MARK_MAGIC
    cmp     r0, r1
    bne     call_real

    ldr     r0, [r4, #S_DONE]
    LDA     r1, FRAMES
    cmp     r0, r1
    bhs     call_real                  /* bounded, and the bound is the point */


    /* ---- the writer's single node, checked as G3 checked it ------------ */
    ldr     r0, [r4, #S_WRITER]
    cmp     r0, #0
    beq     shape_bad
    tst     r0, #3
    bne     shape_bad
    ldr     r1, [r0, #0x90]            /* node count */
    cmp     r1, #1
    bne     shape_bad
    ldr     r9, [r0, #0x8C]            /* r9 = head, the node */
    add     r3, r0, #0x0C
    cmp     r9, r3
    bne     shape_bad
    ldr     r5, [r9, #0x04]            /* r5 = the CAMERA'S OWN buffer */
    ldr     r3, [r9, #0x08]
    cmp     r5, #0
    beq     shape_bad
    tst     r5, #3
    bne     shape_bad
    ldr     r0, [r4, #S_DNG_LEN]
    cmp     r3, r0
    bne     shape_bad                  /* only the exact stock length */
    str     r5, [r4, #S_CAM_BUF]
    /* r11 holds the camera's buffer through its uncached alias, once, for the
     * whole frame. It used to be recomputed at every use -- six times -- and
     * r11 is callee-saved, so every firmware call below preserves it. */
    add     r11, r5, #UNCACHED

    /* ---- is this frame the shape we were seeded for? ------------------
     * The five tags that describe it sit at fixed offsets, measured on both
     * FHD and UHD. The first tag's ID is checked as a canary; the others are
     * covered by their VALUES, because a shifted layout cannot also happen to
     * hold the seeded width, height, depth and strip offset. Anything that does not match goes to the card untouched -- which
     * is what lets the operator record any format without this probe having
     * to be told in advance which one it is. */
    ldrh    r0, [r11, #T_WIDTH]
    cmp     r0, #256
    bne     wrong_shape
    ldr     r0, [r11, #(T_WIDTH + TAG_VALUE)]
    ldr     r1, [r4, #S_WIDTH]
    cmp     r0, r1
    bne     wrong_shape

    ldr     r0, [r11, #(T_HEIGHT + TAG_VALUE)]
    ldr     r1, [r4, #S_HEIGHT]
    cmp     r0, r1
    bne     wrong_shape

    ldr     r0, [r11, #(T_BITS + TAG_VALUE)]
    ldr     r1, [r4, #S_BITS]
    cmp     r0, r1
    bne     wrong_shape

    ldr     r0, [r11, #(T_STRIP_OFF + TAG_VALUE)]
    ldr     r1, [r4, #S_PIXEL_OFF]
    cmp     r0, r1
    bne     wrong_shape

    ldr     r10, [r4, #S_BLOCK_A]
    ldr     r8, [r4, #S_BLOCK_B]
    cmp     r10, #0
    beq     shape_bad
    cmp     r8, #0
    beq     shape_bad

    /* The template size is seeded, and copy_ifd counts DOWN by sixteen from
     * it. An unseeded zero would underflow that counter and copy four
     * gigabytes out of block A across the camera's memory. Bound it at both
     * ends before anything can use it. */
    ldr     r0, [r4, #S_IFD_SIZE]
    cmp     r0, #0
    beq     shape_bad
    mov     r1, #TEMPLATE_MAX
    cmp     r0, r1
    bhi     shape_bad

    /* Our completion callback's address, seeded by the host because this code
     * is assembled at zero and cannot know where it was placed. A zero here
     * installs a NULL callback: the IRQ handler calls nothing, the event flag
     * is never set, and every frame is passed through as not-ready. Harmless
     * to the card and indistinguishable from a codec that never finishes --
     * which is the exact confusion that cost 2026-09-29. Refuse it. Its
     * ALIGNMENT is asserted by the host, which is where the address is
     * computed from the symbol table. */
    ldr     r0, [r4, #S_CB_ADDR]
    cmp     r0, #0
    beq     shape_bad

    mov     r0, #2
    str     r0, [r4, #S_STAGE]

    /* ---- once per run: open the device and make the flag --------------
     * The open powers the codec, clocks it and ENABLES IRQ 0x29, whose handler
     * C0630431 is the only thing that calls the completion callback. Without
     * it the hardware finishes and nothing signals; 0xC37CF878 read 0 live on
     * 2026-09-29, so it had never happened this boot. It is idempotent. */
    ldr     r0, [r4, #S_DONE]
    cmp     r0, #0
    bne     have_device
    LDA     r12, ENG_OPEN
    blx     r12
    str     r0, [r4, #S_OPEN_RET]
    cmp     r0, #0
    bne     fail_open
    LDA     r12, FLAG_CREATE
    blx     r12
    str     r0, [r4, #S_FLGID]
    cmp     r0, #0
    blt     fail_flag
have_device:
    /* ---- finish the PREVIOUS frame's encode ---------------------------
     * Synchronous and asynchronous are the same flow: a job submitted at the
     * end of one call is collected at the start of the next, so the payload a
     * frame carries is always an EARLIER frame's picture. The only knob is the
     * poll timeout. Short, and a frame that finds the codec still working is
     * passed through untouched -- which is the whole point: the writer thread
     * never waits 13.65 ms again. On 2026-09-30 that wait aborted the clip. */
    ldr     r0, [r4, #S_INFLIGHT]
    cmp     r0, #0
    beq     do_submit                  /* nothing to collect: start one */

    /* ---- wait for it ---------------------------------------------------
     * Synchronous, deliberately: see the header. A timeout is recorded and
     * costs this frame only. */
    sub     sp, sp, #0x10
    mov     r0, #WAIT_TMOUT
    str     r0, [sp]                   /* the fifth argument is stacked */
    add     r3, sp, #8                 /* &pattern */
    mov     r0, #0
    str     r0, [r3]
    ldr     r0, [r4, #S_FLGID]
    mov     r1, #WAIT_PATTERN
    mov     r2, #WAIT_MODE
    LDA     r12, TWAI_FLG
    blx     r12
    ldr     r1, [sp, #8]
    add     sp, sp, #0x10
    str     r0, [r4, #S_WAIT_RET]
    str     r1, [r4, #S_PATTERN]
    cmp     r0, #0
    bne     not_ready                  /* still encoding: leave this frame */
    tst     r1, #4                     /* bit 2 is the engine's error bit */
    bne     fail_engine

    ldr     r0, [r4, #S_FLGID]
    mov     r1, #0
    LDA     r12, CLR_FLG
    blx     r12
    mov     r0, #5
    str     r0, [r4, #S_STAGE]

    /* ---- the per-tile sizes, and their sum ---------------------------- */
    add     r0, r4, #S_TILE0
    mov     r1, #1                     /* byte-swap into our array */
    LDA     r12, F_SIZE
    blx     r12
    str     r0, [r4, #S_SIZE_RET]
    cmp     r0, #1                     /* F_SIZE returns 1 on SUCCESS */
    bne     fail_size
    add     r3, r4, #S_TILE0
    ldr     r2, [r4, #S_TILES]
    mov     r1, #0
sum_tiles:
    ldr     r0, [r3], #4
    add     r1, r1, r0
    subs    r2, r2, #1
    bne     sum_tiles
    str     r1, [r4, #S_SUM]
    cmp     r1, #0
    beq     fail_size

    /* The payload AND the whole trailer must fit after the header, or the
     * write-back would run past the buffer the camera gave us. On 2026-09-29
     * the length was computed from the payload alone and came out 421 bytes
     * SHORT of what the trailer needs, which would have cut the new IFD off
     * the end of every file. */
    ldr     r0, [r4, #S_IFD_SIZE]
    ldr     r2, [r4, #S_TILES]
    add     r0, r0, r2, lsl #3
    add     r0, r0, #18                /* the two alignment round-ups: up to
                                        * fifteen before the IFD and three
                                        * before the tables */
    add     r0, r0, r1                 /* payload + ifd + both tables */
    ldr     r2, [r4, #S_PAYLOAD_CAP]
    sub     r2, r2, #16
    cmp     r0, r2
    bhi     fail_too_big
    mov     r0, #6
    str     r0, [r4, #S_STAGE]


    /* ---- write the compressed tiles back, after the frame's own header -
     * Sixteen bytes at a time, so up to fifteen bytes of block B past the
     * payload come with it. They land inside the camera's buffer, under the
     * cap checked above, and the node length below covers them. */
    add     r6, r8, #UNCACHED          /* source: block B, the output */
    ldr     r0, [r4, #S_PIXEL_OFF]
    add     r7, r11, r0                /* destination: the camera's raster */
    /* The length from the STATE, not from r1. The sum loop used to run just
     * above this; the asynchronous reorder moved it to the top of the frame
     * and put copy_in in between, whose ldm writes r1 with pixel data. On
     * 2026-09-30 that copied block B into the camera's buffer with a random
     * length and took the camera down hard -- a replug could not recover it.
     * Anything the reorder separated has to be re-read, not remembered. */
    ldr     r1, [r4, #S_SUM]
    add     r12, r1, #15
    bic     r12, r12, #15
copy_back:
    ldm     r6!, {r0, r1, r2, r3}
    stm     r7!, {r0, r1, r2, r3}
    subs    r12, r12, #16
    bne     copy_back
    dsb     sy


    /* ---- the trailing IFD --------------------------------------------
     * r5 is the camera's buffer, r10 block A, r4 the state.
     *
     * ALIGNMENT. ifd_at is 0x13400 + the payload, and the payload is whatever
     * the codec produced -- 1,343,147 on 2026-09-29, so ifd_at ended ...3 and
     * every stm and str below went to an unaligned address. ldm and stm
     * REQUIRE word alignment whatever SCTLR.A says, so that is a Data Abort on
     * the writer's own thread, and it wedged the camera three times. The host
     * tool never sees it: Python has no alignment.
     *
     * Rounding UP costs one to fifteen bytes of gap between the last tile and
     * the IFD. Nothing reads that gap: TileOffsets and TileByteCounts describe
     * each tile exactly, and TIFF wants its IFDs word-aligned anyway. */
    ldr     r1, [r4, #S_SUM]
    ldr     r0, [r4, #S_PIXEL_OFF]
    add     r1, r1, r0
    add     r1, r1, #15
    bic     r1, r1, #15                /* r1 = ifd_at, 16-byte aligned */
    str     r1, [r4, #S_IFD_AT]

    add     r6, r10, #UNCACHED
    mov     r0, #TEMPLATE_OFF
    add     r6, r6, r0                 /* r6 = the template in block A */
    add     r7, r11, r1                /* r7 = where it goes */
    ldr     r12, [r4, #S_IFD_SIZE]
    add     r12, r12, #15
    bic     r12, r12, #15              /* the template area carries the slack */
copy_ifd:
    ldm     r6!, {r0, r2, r3, r8}
    stm     r7!, {r0, r2, r3, r8}
    subs    r12, r12, #16
    bne     copy_ifd

    /* Patch the two value fields that carry file offsets. */
    add     r7, r11, r1                /* r7 = the copy, again */
    ldr     r0, [r4, #S_IFD_SIZE]
    add     r2, r1, r0
    add     r2, r2, #3
    bic     r2, r2, #3                 /* r2 = offset_table, word aligned:
                                        * ifd_size is 714 and 714 is not a
                                        * multiple of four */
    ldr     r3, [r4, #S_TILES]
    add     r3, r2, r3, lsl #2         /* r3 = count_table */
    ldr     r0, [r4, #S_PATCH_OFF]
    str     r2, [r7, r0]
    ldr     r0, [r4, #S_PATCH_CNT]
    str     r3, [r7, r0]

    /* The two tables. Offsets run from the raster's own start. */
    add     r6, r11, r2                /* r6 = offset table */
    add     r7, r11, r3                /* r7 = count table */
    add     r12, r4, #S_TILE0
    ldr     r0, [r4, #S_PIXEL_OFF]
    ldr     r8, [r4, #S_TILES]
write_tables:
    ldr     r2, [r12], #4
    str     r0, [r6], #4               /* this tile's offset */
    str     r2, [r7], #4               /* and its byte count */
    add     r0, r0, r2
    subs    r8, r8, #1
    bne     write_tables

    /* The root pointer: the only byte in the frame's own header that moves. */
    str     r1, [r11, #4]
    dsb     sy

    /* ---- shorten the node --------------------------------------------
     * The file ends after the second table, NOT after the payload. Rounded up
     * to 1 KiB: every length this project has given the writer has been 1 KiB
     * aligned, and rounding UP can only include bytes we wrote. */
    /* From the table the device actually wrote, not by re-deriving it: r3 is
     * count_table and it already carries both round-ups. */
    ldr     r0, [r4, #S_TILES]
    add     r1, r3, r0, lsl #2         /* r1 = file_bytes */
set_length:
    str     r1, [r4, #S_FILE_BYTES]
    LDA     r0, 0x3FF
    add     r1, r1, r0
    bic     r1, r1, r0
    str     r1, [r4, #S_NEW_LEN]
    str     r1, [r9, #0x08]
    dsb     sy
    ldr     r0, [r9, #0x08]
    str     r0, [r4, #S_LEN_BACK]
    mov     r0, #0
    str     r0, [r4, #S_INFLIGHT]      /* B has been consumed */
    ldr     r0, [r4, #S_LATE]
    add     r0, r0, #1
    str     r0, [r4, #S_LATE]

    /* This frame is finished. Its own pixels were overwritten by the payload
     * just written, so there is nothing here to encode: the NEXT frame starts
     * the next job. Collect and submit therefore alternate, and that is what
     * lets the engine read the camera's own buffer instead of a copy of it --
     * the only frame ever overwritten is the one being written back into. */
    ldr     r0, [r4, #S_DONE]
    add     r0, r0, #1
    str     r0, [r4, #S_DONE]
    mov     r0, #7
    str     r0, [r4, #S_STAGE]
    b       call_real

do_submit:
    ldr     r8, [r4, #S_BLOCK_B]       /* copy_ifd used r8 as a scratch */

    /* Do NOT start a job on the last frame we will handle. Every run before
     * this one ended with a submit nobody would ever collect: the flag stayed
     * set, the codec stayed open with its power on, and block B held a payload
     * no frame would claim. The clip that followed came out as zero-length
     * files. One frame of compression is a small price for ending clean. */
    ldr     r0, [r4, #S_DONE]
    add     r0, r0, #1
    LDA     r1, FRAMES
    cmp     r0, r1
    bhs     no_more_submits
    /* ---- F_INIT with our geometry ------------------------------------- */
    sub     sp, sp, #0x40
    ldr     r0, [r4, #S_WIDTH]
    str     r0, [sp, #0x00]
    ldr     r0, [r4, #S_HEIGHT]
    str     r0, [sp, #0x04]
    ldr     r0, [r4, #S_DEPTH]
    str     r0, [sp, #0x08]
    mov     r0, #TILE_W
    str     r0, [sp, #0x0C]
    mov     r0, #TILE_H
    str     r0, [sp, #0x10]
    /* The source is THIS frame's own buffer. It survives until the
     * aggregation batch is flushed -- measured 2026-09-29, up to 21
     * frames -- so an encode reading it across one frame period is
     * safe, and no copy of a 42 MB raster is needed anywhere. */
    ldr     r0, [r4, #S_PIXEL_OFF]
    add     r0, r5, r0
    str     r0, [sp, #0x14]
    str     r8, [sp, #0x18]            /* DST_OFF is zero: see the .if */
    LDA     r0, TABLE_OFF
    add     r0, r10, r0
    str     r0, [sp, #0x1C]
    mov     r0, #0
    str     r0, [sp, #0x20]
    mov     r0, sp
    LDA     r12, F_INIT
    blx     r12
    add     sp, sp, #0x40
    str     r0, [r4, #S_INIT_RET]
    cmp     r0, #1                     /* F_INIT returns 1 on SUCCESS */
    bne     fail_init

    /* F_SIZE's loop bound is the driver's variable, and the driver is exactly
     * what this does not run. Publish it or C062FA48 loops zero times. */
    LDA     r1, TILE_COUNT_VAR
    ldr     r0, [r4, #S_TILES]
    str     r0, [r1]

    /* ---- build the 0x30-byte request from the engine block ------------
     * +0x14 source, +0x18 its length, +0x1C destination, +0x20 its length,
     * +0x24 the size table, all five 1 KiB aligned or C062FFF8 refuses. */
    sub     sp, sp, #0x30
    ldr     r0, [r4, #S_WIDTH]
    str     r0, [sp, #0x00]
    ldr     r0, [r4, #S_HEIGHT]
    str     r0, [sp, #0x04]
    ldr     r0, [r4, #S_DEPTH]
    str     r0, [sp, #0x08]
    mov     r0, #TILE_W
    str     r0, [sp, #0x0C]
    mov     r0, #TILE_H
    str     r0, [sp, #0x10]
    LDA     r6, ENGINE_BLOCK
    ldr     r0, [r6, #0x00]            /* translated source */
    str     r0, [sp, #0x14]
    ldr     r0, [r4, #S_DECLARED]
    str     r0, [sp, #0x18]
    ldr     r0, [r6, #0x08]            /* translated destination */
    str     r0, [sp, #0x1C]
    /* The capacity we OWN, not the one F_INIT guessed. The engine refuses a
     * frame whose output exceeds the capacity it is TOLD -- measured
     * 2026-09-28 -- and F_INIT's own figure is 53.6 percent of the raster,
     * about 6.4 bits a sample. Lossless compression cannot promise that on a
     * detailed frame, and on 2026-09-30 a detailed one was refused. Block B
     * holds 132 percent of the raster, so telling the engine its real size
     * turns nearly every refusal into a compressed frame. */
    ldr     r0, [r4, #S_DST_CAP]
    str     r0, [sp, #0x20]
    ldr     r0, [r6, #0x10]            /* the size table, untranslated */
    str     r0, [sp, #0x24]
    ldr     r0, [r6, #0x14]
    str     r0, [sp, #0x28]
    /* Our own, which keeps the status word and then tails into the
     * firmware's -- see our_callback at the end of this file. */
    ldr     r0, [r4, #S_CB_ADDR]
    str     r0, [sp, #0x2C]
    dsb     sy

    ldr     r0, [r4, #S_FLGID]
    mov     r1, #0
    LDA     r12, CLR_FLG
    blx     r12

    mov     r0, sp
    LDA     r12, ENG_SUBMIT
    blx     r12
    str     r0, [r4, #S_SUBMIT_RET]
    cmp     r0, #0
    bne     fail_submit_sp

    /* C062FFF8 only PROGRAMS. The GO bit is [0x300D0000] |= 2 and only the
     * mode-1 starter sets it. */
    LDA     r12, ENG_START
    blx     r12
    str     r0, [r4, #S_START_RET]
    add     sp, sp, #0x30
    cmp     r0, #0
    bne     fail_start
    mov     r0, #6
    str     r0, [r4, #S_STAGE]

    mov     r0, #1
    str     r0, [r4, #S_INFLIGHT]      /* B is the engine's until collected */

no_more_submits:

    ldr     r0, [r4, #S_DONE]
    add     r0, r0, #1
    str     r0, [r4, #S_DONE]
    mov     r0, #7
    str     r0, [r4, #S_STAGE]
    b       call_real

fail_submit_sp:
    add     sp, sp, #0x30
    FAIL_STOP ERR_SUBMIT
fail_open:
    FAIL_STOP ERR_OPEN
fail_flag:
    FAIL_STOP ERR_FLAG
fail_init:
    FAIL_STOP ERR_INIT
fail_start:
    FAIL_STOP ERR_START
fail_engine:
    /* The engine refused this frame, almost always because its output would
     * not fit the capacity F_INIT allows -- about 54 percent of the raster,
     * measured 2026-09-28. That is the engine doing its job on a frame that
     * did not compress enough, not a fault: block B is free again and the run
     * carries on. Treating it as fatal stopped a whole run on 2026-09-30 over
     * one detailed frame. */
    /* Count it, and note WHICH frame it was -- the first one only, so a
     * later refusal cannot overwrite the one worth reading. The status word
     * and the engine's end position are kept by our callback, which runs at
     * completion, where they are still valid. */
    ldr     r0, [r4, #S_ENG_ERRS]
    cmp     r0, #0
    ldreq   r1, [r4, #S_COUNT]
    streq   r1, [r4, #S_ENG_AT]
    add     r0, r0, #1
    str     r0, [r4, #S_ENG_ERRS]
    mov     r0, #0
    str     r0, [r4, #S_INFLIGHT]
    FAIL    ERR_ENGINE
fail_size:
    FAIL_STOP ERR_SIZE
fail_too_big:
    /* Drop this payload and carry on: block B is consumed either way, so the
     * next submit is free to reuse it. Stopping the run over one frame that
     * compressed badly would be the wrong trade. */
    mov     r0, #0
    str     r0, [r4, #S_INFLIGHT]
    FAIL    ERR_TOO_BIG
fail_template:
    FAIL    ERR_TEMPLATE
wrong_shape:
    /* Not the format we were seeded for. Touch nothing. */
    ldr     r0, [r4, #S_WRONG_SHAPE]
    add     r0, r0, #1
    str     r0, [r4, #S_WRONG_SHAPE]
    b       call_real

not_ready:
    /* The codec has not finished the previous frame. Touch nothing: the job
     * keeps running, block A is still its source and block B is still its
     * destination, and the next frame collects it. */
    ldr     r0, [r4, #S_NOT_READY]
    add     r0, r0, #1
    str     r0, [r4, #S_NOT_READY]
    b       call_real


shape_bad:
    ldr     r0, [r4, #S_SHAPE_BAD]
    add     r0, r0, #1
    str     r0, [r4, #S_SHAPE_BAD]
    mov     r0, #ERR_SHAPE
    str     r0, [r4, #S_ERROR]
    b       call_real

skip_frame:
    /* The frame is left completely stock: the raster was copied OUT, which
     * reads and changes nothing, and neither the buffer nor the node has been
     * touched on any path that reaches here. */
    ldr     r0, [r4, #S_SKIPPED]
    add     r0, r0, #1
    str     r0, [r4, #S_SKIPPED]

call_real:
    /* Re-arm last, so every risky instruction above ran with the site
     * unhooked. Two ceilings: compressed frames, and hook entries. */
    /* Both ceilings through a register: an 8-bit-rotated immediate cannot
     * hold 6000, so a raised bound written as one turns into an assembler
     * error rather than a silently wrong comparison. LDA has no such limit.
     * The second ceiling is three times the first, taken from the register
     * the first just loaded -- so raising FRAMES moves both, and the two
     * cannot drift apart. */
    ldr     r0, [r4, #S_GIVE_UP]
    cmp     r0, #0
    bne     no_rearm                   /* the codec misbehaved once; stop */
    ldr     r0, [r4, #S_DONE]
    LDA     r1, FRAMES
    cmp     r0, r1
    bhs     no_rearm
    ldr     r0, [r4, #S_COUNT]
    add     r1, r1, r1, lsl #1         /* three times the bound, still in r1 */
    cmp     r0, r1
    bhs     no_rearm
    LDA     r0, HOOK_SITE
    LDA     r1, HOOK_ARMED
    str     r1, [r0]
    dsb     sy
    isb
no_rearm:
    pop     {r0, r1, r2, r3, r4, r5, r6, r7, r8, r9, r10, r11, r12, lr}
    ldr     pc, [pc, #-4]
    .word   REAL_FLUSH


/* ---------------------------------------------------------------------------
 * The completion callback, installed at request+0x2C instead of the firmware's
 * own C062FCB1.
 *
 * WHY THIS EXISTS. The IRQ handler C0630430 reads the device's status word
 * from 0x300D0004, writes it straight back to clear it, and calls the
 * installed callback with that word in r0. It is the only copy: the register
 * is already clear by then, and the firmware's callback looks at bit 2 and
 * discards the rest. Bit 2 is all we have ever seen -- "the engine refused
 * this frame" with no reason attached. This keeps the whole word.
 *
 * It does not replace the firmware's callback, it precedes it: the last thing
 * it does is jump there, unchanged and with r0 unchanged, so the event flag is
 * still set by the code that has always set it, with the same pattern. If this
 * were to stop working, the symptom would be that no frame ever completes --
 * loud and harmless, not a corrupted file.
 *
 * IT RUNS IN IRQ CONTEXT. It touches the state block and nothing else, calls
 * nothing, and saves everything the AAPCS makes it save. This is ARM code
 * reached by a blx from Thumb, which is why the host must seed an address with
 * bit 0 clear -- the probe checks that before installing it.
 */
.global our_callback
our_callback:
    push    {r0, r1, r4, lr}
    LDA     r4, STATE
    ldr     r1, [r4, #S_CB_HITS]
    add     r1, r1, #1
    str     r1, [r4, #S_CB_HITS]
    /* 0x300D00F8 is where the engine says the stream ended, and the
     * firmware reads it only on a job that SUCCEEDED (C062F91C). Here it is
     * read on both, at completion -- the one moment it is certainly this
     * job's. */
    LDA     r1, DEV_ENDPOS
    ldr     r1, [r1]
    tst     r0, #4                     /* the error bit, as C062FCBC reads it */
    bne     cb_error
    str     r0, [r4, #S_GOOD_STATUS]
    str     r1, [r4, #S_GOOD_ENDPOS]
    b       cb_done
cb_error:
    /* The first refusal only: a later one must not overwrite it. A status
     * word of zero cannot reach here, because bit 2 is set. */
    ldr     r12, [r4, #S_ENG_STATUS]
    cmp     r12, #0
    streq   r0, [r4, #S_ENG_STATUS]
    streq   r1, [r4, #S_ENG_ENDPOS]
cb_done:
    pop     {r0, r1, r4, lr}
    LDA     r12, ENG_CALLBACK
    bx      r12                        /* a tail call: r0 and lr are theirs */
