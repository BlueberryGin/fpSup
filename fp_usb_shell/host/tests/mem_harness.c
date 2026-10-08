/* Execute the actual host MEM1 code (fpshd.c) against a fake worker that
 * follows worker.S's MEM1 rules: windows, READY/DONE, refusal frames.
 * No libusb library, daemon socket or camera is opened. */
#define main fpshd_program_main
#include "../fpshd.c"
#undef main
#include <assert.h>
#include <sys/socket.h>

#define BASE 0x50000000u
#define SPAN (40u << 20)                 /* 40 MiB of fake camera memory */
static unsigned char *ram;
static uint32_t win_base = BASE, win_len = SPAN, win_perm = 3;

/* knobs */
static int short_by, bad_crc, wrong_seq, lose_done;
static int reads, writes, refusals;

/* fake state */
static enum { IDLE, READ_PENDING, READY_PENDING, DATA, DONE_PENDING, REFUSAL } st;
static uint8_t cur_cmd;
static uint32_t cur_seq, cur_addr, cur_len, cur_opts, got;
static fpsh_frame reply;
static double ready_at;   /* when the data TRB was armed: the worker gives up after MEM_TMO */

int libusb_init(libusb_context **c) { *c = (void *)1; return 0; }
void libusb_exit(libusb_context *c) { (void)c; }
libusb_device_handle *libusb_open_device_with_vid_pid(libusb_context *c,
                                                       uint16_t vid, uint16_t pid)
{ (void)c; (void)vid; (void)pid; return (void *)2; }
int libusb_set_auto_detach_kernel_driver(libusb_device_handle *c, int yes)
{ (void)c; (void)yes; return 0; }
int libusb_claim_interface(libusb_device_handle *c, int n) { (void)c; (void)n; return 0; }
int libusb_release_interface(libusb_device_handle *c, int n) { (void)c; (void)n; return 0; }
void libusb_close(libusb_device_handle *c) { (void)c; }
libusb_device *libusb_get_device(libusb_device_handle *c) { (void)c; return (void *)3; }
int libusb_get_active_config_descriptor(libusb_device *d,
                                         struct libusb_config_descriptor **c)
{ (void)d; *c = NULL; return LIBUSB_ERROR_IO; }
void libusb_free_config_descriptor(struct libusb_config_descriptor *c) { (void)c; }
int libusb_clear_halt(libusb_device_handle *c, unsigned char ep)
{ (void)c; (void)ep; return 0; }
const char *libusb_error_name(int r) { (void)r; return "fake"; }

static int allowed(uint32_t a, uint32_t n, uint32_t perm)
{
    uint64_t end = (uint64_t)a + n;
    return n && (win_perm & perm) == perm && a >= win_base &&
           end <= (uint64_t)win_base + win_len;
}

static void make_reply(uint32_t status, const uint32_t *w, int n)
{
    memset(&reply, 0, sizeof reply);
    memcpy(reply.magic, "FPSH", 4);
    reply.version = 1;
    reply.command = cur_cmd;
    reply.sequence = cur_seq + (wrong_seq ? 1 : 0);
    reply.status = (uint16_t)status;
    reply.payload_length = (uint16_t)(4 * n);
    memcpy(reply.payload, w, 4 * (size_t)n);
    reply.checksum = crc32((unsigned char *)&reply, FRAME_SIZE);
}

static void refuse(uint32_t code)
{
    make_reply(code, &code, 1);
    st = REFUSAL;
    refusals++;
}

int libusb_bulk_transfer(libusb_device_handle *c, unsigned char ep,
                         unsigned char *bytes, int length, int *moved,
                         unsigned timeout)
{
    assert(c == (void *)2 && timeout > 0);
    *moved = 0;
    if (st == DATA && now_ms() - ready_at > 500 + (cur_len >> 15))
        st = IDLE;                               /* the worker ended the TRB */
    if (ep == EP_OUT && st == DATA) {           /* the MEMW data phase */
        assert((uint32_t)length == cur_len);
        uint32_t n = cur_len - (uint32_t)short_by;
        memcpy(ram + (cur_addr - BASE), bytes, n);
        got = n;
        *moved = length;
        st = DONE_PENDING;
        return 0;
    }
    if (ep == EP_OUT) {
        assert(st == IDLE);
        assert(length == FRAME_SIZE + 4);
        fpsh_frame tx;
        memcpy(&tx, bytes, sizeof tx);
        uint32_t want = tx.checksum;
        tx.checksum = 0;
        assert(!memcmp(tx.magic, "FPSH", 4) && tx.version == 1 && tx.flags == 0);
        assert(crc32((unsigned char *)&tx, FRAME_SIZE) == want);
        cur_cmd = tx.command;
        cur_seq = tx.sequence;
        uint32_t w[4] = {0};
        memcpy(w, tx.payload, tx.payload_length);
        cur_addr = w[0]; cur_len = w[1]; cur_opts = w[2];
        *moved = length;
        if (cur_cmd == CMD_MEMR) {
            reads++;
            if (cur_len > MEM_XFER_MAX || !allowed(cur_addr, cur_len, 1)) refuse(4);
            else st = READ_PENDING;
        } else if (cur_cmd == CMD_MEMW) {
            writes++;
            uint32_t armed = (cur_len + 1023) & ~1023u;
            if (!cur_len || cur_len > MEM_XFER_MAX) refuse(2);
            else if (!allowed(cur_addr, armed, 2)) refuse(4);
            else {
                uint32_t r[3] = {0, cur_addr, armed};
                make_reply(0, r, 3);
                st = READY_PENDING;
                ready_at = now_ms();
            }
        } else if (cur_cmd == CMD_WIN) {
            win_base = w[1]; win_len = w[2]; win_perm = w[3];
            uint32_t r = 0;
            make_reply(0, &r, 1);
            st = REFUSAL;                      /* i.e. one frame then idle */
        } else assert(!"unexpected command");
        return 0;
    }
    assert(ep == EP_IN);
    switch (st) {
    case READ_PENDING:
        assert((uint32_t)length >= cur_len);
        memcpy(bytes, ram + (cur_addr - BASE), cur_len);
        *moved = (int)cur_len;
        st = IDLE;
        return 0;
    case READY_PENDING:
        memcpy(bytes, &reply, FRAME_SIZE);
        *moved = FRAME_SIZE;
        st = DATA;
        return 0;
    case DONE_PENDING: {
        if (lose_done) { st = IDLE; return LIBUSB_ERROR_TIMEOUT; }
        uint32_t crc = 0;
        if (cur_opts & MEM_OPT_CRC)
            crc = (uint32_t)crc32_z(0, ram + (cur_addr - BASE), got) ^ (bad_crc ? 1 : 0);
        uint32_t r[3] = {0, got, crc};
        make_reply(0, r, 3);
        memcpy(bytes, &reply, FRAME_SIZE);
        *moved = FRAME_SIZE;
        st = IDLE;
        return 0;
    }
    case REFUSAL:
        assert(length >= FRAME_SIZE);
        memcpy(bytes, &reply, FRAME_SIZE);
        *moved = FRAME_SIZE;
        st = IDLE;
        return 0;
    default:
        return LIBUSB_ERROR_TIMEOUT;           /* drain_in: nothing there */
    }
}

/* One socket request through the daemon's own handler, data via real shm. */
static char answer[256];
static const char *request(const char *verb, uint32_t addr, size_t n, uint32_t opts,
                           unsigned char *data)
{
    char name[64];
    snprintf(name, sizeof name, "/fpshd-mem-test-%d", getpid());
    shm_unlink(name);
    int fd = shm_open(name, O_RDWR | O_CREAT, 0600);
    assert(fd >= 0 && ftruncate(fd, (off_t)n) == 0);
    unsigned char *m = mmap(NULL, n, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    assert(m != MAP_FAILED);
    close(fd);
    if (verb[3] == 'W') memcpy(m, data, n);
    int sv[2];
    assert(socketpair(AF_UNIX, SOCK_STREAM, 0, sv) == 0);
    char line[160];
    snprintf(line, sizeof line, "%s 0x%08X %zu %u %s", verb, addr, n, opts, name);
    mem_request(sv[0], line);
    ssize_t k = read(sv[1], answer, sizeof answer - 1);
    answer[k > 0 ? k : 0] = 0;
    close(sv[0]); close(sv[1]);
    if (verb[3] == 'R') memcpy(data, m, n);
    munmap(m, n);
    shm_unlink(name);
    return answer;
}

int main(void)
{
    ram = calloc(1, SPAN);
    unsigned char *a = malloc(SPAN), *b = malloc(SPAN);
    for (size_t i = 0; i < SPAN; i++) a[i] = (unsigned char)(i * 131 + (i >> 9));

    /* sizes either side of a frame, a packet and a transfer */
    const size_t sizes[] = {1, 3, 63, 64, 65, 1023, 1024, 1025, 5000,
                            MEM_XFER_MAX, MEM_XFER_MAX + 1, 2 * MEM_XFER_MAX + 4096};
    for (size_t i = 0; i < sizeof sizes / sizeof *sizes; i++) {
        size_t n = sizes[i];
        uint32_t at = BASE + 4096;
        memset(ram, 0, SPAN);
        writes = reads = 0;
        assert(!strncmp(request("MEMW", at, n, 3, a), "OKW ", 4));
        assert(!memcmp(ram + 4096, a, n));
        assert(writes == (int)((n + MEM_XFER_MAX - 1) / MEM_XFER_MAX));
        memset(b, 0, n);
        assert(!strncmp(request("MEMR", at, n, 1, b), "OKR ", 4));
        assert(!memcmp(a, b, n));
        assert(reads == writes);
    }

    /* the window ends inside the second transfer: first lands, then refused */
    win_len = MEM_XFER_MAX + 8192;
    memset(ram, 0, SPAN);
    request("MEMW", BASE, 2 * MEM_XFER_MAX, 0, a);
    assert(strstr(answer, "ERR mem refused") && strstr(answer, "code=4"));
    char want[64];
    snprintf(want, sizeof want, "done=%u", MEM_XFER_MAX);
    assert(strstr(answer, want));
    assert(!memcmp(ram, a, MEM_XFER_MAX));
    request("MEMR", BASE, 2 * MEM_XFER_MAX, 0, b);
    assert(strstr(answer, "ERR mem refused"));
    win_len = SPAN;

    /* 64-byte read, refused: the one shape a refusal frame shares with data */
    win_perm = 2;
    request("MEMR", BASE, 64, 0, b);
    assert(strstr(answer, "ERR mem refused"));
    request("MEMR", BASE, 10, 0, b);
    assert(strstr(answer, "ERR mem refused"));
    win_perm = 1;
    request("MEMW", BASE, 100, 0, a);
    assert(strstr(answer, "ERR mem refused"));
    win_perm = 3;

    /* the camera got fewer bytes than were sent */
    short_by = 4;
    request("MEMW", BASE, 5000, 0, a);
    assert(strstr(answer, "uncertain") && strstr(answer, "received 4996 of 5000"));
    short_by = 0;

    /* the camera's CRC disagrees */
    bad_crc = 1;
    request("MEMW", BASE, 5000, MEM_OPT_CRC, a);
    assert(strstr(answer, "uncertain") && strstr(answer, "CRC"));
    bad_crc = 0;

    /* DONE never comes */
    lose_done = 1;
    request("MEMW", BASE, 5000, 0, a);
    assert(strstr(answer, "uncertain"));
    lose_done = 0;

    /* a reply for some other request is not ours */
    wrong_seq = 1;
    request("MEMW", BASE, 5000, 0, a);
    assert(strstr(answer, "uncertain") && strstr(answer, "not ours"));
    wrong_seq = 0;

    /* windows go through, and say so */
    assert(mem_win(1, BASE, 4096, 1) == 0 && win_len == 4096 && win_perm == 1);

    /* bad arguments never reach the camera */
    writes = reads = 0;
    int sv[2];
    assert(socketpair(AF_UNIX, SOCK_STREAM, 0, sv) == 0);
    char bad[] = "MEMW 0xFFFFFF00 4096 0 /x";
    mem_request(sv[0], bad);
    ssize_t k = read(sv[1], answer, sizeof answer - 1);
    answer[k] = 0;
    assert(!strncmp(answer, "ERR mem args", 12) && writes == 0);

    printf("PASS mem host\n");
    return 0;
}
