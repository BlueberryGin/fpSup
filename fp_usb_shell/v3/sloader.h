/* Loader v3 -- the contract between the 加載器 (LOADER.BIN) and a sup.
 *
 * Design: projects/usb-shell-sup/notes/LOADER_V3.md.  A sup is one file
 * \fpSup\NAME.BIN, read whole into one block of its own, entered once:
 *
 *     int entry(void *block, const struct sl_svc *svc, const char *path);
 *                                                     returns SL_KEEP / SL_RELEASE
 *
 * `path` (service version 2) is the file the sup was loaded from, e.g.
 * "\fpSup\10LOSS.BIN": NUL-terminated, in the 加載器's resident block, valid
 * for the whole boot.  A sup that writes back to its own file uses it, so a
 * renamed file (a merge page reordering prefixes) still finds itself.  A sup
 * that needs it sets min_svc = 2.
 *
 * The sup claims everything it will change BEFORE it writes anything; a claim
 * that fails means "do not load me", and the sup returns SL_RELEASE having
 * written nothing.  The loader keeps the books (who claimed what, the stock
 * words, the power-off write-back); it never decides for a sup.
 *
 * Service version 6 lets a resident supervisor hand a loaded sup back, load
 * one sup again, or replace a sup in place, without a reboot:
 *
 *     release(svc, sup_id)   undo that sup's journal and claims, restore its
 *                            sites, and free its block.  A kept sup may be
 *                            released when every kept sup loaded after it
 *                            added no journal entry and no claim -- so an
 *                            infrastructure sup may sit on top of a managed
 *                            module; a later patching sup still refuses with
 *                            SL_NOT_LAST.  A released sup stays in the table
 *                            as a released slot (a loader-internal state), so
 *                            load_one may reuse its entry instead of growing
 *                            the table.
 *     load_one(svc, path)    read one \fpSup\NAME.BIN, enter it exactly as the
 *                            boot load does, and append its claims/journal.
 *                            It never resets CAVE_BUMP and never lists or
 *                            re-enters the other sups.
 *     reload(svc, sup_id)    validate the file the sup was loaded from, then
 *                            release it and load that file again.  The old
 *                            sup is untouched until the new file has passed
 *                            every header/length check.
 *
 * All refuse with SL_BUSY while an entry is running.  release() restores the
 * sites live; a reloaded sup's stock words are in the journal again, so the
 * power-off write-back still leaves a stock image.  Releasing the loader's own
 * state, journal or power-off registration is not a thing these calls can do. */
#ifndef SLOADER_H
#define SLOADER_H
#include <stdint.h>

#define SL_MAGIC        0x31425346u      /* "FSB1" */
#define SL_HEADER_LEN   32u
#if defined(SL_ALLSUPS)
#define SL_SVC_VERSION  6u    /* 6: release, load_one, reload, after button */
#else
#define SL_SVC_VERSION  6u    /* 2: entry gets its own path in r2;
                                 3: holder, self, done_at_start, SL_SVC_AT;
                                 5: release, load_one;
                                 6: reload */
#endif

struct sl_header {
    uint32_t magic;        /* SL_MAGIC                                        */
    uint32_t header_len;   /* SL_HEADER_LEN                                   */
    uint32_t file_len;     /* the whole file, header included                 */
    uint32_t block_size;   /* >= file_len; the rest is zeroed (the sup's BSS) */
    uint32_t entry_off;    /* from the block's first byte; bit 0 = Thumb      */
    uint32_t sup_id;       /* four characters; a second file with the same id
                              is not loaded                                   */
    uint32_t version;
    uint32_t min_svc;      /* the service-table version it needs              */
};

enum { SL_KEEP = 0, SL_RELEASE = 1 };

/* What a claim says about the words it covers. */
enum {
    SL_CHAIN     = 1,   /* a hook that stacks: any number of CHAIN claims on the
                           same (addr, len); each layer calls the one below    */
    SL_EXCL      = 2,   /* only one owner: replacement hooks, value patches,
                           sites whose meaning two sups cannot share           */
    SL_SHARED_UI = 3,   /* uishare's own formats (string layers, FSHK/FSFV/
                           FSPG): any number of SHARED_UI claims on the same
                           range                                              */
};

/* claim() results. */
enum {
    SL_OK        = 0,
    SL_CONFLICT  = -1,  /* someone else holds it in a way that excludes this    */
    SL_FULL      = -2,  /* the registry or the journal has no room              */
    SL_BADARG    = -3,  /* zero length, not word-aligned, kind unknown          */
    /* release() / load_one() results (version 5). */
    SL_NOT_FOUND = -4,  /* no kept sup has that id                              */
    SL_BUSY      = -5,  /* a sup's entry is running                             */
    SL_NOT_LAST  = -6,  /* not the last kept sup: LIFO release only             */
};

/* The table a sup is handed.  Every function takes the table itself first:
 * the loader's code is position-independent and has no globals, so this
 * pointer is how it finds its books. */
struct sl_svc {
    uint32_t version;                     /* SL_SVC_VERSION                    */
    uint32_t boot_id;                     /* microseconds at load: unique per boot */
    /* Claim [addr, addr+len) of firmware memory.  `stock` is the firmware's own
     * bytes for that range, taken at build time from the reference image --
     * never read off the camera, because a warm restart keeps the last boot's
     * patches.  On the first claim of a range the loader puts the stock bytes
     * back if the memory differs (a patch a missed power-off left behind), and
     * journals them for the write-back at power-off. */
    int      (*claim)(const struct sl_svc *, uint32_t addr, uint32_t len,
                      const void *stock, uint32_t kind);
    /* A resource that is not a range of firmware words: a register, a flag,
     * an engine.  `id` is any agreed number (an MMIO address works). */
    int      (*claim_res)(const struct sl_svc *, uint32_t id, uint32_t kind);
    /* For a CHAIN site: where the word at `site` branches to now (ARM B/BL, or
     * Thumb-2 B.W/BL when `thumb`), 0 when it is not a branch -- that is, when
     * the caller is the first layer and must replay the stock instruction. */
    uint32_t (*chain_next)(const struct sl_svc *, uint32_t site, uint32_t thumb);
    /* `bytes` of cave, 8-aligned, for veneers.  0 when the arena is full. */
    uint32_t (*cave_alloc)(const struct sl_svc *, uint32_t bytes);
    /* Clean the D-cache and invalidate the I-cache: written code becomes code. */
    void     (*publish)(const struct sl_svc *);
    /* One line to the load log. */
    void     (*log)(const struct sl_svc *, const char *line);
    /* ---- version 3.  Appended: everything above stays where it is, card.S,
     * patch_sup.S and test_sup.S call through fixed offsets (+8 .. +28). ---- */
    /* The path ("\fpSup\10LOSS.BIN") of the i-th sup, in load order and the
     * running one included, that holds a claim covering `addr`; 0 past the
     * last.  Who is in the books -- not what memory says -- is how a sup
     * recognises another's layer on a shared site (uishare NESTED_HOOKS.md). */
    const char *(*holder)(const struct sl_svc *, uint32_t addr, uint32_t i);
    /* The path of the sup whose entry is running; 0 between entries. */
    const char *(*self)(const struct sl_svc *);
    /* LOAD_DONE_US as this loader found it.  The loader writes LOAD_DONE_US
     * only when the last entry has returned, so while entries run the word
     * still equals this -- a table a past boot left behind does not. */
    uint32_t done_at_start;
#if defined(SL_ALLSUPS) || defined(SL_SVC_BUTTON)
    /* ---- version 4 (only the loader with SYSTEM page 6, allsups.c).  A sup
     * that uses it defines SL_SVC_BUTTON and checks version >= 4 first. ----
     * The calling sup's cell in the All Sups grid hands it the AEL key: on
     * AEL there, fn(arg) runs on the UI thread (return quickly; its return
     * value is only shown in FT_Key's diagnostics).  `label`, if not 0, is
     * what the footer says next to the AEL icon (ASCII, < 24 bytes, kept by
     * pointer: put it in the sup's block).  What fn does is the sup's
     * business.  A second call replaces the first. */
    int      (*button)(const struct sl_svc *, int (*fn)(void *arg), void *arg, const char *label);
#endif
    /* ---- version 5/6.  Appended after every version 2/3/4 field, so a sup
     * that calls through a fixed offset keeps working (patch_sup.S uses
     * +8..+24; sloader.h's older entries stay where they were). ----
     * Hand back the SL_KEEP sup `sup_id`: restore its journal/claims, publish,
     * quiesce, and free its block.  Allowed when every kept sup loaded after
     * it added neither a journal entry nor a claim (SL_NOT_LAST otherwise);
     * SL_NOT_FOUND when no kept sup has that id; SL_BUSY while an entry runs.
     * `svc` must be the table the loader handed a sup. */
    int      (*release)(const struct sl_svc *, uint32_t sup_id);
    /* Load one file ("\fpSup\NAME.BIN") now, entering it the way the boot load
     * does but without resetting CAVE_BUMP or touching another sup.  Reuses
     * the released last slot when it has the same id, else appends while there
     * is room.  SL_OK, or SL_BUSY / SL_FULL / SL_BADARG / SL_CONFLICT, or the
     * entry's own SL_RELEASE when it declined. */
    int      (*load_one)(const struct sl_svc *, const char *path);
    /* ---- version 6.  Validate the file at the kept sup's own path, release
     * it, then load that file again.  A bad file returns before the old sup is
     * touched, so it stays loaded and patched.  SL_OK, or SL_BUSY /
     * SL_NOT_FOUND / SL_NOT_LAST / SL_FULL / SL_BADARG / SL_CONFLICT /
     * SL_RELEASE. */
    int      (*reload)(const struct sl_svc *, uint32_t sup_id);
};

/* The resident service table for the whole boot.  sl_boot clears this before
 * anything can fail; sl_load writes &svc once the table is initialized and
 * leaves it there.  A host (or a supervisor that did not stash the table
 * during an entry) reads it here.  Unlike SL_SVC_AT it stays nonzero after
 * the entries return. */
#define SL_BOOK_AT      0xC072F6F0u

/* While the loader runs entries this word holds &svc, and 0 otherwise.  For
 * code that is not handed the table: uishare is compiled into sups whose own
 * callers do not pass it.  Valid only when nonzero, version >= 3 and
 * done_at_start == [LOAD_DONE_US].  (0xC072F6FC was build_autorun's
 * STORE_VER_AT, a constant nothing read or wrote.) */
#define SL_SVC_AT       0xC072F6FCu

#endif
