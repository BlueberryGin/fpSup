/* One sup's Quick Set layers (QS_SHARE.md §2, §4.1, §4.2): one header, three
 * entries, three replays.
 *
 *   C05E6400  parse one NBU record      qs_record(reader, header)
 *   C05D90F0  load one layout           qs_layout(layout, header)
 *   C05E84D8  load an NBR pack          qs_load(args[4], header)
 *
 * Each site is a Thumb B.W to a cave veneer (ldr.w pc,[pc]) that enters here
 * in ARM state, as a call. The header is found with adr (position
 * independent). C05E6400 and C05D90F0 take one argument (og3k_ui.S, camera),
 * so r1 carries the header; C05E84D8 takes four (OG's ensure_ui_pack calls it
 * with (context, pack, bytes, 0)), so they go to the C half as an array.
 * The firmware may call these at sp = 4 mod 8: each entry aligns sp before
 * the C half runs (which keeps it: -mstackrealign) and puts it back.
 *
 * The header's words are qs_layer.h QS_H_*; the entry and replay offsets are
 * QS_ENTRY_* / QS_REPLAY_* there, checked by qs_build.py against this file.
 * A replay is the innermost layer's "next": the bytes the B.W replaced, then
 * the rest of the stock function. */
.syntax unified
.arm
.text

.global qs_layer_head
qs_layer_head:
qs_hdr:
    .rept 40
    .word 0
    .endr
    /* v2 (2026-10-07): the header describes itself -- +160 its length, then
     * each entry's offset; every entry is preceded by its offset too. Readers
     * find a header from any entry and never assume this build's layout. */
    .word   qs_layer_head_end - qs_hdr      @ +160 H_LEN
    .word   qs_entry_record - qs_hdr        @ +164
    .word   qs_entry_layout - qs_hdr        @ +168
    .word   qs_entry_load - qs_hdr          @ +172
qs_layer_head_end:

    .word   qs_entry_record - qs_hdr
.global qs_entry_record
qs_entry_record:                        @ the firmware may call at sp = 4 mod 8:
    push    {r4, lr}                    @ the C half runs at an 8-aligned sp
    mov     r4, sp
    bic     ip, r4, #7
    mov     sp, ip
    adr     r1, qs_hdr
    ldr     ip, [r1, #48]
    blx     ip
    mov     sp, r4
    pop     {r4, pc}

    .word   qs_entry_layout - qs_hdr
.global qs_entry_layout
qs_entry_layout:
    push    {r4, lr}
    mov     r4, sp
    bic     ip, r4, #7
    mov     sp, ip
    adr     r1, qs_hdr
    ldr     ip, [r1, #64]
    blx     ip
    mov     sp, r4
    pop     {r4, pc}

    .word   qs_entry_load - qs_hdr
.global qs_entry_load
qs_entry_load:
    push    {r0-r3, r4, lr}             @ the four arguments, as an array
    mov     r4, sp
    bic     ip, r4, #7
    mov     sp, ip
    mov     r0, r4
    adr     r1, qs_hdr
    ldr     ip, [r1, #76]
    blx     ip
    add     sp, r4, #16
    pop     {r4, pc}

.align 2
.thumb
.global qs_replay_record
.thumb_func
qs_replay_record:                       @ C05E6400: push.w {r4-r11, lr}
    push.w  {r4-r11, lr}
    movw    ip, #0x6405
    movt    ip, #0xc05e
    bx      ip

.align 2
.global qs_replay_layout
.thumb_func
qs_replay_layout:                       @ C05D90F0: push.w {r4-r7, r10, r11, lr}
    push.w  {r4-r7, r10, r11, lr}
    movw    ip, #0x90f5
    movt    ip, #0xc05d
    bx      ip

.align 2
.global qs_replay_load
.thumb_func
qs_replay_load:                         @ C05E84D8: push {lr}; sub sp, #24
    push    {lr}
    sub     sp, #24
    movw    ip, #0x84dd
    movt    ip, #0xc05e
    bx      ip

.align 2
.global qs_layer_end
qs_layer_end:
