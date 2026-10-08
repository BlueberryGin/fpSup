/* Execute the actual host upload implementation against a fake worker.
 * No libusb library, daemon socket or camera is opened. */
#define main fpshd_program_main
#include "../fpshd.c"
#undef main
#include <assert.h>

static int old_worker, ack_lost, bad_ack, reject_upload;
static int out_calls, upload_out_calls, in_calls;
static uint8_t pending;
static uint32_t request_seq;
static upload_state accepted;
static unsigned char stored[UP_DATA_MAX];
static size_t stored_length;

int libusb_init(libusb_context **c) { *c = (void *)1; return 0; }
void libusb_exit(libusb_context *c) { (void)c; }
libusb_device_handle *libusb_open_device_with_vid_pid(libusb_context *c,
                                                       uint16_t vid, uint16_t pid)
{ (void)c; assert(vid == 0x1003 && pid == 0xc432); return (void *)2; }
int libusb_set_auto_detach_kernel_driver(libusb_device_handle *c, int yes)
{ assert(c == (void *)2 && yes == 1); return 0; }
int libusb_claim_interface(libusb_device_handle *c, int n)
{ assert(c == (void *)2 && n == 0); return 0; }
int libusb_release_interface(libusb_device_handle *c, int n)
{ (void)c; (void)n; return 0; }
void libusb_close(libusb_device_handle *c) { (void)c; }
libusb_device *libusb_get_device(libusb_device_handle *c) { (void)c; return (void *)3; }
int libusb_get_active_config_descriptor(libusb_device *d,
                                         struct libusb_config_descriptor **c)
{ (void)d; *c = NULL; return LIBUSB_ERROR_IO; }
void libusb_free_config_descriptor(struct libusb_config_descriptor *c) { (void)c; }
int libusb_clear_halt(libusb_device_handle *c, unsigned char ep)
{ (void)c; (void)ep; return 0; }
const char *libusb_error_name(int r) { (void)r; return "fake"; }

int libusb_bulk_transfer(libusb_device_handle *c, unsigned char ep,
                         unsigned char *bytes, int length, int *moved,
                         unsigned timeout)
{
    assert(c == (void *)2 && timeout > 0);
    if (ep == EP_OUT) {
        out_calls++;
        fpsh_frame tx;
        memcpy(&tx, bytes, sizeof tx);
        uint32_t frame_crc = tx.checksum;
        tx.checksum = 0;
        assert(!memcmp(tx.magic, "FPSH", 4) && tx.version == 1);
        assert(crc32((unsigned char *)&tx, sizeof tx) == frame_crc);
        pending = tx.command;
        request_seq = tx.sequence;
        if (pending == CMD_UPLOAD_CAPS) {
            assert(length == 68 && tx.payload_length == 0);
        } else {
            assert(pending == CMD_UPLOAD && tx.payload_length == 12);
            upload_out_calls++;
            uint32_t address, n, checksum;
            memcpy(&address, tx.payload, 4);
            memcpy(&n, tx.payload + 4, 4);
            memcpy(&checksum, tx.payload + 8, 4);
            assert(n <= UP_DATA_MAX && length >= (int)(FRAME_SIZE + n));
            assert(length <= (int)UP_OUT_MAX);
            assert(crc32(bytes + FRAME_SIZE, n) == checksum);
            if (!reject_upload) {
                accepted = (upload_state){request_seq, address, n, checksum};
                memcpy(stored, bytes + FRAME_SIZE, n);
                stored_length = n;
            }
        }
        *moved = length;
        return 0;
    }
    assert(ep == EP_IN && length == FRAME_SIZE);
    in_calls++;
    if (pending == CMD_UPLOAD && ack_lost) {
        ack_lost = 0;
        *moved = 0;
        return LIBUSB_ERROR_TIMEOUT;
    }
    fpsh_frame rx = {0};
    memcpy(rx.magic, "FPSH", 4);
    rx.version = 1;
    rx.command = pending;
    rx.sequence = request_seq;
    if (pending == CMD_UPLOAD_CAPS) {
        if (!old_worker) {
            rx.payload_length = 20;
            memcpy(rx.payload, "UP01", 4);
            memcpy(rx.payload + 4, &accepted, sizeof accepted);
        }
    } else {
        rx.payload_length = reject_upload ? 4 : 16;
        uint32_t result = reject_upload ? 3 : 0;
        memcpy(rx.payload, &result, 4);
        if (!reject_upload) {
            uint32_t echoed[] = {accepted.address, accepted.length, accepted.checksum};
            if (bad_ack) echoed[0]++;
            memcpy(rx.payload + 4, echoed, sizeof echoed);
        }
    }
    rx.checksum = crc32((unsigned char *)&rx, sizeof rx);
    memcpy(bytes, &rx, sizeof rx);
    *moved = FRAME_SIZE;
    return 0;
}

static void reset(void)
{
    old_worker = ack_lost = bad_ack = reject_upload = 0;
    out_calls = upload_out_calls = in_calls = 0;
    pending = 0;
    memset(&accepted, 0, sizeof accepted);
    stored_length = 0;
    g_cam = NULL;
    g_seq = 100;
    g_ep83_quarantine = 0;
    g_req_timeout = 0;
}

int main(void)
{
    unsigned char bytes[UP_DATA_MAX];
    for (size_t i = 0; i < sizeof bytes; i++) bytes[i] = (unsigned char)(i * 13);
    uint32_t crc = crc32(bytes, sizeof bytes), seq = 0;
    upload_state status;

    reset();
    old_worker = 1;
    assert(upload_caps(&status) == -2 && upload_out_calls == 0);
    assert(out_calls == 1 && in_calls == 1);

    reset();
    assert(upload_caps(&status) == 0 && status.sequence == 0);
    assert(upload_exchange(0x45010000, bytes, sizeof bytes, crc, &seq) == 0);
    assert(seq == 102 && upload_out_calls == 1 && stored_length == sizeof bytes);
    assert(!memcmp(stored, bytes, sizeof bytes));
    assert(upload_caps(&status) == 0 && status.sequence == seq);
    assert(status.address == 0x45010000 && status.length == sizeof bytes &&
           status.checksum == crc);

    reset();
    assert(upload_caps(&status) == 0);
    ack_lost = 1;
    assert(upload_exchange(0x45020000, bytes, sizeof bytes, crc, &seq) == -1);
    assert(upload_caps(&status) == 0 && status.sequence == seq &&
           status.address == 0x45020000 && status.checksum == crc);

    reset();
    assert(upload_caps(&status) == 0);
    bad_ack = 1;
    assert(upload_exchange(0x45020000, bytes, sizeof bytes, crc, &seq) == -1);

    reset();
    assert(upload_caps(&status) == 0);
    reject_upload = 1;
    assert(upload_exchange(0x45020000, bytes, sizeof bytes, crc, &seq) == -2);
    assert(accepted.sequence == 0);

    reset();
    int sockets[2];
    assert(socketpair(AF_UNIX, SOCK_STREAM, 0, sockets) == 0);
    char first[128];
    int header = snprintf(first, sizeof first, "PUT 0x45010000 8 0x%08X\n", crc32(bytes, 8));
    memcpy(first + header, bytes, 3);
    assert(write(sockets[1], bytes + 3, 5) == 5);
    uint32_t address, checksum;
    size_t length;
    unsigned char received[UP_DATA_MAX];
    assert(upload_request(sockets[0], first, (size_t)header + 3,
                          &address, received, &length, &checksum) == 0);
    assert(address == 0x45010000 && length == 8 && checksum == crc32(bytes, 8));
    assert(!memcmp(received, bytes, 8) && out_calls == 0);
    char bad[] = "PUT -1 8 0x00000000\n";
    assert(upload_request(sockets[0], bad, strlen(bad),
                          &address, received, &length, &checksum) < 0);
    assert(out_calls == 0);
    close(sockets[0]); close(sockets[1]);

    puts("PASS upload host");
    return 0;
}
