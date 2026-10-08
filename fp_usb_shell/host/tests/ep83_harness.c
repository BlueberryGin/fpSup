/* Execute the actual fpshd EP83 implementation with fake USB and fake time.
 * No camera, daemon, Unix socket, or libusb library is opened by this program. */
#define main fpshd_program_main
#define clock_gettime ep83_fake_clock_gettime
#include "../fpshd.c"
#undef main
#undef clock_gettime
#include <assert.h>

static uint64_t fake_ms = 10000;
static int opens, claims, closes, releases, clears, bulks, out_calls, in83_calls, in82_calls;
static size_t wanted, sent;
static uint32_t last_seq;
static int out_rc, out_short, data_rc, data_short, data_oversize, terminal_rc, terminal_short;
static int terminal_variant, deadline_stage, descriptor_rc;
static unsigned last_timeout = EP83_DEADLINE_MS;
static struct libusb_endpoint_descriptor eps[3];
static struct libusb_interface_descriptor alt;
static struct libusb_interface iface;
static struct libusb_config_descriptor config;

int ep83_fake_clock_gettime(clockid_t clock_id, struct timespec *t)
{
    assert(clock_id == CLOCK_MONOTONIC);
    t->tv_sec = (time_t)(fake_ms / 1000);
    t->tv_nsec = (long)(fake_ms % 1000) * 1000000;
    return 0;
}
int libusb_init(libusb_context **c) { *c = (void *)1; return 0; }
void libusb_exit(libusb_context *c) { (void)c; }
libusb_device_handle *libusb_open_device_with_vid_pid(libusb_context *c, uint16_t vid, uint16_t pid)
{
    (void)c; assert(vid == 0x1003 && pid == 0xc432); opens++; return (void *)2;
}
int libusb_set_auto_detach_kernel_driver(libusb_device_handle *c, int yes)
{ assert(c == (void *)2 && yes == 1); return 0; }
int libusb_claim_interface(libusb_device_handle *c, int n)
{ assert(c == (void *)2 && n == 0); claims++; return 0; }
int libusb_release_interface(libusb_device_handle *c, int n)
{ assert(c == (void *)2 && n == 0); releases++; return 0; }
void libusb_close(libusb_device_handle *c) { assert(c == (void *)2); closes++; }
libusb_device *libusb_get_device(libusb_device_handle *c)
{ assert(c == (void *)2); return (void *)3; }
int libusb_get_active_config_descriptor(libusb_device *d, struct libusb_config_descriptor **c)
{
    assert(d == (void *)3);
    *c = descriptor_rc ? NULL : &config;
    if (deadline_stage == 4) fake_ms += EP83_DEADLINE_MS;
    return descriptor_rc;
}
void libusb_free_config_descriptor(struct libusb_config_descriptor *c) { assert(c == &config); }
int libusb_clear_halt(libusb_device_handle *c, unsigned char e)
{ (void)c; (void)e; clears++; assert(!"clear_halt forbidden in EP83 test"); return -1; }
const char *libusb_error_name(int r) { (void)r; return "fake"; }

int libusb_bulk_transfer(libusb_device_handle *c, unsigned char ep, unsigned char *b,
                         int length, int *moved, unsigned timeout)
{
    assert(c == (void *)2 && timeout > 0 && timeout <= last_timeout);
    last_timeout = timeout;
    fake_ms += 3;
    bulks++;
    if (ep == EP_OUT) {
        out_calls++;
        assert(in83_calls == 0 && in82_calls == 0);
        assert(length >= 64 && length <= USB_OUT_SIZE);
        fpsh_frame tx;
        memcpy(&tx, b, sizeof tx);
        uint32_t check = tx.checksum; tx.checksum = 0;
        assert(crc32((unsigned char *)&tx, sizeof tx) == check);
        assert(!memcmp(tx.magic, "FPSH", 4) && tx.version == 1 && tx.command == CMD_SHL);
        assert(tx.flags == 0 && !strcmp((char *)b + 20, "shl af_stream 1"));
        last_seq = tx.sequence;
        *moved = out_short ? length - 1 : length;
        if (deadline_stage == 1) fake_ms += EP83_DEADLINE_MS;
        return out_rc;
    }
    if (ep == EP_TELEMETRY) {
        assert(out_calls == 1 && in82_calls == 0);
        in83_calls++;
        assert(length % eps[2].wMaxPacketSize == 0 && length <= (int)EP83_CHUNK_BYTES);
        size_t n = wanted - sent;
        if (n > (size_t)length) n = (size_t)length;
        if (data_short) n = 1;
        if (data_oversize) n = (size_t)length;
        for (size_t i = 0; i < n; i++) b[i] = (unsigned char)((sent + i) % 251);
        sent += n;
        *moved = (int)n;
        if (deadline_stage == 2) fake_ms += EP83_DEADLINE_MS;
        return data_rc;
    }
    assert(ep == EP_IN && out_calls == 1 && sent == wanted);
    in82_calls++;
    assert(length == eps[1].wMaxPacketSize && in82_calls == 1);
    fpsh_frame rx = {0};
    memcpy(rx.magic, "FPSH", 4);
    rx.version = 1; rx.command = CMD_SHL; rx.sequence = last_seq;
    rx.payload_length = 2; memcpy(rx.payload, "ok", 2);
    switch (terminal_variant) {
    case 1: rx.sequence++; break;
    case 2: rx.magic[0] = 'X'; break;
    case 3: rx.version++; break;
    case 4: rx.command = CMD_PING; break;
    case 5: rx.flags = 1; break;
    case 6: rx.flags = 2; break;
    case 7: rx.status = 1; break;
    case 8: rx.payload_length = 45; break;
    default: break;
    }
    rx.checksum = crc32((unsigned char *)&rx, sizeof rx);
    if (terminal_variant == 9) rx.checksum ^= 1;
    memcpy(b, &rx, sizeof rx);
    *moved = terminal_short ? 63 : 64;
    if (deadline_stage == 3) fake_ms += EP83_DEADLINE_MS;
    return terminal_rc;
}

static void reset(int mps, size_t n)
{
    fake_ms = 10000;
    opens = claims = closes = releases = clears = bulks = out_calls = in83_calls = in82_calls = 0;
    out_rc = out_short = data_rc = data_short = data_oversize = terminal_rc = terminal_short = 0;
    terminal_variant = deadline_stage = descriptor_rc = 0;
    wanted = n; sent = 0; last_timeout = EP83_DEADLINE_MS;
    g_cam = NULL; g_seq = 100; g_stop = 0; g_ep83_quarantine = 0;
    memset(g_ep83_fault, 0, sizeof g_ep83_fault);
    eps[0] = (struct libusb_endpoint_descriptor){EP_OUT, 2, (uint16_t)mps};
    eps[1] = (struct libusb_endpoint_descriptor){EP_IN, 2, (uint16_t)mps};
    eps[2] = (struct libusb_endpoint_descriptor){EP_TELEMETRY, 2, (uint16_t)mps};
    alt = (struct libusb_interface_descriptor){0, 0, 3, eps};
    iface = (struct libusb_interface){&alt, 1};
    config = (struct libusb_config_descriptor){1, &iface};
}

static int run_request(const char *custom)
{
    char request[600];
    snprintf(request, sizeof request, "EP83 %zu af_stream 1", wanted);
    unsigned char output[EP83_MAX_BYTES];
    memset(output, 0xa5, sizeof output);
    size_t received = 0;
    uint32_t seq = 0;
    uint64_t deadline = 0;
    int rc = ep83_exchange(custom ? custom : request, output, sizeof output,
                           &received, &seq, &deadline);
    if (rc == 0) {
        assert(received == wanted && seq == 101 && deadline == 12000);
        for (size_t i = 0; i < wanted; i++) assert(output[i] == (unsigned char)(i % 251));
        assert(out_calls == 1 && in82_calls == 1 && claims == opens && opens <= 1);
        assert(!g_ep83_quarantine && !clears && !closes && !releases);
    }
    return rc;
}

static void check_fault_latched(void)
{
    assert(g_ep83_quarantine && g_ep83_fault[0]);
    int before = bulks, before_opens = opens;
    char saved[sizeof g_ep83_fault]; strcpy(saved, g_ep83_fault);
    assert(run_request(NULL) < 0);
    char output[100];
    assert(exchange(CMD_SHL, "shl should_not_run", output, sizeof output, 0) < 0);
    assert(exchange(CMD_PING, NULL, output, sizeof output, 0) < 0);
    drain_in();
    assert(bulks == before && opens == before_opens && !clears);
    assert(!strcmp(saved, g_ep83_fault));
    /* Closing a handle cannot silently clear the daemon-lifetime latch. */
    cam_close();
    assert(g_ep83_quarantine && run_request(NULL) < 0 && opens == before_opens);
}

int main(int argc, char **argv)
{
    assert(argc == 2);
    const char *test = argv[1];
    reset(1024, 32896);
    if (!strcmp(test, "success")) {
        int mps[] = {64, 512, 1024};
        size_t sizes[] = {1, 64, 128, 512, 1024, 16384, 32896, 65536};
        for (size_t i = 0; i < 3; i++) for (size_t j = 0; j < 8; j++) {
            reset(mps[i], sizes[j]); assert(run_request(NULL) == 0);
        }
    } else if (!strcmp(test, "owner_reuse")) {
        /* The normal daemon has already claimed this exact handle for SHL.
         * EP83 must use it without open/claim/release or a second owner. */
        g_cam = (void *)2;
        assert(run_request(NULL) == 0 && opens == 0 && claims == 0);
    } else if (!strcmp(test, "arguments")) {
        const char *bad[] = {"EP83 0 x", "EP83 -1 x", "EP83 +1 x", "EP83 65537 x",
            "EP83 999999999999999999999999 x", "EP83 1", "EP83 1 ",
            "EP83 1x x", "EP83 1 x\ny", "EP83 1 x\ry", "SHL af_stream 1"};
        for (size_t i = 0; i < sizeof bad / sizeof bad[0]; i++) {
            reset(1024, 1); assert(run_request(bad[i]) < 0);
            assert(!opens && !bulks && !g_ep83_quarantine);
        }
        char long_request[600]; memset(long_request, 'x', sizeof long_request);
        memcpy(long_request, "EP83 1 ", 7); long_request[599] = 0;
        assert(run_request(long_request) < 0 && !opens);
    } else if (!strcmp(test, "descriptor")) {
        for (int i = 0; i < 8; i++) {
            reset(1024, 1);
            struct libusb_interface_descriptor alts[2] = {alt, alt};
            if (i == 0) eps[2].bmAttributes = 3;
            if (i == 1) eps[2].wMaxPacketSize = 32;
            if (i == 2) alt.bAlternateSetting = 1;
            if (i == 3) alt.bNumEndpoints = 2;
            if (i == 4) alt.bInterfaceNumber = 1;
            if (i == 5) descriptor_rc = LIBUSB_ERROR_IO;
            if (i == 6) eps[1].bEndpointAddress = EP_TELEMETRY;
            if (i == 7) { iface.altsetting = alts; iface.num_altsetting = 2; }
            assert(run_request(NULL) < 0 && !bulks && !g_ep83_quarantine);
        }
    } else if (!strcmp(test, "out_faults")) {
        for (int i = 0; i < 3; i++) {
            reset(1024, 32896);
            if (i == 0) out_rc = LIBUSB_ERROR_TIMEOUT;
            if (i == 1) out_short = 1;
            if (i == 2) out_rc = LIBUSB_ERROR_NO_DEVICE;
            assert(run_request(NULL) < 0 && out_calls == 1 && !in83_calls && !in82_calls);
            check_fault_latched();
        }
    } else if (!strcmp(test, "data_faults")) {
        for (int i = 0; i < 5; i++) {
            reset(1024, i == 2 ? 128 : 32896);
            if (i == 0) data_rc = LIBUSB_ERROR_PIPE;
            if (i == 1) data_short = 1;
            if (i == 2) data_oversize = 1;
            if (i == 3) data_rc = LIBUSB_ERROR_TIMEOUT;
            if (i == 4) data_rc = LIBUSB_ERROR_OVERFLOW;
            assert(run_request(NULL) < 0 && in83_calls == 1 && !in82_calls);
            check_fault_latched();
        }
    } else if (!strcmp(test, "terminal_faults")) {
        for (int i = 1; i <= 11; i++) {
            reset(1024, 128);
            if (i <= 9) terminal_variant = i;
            if (i == 10) terminal_rc = LIBUSB_ERROR_TIMEOUT;
            if (i == 11) terminal_short = 1;
            assert(run_request(NULL) < 0 && in82_calls == 1);
            check_fault_latched();
        }
    } else if (!strcmp(test, "deadline")) {
        for (int i = 1; i <= 3; i++) {
            reset(1024, 128); deadline_stage = i;
            assert(run_request(NULL) < 0);
            if (i == 1) assert(in83_calls == 0);
            if (i == 2) assert(in82_calls == 0);
            check_fault_latched();
        }
    } else if (!strcmp(test, "sequence_exhausted")) {
        g_seq = UINT32_MAX;
        assert(run_request(NULL) < 0 && !bulks && !g_ep83_quarantine);
    } else if (!strcmp(test, "preflight_deadline")) {
        deadline_stage = 4;
        assert(run_request(NULL) < 0 && !bulks && !g_ep83_quarantine);
    } else if (!strcmp(test, "bounded_output")) {
        unsigned char data = 0;
        assert(ep83_write(-1, &data, 1, fake_ms) < 0 && !bulks);
        assert(ep83_write(-1, &data, 1, fake_ms + 1) < 0 && !bulks);
    } else assert(!"unknown case");
    printf("PASS %s\n", test);
    return 0;
}
