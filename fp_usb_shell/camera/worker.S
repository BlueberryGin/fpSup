.syntax unified
.arm
.text
/* ============================================================================
 * USB shell worker — runs on the camera, serves the PTP gadget's bulk pipes.
 * ============================================================================
 *
 * The gadget is the firmware's own PTP gadget, left exactly as the firmware
 * builds it.  The firmware therefore owns the descriptors, creates and enables
 * the endpoints, and re-creates them after a record-mode reconfiguration.  This
 * worker only moves bytes through pipes that already exist.
 *
 * AutoRun changes one word:
 *   0xC0CF3740  interface class 06/01/01 -> ff/ff/ff, so the host's PTP stack
 *               does not claim interface 0 before the shell daemon can.
 *
 * The gadget's class handler is deliberately left alone.  Removing it looks
 * harmless — FUN_c04dbc10 only answers the PTP class control requests 0x64 /
 * 0x66 / 0x67 and never touches a bulk endpoint — but registration stores it at
 * 0xC30257C0 and the control path at 0xC01E8268 stalls when it is NULL, so the
 * device never gets an address.  Two boots confirmed that.
 *
 * ENDPOINTS.  Taken from the PTP gadget, and matched against the firmware's own
 * bulk transfer routine at 0xC01E5BC0, which arms them exactly this way:
 *
 *     EP 0x01 OUT bulk   logical 1   physical 2   TRB ptr @ 0xC31E3268
 *     EP 0x82 IN  bulk   logical 2   physical 5   TRB ptr @ 0xC31E326C
 *     EP 0x83 IN  bulk       logical 3   physical 7   TRB ptr @ 0xC31E3270
 *                 AutoRun turns PTP's unused interrupt endpoint into this so a
 *                 hook has a pipe of its own.  The worker never touches it.
 *
 *   physical      = (logical << 1) | (direction == IN)
 *   StartTransfer takes the physical number, EndTransfer and Wait the logical
 *   TRB buffer     = CPU address - 0x40000000
 *   TRB control    = 0x813
 *
 * PROTOCOL.  FPSH v1, 64-byte frames, little-endian:
 *
 *     +00 magic "FPSH"   +04 version  +05 command  +06 flags
 *     +08 sequence       +0C payload_length        +0E status
 *     +10 crc32 (whole frame with this field zeroed)
 *     +14 payload, 44 bytes
 *
 *   A reply may span several frames: flags bit 0 means another frame follows.
 *   A shell command may be longer than the payload field — it is read as a
 *   NUL-terminated string. The 16 KiB OUT TRB also accepts a binary upload:
 *   CMD 4 carries destination, length and data CRC in the first frame, then
 *   up to 16320 bytes from offset 64. CMD 5 reports capability and the last
 *   accepted tuple. The frame CRC still covers only the first 64 bytes.
 *
 * COMMANDS.  One: `shl <line>`, which hands <line> to the firmware's own shell
 * dispatcher and returns whatever it printed.  That already includes `mem set`
 * and `mem save`, so the worker needs no memory commands of its own.
 *
 * MEMORY.  The worker sits high in the cave so that 0xC072DE64 through
 * 0xC072F000 stays clear for a gyro logger loaded alongside it — the two share
 * one card, and the shell is how the logger gets developed.
 *
 *     0xC072DE64..0xC072F000   free, for the gyro logger
 *     0xC072F000..0xC072F040   this worker's state
 *     0xC072F050..0xC072F700   this worker's code
 *     0xC072F700..0xC072F700   the one-shot template's scratch and code
 *     0xC072F050..0xC072F058   the pool's address and size: the whole of what
 *                              this worker leaves in the cave, and what the
 *                              host tools find everything else through
 *     pool +0x0000             FPSH frame buffer, 4 KiB
 *     pool +0x2000             shell capture buffer, 16 KiB (derived from the
 *                              pool pointer, never hard-coded)
 *     pool +0x6900             upload last-accepted tuple (16 bytes)
 *     pool +0x10000            file-staging region eligible for binary upload
 *
 * STATE.  Sixteen words at 0xC072F000, readable with
 *     shl mem save \WSTATE.BIN 0xC072F000,,0x40
 *
 *     +00 'VSHL'      +04 task id      +08 last round faulted   +0C rounds
 *     +10 OUT arm     +14 OUT wait     +18 IN arm        +1C IN wait
 *     +20 served      +24 faults       +28 gate holds    +2C last USB_STATE
 *     +30 last DALEPENA  +34 phase of this command (FAULT_LOG)  +38 unused
 *     +3C first gate reason (0x80000000 | DALEPENA<<8 | state)
 * ========================================================================== */

/* --- firmware entry points ------------------------------------------------ */
.equ FN_CRETSK,   0xC0016A58      /* tk_cre_tsk(descriptor) -> id             */
.equ FN_STATSK,   0xC0016BC0      /* tk_sta_tsk(id, code)                     */
.equ FN_DELAY,    0xC01F89C4      /* delay(ms), yields the CPU                */
.equ FN_ENDX,     0xC01E6D49      /* EndTransfer(logical ep)         [thumb]  */
.equ FN_STARTX,   0xC01E6B81      /* StartTransfer(physical ep)      [thumb]  */
.equ FN_WAIT,     0xC01E4E81      /* Wait(logical ep, timeout)       [thumb]  */
.equ FN_SHINIT,   0xC03D9A10      /* shell context one-time init              */
.equ FN_SHPUSH,   0xC03D9AC0      /* push an output sink                      */
.equ FN_SHPOP,    0xC03D9B20      /* pop the output sink                      */
.equ FN_SHRUN,    0xC03D9C20      /* tokenise, dispatch and run one line      */
.equ FN_VSNPRINT, 0xC0013F48      /* vsnprintf(buf, size, fmt, va)            */

/* --- firmware data -------------------------------------------------------- */
/* --- the worker's own pool ------------------------------------------------
 * The worker used to read 0xC3757A7C, the one pool the AutoRun asked for and
 * everything on the card shared.  It asks the allocator for its own now, in
 * `spawn`, and nothing else on the card has to know: the six sites below still
 * load a pointer out of a fixed word, only a different one.
 *
 * Sharing was not free.  The gyro logger's file object lives at pool+0x6400
 * for three kilobytes and POOL_FAULTS below is ten words at pool+0x6800 --
 * they overlapped, on every card that carried both, and nothing said so.
 *
 * Fixed addresses in the cave, NOT words inside the worker's own image:
 * swapworker.py rewrites that image while the worker is running, and a live
 * pool pointer inside it would be swapped out from under the task. */
.equ FRAMEBUF,    0xC072F050      /* the address, or zero if refused           */
.equ WPOOL_SIZE,  0xC072F054      /* what was asked for -- putfile reads it    */
.equ WCODE_PTR,   0xC072F058      /* the SECOND allocation: this worker's own
                                   * code.  Its own block, not an offset inside
                                   * the first one -- see the note in spawn     */
.equ WPOOL_BYTES, 0x100000        /* 1 MiB, as before: putfile stages files at
                                     +0x10000 and check_fits needs the ceiling */
.equ H_GET,       0xC001D740      /* (desc, class, size, 0)                    */
.equ H_ADDR,      0xC001D7F0      /* (desc) -> address                         */
.equ F_DCACHE,    0xC000E91C      /* D-cache clean/invalidate + barriers       */
.equ F_ICACHE,    0xC000EABC      /* whole I-cache invalidate + barriers       */
.equ TRB_OUT,     0xC31E3268      /* endpoint TRB table [1]: EP 0x01 OUT      */
.equ TRB_IN,      0xC31E326C      /* endpoint TRB table [2]: EP 0x82 IN       */
.equ USB_STATE,   0xC31E3748      /* 4 means the link is suspended            */
.equ DALEPENA,    0x2100C720      /* DWC3: bit N = physical endpoint N enabled */
.equ EP_MASK,     0x24            /* physical 2 (EP 0x01 OUT) | 5 (EP 0x82 IN) */
.equ SHELL_CTX,   0xC3756904      /* firmware shell context singleton         */

.equ PHYS_OUT,    2
.equ PHYS_IN,     5
.equ LOG_OUT,     1
.equ LOG_IN,      2
.equ TRB_ARMED,   0x813
.equ TIMEOUT,     50000         /* waiting for a command: no hurry            */
/* Waiting for the host to collect a reply is a different question, and fifty
 * seconds is the wrong answer to it. The host gives up after two hundred
 * milliseconds; everything after that is the camera holding a transfer nobody
 * is coming for, deaf to commands the whole time. That is what looked like the
 * worker dying: rounds crawling one every ten seconds, endpoints healthy, the
 * gate never once closed. Replugging the cable "fixed" it because
 * re-enumeration aborts the wait.
 *
 * Long enough that a host which is still there will be served; short enough
 * that one which has walked away costs a round, not a minute. */
.equ TIMEOUT_IN,  300

/* --- our memory ----------------------------------------------------------- */
.equ STATE,       0xC072F000      /* 16 words                                 */
.equ CAP_LEN,     0xC072F040      /* bytes captured from the shell            */
.equ SWAP_MAGIC,  0xC072F044      /* "SWAP" here and an address below means go */
.equ SWAP_ADDR,   0xC072F048
.equ CAP_PTR,     0xC072F04C      /* where the reply's bytes are, if not the
                                     capture buffer                           */
.equ SWAP_WORD,   0x50415753      /* "SWAP" */
/* The capture buffer lives in the DMA pool, not the code cave.  In the cave it
 * could only be about 1.4 KiB before running into the injection ceiling, and a
 * kilobyte truncates the longer shell commands — `imager mode_list` stopped
 * after seven of seventy sensor modes.  The pool has room to spare and the CPU
 * copies out of it, so no coherency handling is needed. */
.equ POOL_FAULTS, 0x6800        /* ten fault counters, past the buffers */
/* Every fault, as it happened: pool+FAULT_LOG holds a running count, then a
 * ring of 16 x 32 bytes at +0x10 -- {rounds, phase, command, sequence,
 * arm_out, wait_out, arm_in, wait_in}.  The four results are this command's
 * own (cleared when it arrives); phase says how far it got (1 arrived, 2 MEMW
 * data armed, 3 READY out, 4 data in, 5 MEMR block armed).  Read it with
 * `mem get` after a failure instead of guessing which call it was. */
.equ FAULT_LOG,   0x6C00
.equ CAP_OFF,     0x2000          /* capture buffer sits this far past the frame */
.equ CAP_MAX,     0x4000          /* 16 KiB                                   */
.equ UP_OUT_MAX,  0x4000          /* one OUT TRB: frame plus binary data       */
.equ UP_DATA_MAX, 0x3FC0          /* 16 KiB less the 64-byte FPSH frame       */
.equ UP_STATE,    0x6900          /* worker pool: last accepted upload tuple  */
.equ UP_MIN_OFF,  0x10000         /* only the worker's file-staging region    */

/* --- MEM1: memory to and from the host with no copy, both directions -------
 * Each transfer is one TRB pointed at the caller's bytes, up to XFER_MAX, so
 * the CPU never touches the data: the same thing that took reads from 73 to
 * 355 MB/s.  What protects the camera is the window table, not the host:
 *   - every byte of a transfer must lie inside ONE window carrying the
 *     permission (the implicit one is this worker's own pool: read all of it,
 *     write only the staging region past UP_MIN_OFF);
 *   - the host grants windows, but a window must sit in the DMA-reachable
 *     cached alias [DMA_LO, DMA_HI); a writable one must also start at or past
 *     WRITE_LO and may not overlap the pool header (frames, capture buffer,
 *     this table) or this worker's own code;
 *   - a write arms the WHOLE rounded-up length inside the window, so a host
 *     that sends more than it announced still cannot leave it;
 *   - a write whose data never arrives is ended (EndTransfer) before the
 *     worker does anything else, so late bytes cannot land after a failure.
 * DMA_LO..DMA_HI is where the allocator's blocks have been seen (0x45xxxxxx,
 * 0x5F9xxxxx); the image at 0xC0000000 is mapped some other way and is not
 * reachable this way.  WRITE_LO keeps writes off 0x40000000..0x44000000, which
 * is suspected (not shown) to alias the image and its data. */
.equ CMD_MEMW,    6
.equ CMD_MEMR,    7
.equ CMD_WIN,     8
.equ CMD_MEMCAPS, 9
.equ WIN_OFF,     0x6A00          /* worker pool: 8 x {base, len, perm}       */
.equ WIN_SLOTS,   8
.equ PERM_R,      1
.equ PERM_W,      2
.equ OPT_SYNC,    1               /* whole D-cache clean+invalidate around DMA */
.equ OPT_CRC,     2               /* MEMW: the camera CRCs what landed         */
.equ XFER_MAX,    0xFFFC00        /* 24-bit TRB length, rounded to 1 KiB       */
.equ DMA_LO,      0x40000000
.equ DMA_HI,      0x80000000
.equ WRITE_LO,    0x44000000
.equ MEM_TMO,     500             /* ms, plus one per 32 KiB of the transfer  */

/* --- bootstrap ------------------------------------------------------------ */
.equ HOOK_SITE,   0xC00D0794      /* gyro callback, borrowed once             */
.equ HOOK_ORIG,   0xFA046FD7      /* the BLX this site normally holds         */
.equ HOOK_TARGET, 0xC01EC6F9      /* what that BLX called (thumb)             */

.equ TASK_PRIO,   28              /* verified safe                            */
.equ TASK_STACK,  0x2000          /* 8 KiB; the stack pool is shared - do not
                                     raise this, it corrupts other tasks      */
.equ FRAME,       64
.equ CHUNK,       44
.equ BULK_MIN,    128             /* replies at least this big go in one transfer */
.equ UNCACHED,    0x40000000
.equ TO_BUS,      0xC0000000      /* +0xC0000000 mod 2^32 == -0x40000000      */

.macro LDA reg, value
    movw    \reg, #:lower16:\value
    movt    \reg, #:upper16:\value
.endm

/* ==========================================================================
 * bootstrap — entered once from the gyro callback site.  Calls the routine it
 * displaced, creates the worker task, then puts the original word back.
 * ========================================================================== */
/* Not used by the loader build, and not dead either.
 *
 * `build_autorun.py --loader` puts the worker in fpSup.BIN and hooks loader.S
 * instead, which places this code and branches straight to `serve` -- so on a
 * card built that way nothing here ever runs, and STATE+0x00 stays zero rather
 * than reading "VSHL".
 *
 * Without --loader the AutoRun spells the whole worker out and hooks this, and
 * that build is the way back: it is one file, it depends on nothing written
 * recently, and it is what to fall back to when the binary or the loader is the
 * suspect. The loader has been wrong twice already -- once living at the
 * address it was copying to, once guarding itself with a word the worker
 * clears -- and both times this was what a card reader could put back.
 *
 * Its two hundred bytes used to cost fifty `mem set` commands. They now arrive
 * in a file, so keeping it costs nothing worth counting.
 */
.global bootstrap
bootstrap:
    push    {r4, r5, lr}
    LDA     r12, HOOK_TARGET
    blx     r12
    mov     r4, r0                      @ the displaced call's return value

    /* Put the firmware's word back before starting anything: this runs from the
     * callback, at 50-90 Hz, and a second entry while the first is still
     * creating the task is a second task. */
    LDA     r0, HOOK_SITE
    LDA     r1, HOOK_ORIG
    str     r1, [r0]

    /* The task itself is `spawn`, which is also the VBIN entry.  It used to be
     * written out twice -- once here for the callback, once for the file -- and
     * the second copy is what pushed the worker past the parameter block. */
    bl      spawn

    mov     r0, r4
    pop     {r4, r5, pc}

/* --- spawn: the VBIN entry.  Start the worker, and COME BACK. --------------
 *
 * stage2 tail-branches to the entry named in the file's header, and the entry
 * used to be `serve` itself -- which never returns, so stage2 never returned,
 * so the loader never returned, so the `echo` that started all of it never
 * returned and the AutoRun sat on that line forever.  The banner at the end of
 * the script was simply never reached, and the loader grew a task of its own
 * to work around it: it created a task to run ITSELF in, so that the echo
 * handler could return while the file was still being read.  Twenty-four words
 * of loader, in every build, to pay for an entry that did not come back.
 *
 * This is that entry doing what every payload should: make whatever has to
 * outlive the call, then return.  The task is the same one `bootstrap` makes --
 * same priority, same stack, same name -- minus the gyro-callback plumbing
 * around it, because nothing drives us from the callback any more.
 *
 * Four registers pushed rather than three: an interrupt STRDs into the stack
 * of whatever it interrupts, so eight-byte alignment is not optional. */
    .global spawn
spawn:
    push    {r4, r5, r6, r7, r8, lr}
    LDA     r5, STATE
    ldr     r0, [r5]
    LDA     r1, 0x4C485356              @ 'VSHL' -- already claimed?
    cmp     r0, r1
    beq     2f                          @ yes: one worker, not two
    str     r1, [r5]

    /* TWO allocations, not one pool with an agreed offset inside it.
     *
     * The first draft put this worker's code at pool+0x8000 and wrote down that
     * nothing else uses that offset.  That is the shape of the bug this tree
     * has hit most often -- USB_SHELL_HOOK_WORK_POSTMORTEM.md opens with three
     * of them in one evening, one of which was a file object placed into the
     * worker's own code.  An offset kept free by a comment is kept free until
     * somebody does not read the comment.
     *
     * So the code gets an allocation of its own.  The allocator will not hand
     * the same bytes to two callers, which is a guarantee no convention can
     * make, and it is the whole point of asking it rather than carving up one
     * block by hand.
     *
     * Class 0 is USER, the same channel the AutoRun's `memmgr bufmem get` took
     * its megabyte from; NOT class 10, which is the CinemaDNG frame buffer.
     *
     * Both descriptors live on the stack.  heapalloc.S says not to do that --
     * for its own reason, which is that it frees later and needs the descriptor
     * again.  Neither of these is ever freed: they last as long as the worker,
     * which is as long as the camera is on. */
    sub     sp, sp, #32                 @ two descriptors, sixteen bytes each
    mov     r0, sp
    mov     r1, #0
    mov     r2, #WPOOL_BYTES
    mov     r3, #0
    LDA     r12, H_GET
    blx     r12
    mov     r0, sp
    LDA     r12, H_ADDR
    blx     r12
    mov     r6, r0                      @ the buffers, or zero

    add     r0, sp, #16
    mov     r1, #0
    ldr     r2, wbody_len               @ exactly what the body needs
    mov     r3, #0
    LDA     r12, H_GET
    blx     r12
    add     r0, sp, #16
    LDA     r12, H_ADDR
    blx     r12
    add     sp, sp, #32
    mov     r7, r0                      @ the code, or zero

    /* Published before they are checked, so a zero in either word is what says
     * which one the allocator refused.  Refusal is a real answer -- playback
     * takes ten megabytes of its own -- and the answer to it is no worker at
     * all, rather than one whose replies are written to address zero.
     *
     * A refusal of the second leaks the first.  There is no worker either way,
     * and freeing it here would cost more words than the leak costs bytes. */
    LDA     r4, FRAMEBUF
    str     r6, [r4]
    str     r7, [r4, #8]                @ WCODE_PTR
    cmp     r6, #0
    cmpne   r7, #0
    beq     2f
    mov     r0, #WPOOL_BYTES
    str     r0, [r4, #4]                @ WPOOL_SIZE

    /* Copy the body into its own allocation.
     *
     * Everything from `wbody` down is position-independent and is running,
     * right now, out of the loader's staging buffer -- which the loader hands
     * back to the allocator as soon as stage2 returns.  So it has to be
     * somewhere of our own before then.
     *
     * The cave is not somewhere of our own.  It is firmware RAM nobody
     * allocated to us, it is full, and three separate things hit its ceiling in
     * one evening. */
8:  add     r8, pc, #(wbody - 8b - 8)   @ source, here in the staging buffer
    ldr     r5, wbody_len
    mov     r0, r7                      @ walking destination
1:  ldr     ip, [r8], #4
    str     ip, [r0], #4
    subs    r5, r5, #4
    bne     1b

    /* Data to the caches until this runs, and the I-cache may hold whatever
     * used to be at those addresses. */
    LDA     r12, F_DCACHE
    blx     r12
    LDA     r12, F_ICACHE
    blx     r12

    LDA     r5, STATE                   @ reloaded: r5 was the copy's counter
    sub     sp, sp, #32
    mov     r0, #0
    str     r0, [sp, #0]                @ exinf
    mov     r0, #0x41
    str     r0, [sp, #4]                @ tskatr
    ldr     r0, serve_off               @ serve, in the code allocation
    add     r0, r0, r7
    str     r0, [sp, #8]                @ entry
    mov     r0, #TASK_PRIO
    str     r0, [sp, #12]
    movw    r0, #TASK_STACK
    str     r0, [sp, #16]
    LDA     r0, 0x4853565F              @ dsname "_VSH"
    str     r0, [sp, #20]
    movw    r0, #0x004C                 @ "L"
    str     r0, [sp, #24]
    mov     r0, #0
    str     r0, [sp, #28]
    mov     r0, sp
    LDA     r12, FN_CRETSK
    blx     r12
    add     sp, sp, #32
    str     r0, [r5, #4]                @ task id, for `shl` to report
    cmp     r0, #0
    ble     2f
    mov     r1, #0
    LDA     r12, FN_STATSK
    blx     r12
    str     r0, [r5, #8]
2:  mov     r0, #0
    pop     {r4, r5, r6, r7, r8, pc}

    .align  2
wbody_len:  .word   wbody_end - wbody
serve_off:  .word   serve - wbody      @ NOT `+ CODE_OFF`: mixing a label
                                        @ difference with an .equ makes GAS
                                        @ emit a relocation against an absolute
                                        @ symbol, and armasm refuses anything
                                        @ that is not in .text

/* ==========================================================================
 * wbody -- everything below here is copied into the pool and runs from there.
 *
 * Nothing above this line is: `bootstrap` and `spawn` run wherever they were
 * placed, which on a --loader card is the loader's staging buffer and on the
 * fallback card is the cave.  Both are gone or idle by the time the task runs.
 *
 * THE RULE FOR EVERYTHING BELOW: it may not name its own address.  Branches,
 * `adr` and literal-pool loads are pc-relative and move with the code;
 * firmware addresses and the two cave words above are absolute and do not
 * move; the pool is read from FRAMEBUF at run time.  A `.word some_label_of_
 * ours` would need a relocation, and armasm.py refuses every relocation that
 * is not pc-relative -- so this rule is enforced by the build, not by reading.
 * The one thing it cannot catch is an address written as a bare number, and
 * there is none below: `grep 0xC072F` says so.
 * ========================================================================== */
wbody:


/* Ahead of `serve`, not after it.
 *
 * `serve` reaches this with `adr`, which is pc-relative and has a small range,
 * and every line added to `serve` pushed it further away until the assembler
 * refused -- twice in one evening, for changes that had nothing to do with it.
 * Sitting before its only caller, the distance shrinks as the code above it
 * grows instead of stretching.
 */
/* ==========================================================================
 * capture — the shell calls its sink as printf(fmt, a1, a2, a3), with any
 * further arguments already on the stack, so &a1 has to sit next to them.
 * ========================================================================== */
capture:
    push    {r1, r2, r3}                @ makes a contiguous va_list
    push    {r4, r5, lr}
    mov     r2, r0                      @ fmt
    add     r3, sp, #12                 @ &a1
    LDA     r4, CAP_LEN
    ldr     r5, [r4]
    LDA     r0, FRAMEBUF
    ldr     r0, [r0]
    add     r0, r0, #CAP_OFF
    add     r0, r0, r5
    movw    r1, #CAP_MAX
    subs    r1, r1, r5
    ble     capture_done
    sub     r1, r1, #1                  @ leave room for the NUL
    LDA     r12, FN_VSNPRINT
    blx     r12
    cmp     r0, #0
    ble     capture_done
    ldr     r5, [r4]
    add     r5, r5, r0
    str     r5, [r4]
capture_done:
    pop     {r4, r5, lr}
    add     sp, sp, #12
    bx      lr

/* ==========================================================================
 * serve — the worker task.  r4 holds STATE for its whole life.
 * ========================================================================== */
serve:
    push    {r4, r5, r6, r7, r8, r9, r10, lr}   @ 8 words keeps sp 8-byte aligned
    LDA     r4, STATE
    /* A new worker, including a live swap, must not report a previous pool
     * user's bytes as a completed upload.  The tuple is private to this task
     * and lives clear of the command/capture and staging regions. */
    LDA     r0, FRAMEBUF
    ldr     r0, [r0]
    add     r0, r0, #UP_STATE
    mov     r1, #0
    str     r1, [r0]
    str     r1, [r0, #4]
    str     r1, [r0, #8]
    str     r1, [r0, #12]
    /* Nor inherit a previous worker's windows: a grant is for this worker's
     * lifetime, and the host asks again after a swap. */
    add     r0, r0, #(WIN_OFF - UP_STATE)
    mov     r2, #(WIN_SLOTS * 3)
1:  str     r1, [r0], #4
    subs    r2, r2, #1
    bne     1b

next_round:
    ldr     r0, [r4, #0x0C]
    add     r0, r0, #1
    str     r0, [r4, #0x0C]

    /* Somewhere to go, if the host has somewhere for this task to be.
     *
     * Every change to this file has cost a power cycle, because the code is
     * placed at boot and this task is running inside it -- overwriting it from
     * a borrowed shell command means overwriting the instructions doing the
     * overwriting. Eight reboots in one evening.
     *
     * The way out is not to do the copying here. loader.S sits at the bottom of
     * the cave, well clear of this code, and its `load` entry already reads
     * fpSup.BIN, places what it says and branches to the entry named in it. This
     * task jumping there replaces the worker with itself, from outside itself.
     *
     * Two words rather than one: an address alone is a jump to whatever
     * happened to be written, and this runs every round. The magic is cleared
     * before the branch so a half-written pair cannot fire twice. */
    LDA     r0, SWAP_MAGIC
    ldr     r1, [r0]
    LDA     r2, SWAP_WORD
    cmp     r1, r2
    bne     no_swap
    mov     r1, #0
    str     r1, [r0]
    LDA     r0, SWAP_ADDR
    ldr     r0, [r0]
    cmp     r0, #0
    bxne    r0
no_swap:

    /* Do not touch an endpoint the firmware has not configured yet.  DALEPENA is
     * the controller's own record of which physical endpoints exist, so it is
     * the only honest test: it stays 0x003 (EP0 only) until the host completes
     * SET_CONFIGURATION.  Issuing DEPCMDs before that competes with the driver
     * while it is trying to enumerate, and the enumeration is what loses.
     * USB_STATE is not a substitute — it reads 2 long before the endpoints
     * exist.  It is still worth checking for suspend, where arming makes the
     * controller busy-spin. */
    LDA     r0, DALEPENA
    ldr     r1, [r0]
    str     r1, [r4, #0x30]
    and     r1, r1, #EP_MASK
    cmp     r1, #EP_MASK
    bne     hold

    LDA     r0, USB_STATE
    ldr     r1, [r0]
    str     r1, [r4, #0x2C]
    cmp     r1, #4
    beq     hold

    bl      arm_out
    str     r0, [r4, #0x10]
    cmp     r0, #0
    bne     fault

    mov     r0, #LOG_OUT
    movw    r1, #TIMEOUT
    bl      wait_done
    str     r0, [r4, #0x14]
    cmp     r0, #0
    bne     fault                       @ idle timeout: just arm again
got_command:
    bl      out_owned
    mov     r0, #1
    str     r0, [r4, #0x34]             @ phase 1: a command arrived
    mov     r0, #0
    str     r0, [r4, #0x10]             @ this command's results, from scratch
    str     r0, [r4, #0x14]
    str     r0, [r4, #0x18]
    str     r0, [r4, #0x1C]

    LDA     r0, FRAMEBUF
    ldr     r6, [r0]
    mov     r0, #UNCACHED
    add     r6, r6, r0                  @ r6 = uncached view of the frame
    add     r10, r6, #20                @ payload

    ldrb    r0, [r6, #5]
    cmp     r0, #4                     @ CMD_UPLOAD: absolute, CRC-checked write
    beq     receive_upload
    cmp     r0, #5                     @ CMD_UPLOAD_CAPS: safe old-worker probe
    beq     reply_upload_caps
    cmp     r0, #CMD_MEMW              @ MEM1: TRB straight at the destination
    beq     mem_write
    cmp     r0, #CMD_MEMR              @ MEM1: TRB straight at the source
    beq     mem_read
    cmp     r0, #CMD_WIN               @ MEM1: grant or revoke a window
    beq     win_set
    cmp     r0, #CMD_MEMCAPS           @ MEM1: capability and window table
    beq     mem_caps

    ldr     r0, [r10]
    LDA     r1, 0x206C6873              @ "shl "
    cmp     r0, r1
    bne     reply_nothing

/* --- run one firmware shell line, capturing what it prints ---------------- */
    add     r7, r10, #4                 @ the line; writable, so strtok is fine
    LDA     r0, SHELL_CTX
    ldr     r1, [r0]
    cmp     r1, #0
    bne     shell_ready
    LDA     r0, SHELL_CTX
    LDA     r12, FN_SHINIT
    blx     r12
shell_ready:
    LDA     r0, CAP_LEN
    mov     r1, #0
    str     r1, [r0]

    LDA     r0, SHELL_CTX
    adr     r1, capture
    LDA     r12, FN_SHPUSH
    blx     r12

    LDA     r0, SHELL_CTX
    mov     r1, r7
    LDA     r12, FN_SHRUN
    blx     r12

    LDA     r0, SHELL_CTX
    LDA     r12, FN_SHPOP
    blx     r12                         @ hand the console back to the UART

    LDA     r0, CAP_LEN
    ldr     r9, [r0]

    /* Send from wherever the answer already is.
     *
     * Everything used to be copied into the capture buffer first, and that
     * buffer is sixteen kilobytes -- so a thirty-one megabyte file came back in
     * nineteen hundred and ninety-five round trips, each one a request the host
     * had to make and the camera had to be ready for. The limit was never the
     * link or the card (the card reads that file into memory in 0.17 s, at 183
     * MB/s); it was a staging buffer we were copying through for no reason.
     *
     * A TRB carries a 24-bit length, so one transfer can be sixteen megabytes.
     * A payload that already knows where its bytes are puts the address here
     * and the DMA reads them where they lie. The capture buffer stays for what
     * it is actually for: catching what the firmware's printf writes. */
    LDA     r0, CAP_PTR
    ldr     r8, [r0]
    cmp     r8, #0
    bne     reply_entry
    LDA     r0, FRAMEBUF
    ldr     r8, [r0]
    add     r8, r8, #CAP_OFF
    b       reply_entry

reply_nothing:
    mov     r9, #0
    LDA     r0, FRAMEBUF
    ldr     r8, [r0]
    add     r8, r8, #CAP_OFF
    b       reply_entry

/* --- upload protocol: one 16 KiB OUT, then one FPSH acknowledgment ---------
 * The source is the uncached view of the worker-owned frame buffer. Only
 * pool+0x10000..pool+1 MiB is writable, and the host still checks its file
 * object reservation and reads back the entire staging region before file I/O.
 * The last accepted tuple is published before the acknowledgment; a lost
 * reply can be resolved with CMD_UPLOAD_CAPS rather than blindly appending. */
reply_upload_caps:
    LDA     r0, FRAMEBUF
    ldr     r0, [r0]
    add     r0, r0, #UP_STATE
    LDA     r1, 0x31305055              @ "UP01" in little-endian
    str     r1, [r6, #20]
    ldr     r1, [r0]
    str     r1, [r6, #24]
    ldr     r1, [r0, #4]
    str     r1, [r6, #28]
    ldr     r1, [r0, #8]
    str     r1, [r6, #32]
    ldr     r1, [r0, #12]
    str     r1, [r6, #36]
    mov     r0, #0
    strh    r0, [r6, #6]                @ never inherit the request's raw flag
    add     r8, r6, #20
    mov     r9, #20
    b       reply_entry

receive_upload:
    ldr     r0, [r6]
    LDA     r1, 0x48535046              @ "FPSH"
    cmp     r0, r1
    bne     upload_bad_frame
    ldrb    r0, [r6, #4]
    cmp     r0, #1
    bne     upload_bad_frame
    ldrh    r0, [r6, #6]
    cmp     r0, #0
    bne     upload_bad_frame
    ldrh    r0, [r6, #12]
    cmp     r0, #12                    @ dest, length, data CRC
    bne     upload_bad_frame
    ldrh    r0, [r6, #14]
    cmp     r0, #0
    bne     upload_bad_frame
    ldr     r7, [r6, #16]
    mov     r0, #0
    str     r0, [r6, #16]
    mov     r0, r6
    mov     r1, #FRAME
    bl      crc_block
    str     r7, [r6, #16]
    cmp     r3, r7
    bne     upload_bad_frame

    ldr     r8, [r6, #20]               @ absolute destination
    ldr     r9, [r6, #24]               @ actual data length
    ldr     r7, [r6, #28]               @ expected data CRC
    cmp     r9, #0
    beq     upload_bad_bounds
    movw    r0, #UP_DATA_MAX
    cmp     r9, r0
    bhi     upload_bad_bounds
    orr     r0, r8, r9
    tst     r0, #3                     @ the file staging path is word-aligned
    bne     upload_bad_bounds
    LDA     r0, FRAMEBUF
    ldr     r0, [r0]
    add     r1, r0, #UP_MIN_OFF
    cmp     r8, r1
    blo     upload_bad_bounds
    add     r0, r0, #WPOOL_BYTES
    adds    r1, r8, r9
    bcs     upload_bad_bounds
    cmp     r1, r0
    bhi     upload_bad_bounds

    add     r0, r6, #FRAME
    mov     r1, r9
    bl      crc_block
    cmp     r3, r7
    bne     upload_bad_crc

    add     r0, r6, #FRAME
    mov     r1, r8
    mov     r2, r9
upload_copy:
    ldr     r3, [r0], #4
    str     r3, [r1], #4
    subs    r2, r2, #4
    bne     upload_copy

    LDA     r0, FRAMEBUF
    ldr     r0, [r0]
    add     r0, r0, #UP_STATE
    ldr     r1, [r6, #8]               @ accepted sequence
    str     r1, [r0]
    str     r8, [r0, #4]
    str     r9, [r0, #8]
    str     r7, [r0, #12]

    mov     r0, #0                     @ acknowledgment: status, dest, len, CRC
    str     r0, [r6, #20]
    str     r8, [r6, #24]
    str     r9, [r6, #28]
    str     r7, [r6, #32]
    mov     r9, #16
    b       upload_reply

upload_bad_frame:
    mov     r0, #1
    b       upload_reject
upload_bad_bounds:
    mov     r0, #2
    b       upload_reject
upload_bad_crc:
    mov     r0, #3
upload_reject:
    str     r0, [r6, #20]
    mov     r9, #4
upload_reply:
    mov     r0, #0
    strh    r0, [r6, #6]
    add     r8, r6, #20
    b       reply_entry

/* --- MEM1 (see the constants) ----------------------------------------------
 * Rejection codes, in payload word 0 and in the header's status:
 *   1 frame (magic, version, flags or CRC)   2 arguments
 *   4 no window covers it with that permission
 *   5 StartTransfer refused the data TRB     6 the data never arrived
 * A reply frame keeps the request's header, so the sequence still matches. */

/* CMD 8: payload {slot, base, length, perm}.  perm 0 revokes. */
win_set:
    bl      frame_ok
    cmp     r0, #0
    movne   r0, #1
    bne     win_reply
    ldr     r0, [r6, #20]               @ slot
    cmp     r0, #WIN_SLOTS
    bhs     win_bad
    LDA     r1, FRAMEBUF
    ldr     r1, [r1]
    add     r1, r1, #WIN_OFF
    add     r0, r0, r0, lsl #1
    add     r7, r1, r0, lsl #2          @ r7 = this slot, 12 bytes
    ldr     r8, [r6, #24]               @ base
    ldr     r9, [r6, #28]               @ length
    ldr     r5, [r6, #32]               @ perm
    cmp     r5, #0
    beq     win_store                   @ revoke: always allowed
    bics    r0, r5, #(PERM_R | PERM_W)
    bne     win_bad
    orr     r0, r8, r9
    tst     r0, #3
    bne     win_bad
    cmp     r9, #0
    beq     win_bad
    adds    r1, r8, r9                  @ r1 = end, exclusive
    bcs     win_bad
    cmp     r8, #DMA_LO
    blo     win_bad
    cmp     r1, #DMA_HI
    bhi     win_bad
    tst     r5, #PERM_W
    beq     win_store
    cmp     r8, #WRITE_LO
    blo     win_bad
    LDA     r2, FRAMEBUF
    ldr     r3, [r2]                    @ pool header [pool, pool+UP_MIN_OFF)
    add     r12, r3, #UP_MIN_OFF
    cmp     r8, r12
    bhs     1f
    cmp     r3, r1
    blo     win_bad
1:  ldr     r3, [r2, #8]                @ WCODE_PTR: this code, every byte of it
    ldr     r12, mem_body_len
    add     r12, r3, r12
    cmp     r8, r12
    bhs     win_store
    cmp     r3, r1
    blo     win_bad
win_store:
    cmp     r5, #0
    moveq   r8, #0
    moveq   r9, #0
    str     r8, [r7]
    str     r9, [r7, #4]
    str     r5, [r7, #8]
    mov     r0, #0
    b       win_reply
win_bad:
    mov     r0, #2
win_reply:
    str     r0, [r6, #20]
    mov     r9, #4
    b       upload_reply

/* CMD 9: "MEM1", XFER_MAX, pool, pool size, then the 8 windows: 112 bytes,
 * under BULK_MIN, so it goes out in CPU-copied frames like any short reply. */
mem_caps:
    LDA     r0, FRAMEBUF
    ldr     r1, [r0]
    add     r8, r1, #CAP_OFF            @ build it in the capture buffer
    LDA     r2, 0x314D454D              @ "MEM1"
    str     r2, [r8]
    LDA     r2, XFER_MAX
    str     r2, [r8, #4]
    str     r1, [r8, #8]
    mov     r2, #WPOOL_BYTES
    str     r2, [r8, #12]
    add     r1, r1, #WIN_OFF
    add     r2, r8, #16
    mov     r3, #(WIN_SLOTS * 3)
1:  ldr     r0, [r1], #4
    str     r0, [r2], #4
    subs    r3, r3, #1
    bne     1b
    mov     r0, #0
    strh    r0, [r6, #6]
    mov     r9, #(16 + WIN_SLOTS * 12)
    b       reply_entry

/* CMD 7: payload {address, length, options}.  The block goes out in ONE
 * transfer from where it lies, no header: the host asked for exactly this many
 * bytes.  A refusal is a 64-byte frame instead, which ends the host's read on
 * a short packet and carries the request's sequence and a non-zero status. */
mem_read:
    bl      frame_ok
    cmp     r0, #0
    movne   r0, #1
    bne     mem_refuse
    ldr     r8, [r6, #20]
    ldr     r9, [r6, #24]
    ldr     r7, [r6, #28]
    LDA     r0, XFER_MAX
    cmp     r9, r0
    movhi   r0, #2
    bhi     mem_refuse
    mov     r0, r8
    mov     r1, r9
    mov     r2, #PERM_R
    bl      range_ok
    cmp     r0, #0
    bne     mem_refuse
    tst     r7, #OPT_SYNC               @ what the CPU wrote, out to DRAM
    beq     1f
    LDA     r12, F_DCACHE
    blx     r12
1:  mov     r0, #5
    str     r0, [r4, #0x34]             @ phase 5: MEMR block
    bl      arm_in_block
    str     r0, [r4, #0x18]
    cmp     r0, #0
    bne     fault
    mov     r0, #LOG_IN
    mov     r1, #MEM_TMO
    add     r1, r1, r9, lsr #15
    bl      wait_done
    str     r0, [r4, #0x1C]
    cmp     r0, #0
    bne     fault
    b       reply_done

/* CMD 6: payload {address, length, options}.  Two phases, so the host never
 * sends a byte the worker has not agreed to put somewhere:
 *   1. check, arm the OUT TRB at the destination, answer READY {0, addr, armed}
 *   2. the host sends exactly `length` bytes; answer DONE {0, received, crc}
 * The data TRB is armed BEFORE READY goes out, so the host cannot outrun it. */
mem_write:
    bl      frame_ok
    cmp     r0, #0
    movne   r0, #1
    bne     mem_refuse
    ldr     r8, [r6, #20]               @ destination
    ldr     r9, [r6, #24]               @ announced length
    ldr     r7, [r6, #28]               @ options
    cmp     r9, #0
    moveq   r0, #2
    beq     mem_refuse
    LDA     r0, XFER_MAX
    cmp     r9, r0
    movhi   r0, #2
    bhi     mem_refuse
    add     r5, r9, #0x3FC
    add     r5, r5, #3
    bic     r5, r5, #0x3FC
    bic     r5, r5, #3                  @ r5 = armed: whole 1 KiB packets
    mov     r0, r8
    mov     r1, r5                      @ the window must hold all of it
    mov     r2, #PERM_W
    bl      range_ok
    cmp     r0, #0
    bne     mem_refuse
    tst     r7, #OPT_SYNC               @ no dirty line may land on top later
    beq     1f
    LDA     r12, F_DCACHE
    blx     r12
1:  mov     r0, #TO_BUS
    add     r0, r8, r0
    LDA     r1, TRB_OUT
    ldr     r1, [r1]
    str     r0, [r1]
    mov     r0, #0
    str     r0, [r1, #4]
    str     r5, [r1, #8]
    movw    r0, #TRB_ARMED
    str     r0, [r1, #12]
    dsb
    mov     r0, #PHYS_OUT
    LDA     r12, FN_STARTX
    blx     r12
    str     r0, [r4, #0x10]
    cmp     r0, #0
    beq     2f
    mov     r0, #1
    str     r0, [r4, #0x08]             @ next arm_out ends whatever is left
    mov     r0, #5
    b       mem_refuse

2:  mov     r0, #2
    str     r0, [r4, #0x34]             @ phase 2: data TRB armed
    mov     r0, #0                      @ READY
    str     r0, [r6, #20]
    str     r8, [r6, #24]
    str     r5, [r6, #28]
    mov     r1, #12
    bl      send_status
    cmp     r0, #0
    bne     mem_write_abort
    mov     r0, #3
    str     r0, [r4, #0x34]             @ phase 3: READY reached the host

    mov     r0, #LOG_OUT                @ the host sends the moment READY lands
    mov     r1, #MEM_TMO
    add     r1, r1, r5, lsr #15
    bl      wait_done
    str     r0, [r4, #0x14]
    cmp     r0, #0
    bne     mem_write_lost
    bl      out_owned
    mov     r0, #4
    str     r0, [r4, #0x34]             @ phase 4: the data is in

    LDA     r0, TRB_OUT                 @ received = armed - what is left
    ldr     r0, [r0]
    ldr     r0, [r0, #8]
    bic     r0, r0, #0xFF000000
    sub     r9, r5, r0
    tst     r7, #OPT_SYNC               @ drop lines fetched while DMA ran
    beq     3f
    LDA     r12, F_DCACHE
    blx     r12
3:  mov     r3, #0
    tst     r7, #OPT_CRC
    beq     4f
    mov     r0, r8
    mov     r1, r9
    bl      crc_block
4:  mov     r0, #0                      @ DONE
    str     r0, [r6, #20]
    str     r9, [r6, #24]
    str     r3, [r6, #28]
    mov     r1, #12
    bl      send_status
    cmp     r0, #0
    bne     fault
    b       reply_done

mem_write_lost:
    mov     r0, #LOG_OUT                @ nothing may land after we give up
    LDA     r12, FN_ENDX
    blx     r12
    mov     r0, #6
    str     r0, [r6, #20]
    mov     r1, #4
    bl      send_status                 @ best effort: the host may be gone
    b       fault
mem_write_abort:
    mov     r0, #LOG_OUT                @ READY never reached the host
    LDA     r12, FN_ENDX
    blx     r12
    b       fault

/* Tell the USB driver EP 0x01 OUT is in use, between our transfers too.
 *
 * Its own StartTransfer (0xC01E6B80) sets OUT_BUSY when a bulk OUT starts and
 * its completion handler (0xC01E7780) clears it.  While it is clear, an
 * XferNotReady on the endpoint -- the host sending before anything is armed --
 * makes whoever is waiting on "any event" (usbTask, FUN_c0033b80, which runs
 * whether or not PTP does) call FUN_c01e76b8, which arms OUT on the firmware's
 * buffer [0xC3025744] and starts it.  The host's next command lands there and
 * the worker never sees it.  Measured: 1 MEMR in 10 lost this way, the lost
 * frame found whole in that buffer (camera, 2026-10-06); the 1-in-20..60 PUT
 * losses and PTP's 0x2003 answers were the same race.
 * So it is set again the moment a transfer of ours completes.  Clobbers r0, r1. */
.equ OUT_BUSY,    0xC31E3974
out_owned:
    LDA     r0, OUT_BUSY
    mov     r1, #1
    str     r1, [r0]
    bx      lr

/* r0 = logical endpoint, r1 = milliseconds -> r0 = 0 once that endpoint's TRB
 * is back from the controller (HWO clear), -1 if it is still out at the end.
 *
 * Every wait in this worker goes through here, because the firmware's Wait
 * answers a different question: whether the completion BIT was seen, and the
 * bit is shared.  usbTask waits on "any event" (FUN_c01e9380(-1)) and the same
 * dispatcher clears what it handles, so a completion can be consumed before or
 * instead of ours.  Measured: a MEMW frame sitting whole in our frame buffer
 * while Wait(OUT) ran out its 50 s; a MEMW data phase "never arriving" after
 * all four bytes did (camera, 2026-10-06, fault log).  The TRB cannot be
 * consumed by anybody, so it is what decides; Wait only sleeps between looks,
 * and a stale bit that wakes it early costs one more look, nothing else. */
.equ WAIT_SLICE,  20
wait_done:
    push    {r4, r5, r6, lr}
    mov     r4, r0
    mov     r5, r1
    LDA     r6, TRB_OUT
    cmp     r4, #LOG_OUT
    beq     1f
    LDA     r6, TRB_IN
1:  ldr     r0, [r6]
    ldr     r0, [r0, #12]
    tst     r0, #1                      @ HWO: still the controller's
    beq     8f
    cmp     r5, #0
    ble     9f
    mov     r0, r4
    mov     r1, #WAIT_SLICE
    LDA     r12, FN_WAIT
    blx     r12
    sub     r5, r5, #WAIT_SLICE
    b       1b
8:  mov     r0, #0
    pop     {r4, r5, r6, pc}
9:  mvn     r0, #0
    pop     {r4, r5, r6, pc}

/* r0 = refusal code: one frame carrying it, then the next round. */
mem_refuse:
    str     r0, [r6, #20]
    mov     r1, #4
    bl      send_status
    cmp     r0, #0
    bne     fault
    b       reply_done

/* r0 = status (also the header's), r1 = payload bytes already at r6+20.
 * -> r0 = arm or wait result, 0 when the host has the frame. */
send_status:
    push    {r5, lr}
    strh    r1, [r6, #12]
    strh    r0, [r6, #14]
    mov     r0, #0
    strh    r0, [r6, #6]
    str     r0, [r6, #16]
    mov     r0, r6
    mov     r1, #FRAME
    bl      crc_block
    str     r3, [r6, #16]
    bl      arm_in
    str     r0, [r4, #0x18]
    cmp     r0, #0
    bne     1f
    mov     r0, #LOG_IN
    movw    r1, #TIMEOUT_IN
    bl      wait_done
    str     r0, [r4, #0x1C]
1:  pop     {r5, pc}

/* The request frame at r6: magic, version 1, flags 0, CRC over 64 bytes.
 * -> r0 = 0 when it is ours.  Clobbers r0-r3, ip. */
frame_ok:
    push    {r7, lr}
    ldr     r0, [r6]
    LDA     r1, 0x48535046              @ "FPSH"
    cmp     r0, r1
    bne     1f
    ldrb    r0, [r6, #4]
    cmp     r0, #1
    bne     1f
    ldrh    r0, [r6, #6]
    cmp     r0, #0
    bne     1f
    ldr     r7, [r6, #16]
    mov     r0, #0
    str     r0, [r6, #16]
    mov     r0, r6
    mov     r1, #FRAME
    bl      crc_block
    str     r7, [r6, #16]
    cmp     r3, r7
    moveq   r0, #0
    beq     2f
1:  mov     r0, #1
2:  pop     {r7, pc}

/* r0 = address, r1 = length, r2 = permission wanted.
 * -> r0 = 0 when ONE window holds all of [r0, r0+r1) with that permission,
 *    2 for a zero or wrapping range, 4 for none.  Clobbers r0-r3, ip. */
range_ok:
    push    {r4, r5, r6, lr}
    cmp     r1, #0
    beq     8f
    adds    r1, r0, r1                  @ r1 = end, exclusive
    bcs     8f
    LDA     r3, FRAMEBUF                @ the worker's pool, implicitly
    ldr     r3, [r3]
    tst     r2, #PERM_W
    addne   r12, r3, #UP_MIN_OFF        @ writable only past the header
    moveq   r12, r3
    add     r3, r3, #WPOOL_BYTES
    cmp     r0, r12
    blo     2f
    cmp     r1, r3
    bls     7f
2:  LDA     r3, FRAMEBUF
    ldr     r3, [r3]
    add     r3, r3, #WIN_OFF
    mov     r4, #WIN_SLOTS
3:  ldr     r5, [r3, #8]                @ perm
    and     r6, r5, r2
    cmp     r6, r2
    bne     4f
    ldr     r5, [r3]                    @ base
    cmp     r0, r5
    blo     4f
    ldr     r6, [r3, #4]
    add     r5, r5, r6                  @ window end
    cmp     r1, r5
    bls     7f
4:  add     r3, r3, #12
    subs    r4, r4, #1
    bne     3b
    mov     r0, #4
    pop     {r4, r5, r6, pc}
7:  mov     r0, #0
    pop     {r4, r5, r6, pc}
8:  mov     r0, #2
    pop     {r4, r5, r6, pc}

/* The body's length, from inside the body: wbody_len sits before `wbody` and
 * is not copied into the code allocation this runs from. */
mem_body_len:   .word   wbody_end - wbody

/* ==========================================================================
 * reply — send r9 bytes from r8.
 *
 * Small replies go 44 to a frame, every frame but the last setting flags bit 0.
 * The received header is reused so the daemon's magic and sequence checks still
 * pass.
 *
 * Large ones do not.  Forty-four bytes per 64-byte frame, one armed transfer and
 * one blocking wait apiece, is 1.1% of what the endpoint can carry -- it is bulk,
 * 1024-byte packets, burst 3, so 4096 bytes fit in a single transfer.  Reading a
 * 250 KB file that way took eighty thousand round trips at 160 microseconds each,
 * and the slowness had nothing to do with the card or the link.
 *
 * So: one header frame saying how many bytes follow and carrying their CRC-32,
 * then the block itself in a single transfer, straight out of the capture buffer
 * with no copy.  Three thousand bytes go from 137 transfers to two.
 * ========================================================================== */
reply_entry:
    /* The host can say in the command frame that it already knows how many bytes
     * are coming and wants only those. That removes the header frame, and with
     * it a race: the block's TRB is armed after the header transfer completes,
     * so a host that reads the header and immediately asks for the block can
     * arrive before there is anywhere to put it. Nine percent of large replies
     * failed that way, and a failure costs a 200 ms timeout -- which was the
     * whole difference between 4 ms and 22 ms an exchange. */

    /* Read here and not at the loop head below.  fill_header writes this same
     * field to say another frame follows, so a reply of more than 44 bytes came
     * back round, read its own 1, and dived into the raw path -- which broke
     * every reply longer than one frame, `version` included. */
    ldrh    r0, [r6, #6]
    tst     r0, #1
    bne     reply_raw

    cmp     r9, #BULK_MIN
    bhs     reply_bulk

reply:
    mov     r5, r9
    cmp     r5, #CHUNK
    movhi   r5, #CHUNK
    sub     r9, r9, r5

    add     r7, r6, #20
    mov     r0, #0
copy_payload:
    cmp     r0, r5
    bhs     pad_payload
    ldrb    r1, [r8, r0]
    strb    r1, [r7, r0]
    add     r0, r0, #1
    b       copy_payload
pad_payload:
    cmp     r0, #CHUNK
    bhs     fill_header
    mov     r1, #0
    strb    r1, [r7, r0]
    add     r0, r0, #1
    b       pad_payload

fill_header:
    add     r8, r8, r5                  @ advance for the next frame
    cmp     r9, #0
    movne   r0, #1
    moveq   r0, #0
    strh    r0, [r6, #6]                @ flags: more to come
    strh    r5, [r6, #12]               @ payload_length
    mov     r0, #0
    strh    r0, [r6, #14]               @ status
    str     r0, [r6, #16]               @ crc field zeroed for the calculation

    mov     r0, r6
    mov     r1, #FRAME
    bl      crc_block
    str     r3, [r6, #16]

    bl      arm_in
    str     r0, [r4, #0x18]
    cmp     r0, #0
    bne     fault
    mov     r0, #LOG_IN
    movw    r1, #TIMEOUT_IN
    bl      wait_done
    str     r0, [r4, #0x1C]
    cmp     r0, #0
    bne     fault

    cmp     r9, #0
    bgt     reply

reply_done:
    mov     r0, #0
    LDA     r1, CAP_PTR                 @ one command's answer, not the next one's
    str     r0, [r1]
    str     r0, [r4, #0x08]             @ clean round: nothing to end next time
    ldr     r0, [r4, #0x20]
    add     r0, r0, #1
    str     r0, [r4, #0x20]
    b       next_round

fault:
    LDA     r0, FRAMEBUF
    ldr     r0, [r0]
    add     r0, r0, #FAULT_LOG
    ldr     r1, [r0]
    add     r2, r1, #1
    str     r2, [r0]
    and     r1, r1, #15
    add     r1, r0, r1, lsl #5
    add     r1, r1, #0x10               @ this fault's 32 bytes
    ldr     r2, [r4, #0x0C]
    str     r2, [r1]                    @ rounds
    ldr     r2, [r4, #0x34]
    str     r2, [r1, #4]                @ phase
    LDA     r0, FRAMEBUF
    ldr     r0, [r0]
    mov     r2, #UNCACHED
    add     r0, r0, r2                  @ the frame, uncached
    ldrb    r2, [r0, #5]
    str     r2, [r1, #8]                @ command
    ldr     r2, [r0, #8]
    str     r2, [r1, #12]               @ sequence
    ldr     r2, [r4, #0x10]
    str     r2, [r1, #16]
    ldr     r2, [r4, #0x14]
    str     r2, [r1, #20]
    ldr     r2, [r4, #0x18]
    str     r2, [r1, #24]
    ldr     r2, [r4, #0x1C]
    str     r2, [r1, #28]
    mov     r0, #0
    str     r0, [r4, #0x34]
    mov     r0, #1
    str     r0, [r4, #0x08]             @ next arm_out has something to clean up
    ldr     r0, [r4, #0x24]
    add     r0, r0, #1
    str     r0, [r4, #0x24]
    mov     r0, #1
    LDA     r12, FN_DELAY
    blx     r12
    b       next_round

hold:
    ldr     r0, [r4, #0x28]
    add     r0, r0, #1
    str     r0, [r4, #0x28]
    mov     r0, #50
    LDA     r12, FN_DELAY
    blx     r12
    b       next_round

/* --- arm EP 0x01 OUT for one command; returns the StartTransfer status ----- */
arm_out:
    push    {r4, r5, r6, r7, lr}

    /* Only after a round that went wrong.
     *
     * This used to end the transfer before every arm, tens of thousands of
     * times, to drop a TRB a timed-out round might have left. The controller
     * then rejected one Start Transfer in four -- a hundred and seventy-seven
     * faults out of a hundred and seventy-nine, all of them here, all of them
     * DEPCMD answering with a non-zero status, which on this controller means
     * it had no transfer resource to give. Ending and starting a transfer every
     * round is what spends them.
     *
     * A round that ended cleanly has nothing to drop. STATE+0x08 says whether
     * the last one did: `fault` sets it, `reply_done` clears it. */
    ldr     r0, [r4, #0x08]
    cmp     r0, #0
    beq     arm_out_go
    mov     r0, #LOG_OUT
    LDA     r12, FN_ENDX
    blx     r12                         @ drop the TRB the bad round left
arm_out_go:
    LDA     r0, FRAMEBUF
    ldr     r6, [r0]
    /* Forget the last command before waiting for the next.  A wake with no new
     * frame (a completion bit left over from some other transfer) must find
     * nothing to run, not run the previous command a second time. */
    mov     r0, #UNCACHED
    add     r0, r6, r0
    mov     r1, #0
    str     r1, [r0]                    @ magic
    str     r1, [r0, #4]                @ version, command, flags
    str     r1, [r0, #20]               @ "shl "
    mov     r0, #TO_BUS
    add     r5, r6, r0
    LDA     r0, TRB_OUT
    ldr     r7, [r0]
    str     r5, [r7]
    mov     r0, #0
    str     r0, [r7, #4]
    movw    r0, #UP_OUT_MAX            @ ordinary commands finish on a short packet
    str     r0, [r7, #8]
    movw    r0, #TRB_ARMED
    str     r0, [r7, #12]
    dsb
    mov     r0, #PHYS_OUT
    LDA     r12, FN_STARTX
    blx     r12
    pop     {r4, r5, r6, r7, pc}

/* --- the block alone, because the host said it knows the length ----------- */
reply_raw:
    /* Never answer with nothing.
     *
     * The host asked for a block of a length it already knows, and is sitting
     * in a bulk IN waiting for exactly that many bytes. Sending none used to
     * count the command as served and move on, which leaves the host with
     * nothing to do but time out -- and a timeout is a cancelled transfer, and
     * enough cancelled transfers and the firmware's own wait returns the error
     * that takes it to FUN_c01e6ef8, which ends every endpoint and drops the
     * device off the bus. The camera stays perfectly happy; only the USB side
     * of it disappears, and it does not come back on its own.
     *
     * One byte is not the answer the host wanted, but it ends the transfer.
     * A short read is an error it can see and retry in microseconds, instead
     * of a silence it can only wait out. */
    cmp     r9, #0
    moveq   r9, #1
    bl      arm_in_block
    cmp     r0, #0
    bne     fault
    mov     r0, #LOG_IN
    movw    r1, #TIMEOUT_IN
    bl      wait_done
    str     r0, [r4, #0x1C]
    cmp     r0, #0
    bne     fault
    b       reply_done

/* --- one header frame, then the block in a single transfer ---------------- */
reply_bulk:
    mov     r0, r8
    mov     r1, r9
    bl      crc_block                   @ r3 = CRC-32 of the block

    add     r7, r6, #20
    str     r3, [r7]                    @ payload[0..3] = the block's CRC
    mov     r0, #2
    strh    r0, [r6, #6]                @ flags: a raw block follows
    strh    r9, [r6, #12]               @ payload_length = its length
    mov     r0, #0
    strh    r0, [r6, #14]
    str     r0, [r6, #16]

    mov     r0, r6
    mov     r1, #FRAME
    bl      crc_block
    str     r3, [r6, #16]

    bl      arm_in
    str     r0, [r4, #0x18]
    cmp     r0, #0
    bne     fault
    mov     r0, #LOG_IN
    movw    r1, #TIMEOUT_IN
    bl      wait_done
    cmp     r0, #0
    bne     fault

    bl      arm_in_block
    cmp     r0, #0
    bne     fault
    mov     r0, #LOG_IN
    movw    r1, #TIMEOUT_IN
    bl      wait_done
    str     r0, [r4, #0x1C]
    cmp     r0, #0
    bne     fault
    b       reply_done

/* r0 = start, r1 = length -> r3 = CRC-32 (zlib's: reflected 0xEDB88320,
 * init and final ~0).  Clobbers r0-r2, ip.
 *
 * The firmware's own table-driven crc32(buf, len), 0xC06AE998, table at
 * 0xC2E3B794 -- a leaf in the image.  This used to be a bitwise loop here, and
 * that loop, running from the worker's heap block, cost 9 us a byte whether
 * the data was cached or not: 150 ms of every 16 KiB UP01 chunk, i.e. the
 * whole of the 0.1 MiB/s upload.  The firmware routine on the same uncached
 * frame: 0.6 us a byte, same value as zlib.crc32 (camera, 2026-10-06). */
.equ FW_CRC32,    0xC06AE998
crc_block:
    push    {r4, lr}
    LDA     r12, FW_CRC32
    blx     r12
    mov     r3, r0
    pop     {r4, pc}

/* --- arm EP 0x82 IN with r9 bytes at r8, no copy -------------------------- */
arm_in_block:
    push    {r4, r5, r6, r7, lr}
    mov     r0, #TO_BUS
    add     r5, r8, r0
    LDA     r0, TRB_IN
    ldr     r7, [r0]
    str     r5, [r7]
    mov     r0, #0
    str     r0, [r7, #4]
    str     r9, [r7, #8]
    movw    r0, #TRB_ARMED
    str     r0, [r7, #12]
    dsb
    mov     r0, #PHYS_IN
    LDA     r12, FN_STARTX
    blx     r12
    pop     {r4, r5, r6, r7, pc}

/* --- arm EP 0x82 IN with the 64-byte reply frame -------------------------- */
arm_in:
    push    {r4, r5, r6, r7, lr}
    LDA     r0, FRAMEBUF
    ldr     r6, [r0]
    mov     r0, #TO_BUS
    add     r5, r6, r0
    LDA     r0, TRB_IN
    ldr     r7, [r0]
    str     r5, [r7]
    mov     r0, #0
    str     r0, [r7, #4]
    mov     r0, #FRAME
    str     r0, [r7, #8]
    movw    r0, #TRB_ARMED
    str     r0, [r7, #12]
    dsb                                 @ frame and TRB visible before the DMA
    mov     r0, #PHYS_IN
    LDA     r12, FN_STARTX
    blx     r12
    pop     {r4, r5, r6, r7, pc}

    .align  2
wbody_end:
    .word   0                           @ a label at the very end of .text comes
                                        @ out as a relocation armasm refuses;
                                        @ this word keeps it inside, and is not
                                        @ copied (the length stops at the label)
