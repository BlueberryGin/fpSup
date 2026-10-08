

/* ============================================================================
 * A block of records, as gcsv rows.  Runs in the writer, not in a hook.
 * ============================================================================
 *
 * The producers are not touched by any of this.  The accelerometer hook and the
 * gyro drain still write the same eight-byte records into the same block, the
 * block still becomes the writer's when it fills, and the interrupt path is the
 * one that ran eleven minutes with nothing dropped.  What changes is what the
 * writer does when it wakes: it used to hand the block to F_WRITE as it stood,
 * and now it turns it into text first.
 *
 * Which is where this belongs.  The writer is a task at priority 6 with the
 * whole block in front of it and the stream in order; a hook is an interrupt
 * that sees one sample and has no business formatting anything.
 *
 * Seeing the stream in order is also what makes the accelerometer land right.
 * A reading is written into the block AFTER the gyro samples it came after --
 * that is the drain-then-append order the hook has always used -- so a row is
 * held back by one record: when the next record is a reading, the held row
 * carries it; when the next record is another sample, the held row goes out
 * plain.  The reading ends up on the sample it followed, which is where it
 * happened, and nothing has to be patched after it is written.
 *
 * A row held at the end of a block stays held, in G_HELD, and goes out at the
 * head of the next one.  Blocks are a card-sized unit, not a sample-sized one.
 *
 * TWO RULES, both from gcsvgen.S -- the only things taken from v1.4, and both
 * are rules rather than code:
 *
 *   1. A row with no reading ends after gz with NO trailing comma.  It must not
 *      be "...,gz,,": the empty fields parse as three ZEROES, and the file
 *      still opens and still looks right.
 *
 *   2. The accelerometer's axes are not the gyro's and gcsv carries one
 *      orientation string for both, so the quarter turn happens here: native Y
 *      becomes gcsv X, native X negated becomes gcsv Y, Z stays Z.
 *
 * Nothing is scaled.  The columns carry the sensor's own counts, exactly as the
 * records carried them, and gscale and ascale in the header are what turn them
 * into units.  Only the alphabet changes.
 *
 * t is the sample number, because tscale is the sample period.  No timestamp is
 * computed, so none can round and none can drift.
 * ========================================================================== */

/* Worst case for one row: ten digits of t, then three of "-32768," and three
 * more for the reading, then a newline.  Fifty-four; sixty-four with room. */
.equ ROW_MAX,        64

/* ------------------------------------------------------------------ digits */

/* Two decimal digits per aligned entry; text stores remain byte-safe. */
    .balign 2
dec_pairs:
    .ascii  "0001020304050607080910111213141516171819"
    .ascii  "2021222324252627282930313233343536373839"
    .ascii  "4041424344454647484950515253545556575859"
    .ascii  "6061626364656667686970717273747576777879"
    .ascii  "8081828384858687888990919293949596979899"
    .balign 4

/* Anyone can have the table's address without being near it: `adr` reaches
 * only an eight-bit rotated immediate, and put_u32_6 sits far enough away that
 * it cannot. */
dec_pairs_at:
    adr     r0, dec_pairs
    bx      lr

/* Decimal length from floor(log2(value)).  Each binary interval crosses at
 * most one decimal boundary.  Entries 30 and 31 deliberately start at nine:
 * comparing against 10^9 then gives ten without needing a 10^10 constant. */
u32_digits_by_bit:
    .byte   1,1,1,1,2,2,2,3,3,3,4,4,4,4,5,5
    .byte   5,6,6,6,7,7,7,7,8,8,8,9,9,9,9,9
    .balign 4
u32_powers_of_ten:
    .word   1,10,100,1000,10000,100000,1000000,10000000,100000000,1000000000

/* put_u32 -- r0 = value, r1 = destination.  Returns the destination advanced.
 *
 * Determine the length, then fill backwards directly in the final output.
 * No digit scratch or subsequent copy is needed.  /100 remains the exact
 * reciprocal multiply (n * 0x51EB851F) >> 37, two digits a turn.  Text can
 * start at any byte, so only byte stores are used even for decimal pairs.
 * One- and two-digit values bypass length lookup and the saved registers.
 */
    .global put_u32
put_u32:
    cmp     r0, #10
    addlo   r0, r0, #'0'
    strblo  r0, [r1], #1
    movlo   r0, r1
    bxlo    lr
    cmp     r0, #100
    bhs     1f
    adr     r2, dec_pairs
    add     r2, r2, r0, lsl #1
    ldrh    r2, [r2]
    strb    r2, [r1], #1
    lsr     r2, r2, #8
    strb    r2, [r1], #1
    mov     r0, r1
    bx      lr

1:
    push    {r4, r5, r6, lr}
    clz     r2, r0
    rsb     r2, r2, #31
    adr     r3, u32_digits_by_bit
    ldrb    r2, [r3, r2]
    adr     r3, u32_powers_of_ten
    ldr     r3, [r3, r2, lsl #2]
    cmp     r0, r3
    addhs   r2, r2, #1
    add     r1, r1, r2                 @ returned end of text
    mov     r4, r1                     @ digits grow down from that end
    adr     r5, dec_pairs
    movw    r6, #0x851F
    movt    r6, #0x51EB

2:  cmp     r0, #100
    blo     3f
    umull   r2, r3, r0, r6
    lsr     r3, r3, #5                  @ r3 = value / 100
    add     ip, r3, r3, lsl #2          @ 5q
    sub     r0, r0, ip, lsl #4          @ -80q  } value - 100q, no multiply
    sub     r0, r0, ip, lsl #2          @ -20q  }
    add     ip, r5, r0, lsl #1
    ldrh    ip, [ip]
    strb    ip, [r4, #-2]!
    lsr     ip, ip, #8
    strb    ip, [r4, #1]
    mov     r0, r3
    b       2b

3:  cmp     r0, #10
    blo     4f
    add     ip, r5, r0, lsl #1
    ldrh    ip, [ip]
    strb    ip, [r4, #-2]!
    lsr     ip, ip, #8
    strb    ip, [r4, #1]
    b       5f
4:  add     ip, r0, #'0'
    strb    ip, [r4, #-1]

5:  mov     r0, r1
    pop     {r4, r5, r6, lr}
    bx      lr

/* put_i16 -- r0 = signed value, r1 = destination.  Returns it advanced. */
    .global put_i16
put_i16:
    push    {r4, lr}
    cmp     r0, #0
    bge     1f
    mov     r4, #'-'
    strb    r4, [r1], #1
    rsb     r0, r0, #0
1:  bl      put_u32
    pop     {r4, lr}
    bx      lr

/* put_u32_6 -- r0 = value, r1 = destination.  Exactly six digits, zero padded.
 *
 * The drop count sits in the header's note line and is rewritten when the take
 * closes.  Fixed width is the whole point: a number that grew a digit would
 * push the rest of the file along by one byte.
 *
 * Bytes, not halfwords, because the destination is wherever the caller's text
 * happens to be and need not be even.
 *
 * The arithmetic was checked against Python over 0..1000001 before it was
 * written -- the first attempt reconstructed 100q as 80q, which is the same
 * slip put_u32 nearly shipped with.
 */
    .global put_u32_6
put_u32_6:
    push    {r4, r5, r6, r7, r8, lr}
    mov     r4, r1
    mov     r8, r0
    bl      dec_pairs_at
    mov     r5, r0                      @ the table
    mov     r0, r8
    movw    r7, #0x851F
    movt    r7, #0x51EB
    movw    ip, #0x423F                 @ 999999 and no more
    cmp     r0, ip
    movhi   r0, ip

    umull   ip, r6, r0, r7
    lsr     r6, r6, #5                  @ r6 = value / 100
    umull   ip, r8, r6, r7
    lsr     r8, r8, #5                  @ r8 = value / 10000

    add     ip, r5, r8, lsl #1          @ the ten-thousands pair
    ldrh    ip, [ip]
    strb    ip, [r4], #1
    lsr     ip, ip, #8
    strb    ip, [r4], #1

    add     ip, r8, r8, lsl #2          @ 5q
    sub     r1, r6, ip, lsl #4          @ -80q  } value/100 - 100*(value/10000)
    sub     r1, r1, ip, lsl #2          @ -20q  }
    add     ip, r5, r1, lsl #1
    ldrh    ip, [ip]
    strb    ip, [r4], #1
    lsr     ip, ip, #8
    strb    ip, [r4], #1

    add     ip, r6, r6, lsl #2
    sub     r0, r0, ip, lsl #4          @ value - 100*(value/100)
    sub     r0, r0, ip, lsl #2
    add     ip, r5, r0, lsl #1
    ldrh    ip, [ip]
    strb    ip, [r4], #1
    lsr     ip, ip, #8
    strb    ip, [r4], #1

    mov     r0, r4
    pop     {r4, r5, r6, r7, r8, lr}
    bx      lr

/* ------------------------------------------------------------------- rows */

/* gcsv_emit -- r0 = the sample, r1 = the reading or zero, r2 = destination.
 *
 * Both point at eight-byte records in the stream's own layout: x at +0, y at
 * +2, z at +6.  Returns the destination advanced.  Consumes one t.
 */
    .global gcsv_emit
gcsv_emit:
    push    {r4, r5, r6, r7, r8, lr}
    mov     r4, r0
    mov     r7, r1
    mov     r5, r2

    SHAREDAT    r6, O_G_ROW
    ldr     r0, [r6]
    add     ip, r0, #1
    str     ip, [r6]
    mov     r1, r5
    bl      put_u32
    mov     r5, r0

    mov     r0, #','
    strb    r0, [r5], #1
    ldrsh   r0, [r4]
    mov     r1, r5
    bl      put_i16
    mov     r5, r0
    mov     r0, #','
    strb    r0, [r5], #1
    ldrsh   r0, [r4, #2]
    mov     r1, r5
    bl      put_i16
    mov     r5, r0
    mov     r0, #','
    strb    r0, [r5], #1
    ldrsh   r0, [r4, #6]                @ z sits past the tag, as in the ring
    mov     r1, r5
    bl      put_i16
    mov     r5, r0

    cmp     r7, #0
    beq     9f                          @ no reading: end after gz, and end
                                        @ there -- ",," is three zeroes, not
                                        @ three blanks

    mov     r0, #','
    strb    r0, [r5], #1
    ldrsh   r0, [r7, #2]                @ native Y -> gcsv X
    mov     r1, r5
    bl      put_i16
    mov     r5, r0
    mov     r0, #','
    strb    r0, [r5], #1
    ldrsh   r0, [r7]                    @ native X negated -> gcsv Y
    rsb     r0, r0, #0
    mov     r1, r5
    bl      put_i16
    mov     r5, r0
    mov     r0, #','
    strb    r0, [r5], #1
    ldrsh   r0, [r7, #6]                @ Z is Z
    mov     r1, r5
    bl      put_i16
    mov     r5, r0

9:  mov     r0, #'\n'
    strb    r0, [r5], #1
    mov     r0, r5
    pop     {r4, r5, r6, r7, r8, lr}
    bx      lr

/* gcsv_format -- r0 = the records, r1 = how many, r2 = destination,
 *                r3 = one past the last byte that may be written.
 *
 * Returns r0 = the destination advanced, r1 = how many records are LEFT --
 * the caller subtracts to find how many were consumed.
 * One pass, in order, holding one row back so a reading can land on the sample
 * it followed; whatever is still held when the records run out stays held for
 * the next call, block boundary or not.
 *
 * The current block remains owned by the writer throughout this call.  Keep
 * the held sample as a pointer into that block, and copy only the final pending
 * sample to G_HELD before returning.
 * No pointer into a block survives the call that can release it.
 *
 * It stops when fewer than ROW_MAX bytes are left rather than when the buffer
 * is exactly full, so a row is never cut in half.  The caller writes what it
 * got and can resume at the first unconsumed record.  The shipping writer's
 * text budget holds the entire block, so it still makes one write per block.
 */
    .global gcsv_format
gcsv_format:
    push    {r4, r5, r6, r7, r8, r9, r10, lr}
    mov     r4, r0
    mov     r5, r1
    mov     r6, r2
    mov     r7, r3
    sub     r7, r7, #ROW_MAX            @ the last place a row may start
    SHAREDAT    r8, O_G_HELD
    ldr     r0, [r8, #8]               @ G_HELD_OK from the preceding call
    cmp     r0, #0
    moveq   r10, #0
    movne   r10, r8

1:  cmp     r5, #0
    beq     9f
    cmp     r6, r7
    bhi     9f                          @ no room for another row
    ldrsh   r0, [r4, #4]
    cmp     r0, #TAG_ACCEL
    beq     3f

    /* A sample.  Whatever was held had no reading after it, so it goes out
     * plain, and this one takes its place. */
    cmp     r10, #0
    beq     2f
    mov     r0, r10
    mov     r1, #0
    mov     r2, r6
    bl      gcsv_emit
    mov     r6, r0
2:  mov     r10, r4
    b       4f

3:  /* A reading.  It came after the held sample, so that row carries it. */
    cmp     r10, #0
    beq     4f                          @ nothing held: the row it belongs to
                                        @ went out with the previous block
    mov     r0, r10
    mov     r1, r4
    mov     r2, r6
    bl      gcsv_emit
    mov     r6, r0
    mov     r10, #0

4:  add     r4, r4, #8
gcsv_record_consumed:
    sub     r5, r5, #1
    b       1b

9:  /* Retain only the sample that must outlive this call. */
    cmp     r10, #0
    beq     8f
    cmp     r10, r8
    ldmiane r10, {r0, r1}
gcsv_hold_store:
    stmiane r8, {r0, r1}
    mov     r0, #1
    b       7f
8:  mov     r0, #0
7:  str     r0, [r8, #8]                @ G_HELD_OK
    mov     r0, r6
    mov     r1, r5                      @ what is left, not what was taken
    pop     {r4, r5, r6, r7, r8, r9, r10, lr}
    bx      lr

/* gcsv_last -- r0 = destination.  Returns it advanced.
 *
 * The take is over: send out whatever row is still held.  Without this the very
 * last sample of every take is the one that never arrives, which is the kind of
 * thing nobody notices until they are looking for something else.
 */
    .global gcsv_last
gcsv_last:
    push    {r4, r5, r6, lr}
    mov     r6, r0
    SHAREDAT    r5, O_G_HELD
    ldr     r0, [r5, #8]
    cmp     r0, #0
    beq     9f
    mov     r0, r5
    mov     r1, #0
    mov     r2, r6
    bl      gcsv_emit
    mov     r6, r0
    mov     r0, #0
    str     r0, [r5, #8]
9:  mov     r0, r6
    pop     {r4, r5, r6, lr}
    bx      lr
