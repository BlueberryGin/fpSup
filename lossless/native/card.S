.syntax unified
.arm
.text
@ entry is at offset 0; not global, so every adr to it resolves here
/* ============================================================================
 * fpLossless TEST card: launcher and hook shims.
 * ============================================================================
 *
 * Built by lossless/build_card.py, which appends the compiled C (card.c and
 * the modules it links, one translation unit, no relocations) at `c_base` and
 * passes every C entry's offset in. Nothing here names an address of its own
 * choosing in the cave: the veneers come off the cave's bump allocator at
 * boot, as gyro's do (writer_core.inc.S s_hook), and the hook sites are
 * journaled by stage2 because the builder declares each as a 4-byte section
 * holding the firmware's own word (SUP_BUILD_RULES §4).
 *
 * THE LAUNCHER runs where the file landed (a destination-zero section). It
 * asks the allocator for its own block, copies the whole blob there, zeroes
 * the state after it, publishes D/I, calls the resident init -- and only then
 * arms, all or nothing: every site must still hold its stock word and the cave
 * must have room for every veneer, or not one site is written.
 * ========================================================================== */

.equ H_GET,        0xC001D740    /* (desc, class, size, 0): gsup_launch.S    */
.equ H_ADDR,       0xC001D7F0    /* (desc) -> address                         */
.equ USER_CLASS,   0             /* NOT class 10: that is the frames' pool    */
.equ F_DCACHE,     0xC000E91C
.equ F_ICACHE,     0xC000EABC
.equ CAVE_BUMP,    0xC072E060
.equ CAVE_END,     0xC072EFB4    /* the park stub: nothing may reach it       */
.equ VENEER_LDR,   0xE51FF004    /* ldr pc, [pc, #-4]                          */
.equ CARD_MAGIC,   0x43504C46    /* "FLPC": how a host finds the state        */

.equ SITE_REC,     0xC03A33C8
.equ STOCK_REC,    0xEBFFFC1A    /* bl C03A2438                               */
.equ SITE_ARRIVE,  0xC038BFF0
.equ STOCK_ARRIVE, 0xE12FFF33    /* blx r3 -- the creator's vtable call       */
.equ SITE_STOP,    0xC0398D88
.equ STOCK_STOP,   0xE92D49F0    /* push {r4-r8, fp, lr}: the entry           */
.equ SITE_FLUSH,   0xC03A5490
.equ STOCK_FLUSH,  0xEB0BD652    /* bl C069ADE0                               */
.equ SITE_PLAY,    0xC05C0EA4    /* the player's frame read, after its f_read */
.equ STOCK_PLAY,   0xE595201C    /* ldr r2, [r5, #0x1c]                       */
.equ SITE_CLIP,    0xC05BDDAC    /* clip open: the first frame's size stored  */
.equ STOCK_CLIP,   0xE58430A0    /* str r3, [r4, #0xa0]                       */
.equ SITE_END,     0xC05C2E90    /* the player's pool freed: entry            */
.equ STOCK_END,    0xE92D4070    /* push {r4, r5, r6, lr}                     */
.equ END_RESUME,   0xC05C2E94
.equ SITE_POOL,    0xC05C2D10    /* the player's pool made: entry             */
.equ STOCK_POOL,   0xE92D44F0    /* push {r4, r5, r6, r7, sl, lr}             */
.equ POOL_RESUME,  0xC05C2D14
.equ CREATOR_ENQ,  0xC037DD50
.equ STOP_RESUME,  0xC0398D8C
.equ REAL_FLUSH,   0xC069ADE0

/* Passed in by the builder: BLOB_LEN, BLOCK_BYTES, STATE_OFF and the five C
 * entry offsets from c_base (Thumb, bit 0 set). */

.macro LDA reg, value
    movw    \reg, #:lower16:\value
    movt    \reg, #:upper16:\value
.endm

/* The resident address of a label: its staging address plus r11, the
 * distance from where the file landed to the block. The pc-relative distance
 * is a word beside the code, so any distance assembles (adr's immediate ran
 * out once the player's three shims were added). */
.macro RESIDENT reg, label
    ldr     \reg, 8f
9:  add     \reg, pc, \reg
    add     \reg, \reg, r11
    b       7f
8:  .word   \label - 9b - 8
7:
.endm

entry:
    push    {r3, r4, r5, r6, r7, r8, r9, r10, r11, lr}   @ ten: 8-aligned
    sub     sp, sp, #16                 @ the allocator descriptor, kept:
                                        @ this block is never freed
    mov     r0, sp
    mov     r1, #USER_CLASS
    LDA     r2, BLOCK_BYTES
    mov     r3, #0
    LDA     ip, H_GET
    blx     ip
    mov     r0, sp
    LDA     ip, H_ADDR
    blx     ip
    movs    r5, r0                      @ r5 = the resident base
    beq     out                         @ refused: stand down, camera is stock
    tst     r5, #7
    bne     out

    adr     r6, entry
    sub     r11, r5, r6                 @ r11 = resident - staging

    /* copy the whole blob -- this code included -- to the block */
    mov     r7, r5
    LDA     r8, BLOB_LEN
1:  ldr     ip, [r6], #4
    str     ip, [r7], #4
    subs    r8, r8, #4
    bne     1b
    /* zero the rest: the card state lives at STATE_OFF */
    LDA     r8, BLOCK_BYTES
    add     r8, r5, r8
    mov     ip, #0
2:  cmp     r7, r8
    strlo   ip, [r7], #4
    blo     2b

    /* the resident words the shims read: the state, and each C entry */
    LDA     r6, STATE_OFF
    add     r6, r5, r6                  @ r6 = the card state
    RESIDENT r4, c_base                 @ r4 = the resident C text
    RESIDENT r0, g_card
    str     r6, [r0]
    RESIDENT r0, w_rec
    LDA     ip, OFF_REC
    add     ip, r4, ip
    str     ip, [r0]
    RESIDENT r0, w_arrive
    LDA     ip, OFF_ARRIVE
    add     ip, r4, ip
    str     ip, [r0]
    RESIDENT r0, w_stop
    LDA     ip, OFF_STOP
    add     ip, r4, ip
    str     ip, [r0]
    RESIDENT r0, w_flush
    LDA     ip, OFF_FLUSH
    add     ip, r4, ip
    str     ip, [r0]
    RESIDENT r0, w_task
    LDA     ip, OFF_TASK
    add     ip, r4, ip
    str     ip, [r0]
    RESIDENT r0, w_play
    LDA     ip, OFF_PLAY
    add     ip, r4, ip
    str     ip, [r0]
    RESIDENT r0, w_clip
    LDA     ip, OFF_CLIP
    add     ip, r4, ip
    str     ip, [r0]
    RESIDENT r0, w_end
    LDA     ip, OFF_END
    add     ip, r4, ip
    str     ip, [r0]
    RESIDENT r0, w_pool
    LDA     ip, OFF_POOL
    add     ip, r4, ip
    str     ip, [r0]

    LDA     ip, F_DCACHE
    blx     ip
    LDA     ip, F_ICACHE
    blx     ip

    /* the resident init, in the resident copy */
    mov     r0, r6
    mov     r1, r5
    LDA     r2, BLOCK_BYTES
    RESIDENT r3, task_shim              @ the codec task's entry
    LDA     ip, OFF_INIT
    add     ip, r4, ip
    blx     ip
    cmp     r0, #0
    bne     out

    /* ---- arm: every site stock, or none ---------------------------- */
    LDA     r0, SITE_REC
    LDA     r1, STOCK_REC
    ldr     r2, [r0]
    cmp     r2, r1
    bne     out
    LDA     r0, SITE_ARRIVE
    LDA     r1, STOCK_ARRIVE
    ldr     r2, [r0]
    cmp     r2, r1
    bne     out
    LDA     r0, SITE_STOP
    LDA     r1, STOCK_STOP
    ldr     r2, [r0]
    cmp     r2, r1
    bne     out
    LDA     r0, SITE_FLUSH
    LDA     r1, STOCK_FLUSH
    ldr     r2, [r0]
    cmp     r2, r1
    bne     out
    LDA     r0, SITE_PLAY
    LDA     r1, STOCK_PLAY
    ldr     r2, [r0]
    cmp     r2, r1
    bne     out
    LDA     r0, SITE_CLIP
    LDA     r1, STOCK_CLIP
    ldr     r2, [r0]
    cmp     r2, r1
    bne     out
    LDA     r0, SITE_END
    LDA     r1, STOCK_END
    ldr     r2, [r0]
    cmp     r2, r1
    bne     out
    LDA     r0, SITE_POOL
    LDA     r1, STOCK_POOL
    ldr     r2, [r0]
    cmp     r2, r1
    bne     out

    /* 80 bytes off the bump: four veneers, the state record (+32, where a
     * host looks for it), and the player's four veneers after it (+48) */
    LDA     r7, CAVE_BUMP
    ldr     r8, [r7]                    @ r8 = our cave block
    tst     r8, #3
    bne     out
    add     r9, r8, #80
    LDA     r10, CAVE_END
    cmp     r9, r10
    bhi     out                         @ the cave is full: arm nothing
    cmp     r8, #0xC0000000
    blo     out
    str     r9, [r7]

    LDA     r0, VENEER_LDR
    str     r0, [r8, #0]
    RESIDENT r1, rec_shim
    str     r1, [r8, #4]
    str     r0, [r8, #8]
    RESIDENT r1, arrive_shim
    str     r1, [r8, #12]
    str     r0, [r8, #16]
    RESIDENT r1, stop_shim
    str     r1, [r8, #20]
    str     r0, [r8, #24]
    RESIDENT r1, flush_shim
    str     r1, [r8, #28]
    LDA     r0, CARD_MAGIC
    str     r0, [r8, #32]               @ how a host finds the state
    str     r6, [r8, #36]
    str     r5, [r8, #40]               @ and the resident base
    LDA     r0, BLOB_LEN
    str     r0, [r8, #44]
    LDA     r0, VENEER_LDR
    str     r0, [r8, #48]
    RESIDENT r1, play_shim
    str     r1, [r8, #52]
    str     r0, [r8, #56]
    RESIDENT r1, clip_shim
    str     r1, [r8, #60]
    str     r0, [r8, #64]
    RESIDENT r1, end_shim
    str     r1, [r8, #68]
    str     r0, [r8, #72]
    RESIDENT r1, pool_shim
    str     r1, [r8, #76]

    LDA     ip, F_DCACHE
    blx     ip
    LDA     ip, F_ICACHE
    blx     ip

    /* the four site words, last */
    LDA     r0, SITE_REC
    add     r1, r8, #0
    mov     r2, #0xEB000000             @ bl
    bl      encode
    LDA     r0, SITE_ARRIVE
    add     r1, r8, #8
    mov     r2, #0xEB000000             @ bl: sets lr exactly as blx r3 did
    bl      encode
    LDA     r0, SITE_STOP
    add     r1, r8, #16
    mov     r2, #0xEA000000             @ b: the caller's lr must survive
    bl      encode
    LDA     r0, SITE_FLUSH
    add     r1, r8, #24
    mov     r2, #0xEB000000             @ bl
    bl      encode
    LDA     r0, SITE_PLAY
    add     r1, r8, #48
    mov     r2, #0xEB000000             @ bl: lr was spent on the f_read call
    bl      encode
    LDA     r0, SITE_CLIP
    add     r1, r8, #56
    mov     r2, #0xEB000000             @ bl: lr was spent on the size call
    bl      encode
    LDA     r0, SITE_END
    add     r1, r8, #64
    mov     r2, #0xEA000000             @ b: the caller's lr must survive
    bl      encode
    LDA     r0, SITE_POOL
    add     r1, r8, #72
    mov     r2, #0xEA000000             @ b: the caller's lr must survive
    bl      encode

    LDA     ip, F_DCACHE
    blx     ip
    LDA     ip, F_ICACHE
    blx     ip

out:
    add     sp, sp, #16
    mov     r0, #0
    pop     {r3, r4, r5, r6, r7, r8, r9, r10, r11, pc}

/* encode: [r0] = r2 | ((r1 - r0 - 8) >> 2 & 0xFFFFFF). Signed: the veneer
 * may lie below the site. */
encode:
    sub     r3, r1, r0
    sub     r3, r3, #8
    asr     r3, r3, #2
    bic     r3, r3, #0xFF000000
    orr     r3, r3, r2
    str     r3, [r0]
    bx      lr

/* ---- the shims, reached through the veneers ------------------------------ */

/* C03A33C8: bl C03A2438(camera, request). Tail call with the state third;
 * lr is the firmware's, so the body returns straight to C03A3398. */
rec_shim:
    ldr     r2, g_card
    ldr     ip, w_rec
    bx      ip

/* C038BFF0: blx r3 with r3 = creator->vtable[0x10]. Only the creator enqueue
 * we know is ours to take; anything else is called exactly as it would have
 * been, with lr already pointing after the site. */
arrive_shim:
    LDA     ip, CREATOR_ENQ
    cmp     r3, ip
    bxne    r3
    ldr     r3, g_card
    ldr     ip, w_arrive
    bx      ip

/* C0398D88 entry: our stop first, then the displaced first instruction, then
 * the rest of the original. Six registers: the stack stays 8-aligned. */
stop_shim:
    push    {r0, r1, r2, r3, ip, lr}
    mov     r1, r0                      @ the stop kind: 1 and 9 are checks
    ldr     r0, g_card
    ldr     ip, w_stop
    blx     ip
    pop     {r0, r1, r2, r3, ip, lr}
    push    {r4, r5, r6, r7, r8, fp, lr}   @ STOCK_STOP, executed here
    ldr     pc, stop_resume

/* C03A5490: bl C069ADE0(writer, ...). Our flush first, every argument kept,
 * then the real flush with the firmware's lr. */
flush_shim:
    push    {r0, r1, r2, r3, ip, lr}
    ldr     r1, g_card
    ldr     ip, w_flush
    blx     ip
    pop     {r0, r1, r2, r3, ip, lr}
    ldr     pc, flush_real

/* C05C0EA4, right after the player's f_read of a frame file: r5 is its slot
 * (+0x14 buffer, +0x18 capacity, +0x1c bytes read). Ours first, then the
 * displaced load, back to C05C0EA8 through the lr the site's bl set. */
play_shim:
    push    {r0, r1, r2, r3, ip, lr}
    mov     r0, r5
    ldr     r1, g_card
    ldr     ip, w_play
    blx     ip
    pop     {r0, r1, r2, r3, ip, lr}
    ldr     r2, [r5, #0x1c]             @ STOCK_PLAY, executed here
    bx      lr

/* C05BDDAC: str r3, [r4, #0xa0] -- r3 the first frame file's size, r4 the
 * player, r6 the clip's file descriptor. The size stored is ours to say. */
clip_shim:
    push    {r0, r1, r2, r3, ip, lr}
    ldr     r0, g_card
    mov     r1, r4
    mov     r2, r6
    ldr     ip, w_clip
    blx     ip
    str     r0, [sp, #12]               @ becomes r3
    pop     {r0, r1, r2, r3, ip, lr}
    str     r3, [r4, #0xa0]             @ STOCK_CLIP, executed here
    bx      lr

/* C05C2E90 entry: the player's buffers are about to be freed. Ours (the
 * decoder's scratch) first, then the displaced push and the original. */
end_shim:
    push    {r0, r1, r2, r3, ip, lr}
    ldr     r0, g_card
    ldr     ip, w_end
    blx     ip
    pop     {r0, r1, r2, r3, ip, lr}
    push    {r4, r5, r6, lr}            @ STOCK_END, executed here
    ldr     pc, end_resume

/* C05C2D10 entry: the player is about to make its buffer pool (taking the
 * RAW pool until it is empty). The decoder's scratch first, then the
 * displaced push and the original. */
pool_shim:
    push    {r0, r1, r2, r3, ip, lr}
    ldr     r0, g_card
    ldr     ip, w_pool
    blx     ip
    pop     {r0, r1, r2, r3, ip, lr}
    push    {r4, r5, r6, r7, sl, lr}    @ STOCK_POOL, executed here
    ldr     pc, pool_resume

/* The codec task's entry (tk_sta_tsk). The body takes the state and never
 * returns: a task cannot be ended in this firmware. */
task_shim:
    ldr     r0, g_card
    ldr     ip, w_task
    bx      ip

    .align  2
g_card:      .word 0
w_rec:       .word 0
w_arrive:    .word 0
w_stop:      .word 0
w_flush:     .word 0
w_task:      .word 0
w_play:      .word 0
w_clip:      .word 0
w_end:       .word 0
w_pool:      .word 0
stop_resume: .word STOP_RESUME
flush_real:  .word REAL_FLUSH
end_resume:  .word END_RESUME
pool_resume: .word POOL_RESUME
    .align  3
c_base:
    /* the compiled C is appended here by build_card.py */
