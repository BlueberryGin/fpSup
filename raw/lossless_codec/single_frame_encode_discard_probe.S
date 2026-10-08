.syntax unified
.arm
.text

/*
 * One-shot FHD codec probe at the verified live call C0722AFC.
 *
 * PHASE=0 is the required context preflight: allocate one aligned 4 MiB block,
 * prove the non-overlapping codec layout, and acquire/release power 3 + clock 5.
 * PHASE=1 performs one encode into that allocation and discards the result.
 * The live installer refuses PHASE=1 until the PHASE=0 context result has been
 * reviewed explicitly.
 *
 * PHASE=2 is the live encode.  It allocates NOTHING: the host seeds the
 * allocator, the handle retained by exact_writer_scratch_preflight, and both
 * proof magics, and this routine re-derives and re-validates the whole layout
 * from that handle before use.  The three guard words the scratch phase left
 * behind must still be intact; a trampled guard means the retained block is not
 * what we think it is, and the block is then never freed or written.
 *
 * PHASE=3 is the sustained run: the same consume path, repeated over FRAMES
 * consecutive writer calls, appending one 16-byte record per frame to a log at
 * LOG.  It re-arms the hook only at the very end of each frame, so the risky
 * window is always unhooked exactly as in the one-shot design: a fault costs
 * that frame's writer call and stops, it never loops.  A re-arm the fetch path
 * does not see costs a frame, not correctness -- `frames_done` shows it.  No
 * cache maintenance is called: 0xC000E91C is a set/way D-cache clean and would
 * perturb the very timing being measured.
 *
 * Release policy for PHASE=2 and the final frame of PHASE=3, deliberately
 * asymmetric:
 *   - adopted and the encode never started      -> free
 *   - adopted and the encode returned success   -> free
 *   - the encode was entered and did not succeed-> RETAIN.  The codec may still
 *     own the buffer as a DMA target; freeing it under a live engine is worse
 *     than leaking 4 MiB until a power cycle.
 * The engine is a single global with no ownership arbitration (see
 * research/imaging-hw/notes/HW_LOSSLESS_JPEG_CODEC.md); a wrong free here is
 * how the still path was killed on 2026-08-30.
 *
 * Neither phase stores through the DNG buffer or the segment pointer.  The
 * original buffer is source-only, seg[1] remains 0x318200, and the real
 * FUN_c069ac88 is tail-called with its original r0-r3/r12/LR.
 *
 * Codec layout inside the single allocator-owned block:
 *
 *   handle + 000000 .. 3DFFFF   compressed output/work, cap 0x3E0000
 *   handle + 3E0000              overrun guard
 *   handle + 3E1000 .. 3E102F   final 12-word size table (init[7])
 *   handle + 3E1030              table guard
 *   handle + 3E1400 .. 3E142F   codec temporary size table
 *   handle + 3E1430              temporary-table guard
 *
 * The arena covers the worst-case output bound for 512x368, so the tables sit
 * above anything the codec can legitimately produce. They are not protected by
 * the post-hoc size sum: that sum is read FROM the table an overrun would have
 * destroyed.
 *
 * F_GET is asked for 0x400 alignment directly.  The low 0x4xxxxxxx handle is
 * handed to the codec; FUN_c001b668's cached 0xCxxxxxxx alias is never used.
 */

#ifndef PHASE
#define PHASE 0
#endif

/* PHASE=2 routes the source-integrity exit through its release step; the
 * reviewed PHASE=0/1 images keep their original control flow and size. */
#if PHASE == 4 || PHASE == 5 || PHASE == 7 || PHASE == 8 || PHASE == 9 \
    || PHASE == 11
#define SOURCE_EXIT complete          /* one-shot, and the flush reads it */
#elif PHASE >= 2
#define SOURCE_EXIT after_source
#else
#define SOURCE_EXIT complete
#endif

#ifndef FRAMES
#define FRAMES 8
#endif

/* KEEP_BLOCK retains the arena after a successful encode so the host can read
 * the compressed payload back out. The normal path frees it, because a block
 * nobody reads is a block nobody should hold; retention is opt-in and the
 * caller owes an explicit release. */
#ifndef KEEP_BLOCK
#define KEEP_BLOCK 0
#endif

.equ HOOK_SITE,       0xC0722AFC
.equ HOOK_ORIG,       0xEBFDE061
.equ REAL_WRITE,      0xC069AC88
/* Claimed at run time; the defaults are the historical fixed addresses, which
 * collide with callfn/putfile/dump parameter blocks. See probe_placement.py. */
#ifndef STATE
#define STATE 0xC072F700
#endif
.equ DONE_MAGIC,      0x454E4F44
.equ GUARD_MAGIC,     0xA55AA55A

.equ WIDTH,           1936
.equ HEIGHT,          1090
.equ DEPTH,           0
/* Overridable so a build can prove the height actually reaches the codec.
 * Until 2026-09-23 every tile field was written from the width, which
 * 512x512 hid completely; a 512x368 build still emitted 512x512. */
#ifndef TILE_W
#define TILE_W 512
#endif
#ifndef TILE_H
#define TILE_H 368
#endif
.equ TILE_COUNT,      12
.equ DNG_LEN,         0x318200
.equ PIXEL_OFF,       0x13400
.equ RAW_BYTES,       0x304CB0
.equ ALLOC_SIZE,      0x400000
/* Output arena sized from the worst-case bound, not from the raster.
 * projects/lossless-sup/tools/output_bound.py, 14.0 bits/sample over padded
 * samples: 512x368 bounds at 3,957,832 B. The arena is 4,063,232 B, a 2.7%
 * margin, because that bound models optimal Huffman and the camera's actual
 * table is not known to match it.
 * The old 0x305000 sat 848 B above the packed raster, so a noisy frame
 * overran the guard and destroyed the size table BEFORE the post-hoc sum
 * could notice (ROADMAP P0-3). The tables now live above the bound, so an
 * overrun cannot reach them.
 * 512x512 bounds at 5,506,107 B and has NO safe layout inside 4 MiB; the host
 * builder refuses that geometry rather than leaving it to a runtime check. */
/* ONE layout for every phase. It was briefly split per phase so the compressed
 * write could keep its margin, and that is exactly the guards-written-here,
 * checked-there failure the scratch/encode agreement test exists to catch.
 * These values satisfy both uses:
 *   discard  (output at +0)        capacity 4,128,768   margin 4.32%
 *   compress (output at +0x13800)  capacity 4,048,896   margin 2.30% */
.equ WORK_CAP,        0x3F0000
.equ TABLE_OFF,       0x3F1000
.equ TABLE_TMP_OFF,   0x3F1400

.equ F_ALLOC,         0xC001CF78
.equ F_GET,           0xC001D038
.equ F_INIT,          0xC05A6890
.equ F_ENC,           0xC05A6920
.equ F_SIZE,          0xC05A6990
.equ F_CLOCK,         0xC002B6E0       /* free-running 1 MHz microsecond tick */
.equ F_FREE,          0xC001D2B8
/* The firmware's own destination-length variable, written by FUN_c062f5a8 on
 * the native path. Read only, and only to record what it held. */
.equ DECL_VAR,        0xC302CDE0
.equ F_PWR,           0xC0708C61       /* Thumb */
.equ F_CLK,           0xC07095A9       /* Thumb */

/* First nine words retain exact_dng_writer_probe's transaction contract. */
.equ S_COUNT,         0x00
.equ S_R0,            0x04
.equ S_R1,            0x08
.equ S_R2,            0x0C
.equ S_LR,            0x10
.equ S_BUF,           0x14
.equ S_LEN,           0x18
.equ S_DONE,          0x1C
.equ S_RESTORED,      0x20
.equ S_PHASE,         0x24
.equ S_STAGE,         0x28
.equ S_ERROR,         0x2C
.equ S_ALLOCATOR,     0x30
.equ S_HANDLE,        0x34
.equ S_ALLOC_END,     0x38
.equ S_WORK_BASE,     0x3C
.equ S_WORK_END,      0x40
.equ S_WORK_GUARD,    0x44
.equ S_TABLE_BASE,    0x48
.equ S_TABLE_TMP,     0x4C
.equ S_T0,            0x50
.equ S_T1,            0x54
.equ S_INIT_RET,      0x58
.equ S_ENC_RET,       0x5C
.equ S_SIZE_RET,      0x60
.equ S_COMP_SIZE,     0x64
.equ S_PWR_ON,        0x68
.equ S_CLK_ON,        0x6C
.equ S_CLK_OFF,       0x70
.equ S_PWR_OFF,       0x74
.equ S_HDR_BEFORE,    0x78
.equ S_HDR_AFTER,     0x7C
.equ S_PIX0_BEFORE,   0x80
.equ S_PIX0_AFTER,    0x84
.equ S_PIXN_BEFORE,   0x88
.equ S_PIXN_AFTER,    0x8C
.equ S_LEN_AFTER,     0x90
.equ S_GUARD_WORK,    0x94
.equ S_GUARD_TABLE,   0x98
.equ S_GUARD_TMP,     0x9C
.equ S_TILE_COUNT,    0xA0
.equ S_TILE0,         0xA4             /* 12 words, through +0xD0 */
.equ S_POWER_PROOF,   0xD4             /* "PPWR", seeded by the host */
.equ S_SCRATCH_PROOF, 0xD8             /* "ALOC", seeded by the host */
/* PHASE=7's three result words do NOT live in the state block. That block is
 * full - 64 of 64 words - and the three that looked free (0xDC, 0xE0, 0xE4)
 * are the scratch phase's guard records, which scratch_preflight_passed READS
 * as a gate. Overlaying them would have made every copy-back invalidate the
 * gate that admits the next one. They go in the retained block at RESULT_OFF
 * instead, which costs no cave space and no state word. */
.equ S_FREE_ERROR,    0xEC
.equ S_FREE_DONE,     0xF0
.equ S_ENC_STARTED,   0xF4
.equ S_ADOPTED,       0xF8
.equ S_FRAMES_DONE,   0xFC

.equ POWER_MAGIC,     0x52575050       /* "PPWR" */
.equ SCRATCH_MAGIC,   0x434F4C41       /* "ALOC" */
.equ FREE_MAGIC,      0x45455246       /* "FREE" */

/* ---- allocation lifecycle records -------------------------------------
 * Two records, sixteen words each, in BLOCK A. Not in the state block: that
 * is full, and the words that look free there are a gate's input. Not host
 * bookkeeping either, and that is the point of them.
 *
 * What a record is for: before this, the second block's handle arrived as a
 * command-line argument and the only checks on it were range, alignment and
 * non-overlap - which prove the SHAPE of a handle, not that it is ours. A
 * record that lives INSIDE the block it describes can be checked against the
 * block: L_OWNMARK is written at OWN_OFF in the block's own memory together
 * with the block's own address, so a handle that is not the one we acquired
 * does not carry its own mark, and a handle typed in by a host cannot forge
 * one without writing into that memory first.
 *
 * L_TICK is the free-running microsecond counter at the moment of
 * acquisition, which is the same-boot witness: the counter restarts at a
 * reboot, so a current reading BELOW the recorded one means the block was
 * acquired in a different boot and the handle is stale. Two honest limits on
 * that: it is 32 bits at 1 MHz, so it wraps about every 71.6 minutes, and a
 * wrap therefore shows up as a refusal (which is the safe direction); and a
 * reboot could in principle pass it if the recorded tick were tiny. So the
 * tick is corroboration and the mark is the evidence.
 */
.equ LIFE_OFF,        0x3F4000         /* two records, clear of everything.
                                        * 0x3F4000 and OWN_OFF below are both
                                        * 8-bit-rotated ARM immediates, so the
                                        * pointer arithmetic costs one
                                        * instruction instead of three. In an
                                        * image with 84 bytes of headroom that
                                        * is not a micro-optimisation. */
.equ LIFE_A,          0x00             /* work + destination, ours since scratch */
.equ LIFE_B,          0x40             /* generated source, acquired by PHASE 9 */
.equ L_MAGIC,         0x00
.equ L_ROLE,          0x04             /* 1 = A, 2 = B */
.equ L_ALLOC,         0x08             /* the allocator object F_ALLOC returned */
.equ L_HANDLE,        0x0C
.equ L_BYTES,         0x10             /* what we asked for */
.equ L_END,           0x14             /* handle + bytes */
.equ L_TICK,          0x18             /* tick at acquisition */
.equ L_SEQ,           0x1C             /* S_COUNT at acquisition */
.equ L_PURPOSE,       0x20             /* "WORK" / "SRCE" */
.equ L_OWNMARK,       0x24             /* the magic we also wrote into the block */
.equ L_HELD,          0x28             /* tick of the last still-held check */
.equ L_STATE,         0x2C             /* 1 held, 2 unknown, 3 freed */
.equ L_FREERET,       0x30             /* who reclaims it records the result */
.equ L_FREETICK,      0x34
.equ LIFE_MAGIC,      0x4546494C       /* "LIFE" */
.equ OWN_MAGIC,       0x4E574F42       /* "BOWN" */
.equ PURPOSE_WORK,    0x4B524F57       /* "WORK" */
.equ PURPOSE_SRC,     0x45435253       /* "SRCE" */
.equ OWN_OFF,         0x3FC000         /* the mark, inside each block */
.equ LIFE_HELD,       1
.equ LIFE_UNKNOWN,    2                /* ours, shape or ownership unclear:
                                        * nothing may free or reuse it until a
                                        * human has looked at the record */
.equ LIFE_FREED,      3
/* Computed from the claimed code address; the old constant silently encoded
 * both a fixed hook site and a fixed code base. */
#ifndef HOOK_ARMED
#define HOOK_ARMED 0xEB00333F
#endif
/* PHASE=5 shares the allocation between an owned header and the codec output,
 * so the layout differs from the discard phases. The header must sit
 * immediately before the payload: the writer registers ONE node covering the
 * whole file, measured in G3. */
#if PHASE == 5 || PHASE == 6 || PHASE == 7 || PHASE == 8
.equ HDR_BYTES,       78848            /* stock header, copied per frame */
.equ IFD_AT,          0x13400          /* new IFD + tile tables, 810 of 1024 */
.equ OUT_OFF,         0x13800          /* codec output; 1 KiB aligned */
.equ TEMPLATE_OFF,    0x3F2000         /* host-written IFD template, 1024 B */
.equ TEMPLATE_BYTES,  1024
.equ TEMPLATE_META,   0x3F2400         /* two words: offset-table, count-table */
/* PHASE=8 ZERO-COPY. The codec writes its output straight into the frame
 * buffer at +PIXEL_OFF, exactly where the stock pixels start, and the new IFD
 * goes AFTER the payload rather than in front of it. That is what removes the
 * copy: the leading-IFD layout needs an expanded 79,872-byte header, which
 * shifts the payload to +0x13800 and puts the codec's output 0x400 bytes AHEAD
 * of the source it has not read yet. With a trailing IFD the payload starts
 * where the source starts and only 810 bytes of trailer are ever written.
 *
 * The host writes the trailer template and its four metadata words; ifd_at
 * depends on the encoded size, so tags 324 and 325 carry file offsets that are
 * patched here. Offsets and sizes come from tools/trailing_ifd.py so there is
 * one place that computes them. */
.equ TRAILER_OFF,     0x3F3000         /* the 810-byte trailer, host-written */
.equ TRAILER_META,    0x3F3400         /* ifd_size, patch_off, patch_cnt,
                                        * bytes, bytes rounded to 16 */
#else
.equ OUT_OFF,         0
#endif
/* PHASE=9, the engine output-bound probe. A smaller geometry than the
 * product's, chosen so the source and the destination both fit in one 4 MiB
 * block: 1536x736 is six whole 512x368 tiles with no padding, and
 * depth * w * h / 8 = 1,695,744 for each. The declared destination length the
 * firmware computes is round_up(that, 1024), which for this geometry is the
 * same number, so the guard sits exactly at the boundary. */
/* The result words live in the retained block for every phase that reports
 * through them, so this is defined unconditionally. Leaving it inside the
 * compressed-phase #if made it an UNDEFINED symbol for PHASE=9, which the
 * assembler turned into an external relocation rather than an error. */
/* +0 file_bytes, +4 copy_bytes or ifd_at, +8 fit, +0xC t_after, and for
 * PHASE=9 the guard readbacks from +0x14 onward. */
.equ RESULT_OFF,      0x3F2800

/* PHASE=11 verifies the encoder's asynchronous ABI, transcribed statically on
 * 2026-09-29 and recorded in projects/lossless-sup/tools/codec_request.py.
 *
 * The point of the phase: F_ENC blocks for 13.7 ms because its driver waits for
 * the engine's completion event, not because the hardware is synchronous. If
 * the submit can be issued and the hook can RETURN, the compression proceeds
 * while later frames go through untouched - which is the whole architecture.
 *
 * Stage one, always: call F_INIT exactly as the working phases do, then read
 * the engine's own parameter block back. The mapping from initStruct's nine
 * words to those six fields has NOT been established, and everything else rests
 * on it, so it is measured before anything is built from it. This stage cannot
 * misdirect a DMA: it submits nothing.
 *
 * Stage two, ASYNC_SUBMIT: build the 0x30-byte request from the fields just
 * read, clear the flag, submit, and return WITHOUT waiting. Later frames poll.
 */
.equ ENGINE_BLOCK,    0xC302CDD4
.equ FLAG_ID_AT,      0xC2F2E60C
.equ FLAG_CREATE,     0xC062F441       /* Thumb, idempotent: creates the flag if
                                        * [FLAG_ID_AT] is negative and returns
                                        * the id either way. Measured 2026-09-29:
                                        * after F_INIT alone that word reads -42
                                        * = E_NOEXS, so waiting on it without
                                        * this call waits on nothing. */
.equ ENG_SUBMIT,      0xC062FFF9       /* Thumb: the low bit selects the state */
.equ ENG_CALLBACK,    0xC062FCB1       /* the firmware's own, Thumb */
.equ TILE_COUNT_VAR,  0xC302CDEC       /* the driver's tile count; F_SIZE's
                                        * loop bound via C062FA48 */
.equ ENG_START,       0xC062FEB1       /* Thumb wrapper for C0630208, the
                                        * MODE-1 STARTER. C062FFF8 only
                                        * PROGRAMS the registers; the GO bit
                                        * is [0x300D0000] |= 2 and only this
                                        * sets it. Without it the engine is
                                        * fully configured and never runs,
                                        * which is what made every poll of
                                        * the 2026-09-29 run time out.
                                        * Returns 0, or 1 if the mode word
                                        * is not 1. */
.equ TWAI_FLG,        0xC0016C08       /* (flgid, waiptn, wfmode, &ptn, tmout) */
.equ CLR_FLG,         0xC00169C8       /* (flgid, clrptn) */
.equ WAIT_PATTERN,    5                /* bit 0 done, bit 2 error */
.equ WAIT_MODE,       1                /* TWF_ORW */
.equ POLL_TMOUT,      1                /* a poll, not a wait */
.equ E_TMOUT,         -50
/* Where PHASE 11 records, inside the retained block and clear of everything. */
.equ ASYNC_OFF,       0x3F6000
.equ A_MAGIC,         0x00             /* "ASY1", written by the host */
.equ A_STAGE,         0x04
.equ A_INIT_RET,  0x08
.equ A_ENG0,      0x0C             /* the six engine fields, as read back */
.equ A_ENG4,      0x10
.equ A_ENG8,      0x14
.equ A_ENGC,      0x18
.equ A_ENG10,     0x1C
.equ A_ENG14,     0x20
.equ A_FLGID,     0x24
.equ A_SUBMIT_RET,0x28
.equ A_T_SUBMIT,  0x2C
.equ A_POLLS,     0x30
.equ A_LAST_ER,   0x34             /* the last twai_flg return */
.equ A_PATTERN,   0x38
.equ A_T_DONE,    0x3C
.equ A_SIZE_RET,  0x40
.equ A_SUM,       0x44
.equ A_FLGID_RAW, 0x48             /* the word before creation */
.equ A_START_RET, 0x4C             /* the mode-1 starter's return */
.equ A_REQ,           0x80
.equ ASYNC_MAGIC,     0x31595341       /* "ASY1" */             /* the request itself, 0x30 bytes */

/* Two variants, because the first one was refused.
 *
 * PROBE_FRAME reads the CAMERA'S OWN FRAME as the source, so only the
 * destination has to be ours and the product geometry fits in one block:
 * declared = round_up(12 * 1936 * 1090 / 8, 1024) = 0x305000, and the
 * destination at +0 leaves the guards at +0x305000, clear of WORK_CAP.
 * The source is READ ONLY - nothing here writes through it. A real frame
 * compresses to about 1.4 MB and will never reach the limit, so this variant
 * does NOT answer the bound. It answers whether the geometry was the refusal,
 * which is what decides whether a two-block version is worth building.
 *
 * Without PROBE_FRAME the source is generated in our own block, which needs a
 * reduced geometry to fit beside the destination -- and 1536x736 is what the
 * engine refused on 2026-09-26 with F_ENC returning 0. Kept so the refusal is
 * reproducible. */
/* PROBE_TWO is the one that can actually answer the bound: the product
 * geometry, which the engine insists on, AND a source we generate, which a real
 * frame can never be. Those do not fit in one 4 MiB block - 3,165,360 of source
 * and 3,166,208 of declared destination - so it uses two. The second handle is
 * taken from the lifecycle record the acquire step wrote into the FIRST block,
 * because the state
 * block has no free word. It is validated here the same way the first one is:
 * a handle we were handed is still a handle we check. */
#ifdef PROBE_TWO
.equ PROBE_W,         WIDTH
.equ PROBE_H,         HEIGHT
.equ PROBE_TILES,     TILE_COUNT
.equ PROBE_DECLARED,  0x305000         /* round_up(12*1936*1090/8, 1024) */
.equ PROBE_DST,       0x000000         /* block A */
.equ PROBE_SRC,       0x000000         /* block B */
.equ PROBE_BYTES,     RAW_BYTES        /* what the engine reads: 0x304CB0 */
#elif defined(PROBE_FRAME)
.equ PROBE_W,         WIDTH
.equ PROBE_H,         HEIGHT
.equ PROBE_TILES,     TILE_COUNT
.equ PROBE_DECLARED,  0x305000         /* round_up(12*1936*1090/8, 1024) */
.equ PROBE_DST,       0x000000         /* ours; the source is the frame's */
#else
.equ PROBE_W,         1536
.equ PROBE_H,         736
.equ PROBE_TILES,     6
.equ PROBE_BYTES,     1695744          /* 12 * 1536 * 736 / 8 */
.equ PROBE_DECLARED,  1695744          /* round_up(PROBE_BYTES, 1024) */
.equ PROBE_SRC,       0x000000
.equ PROBE_DST,       0x1A0000         /* clear of the source, guards above */
#endif

.equ MIRROR_MARK,     0x5252494D       /* "MIRR", in the frame's tail padding */
.equ UNCACHED,        0x40000000       /* worker.S's uncached view offset */

#ifndef LOG
#define LOG 0xC072FF00
#endif
.equ RING_MASK,       7                /* 8 rows x 16 bytes = the last 8 frames */
.equ AGG_OFF,         0x80             /* worst-case trackers, every frame */
.equ AGG_MAX_US,      0x00
.equ AGG_MAX_BYTES,   0x04
.equ AGG_OVER_BUDGET, 0x08
.equ BUDGET_US,       41708            /* one 24000/1001 frame */

.equ ERR_KIND,        1
.equ ERR_SEG_NULL,    2
.equ ERR_LENGTH,      3
.equ ERR_BUF_NULL,    4
.equ ERR_TIFF,        5
.equ ERR_SRC_ALIGN,   6
.equ ERR_ALLOCATOR,   7
.equ ERR_ALLOC,       8
.equ ERR_ALIAS,       9
.equ ERR_ALIGN,       10
.equ ERR_RANGE,       11
.equ ERR_OVERLAP,     12
.equ ERR_PWR_ON,      13
.equ ERR_CLK_ON,      14
.equ ERR_CLK_OFF,     15
.equ ERR_PWR_OFF,     16
.equ ERR_INIT,        17
.equ ERR_ENCODE,      18
.equ ERR_SIZE_CALL,   19
.equ ERR_SIZE_RANGE,  20
.equ ERR_GUARD,       21
.equ ERR_SOURCE,      22
.equ ERR_PROOF,       23
.equ ERR_STALE_GUARD, 24
.equ ERR_RETAINED,    25
.equ ERR_LIFE,        26      /* block A's lifecycle record is not ours */
.equ ERR_OWN,         27      /* the mark inside the block is not there */
.equ ERR_CLOCK_RESET, 28      /* the tick went backwards: not this boot */
.equ ERR_ALLOC_B,     29      /* the second block could not be acquired */
.equ ERR_SHAPE_B,     30      /* it came back the wrong shape */
.equ ERR_OVERLAP_B,   31      /* it overlaps block A or the frame */
.equ ERR_OWN_B,       32      /* we cannot write to what we were handed */

.macro LDA reg, value
    movw    \reg, #:lower16:\value
    movt    \reg, #:upper16:\value
.endm

.macro FAIL code, target
    mov     r0, #\code
    str     r0, [r4, #S_ERROR]
    b       \target
.endm

.global probe_entry
probe_entry:
    /* 12 words: preserve every register used here and keep SP 8-byte aligned. */
    push    {r0, r1, r2, r3, r4, r5, r6, r7, r8, r10, r12, lr}

    LDA     r4, STATE
    ldr     r3, [r4, #S_COUNT]
    add     r3, r3, #1
    str     r3, [r4, #S_COUNT]
#if PHASE == 3 || PHASE == 6
    /* The loop counter is frames actually processed, not hook entries. */
    ldr     r0, [r4, #S_FRAMES_DONE]
    cmp     r0, #FRAMES
    bhs     tail_call
#elif PHASE == 11
    /* This phase needs SEVERAL entries - one to submit, the rest to poll - so
     * the one-shot guard below is wrong for it, and wrong in a way that hides:
     * the hook is restored FURTHER DOWN, so an entry that leaves here never puts
     * the site back. On 2026-09-29 that left the hook armed for a whole clip,
     * 103 entries that did nothing but increment this counter, and a poll count
     * that never moved. */
    cmp     r3, #FRAMES
    bhi     tail_call
#else
    cmp     r3, #1
    bne     tail_call
#endif

    str     r0, [r4, #S_R0]
    str     r1, [r4, #S_R1]
    str     r2, [r4, #S_R2]
    str     lr, [r4, #S_LR]
    mov     r5, r1
    mov     r0, #PHASE
    str     r0, [r4, #S_PHASE]
    mov     r0, #1
    str     r0, [r4, #S_STAGE]

    /* Restore before any pointer dereference or firmware call. */
    LDA     r7, HOOK_SITE
    LDA     r8, HOOK_ORIG
    str     r8, [r7]
    ldr     r8, [r7]
    str     r8, [r4, #S_RESTORED]
    mov     r0, #2
    str     r0, [r4, #S_STAGE]

    cmp     r2, #2
    bne     bad_kind
    cmp     r5, #0
    beq     bad_seg
    ldr     r6, [r5]
    ldr     r7, [r5, #4]
    str     r6, [r4, #S_BUF]
    str     r7, [r4, #S_LEN]
    LDA     r0, DNG_LEN
    cmp     r7, r0
    bne     bad_length
    cmp     r6, #0
    beq     bad_buf
    ldr     r0, [r6]
    str     r0, [r4, #S_HDR_BEFORE]
    LDA     r1, 0x002A4949
    cmp     r0, r1
    bne     bad_tiff
    LDA     r0, PIXEL_OFF
    add     r7, r6, r0                 /* source pixels, read-only */
    LDA     r0, 0x3FF
    tst     r7, r0
    bne     bad_src_align

    ldr     r0, [r7]
    str     r0, [r4, #S_PIX0_BEFORE]
    LDA     r1, RAW_BYTES - 4
    ldr     r0, [r7, r1]
    str     r0, [r4, #S_PIXN_BEFORE]
    mov     r0, #3
    str     r0, [r4, #S_STAGE]

#if PHASE >= 2
    /* Consume the retained block. Nothing is allocated; both proof magics must
     * be present or the seeded handle is not one we may touch. */
    ldr     r0, [r4, #S_POWER_PROOF]
    LDA     r1, POWER_MAGIC
    cmp     r0, r1
    bne     bad_proof
    ldr     r0, [r4, #S_SCRATCH_PROOF]
    LDA     r1, SCRATCH_MAGIC
    cmp     r0, r1
    bne     bad_proof
    ldr     r0, [r4, #S_ALLOCATOR]
    cmp     r0, #0
    beq     bad_allocator
    ldr     r8, [r4, #S_HANDLE]
    cmp     r8, #0
    beq     bad_alloc
#else
    /* One allocator-owned block, directly aligned by F_GET's third argument. */
    mov     r0, #0
    LDA     r12, F_ALLOC
    blx     r12
    str     r0, [r4, #S_ALLOCATOR]
    cmp     r0, #0
    beq     bad_allocator
    mov     r8, r0

    sub     sp, sp, #8
    mov     r0, r8
    LDA     r1, ALLOC_SIZE
    LDA     r2, 0x400
    mov     r3, #0
    str     r3, [sp]                   /* fifth argument */
    LDA     r12, F_GET
    blx     r12
    add     sp, sp, #8
    mov     r8, r0                     /* low DMA handle, also work/output */
    str     r8, [r4, #S_HANDLE]
    cmp     r8, #0
    beq     bad_alloc
#endif
    mov     r0, r8, lsr #30
    cmp     r0, #1                     /* require 0x4xxxxxxx..0x7xxxxxxx */
    bne     bad_alias
    LDA     r0, 0x3FF
    tst     r8, r0
    bne     bad_align

    LDA     r0, ALLOC_SIZE
    adds    r1, r8, r0
    bcs     bad_range
    str     r1, [r4, #S_ALLOC_END]
    str     r8, [r4, #S_WORK_BASE]
    LDA     r0, WORK_CAP
    adds    r2, r8, r0
    bcs     bad_range
    str     r2, [r4, #S_WORK_END]
    str     r2, [r4, #S_WORK_GUARD]
    LDA     r0, TABLE_OFF
    adds    r3, r8, r0
    bcs     bad_range
    str     r3, [r4, #S_TABLE_BASE]
    LDA     r0, TABLE_TMP_OFF
    adds    r0, r8, r0
    bcs     bad_range
    str     r0, [r4, #S_TABLE_TMP]
    add     r12, r0, #0x34
    cmp     r12, r1
    bhi     bad_range

    /* Allocator range must be disjoint from the original DNG segment. */
    ldr     r0, [r4, #S_LEN]
    adds    r0, r6, r0
    bcs     bad_range
    cmp     r1, r6
    bls     ranges_ok
    cmp     r0, r8
    bls     ranges_ok
    b       bad_overlap

ranges_ok:
    LDA     r12, GUARD_MAGIC
#if PHASE >= 2
    /* The scratch phase wrote these. If any is gone, something else has been
     * in this block: do not encode into it and do not free it. */
    ldr     r0, [r2]
    cmp     r0, r12
    bne     bad_stale_guard
    ldr     r0, [r3, #0x30]
    cmp     r0, r12
    bne     bad_stale_guard
    ldr     r0, [r4, #S_TABLE_TMP]
    ldr     r0, [r0, #0x30]
    cmp     r0, r12
    bne     bad_stale_guard

#if PHASE == 9
    /* The lifecycle record, checked before the block is adopted. A guard
     * magic says "nothing has scribbled here"; it does not say "this is the
     * block we acquired". The record inside the block names the handle we are
     * about to use, and the block carries our mark at OWN_OFF together with
     * its own address - which a host that merely typed a handle cannot
     * produce without writing into that memory first.
     *
     * The same-boot check is NOT here, deliberately. It costs a call to the
     * clock and this image has 84 bytes of headroom; and between arming and
     * the hook firing a reboot is impossible, because the armed word lives in
     * RAM and a reboot would take the hook with it. The acquire step, which
     * runs outside the hook and has room, is where the clock is read and
     * compared. That argument is the reason for the omission; if it is wrong,
     * the omission is wrong with it.
     *
     * Assembled for PHASE 9 only. Copy-back reached a camera as it stands and
     * its image is 1,764 bytes against a 1,792-byte claim; growing it for a
     * check its own phase does not need would be paid for by the one image
     * that has already been proven.
     */
    add     r0, r8, #UNCACHED
    add     r0, r0, #LIFE_OFF
    mov     r1, r8
    bl      verify_life
    LDA     r12, GUARD_MAGIC           /* verify_life used it as scratch */
#endif
    mov     r0, #1
    str     r0, [r4, #S_ADOPTED]
#endif
    str     r12, [r2]
    str     r12, [r3, #0x30]
    ldr     r0, [r4, #S_TABLE_TMP]
    str     r12, [r0, #0x30]
    mov     r0, #4
    str     r0, [r4, #S_STAGE]

#if PHASE == 4
    /* MIRROR. The first destructive phase, and deliberately the one with no
     * format risk: copy the whole stock DNG into the arena unchanged and point
     * the writer's node at the copy, same length. The bytes that reach the
     * card are the bytes the camera was going to write anyway.
     *
     * It answers one question and only one: can this buffer be handed to the
     * card writer, and is it visible to that DMA? A corrupt or zero frame here
     * is the visibility problem, with no format variable mixed in.
     *
     * The block is NEVER freed by this phase. The flush reads it after we
     * return, so freeing would hand the card a block the allocator has taken
     * back. The host releases it once the recording has stopped.
     *
     * Writes go through the worker's uncached view (base + UNCACHED), the
     * idiom fp_usb_shell/camera/worker.S uses for its own DMA, which takes a
     * barrier rather than a cache clean. That is the hypothesis under test;
     * the fallback is a full clean.
     */
    /* finish_source still needs r5/r6/r7 as the segment, DNG and pixel
     * pointers, and the copy destroys all three. Saving them is what the first
     * live run forgot: the redirect worked and ERR_SOURCE came from
     * finish_source reading the copy's leftovers instead. */
    push    {r5, r6, r7, r10}
    ldr     r5, [r4, #S_BUF]           /* stock DNG buffer, source */
    LDA     r0, UNCACHED
    add     r6, r8, r0                 /* destination, uncached view */
    mov     r10, r6                    /* base kept for the watermark */
    LDA     r7, DNG_LEN

    LDA     r12, F_CLOCK
    blx     r12
    str     r0, [r4, #S_T0]
mirror_copy:
    ldm     r5!, {r0, r1, r2, r3}
    stm     r6!, {r0, r1, r2, r3}
    subs    r7, r7, #16
    bne     mirror_copy

    /* A watermark, because a byte-identical copy cannot prove itself: if the
     * redirect never took, the card would receive the original and look
     * exactly the same. This word lands in the trailing zero padding after the
     * raster, which no decoder reads, so the frame is still the frame. */
    LDA     r0, MIRROR_MARK
    LDA     r1, DNG_LEN - 16
    str     r0, [r10, r1]
    dsb     sy
    LDA     r12, F_CLOCK
    blx     r12
    str     r0, [r4, #S_T1]
    pop     {r5, r6, r7, r10}

    /* Redirect the node. seg[1] is unchanged on purpose: same length, same
     * bytes, so anything different on the card can only be the buffer or its
     * visibility. */
    ldr     r0, [r4, #S_R1]            /* the {buffer, length} pair */
    str     r8, [r0]
    LDA     r1, DNG_LEN
    str     r1, [r0, #4]
    dsb     sy
    mov     r0, #9
    str     r0, [r4, #S_STAGE]
    b       finish_source              /* no codec ran; no guards to re-read */
#elif PHASE == 0
    /* Context-only. Do not pre-stage these calls before a later F_ENC; that
     * wrapper owns its own balanced power/clock/IRQ sequence. */
    mov     r0, #1
    LDA     r12, F_PWR
    blx     r12
    str     r0, [r4, #S_PWR_ON]
    cmp     r0, #0
    bne     preflight_pwr_failed

    mov     r0, #5
    mov     r1, #1
    LDA     r12, F_CLK
    blx     r12
    str     r0, [r4, #S_CLK_ON]
    cmp     r0, #0
    bne     preflight_clk_failed

    mov     r0, #5
    mov     r1, #0
    LDA     r12, F_CLK
    blx     r12
    str     r0, [r4, #S_CLK_OFF]

    mov     r0, #0
    LDA     r12, F_PWR
    blx     r12
    str     r0, [r4, #S_PWR_OFF]
    ldr     r1, [r4, #S_CLK_OFF]
    cmp     r1, #0
    bne     preflight_bad_clk_off
    cmp     r0, #0
    bne     preflight_bad_pwr_off
    mov     r0, #5
    str     r0, [r4, #S_STAGE]
    b       finish_checked

preflight_clk_failed:
    mov     r0, #0
    LDA     r12, F_PWR
    blx     r12
    str     r0, [r4, #S_PWR_OFF]
    FAIL    ERR_CLK_ON, finish_checked
preflight_pwr_failed:
    FAIL    ERR_PWR_ON, finish_checked
preflight_bad_clk_off:
    FAIL    ERR_CLK_OFF, finish_checked
preflight_bad_pwr_off:
    FAIL    ERR_PWR_OFF, finish_checked
#elif PHASE == 11
    /* ASYNCHRONOUS ENCODE ABI. Everything is in our own retained block: the
     * source is generated here, the destination and the size table are the
     * arena's, and seg is never read for its buffer nor written. The frame
     * reaches the card exactly as it would have. */
    push    {r5, r6, r7, r10}
    add     r10, r8, #UNCACHED
    LDA     r5, ASYNC_OFF
    add     r5, r10, r5                /* r5 = the record area */

    /* The record area lives in the RETAINED BLOCK, which the allocator does not
     * zero. On 2026-09-29 A_STAGE therefore started as another owner's leftover
     * bytes, the phase read it as "already submitted" and never once ran its
     * first-frame path. A magic the host writes at arming is the gate now: no
     * magic, no work. */
    ldr     r0, [r5, #A_MAGIC]
    LDA     r1, ASYNC_MAGIC
    cmp     r0, r1
    bne     async_give_up
    ldr     r0, [r5, #A_STAGE]
    cmp     r0, #0
    bne     async_poll                 /* a later frame: poll, do not resubmit */

    /* ---- first frame: generate a source, then F_INIT ------------------ */
    /* A COMPRESSIBLE source: samples alternating 1024 and 3072, three constant
     * words repeating. An incompressible one is refused outright - measured on
     * 2026-09-28 - so the LCG pattern would have tested nothing here. */
    LDA     r6, PROBE_SRC
    add     r6, r10, r6
    LDA     r7, PROBE_BYTES
    LDA     r0, 0x40000C40
    LDA     r1, 0x0C40000C
    LDA     r2, 0x000C4000
async_fill:
    stm     r6!, {r0, r1, r2}
    subs    r7, r7, #12
    bne     async_fill

    /* The geometry goes into BOTH the initStruct and the request, and the two
     * use the same offsets for it, so each constant is loaded once and stored
     * twice. Five duplicated LDA pairs was 40 bytes in an image with 28 to
     * spare. */
    sub     sp, sp, #0x40
#ifdef ASYNC_SUBMIT
    add     r7, r5, #A_REQ
#else
    mov     r7, sp                     /* no request: store the geometry twice
                                        * into the same place, harmlessly */
#endif
    LDA     r0, PROBE_W
    str     r0, [sp, #0x00]
    str     r0, [r7, #0x00]
    LDA     r0, PROBE_H
    str     r0, [sp, #0x04]
    str     r0, [r7, #0x04]
    mov     r0, #DEPTH
    str     r0, [sp, #0x08]
    str     r0, [r7, #0x08]
    LDA     r0, TILE_W
    str     r0, [sp, #0x0C]
    str     r0, [r7, #0x0C]
    LDA     r0, TILE_H
    str     r0, [sp, #0x10]
    str     r0, [r7, #0x10]
    LDA     r0, PROBE_SRC
    add     r0, r10, r0
    str     r0, [sp, #0x14]
    LDA     r0, PROBE_DST
    add     r0, r10, r0
    str     r0, [sp, #0x18]
    ldr     r0, [r4, #S_TABLE_BASE]
    str     r0, [sp, #0x1C]
    mov     r0, #0
    str     r0, [sp, #0x20]
    mov     r0, sp
    LDA     r12, F_INIT
    blx     r12
    add     sp, sp, #0x40
    str     r0, [r5, #A_INIT_RET]
    cmp     r0, #1
    bne     async_init_failed

    /* ---- read the engine's parameter block back -----------------------
     * Six words in one transfer, and they stay in registers: the request below
     * is built from these rather than re-read from the record, which is both
     * shorter and one fewer place for the two to disagree.
     *   r0 = +0x00  r1 = +0x04  r2 = +0x08  r3 = +0x0C  r7 = +0x10  r12 = +0x14
     */
    LDA     r6, ENGINE_BLOCK
    ldm     r6, {r0, r1, r2, r3}       /* +0x00 +0x04 +0x08 +0x0C */
    ldr     r12, [r6, #0x10]
    ldr     r6, [r6, #0x14]
    add     r1, r5, #A_ENG0
    str     r0, [r1, #0x00]
    str     r2, [r1, #0x08]
    str     r3, [r1, #0x0C]
    str     r12, [r1, #0x10]
    str     r6, [r1, #0x14]
#ifndef ASYNC_SUBMIT
    /* Stage one's own measurements, and only stage one's: +0x04 turned out to be
     * the source length and the raw flag word turned out to be -42. Stage two
     * does not re-measure what stage one established, which is also the 28 bytes
     * that let it fit its claim. */
    LDA     r1, ENGINE_BLOCK
    ldr     r1, [r1, #0x04]
    str     r1, [r5, #A_ENG4]
    LDA     r1, FLAG_ID_AT
    ldr     r1, [r1]
    str     r1, [r5, #A_FLGID_RAW]
#endif
    mov     r0, #1
    str     r0, [r5, #A_STAGE]         /* stage 1: measured, nothing submitted */
    dsb     sy

#ifdef ASYNC_SUBMIT
    /* ---- build the request from what was just read --------------------
     * Built from the mapping MEASURED by stage one on 2026-09-29: F_INIT leaves
     * the source and destination already translated to the uncached alias and
     * the size table untranslated, so the three pointers are copied straight
     * across. */
    /* Read the engine-derived fields back OUT OF THE RECORD rather than keeping
     * them in registers across the calls above. Keeping them there is how the
     * flag-id read came to clobber r6 - the size-table length - which would have
     * put a wrong scratch pointer into a request handed to a DMA. */
    ldr     r0, [r5, #A_ENG0]
    str     r0, [r7, #0x14]            /* source, already uncached by F_INIT */
    ldr     r0, [r5, #A_ENG8]
    str     r0, [r7, #0x1C]            /* destination, likewise */
    ldr     r0, [r5, #A_ENGC]
    str     r0, [r7, #0x20]            /* remaining capacity */
    ldr     r1, [r5, #A_ENG14]
    str     r1, [r7, #0x28]            /* size-table length */
    /* Point the hardware at the ENGINE'S OWN table, not at a scratch after it.
     * F_SIZE delegates to C062FA48, which takes the table pointer from
     * [0xC302CDD4+0x10] and the tile count from [0xC302CDEC], then byte-reverses
     * each entry into the caller's array. On 2026-09-29 this stored
     * table + round_up(len, 1024) instead, so the hardware wrote its sizes
     * 0x400 bytes past where F_SIZE looks: the encode completed, the sizes were
     * genuinely there, and F_SIZE still reported zeros. [C/L] */
    ldr     r0, [r5, #A_ENG10]
    str     r0, [r7, #0x24]

    /* The tile count is the driver's, and the driver is exactly what this phase
     * does not run, so C062FA48 would loop zero times. Publish it. Safe here
     * because during recording the codec has no other user: the camera writes
     * RAW and compresses nothing. */
    LDA     r1, TILE_COUNT_VAR
    mov     r0, #PROBE_TILES
    str     r0, [r1]
    LDA     r0, PROBE_DECLARED
    str     r0, [r7, #0x18]
    LDA     r0, ENG_CALLBACK
    str     r0, [r7, #0x2C]
    dsb     sy

    /* Create the flag if it does not exist, record the id, then clear it and
     * submit. No wait anywhere: that is the whole point of the phase. */
    LDA     r12, FLAG_CREATE
    blx     r12
    str     r0, [r5, #A_FLGID]
    cmp     r0, #0
    blt     async_no_flag
    mov     r1, #0
    LDA     r12, CLR_FLG
    blx     r12
    LDA     r12, F_CLOCK
    blx     r12
    str     r0, [r5, #A_T_SUBMIT]
    add     r0, r5, #A_REQ
    LDA     r12, ENG_SUBMIT
    blx     r12
    str     r0, [r5, #A_SUBMIT_RET]
    LDA     r12, ENG_START
    blx     r12
    str     r0, [r5, #A_START_RET]
    mov     r0, #2
    str     r0, [r5, #A_STAGE]         /* stage 2: submitted, not waited for */
    dsb     sy
#endif
    pop     {r5, r6, r7, r10}
    mov     r0, #14
    str     r0, [r4, #S_STAGE]
    b       async_rearm

async_poll:
    /* A later frame. Poll once with a one-tick timeout and return either way:
     * the whole claim of this phase is that the encode proceeds while frames go
     * through, so this must never block. */
    cmp     r0, #2
    bne     async_done                 /* nothing was submitted */
    ldr     r0, [r5, #A_POLLS]
    add     r0, r0, #1
    str     r0, [r5, #A_POLLS]
    sub     sp, sp, #8
    mov     r0, #POLL_TMOUT
    str     r0, [sp]
    ldr     r0, [r5, #A_FLGID]
    mov     r1, #WAIT_PATTERN
    mov     r2, #WAIT_MODE
    add     r3, sp, #4
    LDA     r12, TWAI_FLG
    blx     r12
    ldr     r1, [sp, #4]
    add     sp, sp, #8
    str     r0, [r5, #A_LAST_ER]
    str     r1, [r5, #A_PATTERN]
    cmp     r0, #0
    bne     async_done                 /* E_TMOUT or an error: try next frame */

    LDA     r12, F_CLOCK
    blx     r12
    str     r0, [r5, #A_T_DONE]
    ldr     r0, [r5, #A_FLGID]
    mov     r1, #0
    LDA     r12, CLR_FLG
    blx     r12
    /* It reported completion: read the sizes with the same call the working
     * phases use, and sum them. */
    add     r0, r4, #S_TILE0
    mov     r1, #1
    LDA     r12, F_SIZE
    blx     r12
    str     r0, [r5, #A_SIZE_RET]
    cmp     r0, #1
    bne     async_size_failed
    add     r3, r4, #S_TILE0
    mov     r2, #PROBE_TILES
    mov     r1, #0
async_sum:
    ldr     r0, [r3], #4
    add     r1, r1, r0
    subs    r2, r2, #1
    bne     async_sum
    str     r1, [r5, #A_SUM]
    str     r1, [r4, #S_COMP_SIZE]
    mov     r0, #3
    str     r0, [r5, #A_STAGE]         /* stage 3: complete */
    dsb     sy
async_done:
    pop     {r5, r6, r7, r10}
    b       async_rearm

async_size_failed:
    mov     r0, #4
    str     r0, [r5, #A_STAGE]
    dsb     sy
    pop     {r5, r6, r7, r10}
    FAIL    ERR_SIZE_CALL, finish_checked

async_init_failed:
    mov     r0, #5
    str     r0, [r5, #A_STAGE]
    dsb     sy
    pop     {r5, r6, r7, r10}
    FAIL    ERR_INIT, finish_checked

async_no_flag:
    /* A negative id is an error code, not a flag. Nothing is submitted: a
     * submit whose completion nobody can wait for is a DMA with no owner. */
    mov     r0, #6
    str     r0, [r5, #A_STAGE]
    dsb     sy
    pop     {r5, r6, r7, r10}
    b       finish_checked

async_give_up:
    /* No magic: do nothing, and do not re-arm either. A phase that cannot tell
     * its own record from someone else's must stop, not guess. */
    pop     {r5, r6, r7, r10}
    b       finish_checked

async_rearm:
    /* Re-arm until the encode has been seen to finish or the ceiling is hit.
     * Two ceilings: the stage, and the raw hook count. */
    ldr     r0, [r4, #S_COUNT]
    cmp     r0, #FRAMES
    bhs     finish_checked
    LDA     r0, HOOK_SITE
    LDA     r1, HOOK_ARMED
    str     r1, [r0]
    dsb     sy
    isb
    b       finish_checked

#elif PHASE == 9
    /* ENGINE OUTPUT BOUND, measured without touching anything of the camera's.
     *
     * The question this answers: FUN_c062f5a8 hands the engine a DESTINATION
     * LENGTH, set to round_up(depth * width * height / 8, 1024). If the engine
     * respects it, the output cannot exceed the source size and zero-copy has a
     * bound rather than a hope. Reading the firmware shows the register being
     * written; it cannot show what happens when the output reaches it.
     *
     * Everything here happens inside our own retained block:
     *   - the source is generated here, not read from the frame. A counter run
     *     through a multiply is incompressible, so the output WANTS to exceed
     *     the declared length. A real frame compresses and would never reach it.
     *   - guard words sit at the declared length and beyond. If they survive,
     *     the engine stopped where it was told.
     *
     * seg is never read for its buffer, never written, and the frame reaches the
     * card exactly as it would have. The geometry is smaller than the product's
     * so that source and destination both fit in one 4 MiB block: 1536x736 at
     * 512x368 is six whole tiles with no padding, 1,695,744 bytes each.
     */
#ifdef PROBE_TWO
    /* Block B's record and mark, checked BEFORE the push.
     *
     * Not a stylistic choice. verify_life's failure paths end at
     * finish_source, which reads the segment, the DNG buffer and the pixel
     * pointer out of r5, r6 and r7 and needs the stack as it was on entry.
     * Checking after `push {r5, r6, r7, r10}` meant a refusal jumped there
     * with those registers holding this phase's working values and the stack
     * sixteen bytes deep - the pop at the tail then restored the wrong
     * registers and the return went nowhere. Emulation caught it; a camera
     * would have caught it too, by freezing.
     *
     * Block B's handle is not kept in a register: it is re-read from the
     * record where it is needed, which costs three instructions and removes
     * the question of what clobbers it.
     */
    add     r0, r8, #UNCACHED
    add     r0, r0, #LIFE_OFF
    add     r0, r0, #LIFE_B
    ldr     r1, [r0, #L_HANDLE]
    cmp     r1, #0
    beq     bad_life                   /* no record: run acquire-second */
    bl      verify_life
    mov     r0, r1, lsr #30
    cmp     r0, #1                     /* the 0x4..0x7 quarter */
    bne     bad_alias
    LDA     r0, 0x3FF
    tst     r1, r0                     /* 1 KiB aligned */
    bne     bad_align
    /* Disjoint from the frame the camera handed us. Only checkable here: the
     * acquire step runs with no frame in flight, and the fill below writes
     * 3.1 MB, so getting this wrong would destroy the frame. */
    ldr     r0, [r4, #S_BUF]
    ldr     r2, [r4, #S_LEN]
    add     r2, r0, r2
    add     r3, r1, #ALLOC_SIZE
    cmp     r3, r0
    bls     probe_second_disjoint
    cmp     r2, r1
    bhi     bad_overlap
probe_second_disjoint:
#endif
    push    {r5, r6, r7, r10}
    LDA     r0, UNCACHED
    add     r10, r8, r0

#ifndef PROBE_FRAME
    /* An incompressible source. x = x * 1664525 + 1013904223, taking the high
     * half of each word, which defeats a magnitude-category coder. */
#ifdef PROBE_TWO
    /* The fill pointer, from the record checked above. */
    add     r5, r8, #UNCACHED
    add     r5, r5, #LIFE_OFF
    add     r5, r5, #LIFE_B
    ldr     r5, [r5, #L_HANDLE]
    add     r5, r5, #UNCACHED
    add     r5, r5, #PROBE_SRC
#else
    LDA     r5, PROBE_SRC
    add     r5, r10, r5
#endif
    LDA     r6, PROBE_BYTES
#ifdef PROBE_COPY_FRAME
    /* SOURCE LOCATION, not the bound.
     *
     * This variant exists because of what 2026-09-28 measured: at the product
     * geometry, with a generated source in our own block, F_ENC refused
     * (enc_ret 0) after 9.8 ms. The same geometry with the camera's own frame
     * as the source was accepted on 9/27. Two things differ between those runs
     * and only one can be the reason: the CONTENT of the source, or WHERE the
     * source lives.
     *
     * So this copies the camera's own pixels into block B and encodes from
     * there. Content held constant, location changed. Accepted means the
     * engine will read from a block we allocated; refused means it will not,
     * and the two-block design cannot answer the bound at all.
     *
     * It is NOT a bound test and must never be reported as one: a real frame
     * compresses (2.197x in C2), so the output never approaches the declared
     * length and the guards prove nothing either way. The frame is read only.
     */
    ldr     r0, [r4, #S_BUF]
    add     r0, r0, #PIXEL_OFF
    add     r0, r0, #UNCACHED
probe_fill:
    ldm     r0!, {r1, r2, r3, r12}
    stm     r5!, {r1, r2, r3, r12}
    subs    r6, r6, #16
    bne     probe_fill
#elif defined(PROBE_RAMP)
    /* GENERATED, but compressible. The last variable.
     *
     * 2026-09-28 established that the engine reads from a block we allocated:
     * the same block with the camera's own pixels in it was accepted. So the
     * earlier refusal was about the source's CONTENT - but in that pair,
     * "incompressible" and "an output that would exceed the declared length"
     * are the same thing, because the generated source demands 3,392,834 B
     * against a declared 3,166,208 B while a real frame needs under a
     * megabyte. This variant separates them: the data is still ours, still
     * synthetic, and compresses easily.
     *
     * Accepted => the discriminator is the output SIZE, and the engine refuses
     * work it cannot fit in the length it was handed.
     * Refused  => the discriminator is something about synthetic data itself.
     *
     * The pattern: 12-bit samples alternating 1024 and 3072. The engine splits
     * each tile into two components by column parity, so one component sees
     * 1024 throughout and the other 3072, and predictor 1 leaves differences
     * of zero. The host encoder measures it at 1.0 bits a sample, 264,752
     * bytes, 8.4% of the declared length.
     *
     * The first attempt was a ramp in BYTE space and the host measured it at
     * 12.15 bits a sample - 3,204,684 bytes, still OVER the declared length,
     * so it would have separated nothing. Three bytes carry two samples, and a
     * byte ramp wrapping every 256 bytes keeps shifting phase against that
     * packing. Measuring before running is what caught it.
     *
     * Three bytes hold the pair and twelve is the first whole number of words
     * holding a whole number of pairs, so the fill is three constants stored
     * in a loop. PROBE_BYTES is 3,165,360 = 263,780 * 12, exactly.
     */
    LDA     r2, 0x40000C40             /* stm stores in REGISTER order, so the */
    LDA     r3, 0x0C40000C             /* three constants are assigned r2,r3,r7 */
    LDA     r7, 0x000C4000             /* to land in memory order */
probe_fill:
    stm     r5!, {r2, r3, r7}
    subs    r6, r6, #12
    bne     probe_fill
#else
    LDA     r7, 0x12345679
    LDA     r2, 1664525
    LDA     r3, 1013904223
probe_fill:
    mla     r7, r7, r2, r3
    eor     r0, r7, r7, lsr #16
    str     r0, [r5], #4
    subs    r6, r6, #4
    bne     probe_fill
#endif
#endif

    /* Guards from the declared length onward. Four words, then one a kilobyte
     * further, so a small overshoot and a large one look different. */
    LDA     r5, PROBE_DST + PROBE_DECLARED
    add     r5, r10, r5
    LDA     r12, GUARD_MAGIC
    str     r12, [r5]
    str     r12, [r5, #4]
    str     r12, [r5, #8]
    str     r12, [r5, #0xC]
    str     r12, [r5, #0x400]
    dsb     sy

    /* initStruct with the probe geometry, and encStruct after it. */
    sub     sp, sp, #0x40
    LDA     r0, PROBE_W
    str     r0, [sp, #0x00]
    str     r0, [sp, #0x28]
    LDA     r0, PROBE_H
    str     r0, [sp, #0x04]
    str     r0, [sp, #0x2C]
    mov     r0, #DEPTH
    str     r0, [sp, #0x08]
    str     r0, [sp, #0x30]
    LDA     r0, TILE_W
    str     r0, [sp, #0x0C]
    str     r0, [sp, #0x34]
    LDA     r0, TILE_H
    str     r0, [sp, #0x10]
    str     r0, [sp, #0x38]
#ifdef PROBE_TWO
    add     r0, r8, #UNCACHED
    add     r0, r0, #LIFE_OFF
    add     r0, r0, #LIFE_B
    ldr     r0, [r0, #L_HANDLE]
    add     r0, r0, #UNCACHED
    str     r0, [sp, #0x14]            /* src: block B, generated above */
#elif defined(PROBE_FRAME)
    ldr     r0, [r4, #S_BUF]
    LDA     r1, PIXEL_OFF
    add     r0, r0, r1
    LDA     r1, UNCACHED
    add     r0, r0, r1
    str     r0, [sp, #0x14]            /* src: the camera's frame, READ ONLY */
#else
    LDA     r0, PROBE_SRC
    add     r0, r10, r0
    str     r0, [sp, #0x14]            /* src: ours, generated above */
#endif
    LDA     r0, PROBE_DST
    add     r0, r10, r0
    str     r0, [sp, #0x18]            /* dst: ours, guarded above */
    ldr     r0, [r4, #S_TABLE_BASE]
    str     r0, [sp, #0x1C]
    mov     r0, #0
    str     r0, [sp, #0x20]

    mov     r0, sp
    LDA     r12, F_INIT
    blx     r12
    str     r0, [r4, #S_INIT_RET]
    cmp     r0, #1
    bne     probe_init_failed

    mov     r0, #1
    str     r0, [r4, #S_ENC_STARTED]   /* set BEFORE the call: a hang must show */
    dmb     sy
    LDA     r12, F_CLOCK
    blx     r12
    str     r0, [r4, #S_T0]
    add     r0, sp, #0x28
    LDA     r12, F_ENC
    blx     r12
    str     r0, [r4, #S_ENC_RET]
    LDA     r12, F_CLOCK
    blx     r12
    str     r0, [r4, #S_T1]

    /* A failed encode must FAIL. Without this the probe summed an untouched
     * size table, reported 0xBCFFEFB0 as a compressed size, left error at 0 and
     * reached stage 8 -- a run that looked clean and carried nonsense. The
     * guards are still read back, because whether a refused encode wrote
     * anything is worth knowing. */
    ldr     r0, [r4, #S_ENC_RET]
    cmp     r0, #1
    bne     probe_encode_failed

    add     r0, r4, #S_TILE0
    mov     r1, #1
    LDA     r12, F_SIZE
    blx     r12
    str     r0, [r4, #S_SIZE_RET]
    add     sp, sp, #0x40
    cmp     r0, #1
    bne     probe_size_failed

    /* Sum what it reported, then read the guards back. */
    add     r3, r4, #S_TILE0
    mov     r2, #PROBE_TILES
    mov     r1, #0
probe_sum:
    ldr     r0, [r3], #4
    add     r1, r1, r0
    subs    r2, r2, #1
    bne     probe_sum
    str     r1, [r4, #S_COMP_SIZE]

    bl      probe_guards
    dsb     sy
    pop     {r5, r6, r7, r10}
    mov     r0, #14
    str     r0, [r4, #S_STAGE]
    b       finish_checked

probe_size_failed:
    /* F_SIZE's return was stored and never tested, so a refused size call
     * summed an untouched table and the sum was reported as a compressed
     * size. The tile words are left exactly as the call left them and are NOT
     * summed: a sum of unknown words is worse than no sum, because it looks
     * like an answer. */
    bl      probe_guards
    dsb     sy
    pop     {r5, r6, r7, r10}
    FAIL    ERR_SIZE_CALL, finish_checked

probe_encode_failed:
    add     sp, sp, #0x40
    /* Read the guards anyway - all five of them. A refusal that still wrote
     * would matter more than the refusal itself, and reading two of the five
     * left the other three holding whatever the last run put there, which is
     * indistinguishable from a guard that survived. */
    bl      probe_guards
    dsb     sy
    pop     {r5, r6, r7, r10}
    FAIL    ERR_ENCODE, finish_checked

probe_init_failed:
    add     sp, sp, #0x40
    /* No encode was attempted, so the guards are the ones we wrote and the
     * result words stay at the host's seeded values rather than being filled
     * with readings that would look like measurements. */
    pop     {r5, r6, r7, r10}
    FAIL    ERR_INIT, finish_checked

/* The five guard words and the declared length, read back into the result
 * block. One copy, called from the success path and from both failure paths,
 * which is what makes "all five, every time" affordable in an image with
 * tens of bytes of headroom.
 *   r10 = block A, uncached. Clobbers r0-r3, r5, r6, r12.
 */
probe_guards:
    LDA     r5, PROBE_DST + PROBE_DECLARED
    add     r5, r10, r5
    LDA     r6, RESULT_OFF
    add     r6, r10, r6
    ldm     r5, {r0, r1, r2, r3}       /* the four at the declared length */
    add     r12, r6, #0x14
    stm     r12, {r0, r1, r2, r3}
    ldr     r0, [r5, #0x400]           /* and the one a kilobyte further out */
    str     r0, [r6, #0x24]
    LDA     r0, PROBE_DECLARED
    str     r0, [r6, #0x28]            /* the constant we passed, not a readback */
    /* And, separately, an actual reading: the firmware's own destination-length
     * variable, as the native writer left it for THIS frame. The address is
     * stored next to the value, because a number whose provenance is a
     * constant elsewhere in a source file is not a readback, and the two have
     * been conflated in a result field before. What this is evidence OF is
     * narrow: it is the length the native path declared for this frame, not
     * the length our own F_INIT call was given. */
    LDA     r0, DECL_VAR
    ldr     r1, [r0]
    str     r0, [r6, #0x2C]
    str     r1, [r6, #0x30]
    bx      lr

#else
    /* Build initStruct (9 words) and encStruct (5 words) in a 64-byte frame. */
    sub     sp, sp, #0x40
    LDA     r0, WIDTH
    str     r0, [sp, #0x00]
    str     r0, [sp, #0x28]
    LDA     r0, HEIGHT
    str     r0, [sp, #0x04]
    str     r0, [sp, #0x2C]
    mov     r0, #DEPTH
    str     r0, [sp, #0x08]
    str     r0, [sp, #0x30]
    LDA     r0, TILE_W
    str     r0, [sp, #0x0C]            /* init tile width */
    str     r0, [sp, #0x34]            /* enc  tile width */
    LDA     r0, TILE_H
    str     r0, [sp, #0x10]            /* init tile height */
    str     r0, [sp, #0x38]            /* enc  tile height */
    str     r7, [sp, #0x14]            /* src = original pixels; read-only */
/* `#if OUT_OFF` cannot work here: OUT_OFF is a .equ, an assembler symbol the
 * preprocessor cannot see, so the condition was always false and the codec
 * kept writing at the arena base. The header copy then destroyed the first two
 * tiles, which is exactly what the card showed: ten SOI markers instead of
 * twelve, the first at the offset tile 2 would have had from zero. */
#if PHASE == 8
    /* ZERO-COPY: output goes into the FRAME BUFFER, at the same offset the
     * source pixels start at, through the uncached view. Nothing is copied
     * afterwards; the codec's own writes are the file's payload. */
    ldr     r0, [r4, #S_BUF]
    LDA     r1, PIXEL_OFF
    add     r0, r0, r1
    LDA     r1, UNCACHED
    add     r0, r0, r1
    str     r0, [sp, #0x18]            /* init[6] output = the source's place */
#elif PHASE == 5 || PHASE == 6 || PHASE == 7
    LDA     r0, OUT_OFF
    add     r0, r8, r0
    str     r0, [sp, #0x18]            /* init[6] output, past the header */
#else
    str     r8, [sp, #0x18]            /* init[6] compressed output/work */
#endif
    ldr     r0, [r4, #S_TABLE_BASE]
    str     r0, [sp, #0x1C]            /* init[7] size-table base */
    mov     r0, #0
    str     r0, [sp, #0x20]

    mov     r0, sp
    LDA     r12, F_INIT
    blx     r12
    str     r0, [r4, #S_INIT_RET]
    cmp     r0, #1
    bne     encode_init_failed
    mov     r0, #5
    str     r0, [r4, #S_STAGE]

#if PHASE >= 2
    mov     r0, #1
    str     r0, [r4, #S_ENC_STARTED]   /* set BEFORE the call: a hang must show */
    dmb     sy
#endif
    LDA     r12, F_CLOCK
    blx     r12
    str     r0, [r4, #S_T0]
    add     r0, sp, #0x28
    LDA     r12, F_ENC
    blx     r12
    str     r0, [r4, #S_ENC_RET]
    LDA     r12, F_CLOCK
    blx     r12
    str     r0, [r4, #S_T1]
    ldr     r0, [r4, #S_ENC_RET]
    cmp     r0, #1
    bne     encode_call_failed
    mov     r0, #6
    str     r0, [r4, #S_STAGE]

    add     r0, r4, #S_TILE0
    mov     r1, #1
    LDA     r12, F_SIZE
    blx     r12
    str     r0, [r4, #S_SIZE_RET]
    cmp     r0, #1
    bne     encode_size_failed

    add     r3, r4, #S_TILE0
    mov     r2, #TILE_COUNT
    mov     r1, #0
sum_sizes:
    ldr     r0, [r3], #4
    adds    r1, r1, r0
    bcs     encode_size_range
    subs    r2, r2, #1
    bne     sum_sizes
    str     r1, [r4, #S_COMP_SIZE]
    cmp     r1, #0
    beq     encode_size_range
    /* The payload starts at +OUT_OFF, so the room it has is the arena MINUS
     * the header region. Comparing against the whole WORK_CAP was lax by
     * OUT_OFF bytes: an output between the two would pass here and have
     * already overwritten the guard and the size tables. OUT_OFF is 0 for the
     * phases that write at the base, so one expression serves both.
     * This is still an after-the-fact check; it cannot undo a DMA that has
     * already run past the end. */
    LDA     r0, WORK_CAP - OUT_OFF
    cmp     r1, r0
    bhi     encode_size_range
    mov     r0, #TILE_COUNT
    str     r0, [r4, #S_TILE_COUNT]
    mov     r0, #7
    str     r0, [r4, #S_STAGE]
    add     sp, sp, #0x40
#if PHASE == 5 || PHASE == 6 || PHASE == 7
    /* Build the owned header in front of the payload.
     * The writer registers ONE node for the whole file (G3), so header and
     * payload have to be contiguous; the codec already wrote its output at
     * +OUT_OFF, which is why the header goes in front of it rather than after.
     *
     * Writes go through the uncached view, which G4a proved is visible to the
     * card DMA with a barrier and no cache maintenance. */
    push    {r5, r6, r7, r10}
    LDA     r0, UNCACHED
    add     r10, r8, r0

    mov     r5, r6                     /* stock header, source */
    mov     r6, r10                    /* arena base, uncached */
    LDA     r7, HDR_BYTES
hdr_copy:
    ldm     r5!, {r0, r1, r2, r3}
    stm     r6!, {r0, r1, r2, r3}
    subs    r7, r7, #16
    bne     hdr_copy

    /* The IFD the host prepared: every entry but the tile tables, already
     * stripped of StripOffsets/RowsPerStrip/StripByteCounts and carrying
     * Compression=7. Only 24 words change per frame. */
    LDA     r0, TEMPLATE_OFF
    add     r5, r10, r0
    LDA     r0, IFD_AT
    add     r6, r10, r0
    mov     r7, #TEMPLATE_BYTES
tmpl_copy:
    ldm     r5!, {r0, r1, r2, r3}
    stm     r6!, {r0, r1, r2, r3}
    subs    r7, r7, #16
    bne     tmpl_copy

    /* Tile offsets accumulate from the payload start; counts come from the
     * size table F_SIZE filled. */
    LDA     r0, TEMPLATE_META
    add     r0, r10, r0
    ldr     r5, [r0]
    ldr     r6, [r0, #4]
    add     r5, r10, r5
    add     r6, r10, r6
    LDA     r7, OUT_OFF
    add     r1, r4, #S_TILE0
    mov     r2, #TILE_COUNT
patch_tiles:
    str     r7, [r5], #4
    ldr     r3, [r1], #4
    str     r3, [r6], #4
    add     r7, r7, r3
    subs    r2, r2, #1
    bne     patch_tiles
    dsb     sy

    /* Root pointer last, after the IFD and both tables are visible. */
    LDA     r0, IFD_AT
    str     r0, [r10, #4]
    dsb     sy

#if PHASE == 7
    /* COPY-BACK. The file we just built goes into the buffer the writer handed
     * us, and ONLY seg[1] changes. seg[0] still points at the camera's own
     * frame buffer, so we hand nothing of ours to the writer and keep nothing
     * alive past this call. That is the whole point: whether the card reads
     * late, or a 64 MiB aggregation batch defers the transfer by tens of
     * frames, stops being our problem. It is the camera's own buffer and the
     * camera already manages its lifetime -- its ring is sized for exactly
     * that batch.
     *
     * The admission rule is file_bytes <= seg[1]. That length was handed to us
     * and the buffer demonstrably holds a DNG of it, so writing that many
     * bytes is safe by construction: nothing about the allocator, the frame
     * ring or its spacing is assumed. FUN_c037a990 allocates header plus
     * payload and nothing more, so the bound is exact rather than merely
     * conservative, and the 8,704-byte gap between ring slots is the
     * allocator's granularity, not room inside ours.
     *
     * A frame that does not fit is left COMPLETELY alone. Nothing is written,
     * seg is untouched, and the stock frame goes to the card exactly as it
     * would have. That is the RAW fallback, and the decision is made here,
     * after the encode, from the size table - it is known, not predicted.
     * Worst-case lossless output is 4,037,760 bytes against 3,244,544
     * available, so this path is required, not an optimisation. */
    ldr     r1, [r4, #S_COMP_SIZE]
    LDA     r2, OUT_OFF
    adds    r1, r1, r2                 /* file_bytes */
    bcs     copyback_skip
    LDA     r0, RESULT_OFF
    add     r0, r10, r0                /* results live in the block, not state */
    str     r1, [r0]

    /* The copy moves four words at a time, so round up to that unit and check
     * the ROUNDED length against the same bound. The extra bytes land past
     * file_bytes and no one reads them, but they still have to be inside the
     * length we were given. */
    adds    r2, r1, #15
    bcs     copyback_skip
    bic     r2, r2, #15
    ldr     r3, [r4, #S_LEN]           /* seg[1], exactly as handed to us */
    cmp     r2, r3
    bhi     copyback_skip

    /* Both sides through the uncached view. G4a proved CPU writes there reach
     * the card DMA with a barrier and no cache maintenance; the writer's
     * buffer sits in the same 0x4..0x7 quarter as our allocation, so the same
     * +UNCACHED alias rule is applied to it. That is an inference about the
     * alias, and the first live frame is what settles it. */
    LDA     r0, UNCACHED
    add     r5, r8, r0
    ldr     r6, [r4, #S_BUF]
    add     r6, r6, r0
    mov     r7, r2
    LDA     r0, RESULT_OFF
    add     r0, r10, r0
    str     r7, [r0, #4]
#ifndef COPYBACK_NO_COPY
copyback_copy:
    ldm     r5!, {r0, r1, r2, r3}
    stm     r6!, {r0, r1, r2, r3}
    subs    r7, r7, #16
    bne     copyback_copy
    dsb     sy
#else
    /* DIAGNOSTIC: everything except the copy into the camera's frame buffer.
     *
     * copy-back froze the camera on 2026-09-28 with no host USB traffic during
     * the clip and exclusive access - the cleanest of three failures against
     * one success. Two things it does that mirror and compress do not: it
     * copies about 1.5 MB into the CAMERA'S OWN frame buffer from inside the
     * hook, and it shortens seg[1] on a buffer that is still the camera's.
     * Changing the length alone is not obviously the suspect, because compress
     * had to shorten seg[1] too and worked - but it did so on OUR buffer.
     *
     * This variant keeps the length change and drops the copy. The card then
     * writes file_bytes of the untouched stock RAW: a truncated DNG, junk on
     * the card and harmless to the camera. If this freezes, the length change
     * is the cause. If it does not, the copy is.
     */
#endif

    /* ONE timestamp, and it is taken here rather than bracketing the copy
     * alone. S_T1 is the end of the encode, so t_after - S_T1 is EVERYTHING
     * that happens after the encode: the 78,848-byte header copy, the template
     * patch, the payload copy and the stores. That whole figure is what the
     * frame budget needs, and the first C2 run measured none of it.
     *
     * The result pointer is computed once and kept in r2, which is free now
     * that the copy loop is done. Two separate LDA/add pairs would have put the
     * image 24 bytes past its design limit, and raising that limit means
     * growing the cave claim, which re-allocates and needs a reboot. */
    LDA     r2, RESULT_OFF
    add     r2, r10, r2
    LDA     r12, F_CLOCK
    blx     r12
    str     r0, [r2, #0xC]             /* t_after: copy and header included */
    ldr     r1, [r2]                   /* file_bytes, from the block */
#ifndef COPYBACK_NO_LENGTH
    ldr     r0, [r4, #S_R1]
    str     r1, [r0, #4]               /* the ONLY field we change */
    str     r1, [r4, #S_LEN_AFTER]
#else
    /* DIAGNOSTIC, the other half of the pair: the copy happens and seg[1] is
     * left exactly as it was handed to us. The card then writes its full
     * 3,244,544 bytes - our compressed file followed by whatever of the stock
     * raster the copy did not overwrite. Junk on the card, harmless to the
     * camera. If this freezes, the copy into the camera's frame buffer is the
     * cause rather than the length. S_LEN_AFTER records the length we did NOT
     * change, so the record cannot be read as if we had. */
    ldr     r0, [r4, #S_LEN]
    str     r0, [r4, #S_LEN_AFTER]
#endif
    dsb     sy
    mov     r1, #1
    str     r1, [r2, #8]
    dsb     sy
    /* Stage 11/12 are PROGRESS markers only: `complete` overwrites S_STAGE
     * with 8 on the way out, so they survive only a fault between here and
     * there. The verdict the host reads is fit at RESULT_OFF + 8 in the
     * retained block, which nothing else writes.
     * Emulation is what made this visible - the stage read 8 either way. */
    mov     r0, #11
    str     r0, [r4, #S_STAGE]
    b       copyback_done

copyback_skip:
    LDA     r0, RESULT_OFF
    add     r0, r10, r0
    mov     r1, #0
    str     r1, [r0, #4]               /* copied nothing */
    str     r1, [r0, #8]               /* fit = 0 */
    dsb     sy
    ldr     r0, [r4, #S_LEN]
    str     r0, [r4, #S_LEN_AFTER]     /* unchanged, and recorded as unchanged */
    mov     r0, #12
    str     r0, [r4, #S_STAGE]

copyback_done:
    pop     {r5, r6, r7, r10}
#else
    /* Redirect: one node, header plus payload, at its real length. */
    ldr     r0, [r4, #S_R1]
    str     r8, [r0]
    ldr     r1, [r4, #S_COMP_SIZE]
    LDA     r2, OUT_OFF
    add     r1, r1, r2
    str     r1, [r0, #4]
    str     r1, [r4, #S_LEN_AFTER]
    dsb     sy
    pop     {r5, r6, r7, r10}
    mov     r0, #10
    str     r0, [r4, #S_STAGE]
#endif
#endif
    b       finish_checked

encode_init_failed:
    add     sp, sp, #0x40
    FAIL    ERR_INIT, finish_checked
encode_call_failed:
    add     sp, sp, #0x40
    FAIL    ERR_ENCODE, finish_checked
encode_size_failed:
    add     sp, sp, #0x40
    FAIL    ERR_SIZE_CALL, finish_checked
encode_size_range:
    add     sp, sp, #0x40
    FAIL    ERR_SIZE_RANGE, finish_checked
#endif

finish_checked:
    ldr     r0, [r4, #S_WORK_GUARD]
    ldr     r1, [r0]
    str     r1, [r4, #S_GUARD_WORK]
    ldr     r0, [r4, #S_TABLE_BASE]
    ldr     r2, [r0, #0x30]
    str     r2, [r4, #S_GUARD_TABLE]
    ldr     r0, [r4, #S_TABLE_TMP]
    ldr     r3, [r0, #0x30]
    str     r3, [r4, #S_GUARD_TMP]
    LDA     r0, GUARD_MAGIC
    cmp     r1, r0
    cmpeq   r2, r0
    cmpeq   r3, r0
    beq     finish_source
    ldr     r1, [r4, #S_ERROR]
    cmp     r1, #0
    moveq   r1, #ERR_GUARD
    streq   r1, [r4, #S_ERROR]

finish_source:
    ldr     r0, [r6]
    str     r0, [r4, #S_HDR_AFTER]
    ldr     r1, [r7]
    str     r1, [r4, #S_PIX0_AFTER]
    LDA     r2, RAW_BYTES - 4
    ldr     r2, [r7, r2]
    str     r2, [r4, #S_PIXN_AFTER]
    ldr     r3, [r5, #4]
    str     r3, [r4, #S_LEN_AFTER]
#if PHASE == 7 || PHASE == 8
    /* PHASE=7 and PHASE=8 rewrite the SOURCE BUFFER on purpose: that is what copy-back
     * is. The AFTER values are still recorded, but they cannot be compared
     * with the BEFORE ones, because +0x13400 now holds the new IFD and tile
     * tables rather than the first pixels. Emulation caught this: every
     * successful copy-back reported ERR_SOURCE, which would have made release
     * refuse a block that is fine and, worse, put a "the source was damaged"
     * error on the one phase where changing the source is the intent - masking
     * a real one.
     *
     * Nothing is lost by dropping the comparison. The BEFORE values are taken
     * before the encode and the encode finishes before the copy starts, so the
     * ordering already guarantees the codec read an undamaged source. What
     * replaces this check is verify_compressed_dng on the file that reaches
     * the card, which is a far stronger statement than three words matching. */
    b       SOURCE_EXIT
#endif
    ldr     r12, [r4, #S_HDR_BEFORE]
    cmp     r0, r12
    ldreq   r12, [r4, #S_PIX0_BEFORE]
    cmpeq   r1, r12
    ldreq   r12, [r4, #S_PIXN_BEFORE]
    cmpeq   r2, r12
#if PHASE == 5 || PHASE == 6
    /* This phase changes seg[1] on purpose, so comparing it with the stock
     * length marks every SUCCESSFUL compressed write as ERR_SOURCE -- which
     * then makes release refuse to free a block that is perfectly fine. The
     * header and pixel comparisons above still apply: they check that the
     * SOURCE was not damaged, which is what this check is for. */
#else
    LDA     r12, DNG_LEN
    cmpeq   r3, r12
#endif
    beq     SOURCE_EXIT
    ldr     r1, [r4, #S_ERROR]
    cmp     r1, #0
    moveq   r1, #ERR_SOURCE
    streq   r1, [r4, #S_ERROR]
    b       SOURCE_EXIT

/* PHASE=4 never frees: the flush reads the buffer after we return, so the
 * release path is not assembled for it either. */
/* PHASE 11 is excluded for the same reason, and it was NOT on 2026-09-29: the
 * release path ran on its first frame, F_FREE took the 4 MiB block back, and
 * every later frame read its record area out of memory the allocator had given
 * to someone else. The values looked like pixels because they were. A phase
 * that keeps a block across frames must never fall into this path. */
#if PHASE >= 2 && PHASE != 4 && PHASE != 5 && PHASE != 7 \
    && PHASE != 8 && PHASE != 9 && PHASE != 11
after_source:
#if PHASE == 3 || PHASE == 6
    /* One 16-byte record per frame. r5/r6/r7 are dead after finish_source. */
    ldr     r5, [r4, #S_FRAMES_DONE]
    LDA     r6, LOG
    /* The rows are a ring over the last 8 frames: a run lasts as long as the
     * recording and 256 bytes cannot hold a row per frame. The worst-case
     * trackers below cover every frame, whatever the run length. */
    and     r7, r5, #RING_MASK
    add     r6, r6, r7, lsl #4
    ldr     r0, [r4, #S_T1]
    ldr     r1, [r4, #S_T0]
    sub     r0, r0, r1                 /* 1 MHz tick; a wrap subtracts correctly */
    str     r0, [r6, #0]
    ldr     r0, [r4, #S_COMP_SIZE]
    str     r0, [r6, #4]
    /* The segment buffer and its first pixel word identify WHICH frame this
     * record describes. Without them, repeated identical sizes cannot be told
     * apart from re-encoding one frame. The encode return and error code are
     * already in the state block, and an error ends the loop anyway. */
    ldr     r0, [r4, #S_BUF]
    str     r0, [r6, #8]
    ldr     r0, [r4, #S_PIX0_BEFORE]
    str     r0, [r6, #12]

    /* Worst case over EVERY frame, not just the rows kept above. This is what
     * decides feasibility; a mean would hide the frame that misses. */
    LDA     r6, LOG + AGG_OFF
    ldr     r2, [r4, #S_T1]
    ldr     r1, [r4, #S_T0]
    sub     r2, r2, r1
    ldr     r0, [r6, #AGG_MAX_US]
    cmp     r2, r0
    strhi   r2, [r6, #AGG_MAX_US]
    ldr     r3, [r4, #S_COMP_SIZE]
    ldr     r0, [r6, #AGG_MAX_BYTES]
    cmp     r3, r0
    strhi   r3, [r6, #AGG_MAX_BYTES]
    LDA     r0, BUDGET_US
    cmp     r2, r0
    ldrhs   r0, [r6, #AGG_OVER_BUDGET]
    addhs   r0, r0, #1
    strhs   r0, [r6, #AGG_OVER_BUDGET]

    add     r5, r5, #1
    str     r5, [r4, #S_FRAMES_DONE]
    dmb     sy

    /* Two independent ceilings. S_COUNT counts every hook entry, so a scribbled
     * frames_done cannot on its own keep this going. */
    ldr     r0, [r4, #S_ERROR]
    cmp     r0, #0
    bne     final_frame                /* stop at the first error, never loop */
    cmp     r5, #FRAMES
    bhs     final_frame
    ldr     r0, [r4, #S_COUNT]
    cmp     r0, #FRAMES * 2
    bhs     final_frame
    /* Re-arm last, so every risky instruction above ran with the site
     * unhooked, exactly as in the one-shot design. */
    LDA     r0, HOOK_SITE
    LDA     r1, HOOK_ARMED
    str     r1, [r0]
    dsb     sy
    isb
    b       complete

final_frame:
#endif
#if PHASE == 6
    /* A sustained compressed run never frees: the flush is still reading the
     * last frame when we return, and the host releases the block once the
     * recording has stopped. */
    b       complete
#elif KEEP_BLOCK
    /* Deliberate retention: the payload is the point of this run, and the
     * release code is not merely skipped here -- it is not assembled, so no
     * path through this image can reach F_FREE. */
    b       complete
#else
    /* Only a block we adopted after every range and guard check may be freed,
     * and only when the codec cannot still be using it. */
    ldr     r0, [r4, #S_ADOPTED]
    cmp     r0, #1
    bne     complete
    ldr     r0, [r4, #S_ENC_STARTED]
    cmp     r0, #0
    beq     release_block
    ldr     r0, [r4, #S_ENC_RET]
    cmp     r0, #1
    beq     release_block
    mov     r0, #ERR_RETAINED
    str     r0, [r4, #S_FREE_ERROR]    /* deliberate leak; power-cycle to reclaim */
    b       complete

release_block:
    ldr     r0, [r4, #S_ALLOCATOR]
    ldr     r1, [r4, #S_HANDLE]
    cmp     r0, #0
    beq     complete
    cmp     r1, #0
    beq     complete
    LDA     r12, F_FREE
    blx     r12
    LDA     r0, FREE_MAGIC
    str     r0, [r4, #S_FREE_DONE]
    b       complete
#endif
#endif

bad_kind:       FAIL ERR_KIND, complete
bad_seg:        FAIL ERR_SEG_NULL, complete
bad_length:     FAIL ERR_LENGTH, complete
bad_buf:        FAIL ERR_BUF_NULL, complete
bad_tiff:       FAIL ERR_TIFF, complete
bad_src_align:  FAIL ERR_SRC_ALIGN, complete
bad_allocator:  FAIL ERR_ALLOCATOR, finish_source
bad_alloc:      FAIL ERR_ALLOC, finish_source
bad_alias:      FAIL ERR_ALIAS, finish_source
bad_align:      FAIL ERR_ALIGN, finish_source
bad_range:      FAIL ERR_RANGE, finish_source
bad_overlap:    FAIL ERR_OVERLAP, finish_source
#if PHASE >= 2
bad_proof:        FAIL ERR_PROOF, complete
bad_stale_guard:  FAIL ERR_STALE_GUARD, finish_source
/* None of these three may free anything: S_ADOPTED is still 0 when they are
 * taken, which is what the release path requires. */
#endif
#if PHASE == 9
bad_life:         FAIL ERR_LIFE, finish_source
bad_own:          FAIL ERR_OWN, finish_source

/* Verify one lifecycle record against the block it claims to describe.
 *   r0 = the record, in the uncached view
 *   r1 = the handle it has to name
 * Clobbers r2, r3, r12. Returns only on success: there is nothing a caller
 * could usefully do with a bad record, and a return code that a caller might
 * forget to test is worse than no return at all.
 *
 * Two questions, and they are different. The RECORD says a block was acquired
 * and is still held. The MARK, inside that block's own memory and containing
 * that block's own address, says the memory we are about to write is the
 * memory the record describes. A handle with the right shape has neither.
 */
verify_life:
    ldr     r2, [r0, #L_MAGIC]
    LDA     r12, LIFE_MAGIC
    cmp     r2, r12
    ldreq   r2, [r0, #L_STATE]
    cmpeq   r2, #LIFE_HELD
    ldreq   r2, [r0, #L_HANDLE]
    cmpeq   r2, r1
    bne     bad_life
    add     r2, r1, #UNCACHED
    add     r2, r2, #OWN_OFF
    ldr     r3, [r2]
    LDA     r12, OWN_MAGIC
    cmp     r3, r12
    ldreq   r3, [r2, #4]
    cmpeq   r3, r1                     /* the block names itself */
    bne     bad_own
    bx      lr
#endif

complete:
    mov     r0, #8
    str     r0, [r4, #S_STAGE]
    LDA     r0, DONE_MAGIC
    str     r0, [r4, #S_DONE]

tail_call:
    pop     {r0, r1, r2, r3, r4, r5, r6, r7, r8, r10, r12, lr}
    ldr     pc, [pc, #-4]
    .word   REAL_WRITE
