#!/usr/bin/env python3
"""Write a local file onto the camera's card, over the shell.

    ./putfile.py hello.txt '\\TEST.TXT'
    ./putfile.py autorun/AutoRun.txt '\\AutoRun.txt'

Which is how the AutoRun gets updated without taking the card out: the file the
shell was started from is just a file, and the shell can replace it.

Mode 7 overwrites but does not truncate an existing file. Use deploy.py for
card updates: it prepares a long enough file and checks the complete readback.

Most of this is native commands. An UP01 worker stages large files through a
binary OUT request; older workers use `echo` and `mem set` repairs. `dir`
confirms the final result. The file write itself is asm/putfile.S, run by
borrowing the `echo` command's handler.

Measured 2026-09-19: a 32 KB file in 10 seconds the first time and half a
second after that -- the first call pays for putting the bulk loader itself in,
one word per command, and every call after it moves 240 bytes a round trip at
65-70 KiB/s.  "Roughly 20 words a second, so a 14 KB AutoRun takes a few
minutes" was this file's own description of itself and had been wrong since the
bulk loader was added.

An UP01 worker accepts 16 KiB binary chunks on EP01, each at an absolute
address with CRC and an acknowledged last-chunk record. Older workers use the
240-byte `echo` loader. Both paths read back staging before opening a card
file; the final card file readback belongs to deploy.py.

`mem set` drops whole commands, so what is staged is read back and repaired.
The loss is not steady: one run repaired 2,673 words of 8,193 and the next
repaired none.
"""
import argparse, os, pathlib, re, socket, struct, sys, time, zlib

from armasm import assemble

HERE = pathlib.Path(__file__).resolve().parent
SOCK = os.environ.get('FPSHD_SOCKET', '/tmp/fpshd.sock')

# Where this tool's blocks are: asked for, not chosen.  These used to be six
# hand-picked addresses in a row -- and the parameter block's was 0xC072F700,
# which store_boot.S and four probes had also picked.  cave.claim gives the same
# address for the same name all boot, so the bulk loader stays resident between
# invocations exactly as it did, and a reboot re-issues everything.
#
# They resolve on first use rather than at import: importing this module must
# not talk to the camera, because half the tree imports it for mem_get alone.
# Once resolved they are cached in globals(), so a process that outlives a
# camera reboot would hold stale ones -- no tool here does, and cave.claim
# stays the authority.
_LAYOUT = {
    'P':          ('putfile.parm',    256),   # the parameter block
    'CODE':       ('putfile.code',    512),   # the working template
    'BULK_STATE': ('bulkload.state',    8),   # the bulk loader's two words
    'BULK':       ('bulkload.code',   256),   # resident alongside it
    'DUMP':       ('dumpraw.code',    256),   # the raw reader
    'DIRECT':     ('dumpdirect.code', 256),   # the direct reader
}
_ENDS = {'CODE_END': 'CODE', 'BULK_END': 'BULK', 'DUMP_END': 'DUMP'}


def __getattr__(name):
    """For importers: `putfile.CODE` resolves the claim on first touch.

    PEP 562 only covers attribute access from OUTSIDE the module -- a bare
    `CODE` inside a function here is an ordinary global lookup and never
    reaches this.  So functions in this file call _claims() first; forgetting
    it is a NameError on that path, which is loud.
    """
    if name in _LAYOUT or name in _ENDS:
        _claims()
        return globals()[name]
    raise AttributeError(name)


def _claims():
    """Resolve every block this tool owns, once per process."""
    if 'CODE' in globals():
        return
    import cave
    for nm, (key, n) in _LAYOUT.items():
        globals()[nm] = cave.claim(key, n)
    for nm, base in _ENDS.items():
        globals()[nm] = globals()[base] + _LAYOUT[base][1]


DUMP_CAP  = 0x4000              # dumpraw's own ceiling; it truncates past this
                                # without saying so, and a short block leaves the
                                # host waiting for bytes that never come
DIRECT_CHUNK = 0x100000         # bytes per round trip on the direct path
# One megabyte measured fastest: 73 MB/s against 41 at two and 11 at three and a
# half. Not understood, so it is a measurement rather than a rule.
DUMP_CHUNK = DUMP_CAP - 4       # bytes per round trip, copying path
# Three thousand was chosen when the reply went out through printf and the rate
# went flat past that. Nothing about the raw path shares that ceiling: a chunk
# costs one command whatever its size, and a 31 MB file at three thousand bytes
# is eleven thousand of them. The round trip is four tenths of a millisecond and
# the misses are two hundred, so almost all of that time was the count, not the
# bytes. Four less than the buffer, because the address tag goes on the end and
# the capture buffer is exactly DUMP_CAP long.
# A run of this size used to stop answering somewhere between a hundred
# kilobytes and three megabytes, and only a cable replug brought it back. Two
# things fixed that: the worker never answers a raw request with nothing (the
# host's cancelled transfer was what took the USB device off the bus), and it
# waits three hundred milliseconds for a reply to be collected rather than fifty
# seconds (during which it was deaf to everything). A 31 MB DNG now comes off
# the card in one call, decodes, and is the picture that was taken.
READ_MAX = 64 * 1024 * 1024
READ_TIMEOUT = 200              # ms
# The median round trip is four tenths of a millisecond, so this is not a wait
# anyone pays: it is what a chunk is worth before giving up on it. Thirty looked
# generous by that measure and lost seven chunks in a hundred; two hundred loses
# one and a half, and a thousand loses exactly the same one and a half, so what
# is left is genuinely gone rather than slow. Being early costs more than being
# late here -- the camera has the block ready, and walking away from one it has
# prepared is what leaves the worker unable to answer anything again.
CHUNK     = 240                 # bytes per command; the line holds about 502 chars
BULK_BATCH = 32                 # checkpoint before a lost command shifts a large suffix
BULK_RETRIES = 6                # bounded whole-batch resends, never wordwise for drift
UPLOAD_CHUNK = 0x3FC0           # 16 KiB OUT TRB minus the FPSH frame
UPLOAD_RETRIES = 3             # retry only after a fresh worker status query
ECHO_SLOT = 0xC0BAC2F8          # command table entry 17, echo's handler pointer
ECHO_ORIG = 0xC03D99A0
# The WORKER's pool, not the card's.  0xC3757A7C was the one pool the AutoRun
# asked for and everything shared; the worker asks the allocator for its own in
# `spawn` now and leaves the address here, in the tail of the loader's reserved
# 512 bytes.  A payload that needs memory does the same and publishes wherever
# its own code reads -- the gyro logger still uses 0xC3757A7C, and that word now
# means "the logger's pool" rather than "everybody's".
# FPSUP_POOL_PTR overrides it, for talking to a card built before the worker
# asked for its own pool -- on those the loader published one at 0xC3757A7C and
# everything shared it.  Being able to drive both is what makes an A/B between
# an old card and a new one one variable instead of two.
POOL_PTR  = int(os.environ.get('FPSUP_POOL_PTR', '0xC072F050'), 0)
POOL_SIZE_AT = POOL_PTR + 4     # what the worker asked for
POOL_OFF  = 0x10000             # USB transfer scratch, past shell and gyro
FOBJ_ROOM = 0x400

P_STATUS, P_OPENR, P_WRITER = 0x08, 0x0C, 0x10
P_DATA, P_LEN, P_FOBJ, P_MODE, P_PATH = 0x14, 0x18, 0x1C, 0x20, 0x24

STATUS = {0: 'never ran', 1: 'started but did not finish', 2: 'written',
          3: 'open returned 0', 4: 'write returned 0'}


def sh(line: str, retries: int = 3, raw: int = 0, timeout_ms: int = 0,
       want_hex: bool = False) -> str:
    """One command, one connection -- the daemon closes after each reply.

    Roughly one command in ten goes missing, in one direction or the other, so a
    reply that does not come back is retried rather than believed.  The daemon
    gives up after 200 ms, which is what makes retrying cheap. Pass
    `retries=0` for non-idempotent commands such as bulkload's append-style
    `echo`, then inspect the camera-side counter instead.

    Not cheap enough, though, when the command itself answers in under two
    milliseconds: a reply lost every few dozen chunks then costs more than the
    entire rest of the read. `timeout_ms` says how long this particular command
    is worth waiting for. Leave it alone for anything that touches the card.
    """
    for attempt in range(retries + 1):
        s = socket.socket(socket.AF_UNIX)
        s.connect(SOCK)
        head = f'BULK {raw} '.encode() if raw else b'SHL '
        if timeout_ms:
            head = f'TMO {timeout_ms} '.encode() + head
        if want_hex:
            head = b'HEX ' + head
        s.sendall(head + line.encode() + b'\n')
        out = b''
        while True:
            b = s.recv(65536)
            if not b:
                break
            out += b
        s.close()
        text = out.decode(errors='replace')
        if not text.startswith('ERR'):
            if text.startswith('OKX '):
                # A raw block. The daemon hex-encodes it because a line-based
                # socket cannot carry arbitrary bytes; decode it back here so
                # there is one return type. Text replies survive the round trip
                # unchanged, which matters because any reply over 128 bytes now
                # comes this way -- including `mem get`.
                try:
                    return bytes.fromhex(text[4:].strip()).decode('latin-1')
                except ValueError:
                    return ''

            if text.startswith('OK '):
                text = text[3:]
            # the daemon escapes newlines so a reply stays one line on the wire
            return text.replace('\\n', '\n')
    return text


def sh_raw(line: str, nbytes: int, timeout_ms: int = 0) -> bytes:
    """A raw block straight into a buffer: `RAW <n>` answers "OKB <n>\\n" and
    the bytes, no hex.  Returns b'' on any error (the caller retries).  The old
    `BULK` path hex-encoded the block and this side grew it with `+=`, which
    is quadratic: 8 MiB reads ran at 5 MB/s."""
    s = socket.socket(socket.AF_UNIX)
    s.connect(SOCK)
    head = (f'TMO {timeout_ms} ' if timeout_ms else '') + f'RAW {nbytes} '
    s.sendall(head.encode() + line.encode() + b'\n')
    first = b''
    while b'\n' not in first and len(first) < 64:
        b = s.recv(64 - len(first))
        if not b:
            break
        first += b
    if not first.startswith(b'OKB '):
        s.close()
        return b''
    hdr, _, rest = first.partition(b'\n')
    n = int(hdr[4:])
    buf = bytearray(n)
    view = memoryview(buf)
    got = len(rest)
    view[:got] = rest[:n]
    while got < n:
        k = s.recv_into(view[got:], n - got)
        if not k:
            break
        got += k
    s.close()
    return bytes(buf) if got == n else b''


_shm = [None]


def _shm_release():
    m, _shm[0] = _shm[0], None
    if m is None:
        return
    try:
        m.close()
    except BufferError:                 # a caller still holds a view: let it go
        pass
    try:
        m.unlink()
    except FileNotFoundError:
        pass


def _shared(nbytes):
    """One shared-memory buffer per process, grown when needed, removed at exit."""
    from multiprocessing import shared_memory
    import atexit
    cur = _shm[0]
    if cur is None or cur.size < nbytes:
        if cur is None:
            atexit.register(_shm_release)
        else:
            _shm_release()
        _shm[0] = shared_memory.SharedMemory(create=True, size=max(nbytes, 16 << 20))
    return _shm[0]


def sh_shm(line: str, nbytes: int, timeout_ms: int = 0):
    """A raw block read by the daemon straight into shared memory (`RAWSHM`):
    nothing crosses the socket but "OKS <n>".  Returns a memoryview of the
    shared buffer (valid until the next call), or None on any error."""
    m = _shared(nbytes)
    s = socket.socket(socket.AF_UNIX)
    s.connect(SOCK)
    head = (f'TMO {timeout_ms} ' if timeout_ms else '') + f'RAWSHM {nbytes} /{m.name.lstrip("/")} '
    s.sendall(head.encode() + line.encode() + b'\n')
    ans = b''
    while b'\n' not in ans and len(ans) < 256:
        b = s.recv(256)
        if not b:
            break
        ans += b
    s.close()
    if not ans.startswith(b'OKS ') or int(ans[4:ans.index(b'\n')]) != nbytes:
        return None
    return m.buf[:nbytes]


# --- MEM1: the worker's TRB pointed straight at the bytes, both directions ----
# No echo handler is borrowed and nothing is copied on the camera; the bytes go
# between libusb and a shared-memory buffer and never cross the socket.  The
# camera refuses anything outside a window (worker.S, MEM1): its own pool is
# implicit (read all, write past +0x10000); anything else must be granted with
# mem_window first, writable only from 0x44000000 up and never over the
# worker's header or code.
MEM_OPT_SYNC, MEM_OPT_CRC = 1, 2
MEM_TRANSIENT_SLOT = 7          # read_direct's grant: given, used, revoked
_mem1 = [None]                  # None = not asked yet, False = old worker


def mem_line(line: str, timeout: float = 60) -> str:
    with socket.socket(socket.AF_UNIX) as conn:
        conn.settimeout(timeout)
        conn.connect(SOCK)
        conn.sendall(line.encode('ascii') + b'\n')
        answer = bytearray()
        while b'\n' not in answer and len(answer) < 1024:
            block = conn.recv(1024)
            if not block:
                break
            answer.extend(block)
    return answer.decode('ascii', errors='replace').strip()


def mem_caps(refresh=False):
    """{'xfer', 'pool', 'pool_size', 'windows'} from a MEM1 worker, else None.

    FPSUP_NO_MEM1=1 says None without asking: the checked old paths, for a
    worker whose MEM1 is suspect (it is how a fixed one gets deployed)."""
    if os.environ.get('FPSUP_NO_MEM1') == '1':
        return None
    if _mem1[0] is None or refresh:
        answer = mem_line('MCAPS', timeout=5)
        nums = answer.split()[1:]
        if answer.startswith('OKMC ') and len(nums) == 27:
            v = [int(x, 0) for x in nums]
            _mem1[0] = {'xfer': v[0], 'pool': v[1], 'pool_size': v[2],
                        'windows': [tuple(v[3 + 3 * i:6 + 3 * i]) for i in range(8)]}
        elif answer in ('ERR unknown', 'ERR mcaps unsupported'):
            _mem1[0] = False            # an older daemon or worker
        else:
            raise RuntimeError(f'MEM1 capability uncertain: {answer!r}')
    return _mem1[0] or None


def mem_window(slot, base, length, perm):
    """Grant (perm 1 read, 2 write, 3 both) or revoke (perm 0) one window."""
    answer = mem_line(f'WIN {slot} 0x{base:08X} {length} {perm}', timeout=5)
    if answer != 'OKW':
        raise RuntimeError(f'window {slot} 0x{base:08X}+{length}: {answer}')


def mem_read(addr, nbytes, sync=True, label='memr  '):
    """Bytes at `addr`, one transfer per 16 MiB, nothing copied on the camera."""
    m = _shared(nbytes)
    answer = mem_line(f'MEMR 0x{addr:08X} {nbytes} {MEM_OPT_SYNC if sync else 0} '
                      f'/{m.name.lstrip("/")}')
    if not answer.startswith('OKR '):
        raise RuntimeError(f'{label}: {answer}')
    ms = float(answer.split()[2])
    print(f'\r  {label} {nbytes} bytes in {ms/1000:.3f}s '
          f'({nbytes/max(ms, 1e-3)/1000:.1f} MB/s)      ', file=sys.stderr)
    return bytes(m.buf[:nbytes])


def mem_write(addr, data, sync=True, crc=False, verify=True, label='memw  '):
    """Write `data` at `addr`, then (by default) read all of it back and compare.

    USB 3 already checks every packet and the camera reports how many bytes
    landed; the readback is what proves they landed where they should and that
    nothing on the camera wrote over them since.  `crc` asks the camera to CRC
    what landed as well -- measured separately, since it is the CPU touching
    every byte, which is exactly what this path exists to avoid."""
    data = bytes(data)
    m = _shared(len(data))
    m.buf[:len(data)] = data
    opts = (MEM_OPT_SYNC if sync else 0) | (MEM_OPT_CRC if crc else 0)
    answer = mem_line(f'MEMW 0x{addr:08X} {len(data)} {opts} /{m.name.lstrip("/")}')
    if not answer.startswith('OKW '):
        raise RuntimeError(f'{label}: {answer}')
    ms = float(answer.split()[2])
    print(f'\r  {label} {len(data)} bytes in {ms/1000:.3f}s '
          f'({len(data)/max(ms, 1e-3)/1000:.1f} MB/s)      ', file=sys.stderr)
    if verify:
        back = mem_read(addr, len(data), sync, 'verify')
        if back != data:
            bad = next(i for i in range(len(data)) if back[i] != data[i])
            raise RuntimeError(f'{label}: readback differs from offset {bad} '
                               f'(0x{addr + bad:08X})')


def mem_set(addr, value):
    sh(f'mem set 0x{addr:08X} 0x{value:08X}')


def mem_get(addr, count=1, tries=4):
    """Read `count` words, retrying while any of them is missing.

    The shell answers one line per word: `get : A:0xc072f000, D:0x4C485356`.
    Parsed exactly rather than by scraping hex, so a reply that is not a dump --
    an error, an echo of the command -- yields nothing instead of numbers that
    look real.

    Whole commands go missing sometimes, in both directions, so a reply with a
    word absent is retried rather than believed.  Words already seen are kept:
    what comes back is consistent, it is the round trip that is not.
    """
    seen = {}
    for _ in range(tries):
        out = sh(f'mem get 0x{addr:08X},,0x{count * 4:X}')
        for a, d in re.findall(r'A:0x([0-9A-Fa-f]+),\s*D:0x([0-9A-Fa-f]+)', out):
            seen[int(a, 16)] = int(d, 16)
        if all(addr + i * 4 in seen for i in range(count)):
            break
    return [seen.get(addr + i * 4) for i in range(count)]


def set_echo_handler(handler, tries=8):
    """Publish or restore the borrowed handler with a checked readback."""
    for _ in range(tries):
        mem_set(ECHO_SLOT, handler)
        got = mem_get(ECHO_SLOT)
        if got and got[0] == handler:
            return
    raise SystemExit(f'echo handler did not become 0x{handler:08X}; '
                     'stop using echo until ownership is established')


def staging_area():
    """Where to stage the bytes.

    Not `memmgr bufmem get`.  That works from the AutoRun, at boot, but issued
    live through the shell it wedged the endpoint and froze the camera -- once,
    which was enough.  So the address is derived from the pool the AutoRun
    already took.

    The offset is the whole point.  +0x2000 was the obvious spot until the shell
    put its own capture buffer there, at which point staging a file meant writing
    into the memory that carries every reply, and the region-is-free check caught
    it on the first command.  The map now is: +0x0000 the shell's frames,
    +0x2000 its capture buffer, +0x6000 the gyro logger, +0x10000 here.
    """
    got = mem_get(POOL_PTR)
    if not got or not 0x40000000 <= got[0] < 0x50000000:
        raise SystemExit(f'0x{POOL_PTR:08X} does not hold a pool address ({got}). '
                         f'The worker publishes it when it starts; zero there '
                         f'means the allocator refused it and there is no worker.')
    return got[0] + POOL_OFF


POOL_SIZE = 1048576             # the fallback, and worker.S's WPOOL_BYTES


def pool_end():
    """One past the end of what the AutoRun asked for.

    Not read from `memmgr bufchk`. The memmgr commands are not safe to issue
    live -- `bufmem get` wedged the endpoint and froze the camera earlier today,
    and putting `bufchk` on the path every transfer takes did it again. The size
    is a constant here and in build_autorun.py, and the two have to agree; the
    check below is a guard against a stale one, not a discovery.
    """
    # Read the size the worker actually asked for rather than trusting the
    # constant above.  The comment under `pool()` is right that memmgr is not
    # safe to issue live -- this is not memmgr, it is a word the worker wrote
    # once, and reading it is what stops this file and worker.S from drifting
    # apart the way this and build_autorun.py used to.
    size = mem_get(POOL_SIZE_AT)
    return mem_get(POOL_PTR)[0] + (size[0] if size and size[0] else POOL_SIZE)


def check_fits(addr, length, what):
    """Refuse rather than overflow.

    Staging 247 KB into the 64 KiB that was left ran off the end of the
    allocation into memory somebody else kept rewriting, so the verify could
    never converge and six passes of retries took six minutes -- reported as
    slowness, not as the out-of-bounds write it was.
    """
    end = pool_end()
    if addr + length > end:
        raise SystemExit(
            f'{what}: 0x{addr:08X}+{length} runs {addr + length - end} bytes past '
            f'the allocation end 0x{end:08X}.\n'
            "Raise the size in build_autorun.py's `memmgr bufmem get` and "
            'POOL_SIZE here, or move less at once.')


def prove(addr, length):
    """Write a pattern across the region, read it back, then read it again.

    The second read is the point: memory that takes a write but belongs to
    something else reads back correctly and is overwritten a moment later.
    """
    spots = [addr, (addr + length // 2) & ~3, (addr + length - 4) & ~3]
    for i, a in enumerate(spots):
        mem_set(a, 0xC0DE0000 | i)
    for wait in (0, 1.0):
        time.sleep(wait)
        for i, a in enumerate(spots):
            got = mem_get(a)
            if not got or got[0] != (0xC0DE0000 | i):
                raise SystemExit(f'0x{a:08X} reads {got} — staging area is not free')


READ_CHUNK = 128                # words per request


def read_back(addr, count):
    """Read `count` words, in requests as large as the reply will carry.

    Throughput flattens at about 44 KiB/s from 48 words up, and the reply for 64
    is well inside the daemon's 8 KiB buffer, so there is nothing to gain by
    going wider and something to lose if a reply is ever truncated.
    """
    out = []
    t0 = time.time()
    for base in range(0, count, READ_CHUNK):
        out += mem_get(addr + base * 4, min(READ_CHUNK, count - base))
        if base and (base // READ_CHUNK) % 16 == 0:
            el = time.time() - t0
            print(f'\r  read   {len(out)}/{count} words  {len(out)*4/el/1024:.1f} KiB/s'
                  f'  {(count-len(out))*el/max(len(out),1):.0f}s left ', end='', flush=True, file=sys.stderr)
    if count > READ_CHUNK * 16:
        print(f'\r  read   {count} words in {time.time()-t0:.1f}s'
              f' ({count*4/(time.time()-t0)/1024:.1f} KiB/s)          ', file=sys.stderr)
    return out


def put_slow(addr, blob, label, passes=6):
    """One word per command.  Used for the bulk loader itself, and for repairs."""
    w = list(struct.unpack(f'<{len(blob)//4}I', blob))
    for i, word in enumerate(w):
        mem_set(addr + i * 4, word)
    for _ in range(passes):
        got = read_back(addr, len(w))
        bad = [i for i in range(len(w)) if got[i] != w[i]]
        if not bad:
            return w
        for i in bad:
            mem_set(addr + i * 4, w[i])
    raise SystemExit(f'{label}: still short after {passes} passes')


_bulk_loaded = False
_dump_loaded = False
_stale = [0]        # blocks that answered a different address


_direct_loaded = False


def ensure_direct():
    _claims()
    global _direct_loaded
    if not _direct_loaded:
        code = assemble(HERE / 'asm' / 'dumpdirect.S')
        put_slow(DIRECT, code, 'direct')
        _direct_loaded = True


def ensure_dump():
    _claims()
    global _dump_loaded
    if not _dump_loaded:
        code = assemble(HERE / 'asm' / 'dumpraw.S', [f'P=0x{P:08X}'])
        if DUMP + len(code) > DUMP_END:
            raise SystemExit(f'dumpraw is {len(code)} bytes and would run into '
                             f'the scratch at 0x{DUMP_END:08X}')
        put_slow(DUMP, code, 'dump  ')
        _dump_loaded = True


def read_direct(addr, nbytes, label='direct'):
    """Read without copying: the worker points its TRB at `addr` itself.

    dumpraw stages everything through the shell's 16 KiB capture buffer, so a
    31 MB file came back in 1995 round trips and sixteen seconds. The card was
    never the slow part -- it reads that file into memory in 0.17 s, at
    183 MB/s -- it was a staging buffer we were copying through for no reason.
    A TRB carries a 24-bit length, so one transfer can be sixteen megabytes.

    Same file, whole thing, 0.42 s.

    `addr` must be somewhere the controller can reach as CPU-0x40000000: the
    pool, or what the firmware's allocator returns, which is where files land.
    The firmware's own image at 0xC0000000 is mapped some other way -- use
    read_bulk for that.

    No address tag either, so a stale reply cannot be told from a real one. That
    mattered when there were two thousand of them; at thirty-two it is a
    different bet, and one worth knowing you are making.
    """
    _claims()
    if mem_caps():
        # A one-off read grant for exactly these bytes, revoked after.
        mem_window(MEM_TRANSIENT_SLOT, addr & ~3, (addr + nbytes - (addr & ~3) + 3) & ~3, 1)
        try:
            return mem_read(addr, nbytes, label=label)
        finally:
            mem_window(MEM_TRANSIENT_SLOT, 0, 0, 0)
    ensure_direct()
    orig = mem_get(ECHO_SLOT)
    if not orig or orig[0] not in (ECHO_ORIG, DIRECT):
        raise SystemExit(f'echo handler is {orig}, not free to borrow')
    out = bytearray()
    t0 = time.time()
    mem_set(ECHO_SLOT, DIRECT)
    try:
        while len(out) < nbytes:
            n = min(DIRECT_CHUNK, nbytes - len(out))
            for attempt in range(6):
                r = sh_shm(f'echo {addr + len(out):X} {n:X}', n, timeout_ms=4000)
                if r is not None:
                    out += r
                    r.release()
                    break
            else:
                raise SystemExit(f'{label}: no reply for {n} bytes at '
                                 f'0x{addr + len(out):08X}')
            el = time.time() - t0
            print(f'\r  {label} {len(out)}/{nbytes} B  {len(out)/el/1024/1024:.1f} MB/s ',
                  end='', flush=True, file=sys.stderr)
    finally:
        for _ in range(8):
            mem_set(ECHO_SLOT, ECHO_ORIG)
            if (mem_get(ECHO_SLOT) or [0])[0] == ECHO_ORIG:
                break
        else:
            print('  WARNING echo handler still borrowed')
    dt = time.time() - t0
    print(f'\r  {label} {nbytes} bytes in {dt:.2f}s '
          f'({nbytes/dt/1024/1024:.1f} MB/s)      ', file=sys.stderr)
    return bytes(out)


def read_bulk(addr, nbytes, label='read  '):
    """Read memory through dumpraw.S rather than `mem get`.

    `mem get` answers thirty-four characters for every four bytes, which is
    26 KiB/s. Printing bare hex instead reached 135. Neither was the link: the
    firmware's printf costs about a microsecond a character, and hex pays it
    twice over by doubling the volume first. dumpraw.S skips printf entirely --
    it puts the bytes in the reply buffer and sets the length itself -- so what
    crosses USB is the data.

    That got a 3000-byte block down to 0.27 ms, and then this function spent ten
    more setting up the next one: three parameter words, written one shell
    command at a time, one of which dumpraw never read. The address rides on the
    command line now, so a chunk is one command and there is nothing to set up
    and nothing remembered between calls.
    """
    _claims()
    ensure_dump()
    orig = mem_get(ECHO_SLOT)
    if not orig or orig[0] not in (ECHO_ORIG, DUMP):
        raise SystemExit(f'echo handler is {orig}, not free to borrow')
    chunk = min(DUMP_CHUNK, DUMP_CAP)
    if nbytes > READ_MAX:
        raise SystemExit(f'{label}: {nbytes} bytes in one go is past the '
                         f'{READ_MAX // 1024 // 1024} MiB this has been shown to '
                         f'survive. Read it in pieces.')
    out = bytearray()
    t0 = time.time()
    mem_set(ECHO_SLOT, DUMP)
    try:
        while len(out) < nbytes:
            n = min(chunk, nbytes - len(out))
            want = addr + len(out)
            for attempt in range(12):
                # The raw path needs a worker that understands it. Fall back to
                # the header-framed one so the tool works against either.
                # The raw path. The framed one carries a header saying how
                # many bytes really follow, which sounded like the answer to a
                # worker that sometimes sends none at all -- and it is, for that
                # one symptom. It was measured: still dies, at a third of the
                # speed. Whatever loses the link is not the reply format.
                # Ask for four more than wanted: dumpraw puts the address it
                # read from on the end. A raw block is otherwise anonymous, and
                # a reply abandoned by one read and collected by the next looks
                # exactly like the right answer -- five blocks in forty-two came
                # back belonging to somewhere else, all of them plausible.
                reply = sh(f'echo {want:X} {n:X}', retries=0, raw=n + 4,
                           timeout_ms=READ_TIMEOUT)
                if len(reply) >= n + 4 and not reply.startswith('ERR'):
                    blk = reply[:n + 4].encode('latin-1')
                    if int.from_bytes(blk[n:n + 4], 'little') == want:
                        out += blk[:n]
                        break
                    _stale[0] += 1
            else:
                raise SystemExit(f'{label}: no reply for {n} bytes at '
                                 f'0x{want:08X}')
            el = time.time() - t0
            print(f'\r  {label} {len(out)}/{nbytes} B  {len(out)/el/1024:.0f} KiB/s ',
                  end='', flush=True, file=sys.stderr)
    finally:
        # Put it back, and check.  A single write is not enough: the transport
        # drops commands, and an unrestored handler means the next `echo`
        # anywhere runs whatever is in the cave.
        for _ in range(8):
            mem_set(ECHO_SLOT, ECHO_ORIG)
            if (mem_get(ECHO_SLOT) or [0])[0] == ECHO_ORIG:
                break
        else:
            print(f'  WARNING echo handler still borrowed — restore it before '
                  f'anything else uses `echo`')
    dt = time.time() - t0
    print(f'\r  {label} {nbytes} bytes in {dt:.1f}s ({nbytes/dt/1024:.0f} KiB/s)      ', file=sys.stderr)
    return bytes(out)


def ensure_bulk():
    """Put the bulk loader on the camera, once per run."""
    _claims()
    global _bulk_loaded
    if _bulk_loaded:
        return
    code = assemble(HERE / 'asm' / 'bulkload.S',
                        [f'P=0x{BULK_STATE:08X}'])
    if BULK + len(code) > BULK_END:
        raise SystemExit('bulkload does not fit its region')
    put_slow(BULK, code, 'bulk  ')
    _bulk_loaded = True


def set_bulk_cursor(destination, accepted, tries=8):
    """Reset a batch to a known offset before resending it."""
    for _ in range(tries):
        mem_set(BULK_STATE, destination)
        mem_set(BULK_STATE + 4, accepted)
        if read_bulk_cursor() == [destination, accepted]:
            return
    raise SystemExit('bulkload cursor did not read back; staged bytes are unconfirmed')


def read_bulk_cursor():
    """Read the two idle cursor words, falling back to one word at a time.

    A shell reply can omit one line even when the other word arrived. The
    loader is idle between host commands, so completing that read separately
    does not introduce a moving-snapshot race.
    """
    pair = mem_get(BULK_STATE, 2)
    destination = pair[0] if len(pair) > 0 else None
    accepted = pair[1] if len(pair) > 1 else None
    if destination is None:
        single = mem_get(BULK_STATE, tries=2)
        destination = single[0] if single else None
    if accepted is None:
        single = mem_get(BULK_STATE + 4, tries=2)
        accepted = single[0] if single else None
    return [destination, accepted]


def bulk_accepted(addr, label):
    destination, accepted = read_bulk_cursor()
    if destination is None or accepted is None:
        raise SystemExit(f'{label}: bulkload cursor unavailable after pair and '
                         'single-word reads; stop before file write')
    if destination != addr + accepted:
        raise SystemExit(f'{label}: bulkload cursor diverged '
                         f'(0x{destination:08X}, {accepted} B); '
                         'stop before file write')
    return accepted


def stage_bulk(addr, blob, label, started):
    """Upload bounded batches; use the camera's byte counter before advancing.

    `echo` is non-idempotent here: a host retry can append a chunk twice, while
    a dropped command shifts every later chunk. Send each command only once,
    then use the camera-side cursor to decide whether to resend this batch at
    its absolute starting address. The full memory comparison in `put()` still
    checks content before any file write.
    """
    set_bulk_cursor(addr, 0)
    offset = 0
    restarted = 0
    retried_chunks = 0
    batch_size = CHUNK * BULK_BATCH
    while offset < len(blob):
        end = min(offset + batch_size, len(blob))
        for attempt in range(BULK_RETRIES):
            if attempt:
                set_bulk_cursor(addr + offset, offset)
                restarted += 1
            restart_batch = False
            for pos in range(offset, end, CHUNK):
                piece = blob[pos:min(pos + CHUNK, end)]
                for delivery in range(BULK_RETRIES):
                    reply = sh('echo ' + piece.hex(), retries=0)
                    if not reply.startswith('ERR'):
                        break
                    accepted = bulk_accepted(addr, label)
                    if accepted == pos + len(piece):
                        # Only the reply was lost; never append the chunk again.
                        break
                    if accepted == pos:
                        if delivery + 1 == BULK_RETRIES:
                            raise SystemExit(f'{label}: chunk at {pos} B did not '
                                             'land; stop before file write')
                        set_bulk_cursor(addr + pos, pos)
                        retried_chunks += 1
                        continue
                    if offset <= accepted < pos + len(piece):
                        # An earlier command silently failed or this one was
                        # partial; replay only this bounded batch.
                        restart_batch = True
                        break
                    raise SystemExit(f'{label}: bulkload accepted {accepted} B '
                                     f'outside chunk {pos}..{pos + len(piece)}; '
                                     'stop before file write')
                if restart_batch:
                    break
            accepted = bulk_accepted(addr, label)
            if not offset <= accepted <= end:
                raise SystemExit(f'{label}: bulkload accepted {accepted} B '
                                 f'outside batch {offset}..{end}; '
                                 'stop before file write')
            if accepted == end:
                offset = end
                elapsed = max(time.time() - started, 1e-6)
                print(f'\r  {label} {offset}/{len(blob)} B  '
                      f'{offset/elapsed/1024:.1f} KiB/s ',
                      end='', flush=True, file=sys.stderr)
                break
        else:
            raise SystemExit(f'{label}: batch at {offset} B did not land after '
                             f'{BULK_RETRIES} attempts; stop before file write')
    return restarted, retried_chunks


def upload_request(header: str, data: bytes = b'') -> str:
    """Use the daemon's binary socket path; no hex or borrowed echo handler."""
    with socket.socket(socket.AF_UNIX) as conn:
        conn.settimeout(5)
        conn.connect(SOCK)
        conn.sendall(header.encode('ascii') + b'\n' + data)
        answer = bytearray()
        while len(answer) < 512:
            block = conn.recv(512 - len(answer))
            if not block:
                break
            answer.extend(block)
            if b'\n' in block:
                break
    return answer.decode('ascii', errors='replace').strip()


def upload_caps():
    """Return the current worker's capability and last accepted tuple."""
    answer = upload_request('UCAPS')
    if answer in ('ERR unknown', 'ERR upload unsupported'):
        return None
    match = re.fullmatch(r'OKUC (\d+) (\d+) (0x[0-9A-Fa-f]{8}) '
                         r'(\d+) (0x[0-9A-Fa-f]{8})', answer)
    if not match:
        raise RuntimeError(f'upload capability uncertain: {answer!r}')
    capacity, sequence, address, length, checksum = match.groups()
    capacity = int(capacity)
    if not 4 <= capacity <= UPLOAD_CHUNK or capacity % 4:
        raise RuntimeError(f'upload capacity is invalid: {capacity}')
    return capacity, (int(sequence), int(address, 16), int(length),
                      int(checksum, 16))


def stage_fast(addr, blob, label, started):
    """Send absolute, CRC-checked binary chunks to an UP01 worker.

    A failed reply has an uncertain outcome. Query the worker's last accepted
    tuple after it has returned to its command loop, then either advance or
    resend the same absolute chunk. The full staging readback in `put()` is
    still the final proof before any card file is opened.
    """
    if addr & 3 or len(blob) & 3:
        raise ValueError('fast upload requires aligned address and length')
    try:
        caps = upload_caps()
    except (OSError, RuntimeError) as exc:
        raise SystemExit(f'{label}: cannot check binary upload support ({exc}); '
                         'stop before file write') from exc
    if caps is None:
        return False
    chunk_size = caps[0]
    offset = 0
    while offset < len(blob):
        piece = blob[offset:offset + chunk_size]
        destination = addr + offset
        checksum = zlib.crc32(piece)
        accepted = False
        for attempt in range(UPLOAD_RETRIES):
            try:
                response = upload_request(
                    f'PUT 0x{destination:08X} {len(piece)} 0x{checksum:08X}', piece)
            except OSError as exc:
                response = f'ERR upload uncertain socket {exc}'
            match = re.fullmatch(r'OKU (\d+) (0x[0-9A-Fa-f]{8}) '
                                 r'(\d+) (0x[0-9A-Fa-f]{8})', response)
            if match:
                _sequence, got_addr, got_len, got_crc = match.groups()
                if (int(got_addr, 16), int(got_len), int(got_crc, 16)) != \
                        (destination, len(piece), checksum):
                    raise SystemExit(f'{label}: upload acknowledgment mismatch; '
                                     'stop before file write')
                accepted = True
                break
            if response.startswith('ERR upload rejected'):
                raise SystemExit(f'{label}: {response}; stop before file write')
            # A successful command can lose only its reply. The worker waits
            # at most 300 ms for EP82; give it time to return before probing.
            time.sleep(0.35)
            for _ in range(UPLOAD_RETRIES):
                try:
                    checked = upload_caps()
                except (OSError, RuntimeError):
                    time.sleep(0.35)
                    continue
                if checked is None:
                    raise SystemExit(f'{label}: upload worker disappeared; '
                                     'stop before file write')
                last = checked[1][1:]
                if last == (destination, len(piece), checksum):
                    accepted = True
                break
            else:
                raise SystemExit(f'{label}: cannot confirm upload at {offset} B; '
                                 'stop before file write')
            if accepted:
                break
        if not accepted:
            raise SystemExit(f'{label}: upload at {offset} B did not land after '
                             f'{UPLOAD_RETRIES} attempts; stop before file write')
        offset += len(piece)
        elapsed = max(time.time() - started, 1e-6)
        print(f'\r  {label} {offset}/{len(blob)} B  '
              f'{offset/elapsed/1024/1024:.1f} MiB/s ',
              end='', flush=True, file=sys.stderr)
    return True


def put(addr, blob, label, passes=6, fast=False):
    """Write, verify, rewrite what did not land, until nothing is left.

    Bytes travel in the command line, about 240 at a time, because `mem set`
    moves four per round trip and that put 14 KB of AutoRun at 448 seconds --
    against 22 to read the same file back, `mem get` answering sixteen words at
    once.  The transport was never the problem.

    Verification stays, and matters more here, not less: `mem set` drops writes
    and whole commands go missing. The camera-side byte counter checkpoints
    each batch, so a missing chunk resends that small batch at its original
    address instead of shifting the rest of the file. The final full readback
    still repairs any residual byte mismatch one word at a time.
    """
    _claims()
    if len(blob) <= 256:
        return put_slow(addr, blob, label, passes)

    w = list(struct.unpack(f'<{len(blob)//4}I', blob))
    t0 = time.time()
    # MEM1: one transfer per 16 MiB, then the whole thing read back and
    # compared -- the same proof the passes below give, at link speed.  Only
    # where DMA reaches: the cave (0xC07xxxxx) is not, and goes the old way.
    # A refusal arms nothing, so falling back is safe; anything uncertain stops.
    if 0x44000000 <= addr < 0x80000000 and mem_caps():
        try:
            mem_write(addr, blob, label=label)
        except RuntimeError as exc:
            if 'ERR mem refused' not in str(exc):
                raise SystemExit(f'{label}: {exc}; stop before file write')
            print(f'  {label} MEM1 refused ({exc}); using the checked path',
                  file=sys.stderr)
        else:
            dt = time.time() - t0
            print(f'\r  {label} {len(blob)} bytes in {dt:.2f}s '
                  f'({len(blob)/dt/1e6:.1f} MB/s), MEM1, read back whole          ',
                  file=sys.stderr)
            return w
    used_fast = fast and stage_fast(addr, blob, label, t0)
    restarted = retried_chunks = 0
    if not used_fast:
        ensure_bulk()
        orig = mem_get(ECHO_SLOT)
        if not orig or orig[0] not in (ECHO_ORIG, BULK):
            raise SystemExit(f'echo handler is {orig}, not free to borrow')
        try:
            set_echo_handler(BULK)
            restarted, retried_chunks = stage_bulk(addr, blob, label, t0)
        finally:
            set_echo_handler(ECHO_ORIG)

    repaired = 0
    for attempt in range(passes):
        # read_bulk, not read_back: the same bytes at a hundred times the rate.
        # `mem get` answers thirty-four characters for every four bytes and gets
        # 22 KiB/s, which used to be half the cost of loading anything. dumpraw
        # is loaded by put_slow, which still verifies the slow way -- it has to,
        # since it is what puts dumpraw there.
        got = list(struct.unpack(f'<{len(w)}I', read_bulk(addr, len(w) * 4,
                                                          'verify')))
        bad = [i for i in range(len(w)) if i >= len(got) or got[i] != w[i]]
        if not bad:
            dt = time.time() - t0
            note = (f', binary upload, {repaired} words repaired' if used_fast else
                    f', {restarted} batches resent, {retried_chunks} chunks '
                    f'retried, {repaired} words repaired')
            print(f'\r  {label} {len(blob)} bytes in {dt:.1f}s '
                  f'({len(blob)/dt/1024:.1f} KiB/s){note}          ', file=sys.stderr)
            return w
        repaired += len(bad)
        print(f'\r  {label} pass {attempt+1}: {len(bad)} words missing, repairing ',
              end='', flush=True, file=sys.stderr)
        for i in bad:
            mem_set(addr + i * 4, w[i])
    raise SystemExit(f'{label}: still short after {passes} passes')


# The shell's own `fl del <path>` (fl table 0xC0BB24CC -> 0xC03E6CF0): a file
# object on the current drive, then 0xC03665A8(obj, path).  Its help text says
# "currently not working"; on 2026-10-06 it deleted a file and refused a
# missing one.  The same table holds delall / delCinemaDng / deloneimg --
# never those.  Mode 7 does not truncate, so a smaller file written over a
# larger one keeps the old tail: delete first.
_DELETABLE = re.compile(r'^\\[A-Za-z0-9_]+(\\[A-Za-z0-9_][A-Za-z0-9_.]*)+$')


def delete_file(remote):
    """Delete ONE file by absolute path.  True deleted, False not there or
    refused.  Refuses anything that is not a plain \\DIR\\...\\NAME.EXT path,
    and anything under \\DCIM or \\CINEMA."""
    if not _DELETABLE.match(remote) or '.' not in remote.rsplit('\\', 1)[1]:
        raise ValueError(f'refusing to delete {remote!r}: not a plain file path')
    if remote.upper().startswith(('\\DCIM', '\\CINEMA')):
        raise ValueError(f'refusing to delete {remote!r}: camera media')
    out = sh(f'fl del {remote}', retries=0)
    if 'OK' in out.split():
        return True
    if "can't delete" in out:
        return False
    raise RuntimeError(f'fl del {remote}: unexpected reply {out!r}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('local')
    ap.add_argument('remote', help=r'camera path, e.g. \TEST.TXT')
    ap.add_argument('--mode', type=lambda s: int(s, 0), default=7,
                    help='7 overwrites or creates, but does not truncate (default); '
                         '0x402 fails if it exists')
    ap.add_argument('--buf', type=lambda s: int(s, 0), default=None)
    a = ap.parse_args()
    _claims()

    data = pathlib.Path(a.local).read_bytes()
    padded = data + b'\0' * (-len(data) % 4)
    path = a.remote.encode() + b'\0'
    if len(path) > 0xD0:
        raise SystemExit('path does not fit the parameter block')

    print(f'  local   {a.local}  {len(data)} bytes')
    print(f'  remote  {a.remote}  mode 0x{a.mode:X}')

    slot = mem_get(ECHO_SLOT)
    if not slot:
        raise SystemExit('could not read the echo handler slot')
    if slot[0] != ECHO_ORIG and slot[0] != CODE:
        raise SystemExit(f'echo handler is 0x{slot[0]:08X}, expected 0x{ECHO_ORIG:08X} '
                         '— something else has borrowed it')

    buf = a.buf or staging_area()
    fobj = buf + len(padded)
    print(f'  buffer  0x{buf:08X}, file object 0x{fobj:08X}')
    check_fits(buf, len(padded) + FOBJ_ROOM, 'staging')
    prove(buf, len(padded) + FOBJ_ROOM)
    print('  proof   region takes writes and still holds them a second later')

    put(buf, padded, 'stage ', fast=True)

    for off, val in ((P_STATUS, 0), (P_OPENR, 0), (P_WRITER, 0), (P_DATA, buf),
                     (P_LEN, len(data)), (P_FOBJ, fobj), (P_MODE, a.mode)):
        mem_set(P + off, val)
    pw = path + b'\0' * (-len(path) % 4)
    # The path is the only variable-length thing in the block, and nothing used
    # to bound it: a long one ran off the end into whatever came next.  That is
    # how a forty-eight byte buffer declared as one word froze the camera on the
    # gyro side; here the block's size is known, so say so.
    if P_PATH + len(pw) > _LAYOUT['P'][1]:
        raise SystemExit(
            f'the path is {len(path)} bytes and the parameter block holds '
            f'{_LAYOUT["P"][1] - P_PATH} past its header -- widen the claim')
    for i, word in enumerate(struct.unpack(f'<{len(pw)//4}I', pw)):
        mem_set(P + P_PATH + i * 4, word)

    code = assemble(HERE / 'asm' / 'putfile.S', [f'P=0x{P:08X}'])
    if CODE + len(code) > CODE_END:
        raise SystemExit('assembly source does not fit the code region')
    put(CODE, code, 'code  ')

    try:
        set_echo_handler(CODE)
        reply = sh('echo')
    finally:
        set_echo_handler(ECHO_ORIG)
        print(f'  restored echo -> 0x{ECHO_ORIG:08X}')

    print(f'  reply   {reply.strip()}')
    if reply.lstrip().startswith('ERR'):
        raise SystemExit('putfile echo reply failed; write outcome is unknown')
    st = mem_get(P + P_STATUS, 3)
    status = st[0] if st else 0
    print(f'  status  {status} — {STATUS.get(status, "unknown")}')
    if len(st) >= 3:
        print(f'  open    {st[1]}     write  {st[2]}   (both are flags, not counts)')
    if status != 2:
        return 1

    name = a.remote.lstrip('\\').split('\\')[-1]
    for line in sh('dir').splitlines():
        if name.lower() in line.lower():
            print(f'  dir     {line.strip()}')
            if str(len(data)) not in line:
                print(f'  WARNING directory size does not read {len(data)}')
            break
    else:
        print(f'  WARNING {name} did not appear in dir')
    return 0


if __name__ == '__main__':
    sys.exit(main())
