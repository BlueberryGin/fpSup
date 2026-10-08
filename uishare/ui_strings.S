/* One layer of the nested string hooks (uishare/NESTED_HOOKS.md, 2026-10-04).
 *
 * ui_pool.c copies these words, unchanged, to the front of a block it owns,
 * fills the header and hangs the three entries on the firmware's string
 * functions through cave veneers.  Each entry answers for this layer's own
 * strings and passes everything else, arguments untouched, to the next layer
 * inward -- or, in the innermost layer, does what the firmware function did
 * (its first word is a B.W now, so it cannot be called).
 *
 *   C05E5B58 resolve(reader r0, offset r1) -> string or 0
 *   C05E61C8 owns(reader r0, pointer r1)   -> 1 if the pointer is borrowed
 *   C05E61E0 remain(reader r0, pointer r1) -> bytes from the pointer to the end
 *
 * Registers: the firmware functions change only r0, r2 and the flags, so a
 * layer changes nothing else either: r1, r3, r4, ip, lr and sp come back as
 * they went in (r3/r4 are borrowed on the stack).  A miss is a tail call: the
 * next entry goes into a stack slot and is popped into pc after r3/r4 are
 * restored, so a chain of layers never deepens the stack.
 *
 * Position independent: the header is found with adr.  gen_strings_code.py
 * turns the assembled words into ui_strings_code.h. */
.syntax unified
.arm
.text

.equ H_NAME,     0              @ 8 bytes: whose layer (NESTED_HOOKS.md §4)
.equ H_VERSION,  8              @ 2
.equ H_BASE,     12             @ first private offset
.equ H_COUNT,    16
.equ H_LO,       20             @ this layer's string bytes are [lo, hi)
.equ H_HI,       24
.equ H_NEXT_RES, 28             @ next layer inward; 0 = the firmware's rule
.equ H_NEXT_OWN, 32
.equ H_NEXT_REM, 36
.equ H_TABLE,    40             @ address of count string addresses
/* v3 (2026-10-06, QS_SHARE.md §4.3): the Quick Set "current format" images.
 * QSID = the first of three offsets (QS/SET/FONT) the first QS sup handed
 * out (0 = this layer has none); this layer answers them with ITS three
 * names (QSNAMES = address of three string addresses) while the resolution
 * value is QSENUM, and passes them on otherwise. */
.equ H_QSID,     44
.equ H_QSENUM,   48
.equ H_QSNAMES,  52
/* v5 (2026-10-07): the layer describes itself, so the code can change without
 * a version change. +56 the header's length in bytes; +60/+64/+68 each entry's
 * offset from the header. Every entry is also preceded by one word: its own
 * offset from the header -- from a site's veneer you find the header as
 * entry - [entry - 4], then check the header's offset agrees. Readers use the
 * length before any field past +72; new fields go at the end. */
.equ H_LEN,      56
.equ H_OFF_RES,  60
.equ H_OFF_OWN,  64
.equ H_OFF_REM,  68
/* The resolution value: the setting itself, in the menu-setting storage's
 * working bank (MenuSettingStorage 0xC31AE3B0; bank +0x4F0C, or +0xA7D4 when
 * the byte at +0xB19C is set; resolution at bank +0x790 -- the setter's
 * MenuItem<eXC_MenuMovRecSize>::v9 writes it, v4 C00A8BA0 finds it).
 * NOT the property object's +0x14 (0xC31ACC30): that is a cache the getter
 * refreshes when it is read, one change behind on the camera (2026-10-07). */
.equ RES_BANK_SEL, 0xC31B954C   @ 0xC31AE3B0 + 0xB19C (byte)
.equ RES_RAW_A,    0xC31B3A4C   @ 0xC31AE3B0 + 0x4F0C + 0x790
.equ RES_RAW_B,    0xC31B9314   @ 0xC31AE3B0 + 0xA7D4 + 0x790

@ hdr is local: adr to a global label would leave a relocation
hdr:
    .word 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0

/* The stack while borrowed. The firmware does not always call these at an
 * 8-aligned sp (callers with an odd frame, e.g. push {r4, r5, lr}, reach
 * C05E5B58 at sp = 4 mod 8; QS_SHARE.md / NESTED_HOOKS.md §4), and an
 * interrupt stores the context with STRD on this stack (arm-stack-alignment):
 * so the frame is 16 bytes on an aligned entry and 20 on a misaligned one,
 * and from the push on, sp is 8-aligned in absolute terms.
 *   [sp] r3   [sp+4] r4   [sp+8] the tail-call slot   [sp+12] the caller's sp
 *   ([sp+16] padding on a misaligned entry)
 * LEAVE and NEXT put the caller's sp back exactly (r2 is the scratch: the
 * firmware's functions change r0, r2 and the flags, nothing else). Until
 * 2026-10-06 the frame was 12 bytes: sp 4 mod 8 for the whole layer. */
.macro ENTER
    tst     sp, #4
    subeq   sp, sp, #8
    subne   sp, sp, #12
    push    {r3, r4}
    add     r3, sp, #16
    addne   r3, r3, #4                  @ the flags are still tst's
    str     r3, [sp, #12]
    adr     r3, hdr
.endm
.macro LEAVE
    ldr     r2, [sp, #12]
    pop     {r3, r4}
    mov     sp, r2
.endm
.macro NEXT field, firmware
    ldr     r4, [r3, #\field]
    cmp     r4, #0
    beq     \firmware
    str     r4, [sp, #8]
    ldr     r2, [sp, #12]
    tst     r2, #4                      @ which frame: 16 or 20 bytes
    pop     {r3, r4}
    ldreq   pc, [sp], #8                @ the next layer, r0/r1 as they came, at the caller's sp
    ldrne   pc, [sp], #12
.endm

    .word   resolve - hdr               @ back to the header
.global resolve
resolve:
    ENTER
    ldr     r2, [r3, #H_QSID]
    cmp     r2, #0
    beq     resolve_range
    sub     r2, r1, r2                  @ which of the three; huge when below
    cmp     r2, #3
    bhs     resolve_range
    movw    r4, #:lower16:RES_BANK_SEL
    movt    r4, #:upper16:RES_BANK_SEL
    ldrb    r2, [r4]
    cmp     r2, #0
    movweq  r4, #:lower16:RES_RAW_A
    movteq  r4, #:upper16:RES_RAW_A
    movwne  r4, #:lower16:RES_RAW_B
    movtne  r4, #:upper16:RES_RAW_B
    ldr     r4, [r4]                    @ the resolution value now
    ldr     r2, [r3, #H_QSENUM]
    cmp     r4, r2
    bne     resolve_range               @ not this sup's format now: the range, then inward
    ldr     r2, [r3, #H_QSID]
    sub     r2, r1, r2
    ldr     r4, [r3, #H_QSNAMES]
    ldr     r0, [r4, r2, lsl #2]
    LEAVE
    bx      lr
resolve_range:
    ldr     r2, [r3, #H_BASE]
    sub     r2, r1, r2                  @ index; huge when below base
    ldr     r4, [r3, #H_COUNT]
    cmp     r2, r4
    bhs     resolve_miss
    ldr     r4, [r3, #H_TABLE]
    ldr     r0, [r4, r2, lsl #2]
    LEAVE
    bx      lr
resolve_miss:
    NEXT    H_NEXT_RES, resolve_firmware
resolve_firmware:                       @ C05E5B58, instruction for instruction, then LEAVE
    cmn     r1, #1                      @ FFFFFFFF: Z and C set
    ldrne   r2, [r0, #0x10]
    cmpne   r1, r2
    movhs   r0, #0
    ldrlo   r0, [r0, #0x14]
    addlo   r0, r1, r0
    LEAVE
    bx      lr

    .word   owns - hdr               @ back to the header
.global owns
owns:
    ENTER
    ldr     r2, [r3, #H_LO]
    ldr     r4, [r3, #H_HI]
    cmp     r1, r2
    blo     owns_miss
    cmp     r1, r4
    bhs     owns_miss
    mov     r0, #1
    LEAVE
    bx      lr
owns_miss:
    NEXT    H_NEXT_OWN, owns_firmware
owns_firmware:                          @ C05E61C8's rule, then LEAVE
    ldr     r2, [r0, #0x14]
    cmp     r2, r1
    bhi     owns_no
    ldr     r0, [r0, #0x10]
    add     r0, r2, r0
    cmp     r0, r1
    movhi   r0, #1
    bhi     owns_done
owns_no:
    mov     r0, #0
owns_done:
    LEAVE
    bx      lr

    .word   remain - hdr               @ back to the header
.global remain
remain:
    ENTER
    ldr     r2, [r3, #H_LO]
    ldr     r4, [r3, #H_HI]
    cmp     r1, r2
    blo     remain_miss
    cmp     r1, r4
    bhs     remain_miss
    sub     r0, r4, r1
    LEAVE
    bx      lr
remain_miss:
    NEXT    H_NEXT_REM, remain_firmware
remain_firmware:                        @ C05E61E0's rule, then LEAVE
    ldr     r2, [r0, #0x10]
    ldr     r0, [r0, #0x14]
    add     r2, r2, r0
    sub     r0, r2, r1
    LEAVE
    bx      lr

.global layer_end
layer_end:
