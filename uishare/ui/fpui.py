"""FPUI: a sup's UI additions, as a list of mechanical edits to the native UI.

A sup's builder describes what it adds (uishare/ui: add_row, add_option, ...);
that becomes an FPUI block in the sup's fpSup.BIN; on the camera
uishare/ui_apply.c carries it out at boot, in the sup's entry. Several sups'
blocks compose in any load order: nothing in a block names a coordinate that
another sup could also take -- object IDs, string offsets and counters are
handed out on the camera.

This module is the format and the REFERENCE APPLIER. ui_apply.c must do
exactly what apply() here does; the tests run both on the same inputs.

Coordinates. Every position in an op is a STOCK position: a byte offset into
the page as the firmware image has it. A page copy remembers what was
inserted where (its delta table), so a stock position maps to the current
copy as

    current(p) = p + sum(n for each insertion (q, n) with q <= p)

and an insertion at q lands after every earlier insertion at q. Edits by
several sups at one stock position therefore stack in load order.

Container, little-endian words (the page data inside is big-endian, as the
firmware's records are):

  +00 "FPUI"  +04 version 1  +08 total bytes  +0C FNV-1a of bytes [0x20, total)
  +10 string count  +14 op words  +18 fragment bytes  +1C 0
  +20 strings: {u32 stock_at, u32 n, n bytes, NUL, zero pad to 4} each
      stock_at = where the stock pool holds it (uis_intern_hinted), or
      0xFFFFFFFF (not in the stock pool), or 0xFFFFFFFE (a name, not interned)
  then the op words, then the fragment bytes (zero padded to 4).

Ops, one header word (opcode << 24 | argument word count) then the arguments:

  PAGE    name#, stock entry offset, stock length, stock next id,
          local ids, insert bytes, inserts
  GUARD   stock pos, expected BE word        (page must hold it, else nothing)
  INSERT  stock pos, fragment offset, length, then 2 words per relocation:
          {kind << 24 | arg, offset in the inserted bytes}
  ADD32   stock pos, delta                   (BE word += delta)
  ADDF    stock pos, delta                   (BE float holding an integer += delta)
  ALLOC   name#, base, step                  (slot k = base + step * counter[name]++)
  DONE                                       (the page is complete; switched in at the end)
  HOOK_FV site, stock word, capacity, handler fragment offset, length, entry
          offset, cave bump word, arena end, veneer word, handler's table word
          offset -- find the shared file-redirect table, or install it
          (uishare/fv_handler.S at the site through a cave veneer)
  FILE    stock address, stock size, growth     (a new copy of the file as it is now)
  CSV_ADD template#                          (append a row; slot = its 0-based index)
  CSV_CELL row (0 first, 1 last before this block's rows), column, template#
  FILE_SET fragment offset, length            (the whole file; only if nobody else did)
  FILE_DONE
  EXPECT  slot, value                        (else the block is refused)
  SETSTR_SLOT slot, base, n, string#, n stock positions
                                             (page word at position[slot - base] = string)

Templates expand {N} = the row count after the row is added (the new row's
number), {P} = N - 1, {Q} = N - 2. Nothing takes effect until the whole block
succeeded: pages are switched and files entered in the table at the end.

Relocation kinds: LOCAL_ID (BE word = first id + arg), STRING (BE word =
offset of string #arg), SLOT_F32 / SLOT_U32 (BE float / word = slot #arg).
"""
import struct

MAGIC, VERSION = b'FPUI', 1
NOT_STOCK, NAME = 0xFFFFFFFF, 0xFFFFFFFE
OP_PAGE, OP_GUARD, OP_INSERT, OP_ADD32, OP_ADDF, OP_ALLOC, OP_DONE = 1, 2, 3, 4, 5, 6, 7
OP_HOOK_FV, OP_FILE, OP_CSV_ADD, OP_CSV_CELL, OP_FILE_SET, OP_FILE_DONE = 8, 9, 10, 11, 12, 13
OP_EXPECT, OP_SETSTR_SLOT = 14, 15
ROW_FIRST, ROW_LAST_BEFORE = 0, 1
R_LOCAL_ID, R_STRING, R_SLOT_F32, R_SLOT_U32 = 1, 2, 3, 4
MAX_COUNTERS = 8            # per page copy (ui_apply.h UIA_COUNTERS)


class FpuiError(ValueError):
    pass


def check(cond, msg):
    if not cond:
        raise FpuiError(msg)


def fnv(data, h=2166136261):
    for b in data:
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    return h


def name_hash(name):
    return fnv(name.encode()) or 1


# ---- integer-valued big-endian floats (the camera build has no float math) --
def int_to_f32(v):
    check(-(1 << 24) < v < (1 << 24), 'float slot out of exact range')
    return struct.unpack('>I', struct.pack('>f', float(v)))[0]


def f32_to_int(word):
    f = struct.unpack('>f', struct.pack('>I', word))[0]
    check(f == int(f) and abs(f) < (1 << 24), 'not an integer-valued float: %r' % f)
    return int(f)


# ---- building a block --------------------------------------------------------
class Block:
    """Strings, ops and fragment bytes of one sup's FPUI block."""

    def __init__(self):
        self.strings = []           # (text, stock_at)
        self.ops = []               # (opcode, [args])
        self.frag = bytearray()

    def string(self, text, stock_at):
        key = (text, stock_at)
        if key not in self.strings:
            self.strings.append(key)
        return self.strings.index(key)

    def op(self, code, *args):
        self.ops.append((code, [a & 0xFFFFFFFF for a in args]))

    def fragment(self, data):
        at = len(self.frag)
        self.frag.extend(data)
        return at

    def encode(self):
        body = bytearray()
        for text, at in self.strings:
            b = text.encode()
            check(b and b'\0' not in b and len(b) < 64, 'bad string %r' % text)
            body += struct.pack('<II', at, len(b)) + b + b'\0'
            body += b'\0' * (-len(body) % 4)
        words = []
        for code, args in self.ops:
            check(len(args) < (1 << 24), 'op too long')
            words.append(code << 24 | len(args))
            words.extend(args)
        body += struct.pack('<%dI' % len(words), *words)
        frag = bytes(self.frag) + b'\0' * (-len(self.frag) % 4)
        body += frag
        total = 0x20 + len(body)
        head = struct.pack('<4sIIIIIII', MAGIC, VERSION, total, fnv(body),
                           len(self.strings), len(words), len(self.frag), 0)
        return head + bytes(body)


def decode(blob):
    """(strings, ops, frag) of an encoded block, every bound checked."""
    check(len(blob) >= 0x20, 'short block')
    magic, version, total, h, ns, nw, nf, zero = struct.unpack_from('<4sIIIIIII', blob)
    check(magic == MAGIC and version == VERSION and zero == 0, 'not an FPUI v1 block')
    check(0x20 <= total <= len(blob) and fnv(blob[0x20:total]) == h, 'block damaged')
    cur, strings = 0x20, []
    for _ in range(ns):
        check(cur + 8 <= total, 'truncated string')
        at, n = struct.unpack_from('<II', blob, cur)
        check(0 < n < 64 and cur + 8 + n + 1 <= total and blob[cur + 8 + n] == 0, 'bad string')
        s = blob[cur + 8:cur + 8 + n]
        check(0 not in s, 'NUL inside a string')
        strings.append((s.decode(), at))
        cur += (8 + n + 1 + 3) & ~3
    check(cur + 4 * nw <= total, 'truncated ops')
    words = list(struct.unpack_from('<%dI' % nw, blob, cur))
    cur += 4 * nw
    check(cur + nf <= total, 'truncated fragment')
    frag = bytes(blob[cur:cur + nf])
    ops, i = [], 0
    while i < len(words):
        code, n = words[i] >> 24, words[i] & 0xFFFFFF
        check(i + 1 + n <= len(words), 'op runs past the op words')
        ops.append((code, words[i + 1:i + 1 + n]))
        i += 1 + n
    return strings, ops, frag


# ---- the reference applier ---------------------------------------------------
class Pool:
    """The shared string pool as uishare/ui_pool.c keeps it (offsets only)."""

    def __init__(self, stock):
        self.stock = bytes(stock)
        self.data = bytearray(stock)

    def intern(self, text, stock_at):
        s = text.encode()
        if stock_at not in (NOT_STOCK, NAME) and stock_at + len(s) < len(self.stock) and \
                self.data[stock_at:stock_at + len(s) + 1] == s + b'\0':
            return stock_at
        start = len(self.stock) if stock_at == NOT_STOCK else 0
        i = self.data.find(s + b'\0', start)
        if i >= 0:
            return i
        self.data.extend(b'\0' * (-len(self.data) % 4))
        at = len(self.data)
        self.data.extend(s + b'\0')
        return at

    def text(self, offset):
        return self.data[offset:self.data.index(b'\0', offset)].decode()


class PageCopy:
    """A page as uishare keeps it: bytes plus what was inserted where."""

    def __init__(self, data, stock_len, next_id, deltas=(), counters=None):
        self.data = bytearray(data)
        self.stock_len = stock_len
        self.next_id = next_id
        self.deltas = list(deltas)              # (stock pos, bytes), in insertion order
        self.counters = dict(counters or {})    # name hash -> count

    def current(self, pos):
        check(0 <= pos <= self.stock_len, 'stock position outside the page')
        return pos + sum(n for q, n in self.deltas if q <= pos)

    def word(self, pos):
        at = self.current(pos)
        return struct.unpack_from('>I', self.data, at)[0]

    def put(self, pos, value):
        struct.pack_into('>I', self.data, self.current(pos), value & 0xFFFFFFFF)


class Files:
    """The shared file-redirect table and the site it hangs on."""

    def __init__(self, read):
        self.read = read                # (address, size) -> stock bytes
        self.table = {}                 # stock address -> composed bytes
        self.hook = None                # None (stock site), 'ours', or 'foreign'
        self.capacity = 0
        self.veneers = 0


def csv_rows(data):
    lines = [l for l in data.replace(b'\r', b'').split(b'\n') if l]
    return max(0, len(lines) - 1)


def expand(tpl, n):
    out = tpl
    for k, v in (('{N}', n), ('{P}', n - 1), ('{Q}', n - 2)):
        out = out.replace(k, str(v))
    check('{' not in out, 'bad template ' + tpl)
    return out.encode()


def csv_add(data, tpl):
    rows = csv_rows(data)
    nl = b'\r\n' if b'\r\n' in data else b'\n'
    if data and not data.endswith(b'\n'):
        data += nl
    return data + expand(tpl, rows + 1) + nl, rows


def csv_cell(data, row, col, tpl):
    cell = expand(tpl, csv_rows(data))
    line, start, i = 0, True, 0
    while i < len(data):
        c = data[i]
        if c == 0x0A:
            start = True
        elif c != 0x0D and start:
            if line == row:
                break
            line, start = line + 1, False
        i += 1
    check(i < len(data), 'no such row')
    for _ in range(col):
        while i < len(data) and data[i] not in b',\r\n':
            i += 1
        check(i < len(data) and data[i] == 0x2C, 'no such column')
        i += 1
    j = i
    while j < len(data) and data[j] not in b',\r\n':
        j += 1
    return data[:i] + cell + data[j:]


def apply(blob, pages, pool, files=None):
    """Carry out one block. pages: {entry name: PageCopy}; a page this block
    touches is replaced by a new PageCopy only if every op succeeded (the
    camera switches the entry at DONE). Returns {entry name: first local id}."""
    strings, ops, frag = decode(blob)
    work, page, name, first_id, slots, result = None, None, None, 0, [], {}
    done_pages, done_files, fwork = {}, {}, None
    for code, a in ops:
        if code == OP_PAGE:
            check(work is None and len(a) == 7, 'bad PAGE')
            name = strings[a[0]][0]
            check(strings[a[0]][1] == NAME, 'PAGE name must not be interned')
            old = pages[name]
            check(old.stock_len == a[2], 'page is not the stock page this block was built for')
            work = PageCopy(old.data, old.stock_len, max(old.next_id, a[3]), old.deltas, old.counters)
            first_id = work.next_id
            work.next_id += a[4]
            check(work.next_id <= 0x10000, 'object ids run out')
        elif code == OP_GUARD:
            check(work is not None and len(a) == 2, 'bad GUARD')
            if work.word(a[0]) != a[1]:
                raise FpuiError('guard at stock +0x%X: 0x%08X, not 0x%08X'
                                % (a[0], work.word(a[0]), a[1]))
        elif code == OP_INSERT:
            check(work is not None and len(a) >= 3 and (len(a) - 3) % 2 == 0, 'bad INSERT')
            pos, off, n = a[0], a[1], a[2]
            check(off + n <= len(frag) and n, 'INSERT outside the fragment')
            data = bytearray(frag[off:off + n])
            for i in range(3, len(a), 2):
                kind, arg, at = a[i] >> 24, a[i] & 0xFFFFFF, a[i + 1]
                check(at + 4 <= n, 'relocation outside the inserted bytes')
                if kind == R_LOCAL_ID:
                    v = first_id + arg
                elif kind == R_STRING:
                    v = pool.intern(*strings[arg])
                elif kind == R_SLOT_F32:
                    v = int_to_f32(slots[arg])
                elif kind == R_SLOT_U32:
                    v = slots[arg]
                else:
                    raise FpuiError('unknown relocation %d' % kind)
                struct.pack_into('>I', data, at, v)
            at = work.current(pos)
            work.data[at:at] = data
            work.deltas.append((pos, n))
        elif code == OP_ADD32:
            check(work is not None and len(a) == 2, 'bad ADD32')
            work.put(a[0], work.word(a[0]) + a[1])
        elif code == OP_ADDF:
            check(work is not None and len(a) == 2, 'bad ADDF')
            delta = a[1] - (1 << 32) if a[1] & 0x80000000 else a[1]
            work.put(a[0], int_to_f32(f32_to_int(work.word(a[0])) + delta))
        elif code == OP_ALLOC:
            check(work is not None and len(a) == 3, 'bad ALLOC')
            key = name_hash(strings[a[0]][0])
            check(key in work.counters or len(work.counters) < MAX_COUNTERS, 'counters full')
            k = work.counters.get(key, 0)
            work.counters[key] = k + 1
            step = a[2] - (1 << 32) if a[2] & 0x80000000 else a[2]
            slots.append((a[1] + step * k) & 0xFFFFFFFF)
        elif code == OP_DONE:
            check(work is not None and not a, 'bad DONE')
            done_pages[name] = work
            result[name] = first_id
            work = None
        elif code == OP_HOOK_FV:
            check(files is not None and len(a) == 10, 'bad HOOK_FV')
            if files.hook == 'foreign':
                raise FpuiError('the redirect site holds someone else\'s code')
            if files.hook is None:
                files.hook, files.capacity, files.veneers = 'ours', a[2], files.veneers + 1
        elif code == OP_FILE:
            check(files is not None and files.hook == 'ours' and fwork is None and work is None
                  and len(a) == 3 and a[0] not in done_files, 'bad FILE')
            cur = files.table.get(a[0])
            fwork = {'stock': a[0], 'data': cur if cur is not None else files.read(a[0], a[1]),
                     'cap': (len(cur) if cur is not None else a[1]) + a[2], 'before': None}
        elif code == OP_CSV_ADD:
            check(fwork is not None and len(a) == 1 and len(slots) < 8, 'bad CSV_ADD')
            data, rows = csv_add(fwork['data'], strings[a[0]][0])
            check(len(data) <= fwork['cap'], 'file grew past its room')
            if fwork['before'] is None:
                fwork['before'] = rows
            fwork['data'] = data
            slots.append(rows)
        elif code == OP_CSV_CELL:
            check(fwork is not None and fwork['before'] is not None and len(a) == 3, 'bad CSV_CELL')
            row = 1 if a[0] == ROW_FIRST else fwork['before'] if a[0] == ROW_LAST_BEFORE else 0
            check(row, 'bad CSV_CELL row')
            fwork['data'] = csv_cell(fwork['data'], row, a[1], strings[a[2]][0])
            check(len(fwork['data']) <= fwork['cap'], 'file grew past its room')
        elif code == OP_FILE_SET:
            check(fwork is not None and len(a) == 2, 'bad FILE_SET')
            if fwork['stock'] in files.table:
                raise FpuiError('another sup already replaced this file')
            fwork['data'] = frag[a[0]:a[0] + a[1]]
        elif code == OP_FILE_DONE:
            check(fwork is not None and not a, 'bad FILE_DONE')
            done_files[fwork['stock']] = fwork['data']
            fwork = None
        elif code == OP_EXPECT:
            check(len(a) == 2 and a[0] < len(slots), 'bad EXPECT')
            if slots[a[0]] != a[1]:
                raise FpuiError('slot %d is %d, the block needs %d' % (a[0], slots[a[0]], a[1]))
        elif code == OP_SETSTR_SLOT:
            check(work is not None and len(a) >= 4 and a[0] < len(slots), 'bad SETSTR_SLOT')
            idx = slots[a[0]] - a[1]
            check(0 <= idx < a[2] and idx < len(a) - 4, 'slot outside the positions')
            work.put(a[4 + idx], pool.intern(*strings[a[3]]))
        else:
            raise FpuiError('unknown op %d' % code)
    check(work is None and fwork is None, 'block ends inside a PAGE or FILE')
    if files is not None:
        fresh = [s for s in done_files if s not in files.table]
        check(len(files.table) + len(fresh) <= files.capacity, 'redirect table full')
        files.table.update(done_files)
    pages.update(done_pages)
    result['slots'] = slots
    return result
