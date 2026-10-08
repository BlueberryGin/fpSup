/* fpshd — host side of the camera USB shell.
 *
 * The camera runs its stock PTP gadget with the class handler removed, so the
 * bulk pipes belong to us and the interface reports itself as vendor-specific.
 *
 *   transport : interface 0, EP 0x01 OUT (commands) / EP 0x82 IN (replies)
 *   frame     : FPSH v1, 64 bytes; a long shell command spills past byte 64 of
 *               the same OUT transfer, because the camera reads it as a string
 *   upload    : CMD 4 sends up to 16320 binary bytes in one 16 KiB EP01 OUT;
 *               CMD 5 probes current worker support and its accepted tuple
 *   replies   : may be chunked — flags bit 0 means another frame follows
 *
 * Socket protocol, one line in and one line out:
 *   PING            -> OK pong seq=<n>
 *   SHL <line>      -> OK <output, with newlines escaped as \n>
 *   STATUS          -> OK name=fpshd ...
 *   QUIT            -> OK bye
 *   EP83 <bytes> <shellline> -> OK83 <bytes> <seq>\n then exactly <bytes>
 *                             binary bytes, or ERR ep83 ... quarantine=<0|1>
 *   UCAPS           -> OKUC <max> <seq> <addr> <len> <crc>
 *   PUT <addr> <len> <crc>\n<binary> -> OKU <seq> <addr> <len> <crc>
 * EP83 is a HOST CANDIDATE only: it requires a separately reviewed camera
 * sender. It installs nothing and proves neither endpoint ownership on the
 * camera nor safe reuse of its DMA storage. Do not send an ordinary shell
 * command here: it must produce exactly the requested EP83 payload and then
 * one original FPSH terminal on EP82. No retries or recovery on this path.
 */
#include <libusb.h>
#include <errno.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/mman.h>
#include <fcntl.h>
#include <unistd.h>

#define FRAME_SIZE   64
#define PAYLOAD_SIZE 44
#define IFACE        0
#define EP_OUT       0x01
#define EP_IN        0x82
#define EP_TELEMETRY 0x83
#define EP83_MAX_BYTES 65536u
#define EP83_CHUNK_BYTES 16384u
#define EP83_DEADLINE_MS 2000u
#define FPSHD_VERSION "3.2.0"
/* Was 16384, the size of the shell's capture buffer -- because everything used
 * to be copied through it. The worker can now point its TRB at wherever the
 * answer already is, and a TRB carries a 24-bit length, so a reply can be
 * megabytes. Kept well under that: these are buffers, not a promise. */
#define MAX_REPLY    (16 * 1024 * 1024)   /* one TRB's 24-bit length */
#define CMD_LINE_MAX 16384
#define USB_OUT_SIZE 1024
#define CMD_MAX      512
#define UP_OUT_MAX   16384u
#define UP_DATA_MAX  (UP_OUT_MAX - FRAME_SIZE)

enum { CMD_PING = 1, CMD_SHL = 3, CMD_UPLOAD = 4, CMD_UPLOAD_CAPS = 5 };

typedef struct __attribute__((packed)) {
    uint8_t  magic[4];
    uint8_t  version, command;
    uint16_t flags;
    uint32_t sequence;
    uint16_t payload_length, status;
    uint32_t checksum;
    uint8_t  payload[PAYLOAD_SIZE];
} fpsh_frame;
_Static_assert(sizeof(fpsh_frame) == FRAME_SIZE, "frame must be 64 bytes");

static uint16_t g_vid = 0x1003, g_pid = 0xc432;
/* A reply that has not arrived in this long is lost, not slow: the transport
 * drops whole commands, roughly one in ten, and waiting five seconds for one of
 * them turned a 550 character `mem get` into an average of 774 ms. The caller
 * retries, which costs a fifth of a second instead of five. Commands that
 * genuinely take longer -- an SD write is up to 692 ms -- report through their
 * own status word, so a timeout there is not a failure either. */
static unsigned g_timeout = 200;
/* Two hundred milliseconds is right for a command that has to open a file, and
 * badly wrong for one that answers in under two. A reply goes missing every few
 * dozen chunks -- retrying costs nothing, waiting for the timeout costs more
 * than the whole rest of the read -- so a caller that knows its command is fast
 * can say so per request with a TMO prefix. */
static unsigned g_req_timeout = 0;
#define TMO (g_req_timeout ? g_req_timeout : g_timeout)
static int g_was_bulk;      /* the last exchange came back as one raw block */
/* Replies under 128 bytes come back as frames rather than one block, and a
 * line-based socket cannot carry those bytes as they are -- a NUL ends the
 * string, a newline ends the line. Big replies were already hex-encoded because
 * they arrive as a block; HEX says to do it for a small one too, so a caller
 * reading memory gets the same thing back whatever the length. */
static int g_want_hex;
static struct timespec g_req_t0;            /* FPSHD_TIMING: request accepted */
static double ms_since(const struct timespec *a)
{
    struct timespec b;
    clock_gettime(CLOCK_MONOTONIC, &b);
    return (b.tv_sec - a->tv_sec) * 1e3 + (b.tv_nsec - a->tv_nsec) / 1e6;
}
/* "ERR shl" says a transfer failed and nothing else, so every theory about why
 * had to be guessed at. libusb already knows. */
static char g_why[96];
static libusb_context      *g_usb;
static libusb_device_handle*g_cam;
static uint32_t             g_seq;
static volatile sig_atomic_t g_stop;
/* Sticky for this daemon lifetime, including after close/reopen. A failed
 * trigger may already be executing on the camera. Blocking the old exchange
 * path also prevents its drain/clear-halt policy from touching that state.
 * Restart/QUIT is NOT evidence that camera DMA or resident code can be freed. */
static int g_ep83_quarantine;
static char g_ep83_fault[96];

static uint32_t crc32(const uint8_t *p, size_t n)
{
    uint32_t c = 0xFFFFFFFFu;
    for (size_t i = 0; i < n; i++) {
        c ^= p[i];
        for (int b = 0; b < 8; b++) c = (c >> 1) ^ (0xEDB88320u & -(c & 1));
    }
    return ~c;
}

static int cam_open(void)
{
    if (g_cam) return 0;
    g_cam = libusb_open_device_with_vid_pid(g_usb, g_vid, g_pid);
    if (!g_cam) {
        snprintf(g_why, sizeof g_why, "open %04x:%04x failed", g_vid, g_pid);
        return -1;
    }
    libusb_set_auto_detach_kernel_driver(g_cam, 1);
    int rc = libusb_claim_interface(g_cam, IFACE);
    if (rc != 0) {
        snprintf(g_why, sizeof g_why, "claim iface %d rc=%d(%s)",
                 IFACE, rc, libusb_error_name(rc));
        libusb_close(g_cam); g_cam = NULL; return -1;
    }
    return 0;
}

static void cam_close(void)
{
    if (!g_cam) return;
    libusb_release_interface(g_cam, IFACE);
    libusb_close(g_cam);
    g_cam = NULL;
}

static int transport_quarantined(void)
{
    if (!g_ep83_quarantine) return 0;
    snprintf(g_why, sizeof g_why, "quarantine=1 %.70s", g_ep83_fault);
    return 1;
}

static int ep83_fault(const char *why)
{
    if (!g_ep83_quarantine) {
        snprintf(g_ep83_fault, sizeof g_ep83_fault, "%s", why);
        g_ep83_quarantine = 1;
    }
    snprintf(g_why, sizeof g_why, "%s", g_ep83_fault);
    return -1;
}

static uint64_t monotonic_ms(void)
{
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t) != 0) return 0;
    return (uint64_t)t.tv_sec * 1000u + (uint64_t)t.tv_nsec / 1000000u;
}

/* libusb timeout=0 means infinite; never pass it after deadline expiry. */
static unsigned ep83_remaining(uint64_t deadline)
{
    uint64_t now = monotonic_ms();
    if (!now || now >= deadline || g_stop) return 0;
    return (unsigned)(deadline - now);
}

static int ep83_args(const char *request, size_t *expected, const char **line)
{
    if (strncmp(request, "EP83 ", 5)) return -1;
    const char *p = request + 5;
    if (*p < '0' || *p > '9') return -1;
    errno = 0;
    char *end;
    unsigned long n = strtoul(p, &end, 10);
    if (errno || !n || n > EP83_MAX_BYTES || *end != ' ') return -1;
    while (*end == ' ') end++;
    /* Reserve four bytes for "shl " and one for its terminating NUL. */
    size_t len = strlen(end);
    if (!len || len > CMD_MAX - 5 || strpbrk(end, "\r\n")) return -1;
    *expected = (size_t)n;
    *line = end;
    return 0;
}

/* Only one alternate setting is accepted, so no unobserved altsetting can
 * select a different endpoint. Host descriptors are necessary but do not
 * establish camera-side runtime mode, native PTP exclusion, or DMA ownership. */
static int ep83_descriptor(int *mps83, int *mps82)
{
    struct libusb_config_descriptor *cfg = NULL;
    int rc = libusb_get_active_config_descriptor(libusb_get_device(g_cam), &cfg);
    if (rc != 0 || !cfg) {
        snprintf(g_why, sizeof g_why, "descriptor rc=%d", rc);
        return -1;
    }
    int found = 0, good = 1, out = 0;
    *mps83 = *mps82 = 0;
    for (int i = 0; i < cfg->bNumInterfaces; i++) {
        const struct libusb_interface *iface = &cfg->interface[i];
        for (int j = 0; j < iface->num_altsetting; j++) {
            const struct libusb_interface_descriptor *alt = &iface->altsetting[j];
            if (alt->bInterfaceNumber != IFACE) continue;
            found++;
            if (iface->num_altsetting != 1 || alt->bAlternateSetting != 0)
                good = 0;
            for (int k = 0; k < alt->bNumEndpoints; k++) {
                const struct libusb_endpoint_descriptor *ep = &alt->endpoint[k];
                if (ep->bEndpointAddress != EP_TELEMETRY &&
                    ep->bEndpointAddress != EP_IN && ep->bEndpointAddress != EP_OUT)
                    continue;
                int mps = ep->wMaxPacketSize;
                if ((ep->bmAttributes & LIBUSB_TRANSFER_TYPE_MASK) !=
                    LIBUSB_TRANSFER_TYPE_BULK ||
                    (mps != 64 && mps != 512 && mps != 1024)) good = 0;
                if (ep->bEndpointAddress == EP_TELEMETRY) {
                    if (*mps83) good = 0;
                    *mps83 = mps;
                } else if (ep->bEndpointAddress == EP_IN) {
                    if (*mps82) good = 0;
                    *mps82 = mps;
                } else out++;
            }
        }
    }
    libusb_free_config_descriptor(cfg);
    if (!good || found != 1 || out != 1 || !*mps83 || !*mps82) {
        snprintf(g_why, sizeof g_why, "requires iface0 alt0 bulk EP01/82/83 MPS64/512/1024");
        return -1;
    }
    return 0;
}

/* One trigger, a finite byte prefix from EP83, then one matching EP82 terminal.
 * No stale-frame skipping, drain, clear-halt, reset, re-claim or retry. A
 * validated terminal demonstrates handler return, not optical/AF validity or
 * permission to detach a hook. The camera sender must independently verify
 * DMA completion before producing that terminal. */
static int ep83_exchange(const char *request, unsigned char *out, size_t cap,
                         size_t *received, uint32_t *sequence, uint64_t *deadline_out)
{
    g_why[0] = 0;
    *received = 0;
    if (transport_quarantined()) return -1;
    size_t expected;
    const char *line;
    if (ep83_args(request, &expected, &line) || expected > cap) {
        snprintf(g_why, sizeof g_why, "usage: EP83 <1..65536> <shellline<=507>");
        return -1;
    }
    uint64_t now = monotonic_ms();
    if (!now) { snprintf(g_why, sizeof g_why, "clock unavailable"); return -1; }
    uint64_t deadline = now + EP83_DEADLINE_MS;
    *deadline_out = deadline;
    if (cam_open() != 0) return -1;
    int mps83, mps82;
    if (ep83_descriptor(&mps83, &mps82)) return -1;
    unsigned timeout = ep83_remaining(deadline);
    if (!timeout) { snprintf(g_why, sizeof g_why, "preflight deadline"); return -1; }
    if (g_seq == UINT32_MAX) {
        snprintf(g_why, sizeof g_why, "sequence exhausted"); return -1;
    }

    unsigned char txbuf[USB_OUT_SIZE] = {0};
    fpsh_frame *tx = (fpsh_frame *)txbuf;
    memcpy(tx->magic, "FPSH", 4);
    tx->version = 1;
    tx->command = CMD_SHL;
    tx->sequence = ++g_seq;
    *sequence = tx->sequence;
    size_t arglen = strlen(line) + 4;
    tx->payload_length = (uint16_t)arglen;
    memcpy(txbuf + 20, "shl ", 4);
    memcpy(txbuf + 24, line, arglen - 4);
    size_t txlen = (20 + arglen + 1 + 3) & ~(size_t)3;
    if (txlen < FRAME_SIZE) txlen = FRAME_SIZE;
    tx->checksum = crc32(txbuf, FRAME_SIZE);

    /* Persist the host intent to stderr before the non-idempotent OUT. Failure
     * to log means no trigger. fflush success is not disk-durability proof. */
    if (fprintf(stderr, "EP83 intent seq=%u expected=%zu host_candidate=1\n",
                *sequence, expected) < 0 || fflush(stderr) != 0) {
        snprintf(g_why, sizeof g_why, "intent log failed"); return -1;
    }
    timeout = ep83_remaining(deadline);
    if (!timeout) { snprintf(g_why, sizeof g_why, "intent deadline"); return -1; }
    int moved = 0;
    int rc = libusb_bulk_transfer(g_cam, EP_OUT, txbuf, (int)txlen, &moved, timeout);
    if (rc != 0 || moved != (int)txlen) {
        char why[96];
        snprintf(why, sizeof why, "OUT uncertain rc=%d moved=%d/%zu", rc, moved, txlen);
        return ep83_fault(why);
    }
    unsigned char chunk[EP83_CHUNK_BYTES];
    while (*received < expected) {
        timeout = ep83_remaining(deadline);
        if (!timeout) return ep83_fault("EP83 deadline");
        size_t left = expected - *received;
        size_t need = left < sizeof chunk ? left : sizeof chunk;
        size_t request_bytes = (need + (size_t)mps83 - 1) / (size_t)mps83 * (size_t)mps83;
        moved = 0;
        rc = libusb_bulk_transfer(g_cam, EP_TELEMETRY, chunk, (int)request_bytes,
                                  &moved, timeout);
        /* The final packet may be short only when it exactly fills remaining.
         * Any errored transfer, including one reporting all bytes, is uncertain. */
        if (rc != 0 || moved <= 0 || (size_t)moved > left ||
            (size_t)moved > request_bytes ||
            ((size_t)moved < request_bytes && (size_t)moved != left)) {
            char why[96];
            snprintf(why, sizeof why, "EP83 rc=%d moved=%d request=%zu remain=%zu",
                     rc, moved, request_bytes, left);
            return ep83_fault(why);
        }
        memcpy(out + *received, chunk, (size_t)moved);
        *received += (size_t)moved;
    }
    timeout = ep83_remaining(deadline);
    if (!timeout) return ep83_fault("terminal deadline");
    unsigned char terminal[1024];
    moved = 0;
    rc = libusb_bulk_transfer(g_cam, EP_IN, terminal, mps82, &moved, timeout);
    if (rc != 0 || moved != FRAME_SIZE) return ep83_fault("terminal transfer not exact FPSH64");
    fpsh_frame rx;
    memcpy(&rx, terminal, sizeof rx);
    uint32_t checksum = rx.checksum;
    rx.checksum = 0;
    if (memcmp(rx.magic, "FPSH", 4) || rx.version != 1 ||
        rx.command != CMD_SHL || rx.sequence != *sequence || rx.flags != 0 ||
        rx.status != 0 || rx.payload_length > PAYLOAD_SIZE ||
        crc32((const uint8_t *)&rx, sizeof rx) != checksum)
        return ep83_fault("terminal identity/status/flags/CRC mismatch");
    if (!ep83_remaining(deadline)) return ep83_fault("terminal exceeded deadline");
    return 0;
}

/* Bounded socket output; no camera operation occurs here. A partial response
 * is deliberately unusable, and a stalled consumer cannot hold this daemon
 * forever. SIGPIPE is ignored by main, as for the existing text protocol. */
static int ep83_write(int fd, const void *data, size_t len, uint64_t deadline)
{
    const unsigned char *p = data;
    while (len) {
        unsigned timeout = ep83_remaining(deadline);
        if (!timeout) return -1;
        struct pollfd f = {fd, POLLOUT, 0};
        int r = poll(&f, 1, (int)timeout);
        if (r < 0 && errno == EINTR) continue;
        if (r <= 0 || (f.revents & (POLLERR | POLLHUP | POLLNVAL))) return -1;
        ssize_t n = send(fd, p, len, MSG_DONTWAIT);
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) continue;
        if (n <= 0) return -1;
        p += n; len -= (size_t)n;
    }
    return 0;
}

/* Send one command frame, collect the (possibly chunked) reply into out.
 * Returns reply length, or -1. */
/* Read and discard anything still sitting on the IN pipe.
 *
 * Every failed exchange used to leave its late reply there, and the next command
 * would read that instead of its own, mismatch the sequence, and leave one
 * behind in turn. The failure rate climbed from 3% to 45% over a session that
 * way -- not a transport fault at all, but a queue nobody emptied. */
/* Clear whatever the camera has already sent and nobody collected.
 *
 * This used to read sixteen times into a 64-byte buffer, which clears a
 * kilobyte. A reply is up to sixteen of those, so a request that timed out left
 * most of its block in the pipe, and the retry was answered with it. Asking for
 * less than the endpoint is holding is itself an error, so the buffer has to be
 * at least one whole reply.
 *
 * Nothing here is about corruption. USB bulk carries its own CRC and retries in
 * hardware; every wrong byte was a right byte from the wrong request. */
static void drain_in(void)
{
    if (transport_quarantined()) return;
    static unsigned char junk[CMD_LINE_MAX];
    for (int i = 0; i < 8; i++) {
        int moved = 0;
        /* Short. Outlasting the camera's own three hundred milliseconds was
         * the obvious thing -- a block abandoned here may still be on its way,
         * and leaving it is leaving exactly what this is for. But a miss then
         * costs the host's timeout plus this, better than half a second, and at
         * five per cent of two thousand chunks that was most of a minute on a
         * 31 MB file: every measurement of "the link is slow" was this.
         *
         * A late block that slips past is not a problem any more. Each one
         * carries the address it was read from, so the next read sees it is
         * somebody else's answer, drops it and asks again -- a few milliseconds
         * instead of five hundred. */
        int rc = libusb_bulk_transfer(g_cam, EP_IN, junk, sizeof junk,
                                      &moved, 20);
        if (rc == LIBUSB_ERROR_PIPE) { libusb_clear_halt(g_cam, EP_IN); continue; }
        if (rc == LIBUSB_ERROR_OVERFLOW) continue;
        if (rc != 0 || moved == 0) break;
    }
}


static int exchange(uint8_t command, const char *arg, char *out, size_t outcap,
                    int raw_len)
{
    g_why[0] = 0;
    if (transport_quarantined()) return -1;
    if (cam_open() != 0) return -1;

    /* The camera arms a 1024-byte OUT TRB and reads the payload as a NUL-terminated
     * string, so a command longer than the 44-byte payload field simply spills past
     * offset 64 in the same transfer.  The CRC still covers only the first 64 bytes,
     * which is what the camera and this daemon agree on. */
    size_t arglen = arg ? strlen(arg) : 0;
    if (arglen > CMD_MAX - 1) arglen = CMD_MAX - 1;
    size_t txlen = 20 + arglen + 1;
    if (txlen < FRAME_SIZE) txlen = FRAME_SIZE;
    txlen = (txlen + 3) & ~3u;
    /* The capability query must end in a short packet even at full speed,
     * where a 64-byte frame is exactly one maximum-size OUT packet. */
    if ((command == CMD_UPLOAD_CAPS || command == 9 /* CMD_MEMCAPS */) &&
        txlen == FRAME_SIZE) txlen += 4;

    unsigned char txbuf[USB_OUT_SIZE];
    memset(txbuf, 0, txlen);
    fpsh_frame *txf = (fpsh_frame *)txbuf;
    memcpy(txf->magic, "FPSH", 4);
    txf->version  = 1;
    txf->command  = command;
    txf->sequence = ++g_seq;
    txf->payload_length = (uint16_t)arglen;
    if (arglen) memcpy(txf->payload, arg, arglen);
    if (raw_len > 0) txf->flags = 1;    /* the reply is raw, and this long */
    txf->checksum = crc32(txbuf, FRAME_SIZE);
    fpsh_frame tx = *txf;                       /* header copy for reply matching */

    g_was_bulk = 0;
    int moved = 0;
    int rc = libusb_bulk_transfer(g_cam, EP_OUT, txbuf, (int)txlen,
                                  &moved, TMO);
    if (rc != 0 || moved != (int)txlen) {
        snprintf(g_why, sizeof g_why, "cmd rc=%d(%s) moved=%d/%d",
                 rc, libusb_error_name(rc), moved, (int)txlen);
        if (rc == LIBUSB_ERROR_NO_DEVICE) cam_close();
        else if (rc == LIBUSB_ERROR_PIPE) libusb_clear_halt(g_cam, EP_OUT);
        return -1;
    }

    size_t used = 0;

    /* Told the length, so there is nothing to negotiate: one transfer, no header
     * frame, no window in which the host can outrun the camera's arming. */
    if (raw_len > 0) {
        if ((size_t)raw_len > outcap) {
        snprintf(g_why, sizeof g_why, "raw_len %d over cap %zu", raw_len, outcap);
        return -1;
    }
        moved = 0;
        struct timespec t0, t1;
        clock_gettime(CLOCK_MONOTONIC, &t0);
        rc = libusb_bulk_transfer(g_cam, EP_IN, (unsigned char *)out,
                                  raw_len, &moved, TMO);
        clock_gettime(CLOCK_MONOTONIC, &t1);
        if (getenv("FPSHD_TIMING"))         /* how long the IN itself took */
            fprintf(stderr, "raw %d B in %.3f ms (request->IN start %.3f ms)\n", moved,
                    (t1.tv_sec - t0.tv_sec) * 1e3 + (t1.tv_nsec - t0.tv_nsec) / 1e6,
                    (t0.tv_sec - g_req_t0.tv_sec) * 1e3 + (t0.tv_nsec - g_req_t0.tv_nsec) / 1e6);
        if (rc != 0 || moved != raw_len) {
            snprintf(g_why, sizeof g_why, "block rc=%d(%s) moved=%d/%d",
                     rc, libusb_error_name(rc), moved, raw_len);
            if (rc == LIBUSB_ERROR_NO_DEVICE) { cam_close(); return -1; }
            if (rc == LIBUSB_ERROR_PIPE) libusb_clear_halt(g_cam, EP_IN);
            drain_in();
            return -1;
        }
        g_was_bulk = 1;
        return moved;
    }

    int skipped = 0;
    for (int frame = 0; frame < CMD_LINE_MAX / PAYLOAD_SIZE + 1; frame++) {
        fpsh_frame rx;
        moved = 0;
        rc = libusb_bulk_transfer(g_cam, EP_IN, (unsigned char *)&rx, FRAME_SIZE,
                                  &moved, TMO);
        if (rc != 0 || moved != FRAME_SIZE) {
            snprintf(g_why, sizeof g_why, "frame%d rc=%d(%s) moved=%d/%d",
                     frame, rc, libusb_error_name(rc), moved, FRAME_SIZE);
            if (rc == LIBUSB_ERROR_NO_DEVICE) { cam_close(); return -1; }
            if (rc == LIBUSB_ERROR_PIPE) libusb_clear_halt(g_cam, EP_IN);
            drain_in();
            return -1;
        }
        if (memcmp(rx.magic, "FPSH", 4) || rx.version != 1) {
            snprintf(g_why, sizeof g_why, "bad magic %02x%02x%02x%02x ver %u",
                     rx.magic[0], rx.magic[1], rx.magic[2], rx.magic[3], rx.version);
            drain_in(); return -1; }
        uint32_t want = rx.checksum;
        rx.checksum = 0;
        if (crc32((const uint8_t *)&rx, FRAME_SIZE) != want) {
            snprintf(g_why, sizeof g_why, "frame crc");
            drain_in(); return -1; }
        /* A reply from an earlier command, arriving after that command gave
         * up waiting for it. Every frame says which request it belongs to, so
         * there is no need to guess and no need to throw the pipe away: read
         * past it -- including the block it announces, or the next read starts
         * in the middle of one -- and carry on looking for the answer to this
         * request.
         *
         * Draining instead was what made a single slow reply poison everything
         * after it. The drain cleared a kilobyte of a reply up to sixteen long,
         * the remainder answered the next request, and the two stayed one apart
         * until it looked like the endpoint had died. */
        if (rx.sequence != tx.sequence) {
            if (rx.flags & 2) {
                static unsigned char skip[CMD_LINE_MAX];
                uint32_t n = rx.payload_length;
                if (n > sizeof skip) { drain_in(); return -1; }
                int got = 0;
                libusb_bulk_transfer(g_cam, EP_IN, skip, (int)n, &got, TMO);
            }
            if (++skipped > 32) {
                snprintf(g_why, sizeof g_why,
                         "%d stale frames, last seq %u wanted %u",
                         skipped, rx.sequence, tx.sequence);
                drain_in(); return -1;
            }
            frame--;                    /* this one did not count */
            continue;
        }

        /* flags bit 1: the header is describing a block that follows in one
         * transfer, rather than carrying 44 bytes itself. Forty-four bytes per
         * 64-byte frame is 1.1% of what this endpoint can move -- it is bulk,
         * 1024-byte packets, burst 3 -- and reading a 250 KB file that way took
         * eighty thousand round trips. The block's CRC-32 rides in the header so
         * it is still checked. */
        if (rx.flags & 2) {
            g_was_bulk = 1;
            uint32_t n = rx.payload_length, want_block;
            memcpy(&want_block, rx.payload, 4);
            if (n >= outcap) { drain_in(); return -1; }
            moved = 0;
            rc = libusb_bulk_transfer(g_cam, EP_IN, (unsigned char *)out,
                                      (int)n, &moved, TMO);
            if (rc != 0 || moved != (int)n) {
                if (rc == LIBUSB_ERROR_PIPE) libusb_clear_halt(g_cam, EP_IN);
                drain_in();
                return -1;
            }
            if (crc32((const uint8_t *)out, n) != want_block) { drain_in(); return -1; }
            used = n;
            break;
        }

        uint16_t n = rx.payload_length;
        if (n > PAYLOAD_SIZE) n = PAYLOAD_SIZE;
        if (used + n < outcap) { memcpy(out + used, rx.payload, n); used += n; }
        if (!(rx.flags & 1)) break;          /* last frame */
    }
    out[used] = 0;
    return (int)used;
}

typedef struct {
    uint32_t sequence, address, length, checksum;
} upload_state;

/* A 68-byte capability request is safe against an old 1024-byte worker: it
 * simply replies with an empty FPSH frame. Never send the large OUT until the
 * current camera worker answers this exact versioned probe. */
static int upload_caps(upload_state *state)
{
    char answer[32];
    int n = exchange(CMD_UPLOAD_CAPS, NULL, answer, sizeof answer, 0);
    if (n < 0) return -1;
    if (n != 20 || memcmp(answer, "UP01", 4)) {
        snprintf(g_why, sizeof g_why, "worker has no UP01 capability");
        return -2;
    }
    memcpy(state, answer + 4, sizeof *state);
    return 0;
}

/* The destination is absolute, so a repeated request writes the same bytes.
 * A missing OUT or acknowledgment is still reported as uncertain: the caller
 * must query the worker's last accepted tuple before deciding to resend. */
static int upload_exchange(uint32_t address, const unsigned char *data,
                           size_t length, uint32_t checksum, uint32_t *sequence)
{
    g_why[0] = 0;
    if (transport_quarantined() || !g_cam || !length || length > UP_DATA_MAX ||
        (length & 3) || (address & 3)) {
        snprintf(g_why, sizeof g_why, "upload preflight");
        return -2;
    }
    if (g_seq == UINT32_MAX) {
        snprintf(g_why, sizeof g_why, "sequence exhausted");
        return -2;
    }

    unsigned char txbuf[UP_OUT_MAX] = {0};
    fpsh_frame *tx = (fpsh_frame *)txbuf;
    memcpy(tx->magic, "FPSH", 4);
    tx->version = 1;
    tx->command = CMD_UPLOAD;
    tx->sequence = ++g_seq;
    *sequence = tx->sequence;
    tx->payload_length = 12;
    memcpy(tx->payload, &address, 4);
    uint32_t n32 = (uint32_t)length;
    memcpy(tx->payload + 4, &n32, 4);
    memcpy(tx->payload + 8, &checksum, 4);
    tx->checksum = crc32(txbuf, FRAME_SIZE);
    memcpy(txbuf + FRAME_SIZE, data, length);
    size_t txlen = FRAME_SIZE + length;
    if (txlen < UP_OUT_MAX && txlen % 64 == 0) txlen += 4;

    int moved = 0;
    int rc = libusb_bulk_transfer(g_cam, EP_OUT, txbuf, (int)txlen, &moved, 1000);
    if (rc != 0 || moved != (int)txlen) {
        snprintf(g_why, sizeof g_why, "upload OUT uncertain rc=%d moved=%d/%zu",
                 rc, moved, txlen);
        if (rc == LIBUSB_ERROR_NO_DEVICE) cam_close();
        return -1;
    }

    fpsh_frame rx;
    moved = 0;
    rc = libusb_bulk_transfer(g_cam, EP_IN, (unsigned char *)&rx,
                              FRAME_SIZE, &moved, 1000);
    if (rc != 0 || moved != FRAME_SIZE) {
        /* Say what did arrive: a 12-byte answer on EP82 is not the worker's
         * (its frames are 64), and a PTP response container is 12 bytes. */
        const unsigned char *b = (const unsigned char *)&rx;
        snprintf(g_why, sizeof g_why, "upload ack uncertain rc=%d moved=%d "
                 "head=%02x%02x%02x%02x.%02x%02x%02x%02x.%02x%02x%02x%02x",
                 rc, moved, b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7],
                 b[8], b[9], b[10], b[11]);
        if (rc == LIBUSB_ERROR_NO_DEVICE) cam_close();
        return -1;
    }
    uint32_t frame_crc = rx.checksum;
    rx.checksum = 0;
    if (memcmp(rx.magic, "FPSH", 4) || rx.version != 1 ||
        rx.command != CMD_UPLOAD || rx.sequence != *sequence ||
        rx.flags != 0 || rx.status != 0 ||
        (rx.payload_length != 4 && rx.payload_length != 16) ||
        crc32((const uint8_t *)&rx, FRAME_SIZE) != frame_crc) {
        snprintf(g_why, sizeof g_why, "upload ack identity/CRC uncertain");
        return -1;
    }
    uint32_t outcome;
    memcpy(&outcome, rx.payload, 4);
    if (outcome != 0) {
        snprintf(g_why, sizeof g_why, "worker rejected upload code=%u", outcome);
        return -2;
    }
    uint32_t echoed[3];
    if (rx.payload_length != 16) {
        snprintf(g_why, sizeof g_why, "upload ack short");
        return -1;
    }
    memcpy(echoed, rx.payload + 4, sizeof echoed);
    if (echoed[0] != address || echoed[1] != length || echoed[2] != checksum) {
        snprintf(g_why, sizeof g_why, "upload ack tuple mismatch");
        return -1;
    }
    return 0;
}

/* Parse and fully receive a binary socket request before any camera I/O.
 * `first` may already contain some payload bytes, including NULs. */
static int upload_request(int fd, char *first, size_t first_len,
                          uint32_t *address, unsigned char *data,
                          size_t *length, uint32_t *checksum)
{
    char *newline = memchr(first, '\n', first_len);
    while (!newline && first_len < 128) {
        struct pollfd p = {fd, POLLIN, 0};
        if (poll(&p, 1, 2000) <= 0) return -1;
        ssize_t got = read(fd, first + first_len, 128 - first_len);
        if (got <= 0) return -1;
        first_len += (size_t)got;
        newline = memchr(first, '\n', first_len);
    }
    if (!newline || newline - first > 100) return -1;
    size_t header_len = (size_t)(newline - first);
    first[header_len] = 0;
    if (strncmp(first, "PUT ", 4)) return -1;
    const char *p = first + 4;
    unsigned long values[3];
    const int bases[3] = {0, 10, 0};
    for (int i = 0; i < 3; i++) {
        while (*p == ' ') p++;
        if (!*p || *p == '+' || *p == '-') return -1;
        errno = 0;
        char *end;
        values[i] = strtoul(p, &end, bases[i]);
        if (errno || end == p || values[i] > UINT32_MAX) return -1;
        p = end;
    }
    while (*p == ' ') p++;
    if (*p || !values[1] || values[1] > UP_DATA_MAX ||
        (values[0] & 3) || (values[1] & 3)) return -1;
    *address = (uint32_t)values[0];
    *length = (size_t)values[1];
    *checksum = (uint32_t)values[2];
    size_t have = first_len - header_len - 1;
    if (have > *length) return -1;
    memcpy(data, newline + 1, have);
    while (have < *length) {
        struct pollfd wait = {fd, POLLIN, 0};
        if (poll(&wait, 1, 2000) <= 0) return -1;
        ssize_t got = read(fd, data + have, *length - have);
        if (got <= 0) return -1;
        have += (size_t)got;
    }
    if (crc32(data, *length) != *checksum) return -1;
    return 0;
}

static void write_line(int fd, const char *s);
/* zlib's, for checking a camera CRC of up to 16 MiB; not <zlib.h>, whose crc32
 * would collide with the bitwise one above that frames use. */
extern unsigned long crc32_z(unsigned long crc, const unsigned char *buf, size_t len);

/* --- MEM1: CMD 6-9, the worker's TRB pointed straight at the caller's bytes --
 * The bytes never cross the socket: MEMR/MEMW name a POSIX shared memory
 * object the caller created, and libusb reads into / writes from it directly.
 * Every refusal is the camera's (see worker.S, MEM1): this side only splits a
 * request into transfers of at most the worker's XFER_MAX and stops at the
 * first one that is not exactly right. Nothing here retries -- an absolute
 * write is idempotent, so the caller may, after it has looked. */
enum { CMD_MEMW = 6, CMD_MEMR = 7, CMD_WIN = 8, CMD_MEMCAPS = 9 };
#define MEM_XFER_MAX 0xFFFC00u
#define MEM_OPT_CRC  2u

static int mem_send(uint8_t command, const uint32_t *words, int n, uint32_t *seq)
{
    /* 68 bytes, so the frame ends on a short packet at any speed. */
    unsigned char txbuf[FRAME_SIZE + 4] = {0};
    fpsh_frame *tx = (fpsh_frame *)txbuf;
    memcpy(tx->magic, "FPSH", 4);
    tx->version = 1;
    tx->command = command;
    tx->sequence = *seq = ++g_seq;
    tx->payload_length = (uint16_t)(4 * n);
    memcpy(tx->payload, words, 4 * (size_t)n);
    tx->checksum = crc32(txbuf, FRAME_SIZE);
    int moved = 0;
    int rc = libusb_bulk_transfer(g_cam, EP_OUT, txbuf, sizeof txbuf, &moved, 1000);
    if (rc != 0 || moved != (int)sizeof txbuf) {
        snprintf(g_why, sizeof g_why, "cmd%u OUT rc=%d(%s) moved=%d", command, rc,
                 libusb_error_name(rc), moved);
        if (rc == LIBUSB_ERROR_NO_DEVICE) cam_close();
        else if (rc == LIBUSB_ERROR_PIPE) libusb_clear_halt(g_cam, EP_OUT);
        return -1;
    }
    return 0;
}

/* A status frame is ours only if magic, CRC, command and sequence all match.
 * -> status (0 = accepted), or -1 with g_why set. */
static int mem_frame_ok(const unsigned char *b, int moved, uint8_t command,
                        uint32_t seq, uint32_t *words, int nwords)
{
    if (moved != FRAME_SIZE) {
        snprintf(g_why, sizeof g_why, "cmd%u reply %d B head=%02x%02x%02x%02x.%02x%02x"
                 "%02x%02x.%02x%02x%02x%02x", command, moved, b[0], b[1], b[2], b[3],
                 b[4], b[5], b[6], b[7], b[8], b[9], b[10], b[11]);
        return -1;
    }
    fpsh_frame rx;
    memcpy(&rx, b, sizeof rx);
    uint32_t want = rx.checksum;
    rx.checksum = 0;
    if (memcmp(rx.magic, "FPSH", 4) || rx.version != 1 || rx.command != command ||
        rx.sequence != seq || crc32((const uint8_t *)&rx, FRAME_SIZE) != want) {
        snprintf(g_why, sizeof g_why, "cmd%u reply not ours (seq %u want %u)",
                 command, rx.sequence, seq);
        return -1;
    }
    if (rx.payload_length < 4 * nwords && rx.status == 0) {
        snprintf(g_why, sizeof g_why, "cmd%u reply short", command);
        return -1;
    }
    memset(words, 0, 4 * (size_t)nwords);
    memcpy(words, rx.payload, rx.payload_length < 4 * nwords ? rx.payload_length
                                                             : 4 * (size_t)nwords);
    if (rx.status)
        snprintf(g_why, sizeof g_why, "cmd%u refused code=%u", command, rx.status);
    return rx.status;
}

static int mem_recv(uint8_t command, uint32_t seq, unsigned timeout,
                    uint32_t *words, int nwords)
{
    unsigned char b[1024];
    int moved = 0;
    int rc = libusb_bulk_transfer(g_cam, EP_IN, b, sizeof b, &moved, timeout);
    if (rc != 0) {
        snprintf(g_why, sizeof g_why, "cmd%u IN rc=%d(%s) moved=%d", command, rc,
                 libusb_error_name(rc), moved);
        if (rc == LIBUSB_ERROR_NO_DEVICE) cam_close();
        else if (rc == LIBUSB_ERROR_PIPE) libusb_clear_halt(g_cam, EP_IN);
        return -1;
    }
    return mem_frame_ok(b, moved, command, seq, words, nwords);
}

static int mem_win(uint32_t slot, uint32_t base, uint32_t len, uint32_t perm)
{
    g_why[0] = 0;
    if (transport_quarantined() || cam_open() != 0) return -1;
    uint32_t w[4] = {slot, base, len, perm}, seq, r[1];
    if (mem_send(CMD_WIN, w, 4, &seq)) return -1;
    /* The ordinary reply path answers: status 0 in the header, code in word 0. */
    int st = mem_recv(CMD_WIN, seq, 1000, r, 1);
    if (st != 0) return -1;
    if (r[0]) { snprintf(g_why, sizeof g_why, "window refused code=%u", r[0]); return -2; }
    return 0;
}

/* -> 0 and the 112-byte table, -2 for a worker without MEM1. */
static int mem_caps(uint32_t *caps /* 28 words */)
{
    g_why[0] = 0;
    char answer[256];
    int n = exchange(CMD_MEMCAPS, NULL, answer, sizeof answer, 0);
    if (n < 0) return -1;
    if (n != 112 || memcmp(answer, "MEM1", 4)) {
        snprintf(g_why, sizeof g_why, "worker has no MEM1 capability");
        return -2;
    }
    memcpy(caps, answer, 112);
    return 0;
}

static double now_ms(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1e3 + t.tv_nsec / 1e6;
}

/* One transfer each way.  `done` counts bytes the camera confirmed. */
static int mem_read_one(uint32_t addr, unsigned char *dst, uint32_t len, uint32_t opts)
{
    uint32_t w[3] = {addr, len, opts}, seq;
    if (mem_send(CMD_MEMR, w, 3, &seq)) return -1;
    /* Ask for at least a frame, so a refusal (64 bytes) never overflows a
     * short read; a granted read of fewer bytes ends on its short packet. */
    static unsigned char small[FRAME_SIZE];
    unsigned char *buf = len < FRAME_SIZE ? small : dst;
    int want = len < FRAME_SIZE ? FRAME_SIZE : (int)len;
    int moved = 0;
    int rc = libusb_bulk_transfer(g_cam, EP_IN, buf, want, &moved,
                                  2000 + (len >> 15));
    if (rc != 0) {
        snprintf(g_why, sizeof g_why, "read IN rc=%d(%s) moved=%d/%u", rc,
                 libusb_error_name(rc), moved, len);
        if (rc == LIBUSB_ERROR_NO_DEVICE) cam_close();
        else { if (rc == LIBUSB_ERROR_PIPE) libusb_clear_halt(g_cam, EP_IN); drain_in(); }
        return -1;
    }
    if ((uint32_t)moved == len) {
        if (buf == small) memcpy(dst, small, len);
        /* A refusal of a 64-byte read is the one shape that fits both; a
         * frame of ours with this sequence and a status is not data. */
        if (len != FRAME_SIZE) return 0;
        uint32_t r[1];
        if (mem_frame_ok(buf, moved, CMD_MEMR, seq, r, 1) > 0) return -2;
        return 0;
    }
    uint32_t r[1];
    int st = mem_frame_ok(buf, moved, CMD_MEMR, seq, r, 1);
    return st > 0 ? -2 : -1;
}

/* Once the frame is out, the camera may have armed its data TRB at `addr`
 * whether or not READY reached us.  Whatever this side sends next would land
 * there as data, so after any failure past that point, say nothing until the
 * worker's own data wait (MEM_TMO + 1 ms per 32 KiB, worker.S) has run out and
 * it has ended the transfer. */
static int mem_write_settle(int rc, uint32_t len)
{
    usleep((useconds_t)(700 + (len >> 15)) * 1000);
    return rc;
}

static int mem_write_one(uint32_t addr, const unsigned char *src, uint32_t len,
                         uint32_t opts)
{
    uint32_t w[3] = {addr, len, opts}, seq, r[3];
    if (mem_send(CMD_MEMW, w, 3, &seq)) return mem_write_settle(-1, len);
    int st = mem_recv(CMD_MEMW, seq, 1000, r, 3);              /* READY */
    if (st > 0) return -2;               /* refused: nothing was armed */
    if (st != 0) return mem_write_settle(-1, len);
    if (r[1] != addr || r[2] < len) {
        snprintf(g_why, sizeof g_why, "ready mismatch addr=0x%08X armed=%u", r[1], r[2]);
        return mem_write_settle(-1, len);
    }
    int moved = 0;
    int rc = libusb_bulk_transfer(g_cam, EP_OUT, (unsigned char *)src, (int)len,
                                  &moved, 2000 + (len >> 15));
    if (rc != 0 || (uint32_t)moved != len) {
        snprintf(g_why, sizeof g_why, "data OUT rc=%d(%s) moved=%d/%u", rc,
                 libusb_error_name(rc), moved, len);
        if (rc == LIBUSB_ERROR_NO_DEVICE) cam_close();
        else if (rc == LIBUSB_ERROR_PIPE) libusb_clear_halt(g_cam, EP_OUT);
        return mem_write_settle(-1, len);
    }
    st = mem_recv(CMD_MEMW, seq, 2000 + (len >> ((opts & MEM_OPT_CRC) ? 12 : 15)),
                  r, 3);                                        /* DONE */
    if (st != 0) return -1;              /* data went; its fate is unknown */
    if (r[1] != len) {
        snprintf(g_why, sizeof g_why, "camera received %u of %u", r[1], len);
        return -1;
    }
    if ((opts & MEM_OPT_CRC) &&
        r[2] != (uint32_t)crc32_z(0, src, len)) {
        snprintf(g_why, sizeof g_why, "camera CRC 0x%08X != host", r[2]);
        return -1;
    }
    return 0;
}

/* MEMR|MEMW <addr> <len> <opts> </shm>: -> "OKR|OKW <len> <ms>" */
static void mem_request(int c, char *line)
{
    int writing = line[3] == 'W';
    unsigned long a, n, o;
    char name[64];
    if (sscanf(line + 5, "%li %li %li %63s", (long *)&a, (long *)&n, (long *)&o, name) != 4 ||
        name[0] != '/' || !n || a > UINT32_MAX || n > 0x40000000 || a + n > 0x100000000UL) {
        write_line(c, "ERR mem args\n"); return;
    }
    g_why[0] = 0;
    if (transport_quarantined() || cam_open() != 0) {
        char e[160]; snprintf(e, sizeof e, "ERR mem %s\n", g_why); write_line(c, e); return;
    }
    int fd = shm_open(name, O_RDWR, 0);
    void *map = fd < 0 ? MAP_FAILED :
        mmap(NULL, n, writing ? PROT_READ : PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (fd >= 0) close(fd);
    if (map == MAP_FAILED) { write_line(c, "ERR mem shm\n"); return; }
    unsigned char *p = map;
    double t0 = now_ms();
    size_t done = 0;
    int rc = 0;
    while (done < n) {
        uint32_t len = n - done > MEM_XFER_MAX ? MEM_XFER_MAX : (uint32_t)(n - done);
        rc = writing ? mem_write_one((uint32_t)(a + done), p + done, len, (uint32_t)o)
                     : mem_read_one((uint32_t)(a + done), p + done, len, (uint32_t)o);
        if (rc) break;
        done += len;
    }
    munmap(map, n);
    char ans[200];
    if (rc)
        snprintf(ans, sizeof ans, "ERR mem %s at=0x%08lX done=%zu %s\n",
                 rc == -2 ? "refused" : "uncertain", a + done, done, g_why);
    else
        snprintf(ans, sizeof ans, "%s %lu %.3f\n", writing ? "OKW" : "OKR", n,
                 now_ms() - t0);
    write_line(c, ans);
}

static void write_all(int fd, const char *buf, size_t n)
{
    while (n) {
        ssize_t w = write(fd, buf, n);
        if (w <= 0) return;
        buf += w; n -= (size_t)w;
    }
}


static void write_line(int fd, const char *s)
{
    size_t n = strlen(s);
    while (n) { ssize_t w = write(fd, s, n); if (w <= 0) return; s += w; n -= (size_t)w; }
}

/* newline/backslash-escape so one reply is always exactly one socket line */
static void write_escaped(int fd, const char *s)
{
    static char buf[CMD_LINE_MAX * 2 + 8]; size_t o = 0;
    for (; *s && o < sizeof buf - 3; s++) {
        if (*s == '\n')      { buf[o++] = '\\'; buf[o++] = 'n'; }
        else if (*s == '\r') { }
        else if (*s == '\\') { buf[o++] = '\\'; buf[o++] = '\\'; }
        else                  buf[o++] = *s;
    }
    buf[o++] = '\n'; buf[o] = 0;
    write_line(fd, buf);
}

static void on_signal(int s) { (void)s; g_stop = 1; }

int main(int argc, char **argv)
{
    const char *sock_path = "/tmp/fpshd.sock";
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--socket") && i + 1 < argc)      sock_path = argv[++i];
        else if (!strcmp(argv[i], "--vid") && i + 1 < argc)    g_vid = (uint16_t)strtoul(argv[++i], 0, 0);
        else if (!strcmp(argv[i], "--pid") && i + 1 < argc)    g_pid = (uint16_t)strtoul(argv[++i], 0, 0);
        else if (!strcmp(argv[i], "--timeout") && i + 1 < argc) g_timeout = (unsigned)strtoul(argv[++i], 0, 0);
        else { fprintf(stderr, "usage: %s [--socket P] [--vid V] [--pid P] [--timeout MS]\n", argv[0]); return 2; }
    }

    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);
    signal(SIGPIPE, SIG_IGN);

    if (libusb_init(&g_usb) != 0) { fprintf(stderr, "libusb_init failed\n"); return 1; }

    unlink(sock_path);
    int srv = socket(AF_UNIX, SOCK_STREAM, 0);
    struct sockaddr_un a; memset(&a, 0, sizeof a);
    a.sun_family = AF_UNIX;
    snprintf(a.sun_path, sizeof a.sun_path, "%s", sock_path);
    if (bind(srv, (struct sockaddr *)&a, sizeof a) || listen(srv, 8)) {
        fprintf(stderr, "bind/listen %s: %s\n", sock_path, strerror(errno)); return 1;
    }
    fprintf(stderr, "fpshd " FPSHD_VERSION ": %s  vid=%04x pid=%04x  iface=%d "
            "ep_out=%02x ep_in=%02x\n",
            sock_path, g_vid, g_pid, IFACE, EP_OUT, EP_IN);

    /* static, not automatic: a four-megabyte reply does not go on the stack. */
    static char line[CMD_LINE_MAX];
    static char reply[MAX_REPLY];
    while (!g_stop) {
        int c = accept(srv, NULL, NULL);
        if (c < 0) { if (errno == EINTR) continue; break; }
        ssize_t n = read(c, line, sizeof line - 1);
        if (n <= 0) { close(c); continue; }
        line[n] = 0;
        if (n >= 2 && line[0] == 'P' && line[1] == 'U') {
            uint32_t address = 0, checksum = 0, sequence = 0;
            size_t length = 0;
            unsigned char data[UP_DATA_MAX];
            if (upload_request(c, line, (size_t)n, &address, data,
                               &length, &checksum)) {
                write_line(c, "ERR upload malformed or incomplete request\n");
            } else {
                g_req_timeout = 0;
                upload_state state;
                int ready = upload_caps(&state);
                if (ready != 0) {
                    char e[160];
                    snprintf(e, sizeof e, "ERR upload %s\n",
                             ready == -2 ? "unsupported" : g_why);
                    write_line(c, e);
                } else {
                    int done = upload_exchange(address, data, length,
                                               checksum, &sequence);
                    char answer[180];
                    if (done == 0)
                        snprintf(answer, sizeof answer,
                                 "OKU %u 0x%08X %zu 0x%08X\n",
                                 sequence, address, length, checksum);
                    else
                        snprintf(answer, sizeof answer,
                                 "ERR upload %s seq=%u %s\n",
                                 done == -1 ? "uncertain" : "rejected",
                                 sequence, g_why);
                    write_line(c, answer);
                }
            }
            close(c); continue;
        }
        char *nl = strpbrk(line, "\r\n");
        /* A fragmented EP83 socket request is rejected before camera I/O.
         * Never trigger a prefix that happened to arrive in the first read. */
        if (!strncmp(line, "EP83 ", 5) &&
            (!nl || (size_t)n != strlen(line) || strspn(nl, "\r\n") != strlen(nl))) {
            char e[96];
            snprintf(e, sizeof e, "ERR ep83 complete single request line required quarantine=%d\n",
                     g_ep83_quarantine);
            write_line(c, e);
            close(c); continue;
        }
        if (nl) *nl = 0;

        if (!strcmp(line, "QUIT")) { write_line(c, "OK bye\n"); close(c); g_stop = 1; break; }
        if (!strcmp(line, "STATUS")) {
            char s[320];
            snprintf(s, sizeof s, "OK name=fpshd version=" FPSHD_VERSION " protocol=1 frame=64 "
                     "payload=44 transport=EP01-EP82 iface=0 camera=%s seq=%u "
                     "ep83=host_candidate upload=UP01-probed mem=MEM1-probed quarantine=%d fault=%s\n",
                     g_cam ? "open" : "closed", g_seq, g_ep83_quarantine,
                     g_ep83_quarantine ? g_ep83_fault : "none");
            write_line(c, s); close(c); continue;
        }
        if (transport_quarantined()) {
            char e[160];
            snprintf(e, sizeof e, "ERR transport %s\n", g_why);
            write_line(c, e); close(c); continue;
        }
        if (!strcmp(line, "UCAPS")) {
            g_req_timeout = 0;
            upload_state state;
            int rc = upload_caps(&state);
            if (rc != 0) {
                char answer[160];
                snprintf(answer, sizeof answer, "ERR upload %s\n",
                         rc == -2 ? "unsupported" : g_why);
                write_line(c, answer);
            } else {
                char answer[160];
                snprintf(answer, sizeof answer,
                         "OKUC %u %u 0x%08X %u 0x%08X\n",
                         (unsigned)UP_DATA_MAX, state.sequence,
                         state.address, state.length, state.checksum);
                write_line(c, answer);
            }
            close(c); continue;
        }
        if (!strncmp(line, "EP83 ", 5)) {
            unsigned char bytes[EP83_MAX_BYTES];
            size_t received = 0;
            uint32_t sequence = 0;
            uint64_t deadline = 0;
            int rc = ep83_exchange(line, bytes, sizeof bytes, &received, &sequence, &deadline);
            if (rc != 0) {
                char e[160];
                snprintf(e, sizeof e, "ERR ep83 %s quarantine=%d\n", g_why, g_ep83_quarantine);
                write_line(c, e);
            } else {
                char header[80];
                int n = snprintf(header, sizeof header, "OK83 %zu %u\n", received, sequence);
                if (ep83_write(c, header, (size_t)n, deadline) ||
                    ep83_write(c, bytes, received, deadline)) {
                    ep83_fault("socket output incomplete after verified terminal");
                    fprintf(stderr, "EP83 seq=%u quarantine=1 %s\n", sequence, g_ep83_fault);
                }
            }
            close(c); continue;
        }
        /* MEM1.  MCAPS -> "OKMC <xfer_max> <pool> <pool_size>" and eight
         * "<base> <len> <perm>"; WIN <slot> <base> <len> <perm> -> "OKW";
         * MEMR|MEMW <addr> <len> <opts> </shm> -> "OKR|OKW <len> <ms>". */
        if (!strcmp(line, "MCAPS")) {
            uint32_t caps[28];
            int rc = mem_caps(caps);
            char a[640];
            if (rc) snprintf(a, sizeof a, "ERR mcaps %s\n", rc == -2 ? "unsupported" : g_why);
            else {
                int o = snprintf(a, sizeof a, "OKMC %u 0x%08X %u", caps[1], caps[2], caps[3]);
                for (int i = 0; i < 8; i++)
                    o += snprintf(a + o, sizeof a - o, " 0x%08X %u %u",
                                  caps[4 + 3 * i], caps[5 + 3 * i], caps[6 + 3 * i]);
                snprintf(a + o, sizeof a - o, "\n");
            }
            write_line(c, a); close(c); continue;
        }
        if (!strncmp(line, "WIN ", 4)) {
            unsigned long w[4];
            char e[160];
            if (sscanf(line + 4, "%li %li %li %li", (long *)&w[0], (long *)&w[1],
                       (long *)&w[2], (long *)&w[3]) != 4 ||
                w[1] > UINT32_MAX || w[2] > UINT32_MAX) {
                write_line(c, "ERR win args\n"); close(c); continue;
            }
            int rc = mem_win((uint32_t)w[0], (uint32_t)w[1], (uint32_t)w[2], (uint32_t)w[3]);
            if (rc) snprintf(e, sizeof e, "ERR win %s\n", g_why);
            else snprintf(e, sizeof e, "OKW\n");
            write_line(c, e); close(c); continue;
        }
        if (!strncmp(line, "MEMR ", 5) || !strncmp(line, "MEMW ", 5)) {
            mem_request(c, line); close(c); continue;
        }
        if (!strcmp(line, "PING")) {
            int r = exchange(CMD_PING, NULL, reply, sizeof reply, 0);
            if (r < 0) write_line(c, "ERR ping\n");
            else { char s[64]; snprintf(s, sizeof s, "OK pong seq=%u\n", g_seq); write_line(c, s); }
            close(c); continue;
        }
        /* TMO <ms> <line>: this one command answers fast, so do not spend a
         * fifth of a second finding out that its reply went missing. */
        g_req_timeout = 0;
        clock_gettime(CLOCK_MONOTONIC, &g_req_t0);
        g_want_hex = 0;
        if (!strncmp(line, "HEX ", 4)) {
            g_want_hex = 1;
            memmove(line, line + 4, strlen(line + 4) + 1);
        }
        if (!strncmp(line, "TMO ", 4)) {
            char *rest;
            unsigned ms = (unsigned)strtoul(line + 4, &rest, 10);
            while (*rest == ' ') rest++;
            if (ms) g_req_timeout = ms;
            memmove(line, rest, strlen(rest) + 1);
        }

        /* RAWSHM <n> <name> <line>: as RAW, but libusb reads straight into the
         * POSIX shared memory object <name> (the caller created it, at least n
         * bytes) and the socket carries only "OKS <n>\n".  Copying 16 MiB down
         * the socket took ~20 ms after a ~45 ms USB transfer, done one after
         * the other; this removes the copy. */
        char shm_name[64] = {0};
        if (!strncmp(line, "RAWSHM ", 7)) {
            char *p = line + 7, *e;
            long n = strtol(p, &e, 10);
            while (*e == ' ') e++;
            char *sp = strchr(e, ' ');
            if (n <= 0 || !sp || (size_t)(sp - e) >= sizeof shm_name || e[0] != '/') {
                write_line(c, "ERR rawshm args\n"); close(c); continue;
            }
            memcpy(shm_name, e, (size_t)(sp - e));
            /* rewrite as "BULK <n> <rest>" for the common path */
            char rest[CMD_LINE_MAX];
            snprintf(rest, sizeof rest, "BULK %ld %s", n, sp + 1);
            snprintf(line, sizeof line, "%s", rest);
        }
        /* RAW <n> <line>: as BULK, but the answer is "OKB <n>\n" and then the
         * bytes themselves, not hex.  Hex doubled the volume and cost the host
         * a full parse; at 8 MiB a read spent most of its time there. */
        int want_bin = 0;
        if (!strncmp(line, "RAW ", 4)) {
            if (strlen(line) + 2 > sizeof line) {
                write_line(c, "ERR line too long\n"); close(c); continue;
            }
            want_bin = 1;
            memmove(line + 1, line, strlen(line) + 1);   /* "RAW " -> "BULK " */
            memcpy(line, "BULK", 4);
        }
        /* BULK <n> <line>: the caller already knows how many bytes come back. */
        int raw_len = 0;
        char *body = line;
        if (!strncmp(line, "BULK ", 5)) {
            raw_len = (int)strtol(line + 5, &body, 10);
            while (*body == ' ') body++;
            memmove(line + 4, body, strlen(body) + 1);
            memcpy(line, "SHL ", 4);
        }
        if (!strncmp(line, "SHL ", 4)) {
            char cmd[CMD_MAX];
            snprintf(cmd, sizeof cmd, "shl %s", line + 4);
            char *dst = reply;
            size_t cap = sizeof reply;
            void *map = MAP_FAILED;
            if (shm_name[0]) {
                int fd = shm_open(shm_name, O_RDWR, 0);
                if (fd >= 0) {
                    map = mmap(NULL, (size_t)raw_len, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
                    close(fd);
                }
                if (map == MAP_FAILED) {
                    write_line(c, "ERR rawshm map\n"); close(c); continue;
                }
                dst = map;
                cap = (size_t)raw_len;
            }
            int r = exchange(CMD_SHL, cmd, dst, cap, raw_len);
            if (map != MAP_FAILED) {
                munmap(map, (size_t)raw_len);
                if (r < 0) {
                    char e[128];
                    snprintf(e, sizeof e, "ERR shl %s\n", g_why[0] ? g_why : "no detail");
                    write_line(c, e);
                } else {
                    char head[32];
                    snprintf(head, sizeof head, "OKS %d\n", r);
                    write_line(c, head);
                }
                close(c); continue;
            }
            if (r < 0) {
                char e[128];
                snprintf(e, sizeof e, "ERR shl %s\n",
                         g_why[0] ? g_why : "no detail");
                write_line(c, e);
            }
            else if (want_bin && g_was_bulk) {
                char head[32];
                int hn = snprintf(head, sizeof head, "OKB %d\n", r);
                write_all(c, head, (size_t)hn);
                write_all(c, reply, (size_t)r);
                if (getenv("FPSHD_TIMING"))
                    fprintf(stderr, "socket written, request total %.3f ms\n",
                            ms_since(&g_req_t0));
            }
            else if (g_was_bulk || g_want_hex) {
                /* A raw block: the camera put the bytes in the reply buffer
                 * itself rather than printing them, so they are not text and
                 * cannot go down a line-based socket as they are. Hex here
                 * rather than on the camera -- doubling the volume matters over
                 * USB and costs nothing over a unix socket. */
                static const char hex[] = "0123456789ABCDEF";
                char *line = malloc((size_t)r * 2 + 8);
                if (!line) { write_line(c, "ERR mem\n"); close(c); continue; }
                memcpy(line, "OKX ", 4);
                for (int i = 0; i < r; i++) {
                    line[4 + i * 2]     = hex[(unsigned char)reply[i] >> 4];
                    line[4 + i * 2 + 1] = hex[(unsigned char)reply[i] & 15];
                }
                line[4 + r * 2] = '\n';
                write_all(c, line, (size_t)r * 2 + 5);
                free(line);
            }
            else { write_line(c, "OK "); write_escaped(c, reply); }
            close(c); continue;
        }
        write_line(c, "ERR unknown\n");
        close(c);
    }

    cam_close();
    close(srv);
    unlink(sock_path);
    libusb_exit(g_usb);
    return 0;
}
