/* A sup that takes its All Sups cell's AEL key (svc->button, service version
 * 4: the loader with SYSTEM page 6) -- for test_allsups.py and the first
 * camera trial.  Its handler counts the presses and answers the count, which
 * the loader shows in FT_Key's diagnostics (2000 + cell + answer x 100).
 * Position-independent: adr only.  Writes nothing in the firmware. */
.syntax unified
.arm
.text

.equ SVC_VERSION, 0
.equ SVC_BUTTON,  44

entry:
    push    {r4, lr}
    mov     r4, r1
    ldr     r0, [r4, #SVC_VERSION]
    cmp     r0, #4
    blo     1f                          @ an older loader: no button, still loaded
    mov     r0, r4
    adr     r1, handler
    adr     r2, presses                 @ its argument: the counter
    adr     r3, label
    ldr     ip, [r4, #SVC_BUTTON]
    blx     ip
1:  mov     r0, #0                      @ SL_KEEP
    pop     {r4, pc}

/* int handler(int *presses) */
handler:
    ldr     r1, [r0]
    add     r1, r1, #1
    str     r1, [r0]
    mov     r0, r1
    bx      lr

.align 2
presses: .word 0
label:   .asciz "Settings"
