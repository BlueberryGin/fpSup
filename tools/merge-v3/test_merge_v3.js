// node tools/merge-v3/test_merge_v3.js
// Runs the page's own compose block (no DOM) against the built index.html and
// checks it against the frozen release folders on disk.
const fs = require('fs'), path = require('path'), crypto = require('crypto'), zlib = require('zlib');
// MERGE_INDEX / MERGE_RELEASES: a page built from another releases folder
// (test_data_files.sh builds one with a synthetic res-custom).
const HERE = __dirname;
const RELEASES = process.env.MERGE_RELEASES || path.join(HERE, '..', '..', 'releases');
const page = fs.readFileSync(process.env.MERGE_INDEX || path.join(HERE, 'index.html'), 'utf8');
const grab = id => page.match(new RegExp(`<script id="${id}"[^>]*>([\\s\\S]*?)</script>`))[1];

global.atob = s => Buffer.from(s, 'base64').toString('binary');
global.TextEncoder = TextEncoder;
global.document = {getElementById: () => ({textContent: grab('cat')})};
const api = new Function(grab('compose') +
  '\nreturn {CAT, DATA_OFF, plan, compose, checks, readme, zipFor, cardName, sha256hex};')();
const {CAT, DATA_OFF, plan, compose, checks, zipFor, cardName, sha256hex} = api;

let fails = 0;
const ok = (c, what) => { console.log((c ? 'ok   ' : 'FAIL ') + what); if (!c) fails++; };
const sha = b => crypto.createHash('sha256').update(b).digest('hex');

// Unzip a stored zip (what zipStore writes) -> {name: Buffer}
function unzip(u8){
  const b = Buffer.from(u8), out = {};
  let o = 0;
  while (b.readUInt32LE(o) === 0x04034B50){
    const n = b.readUInt16LE(o + 26), x = b.readUInt16LE(o + 28), len = b.readUInt32LE(o + 18);
    const crc = b.readUInt32LE(o + 14);
    const name = b.slice(o + 30, o + 30 + n).toString();
    const data = b.slice(o + 30 + n + x, o + 30 + n + x + len);
    if (zlib.crc32 && zlib.crc32(data) !== crc) throw new Error('crc ' + name);
    out[name] = data; o += 30 + n + x + len;
  }
  return out;
}

// 0a. the page's UI script parses (the compose block runs above; this one is DOM-only)
try { new Function(grab('ui')); ok(true, 'ui script parses'); } catch (e) { ok(false, 'ui script parses: ' + e.message); }

// 0. the page's sha256 is SHA-256
ok(sha256hex(new Uint8Array(0)) === sha(Buffer.alloc(0)) &&
   sha256hex(new Uint8Array(1000).fill(7)) === sha(Buffer.alloc(1000, 7)), 'sha256hex matches node');

// 1. every product alone == its release folder, byte for byte
for (const p of CAT.products){
  const z = unzip(zipFor([p.id])), root = cardName([p.id]) + '/';
  const rel = path.join(RELEASES, p.release);
  const want = ['AutoRun.txt', 'fpSup/LOADER.BIN', ...[0,1,2,3,4].map(i => `fpSup/UI/${i}.BIN`), 'fpSup/' + p.file,
                ...(p.data_files || []).map(d => d.path)];
  const same = want.every(f => z[root + f] && z[root + f].equals(fs.readFileSync(path.join(rel, f))));
  const extra = Object.keys(z).filter(k => !want.includes(k.slice(root.length)) && k !== root + 'README.txt');
  ok(same && !extra.length && checks([p.id]).every(r => r.ok), `${p.id} alone = ${p.release}`);
}

// 2. everything at once: clashing ones parked in OFF, the rest run, checks pass
const all = CAT.products.map(p => p.id);
const pl = plan(all), {files} = compose(all);
const byFile = Object.fromEntries(CAT.products.map(p => [p.id, p.file]));
ok(pl.on.every(a => pl.on.every(b => a === b || !CAT.products.find(p => p.id === a).excl.includes(b))),
   'no two running sups clash');
ok(pl.off.every(id => files.some(f => f.name === 'fpSup/' + byFile[id].replace(/\.BIN$/, '.OFF'))), 'parked sups are fpSup/NAME.OFF');
ok(checks(all).every(r => r.ok), `all ${all.length} ticked: checks pass (off: ${pl.off.join(',') || '-'})`);

// 3. a clashing pair (made up from the first two sups when the catalogue has
//    none): first ticked runs, the other is NAME.OFF in the same folder
{
  const [A, B] = CAT.products, saved = A.excl;
  if (!A.excl.includes(B.id)) A.excl = [...A.excl, B.id];
  ok(plan([A.id, B.id]).on.join() === A.id && plan([B.id, A.id]).on.join() === B.id,
     'first ticked of a clashing pair runs');
  const z = unzip(zipFor([A.id, B.id])), root = cardName([A.id, B.id]) + '/';
  const off = B.file.replace(/\.BIN$/, '.OFF');
  ok(z[root + 'fpSup/' + A.file] && z[root + 'fpSup/' + off] && !z[root + 'fpSup/' + B.file],
     `zip: ${A.file} runs, ${B.file} is fpSup/${off}`);
  ok(Object.keys(z).every(k => !k.includes('/OFF/')), 'no OFF folder');
  ok(/\.OFF/.test(z[root + 'README.txt'].toString()), 'README says how to switch');
  ok(checks([A.id, B.id]).every(r => r.ok), 'a clash is not a failed check');
  A.excl = saved;
}

// 4. a corrupted byte is caught by the self-check (mutation)
const p0 = CAT.products[0], keep = p0.data;
const bytes = Buffer.from(keep, 'base64'); bytes[bytes.length - 1] ^= 1; p0.data = bytes.toString('base64');
ok(checks([p0.id]).some(r => !r.ok), 'flipped bit in a sup fails the self-check');
p0.data = keep;

// 5. same selection -> same zip bytes
ok(sha(Buffer.from(zipFor(all))) === sha(Buffer.from(zipFor(all))), 'zip is reproducible');

// 5b. data files: all by default, untick one -> it leaves the card, untick all -> refused
for (const p of CAT.products.filter(p => (p.data_files || []).length)){
  const root = cardName([p.id]) + '/', n = p.data_files.length;
  const has = z => p.data_files.filter(d => z[root + d.path]).length;
  ok(has(unzip(zipFor([p.id]))) === n, `${p.id}: all ${n} data files by default`);
  DATA_OFF[p.id] = [p.data_files[0].path];
  const z = unzip(zipFor([p.id]));
  ok(has(z) === n - 1 && !z[root + p.data_files[0].path], `${p.id}: unticked ${p.data_files[0].label} is left out`);
  DATA_OFF[p.id] = p.data_files.map(d => d.path);
  ok(checks([p.id]).some(r => !r.ok), `${p.id}: no data file ticked is refused`);
  delete DATA_OFF[p.id];
}

// 6. the guide link, when there is one, points at a page that exists
for (const p of [...CAT.products, ...(CAT.pending || [])]){
  if (p.guide) ok(fs.existsSync(path.join(HERE, p.guide)), `${p.id} guide ${p.guide} exists`);
  else console.log(`todo ${p.id}: no guide page yet`);
}

// 6b. everyone thanked on a product's guide page is thanked on its tile
for (const p of [...CAT.products, ...(CAT.pending || [])]){
  if (!p.guide) continue;
  const g = fs.readFileSync(path.join(HERE, p.guide), 'utf8');
  // every "Thank you, ..." box on the page, not just the first
  const names = [...g.matchAll(/credit-h"><span class="en">Thank you, ([^<]+)</g)]
    .flatMap(m => m[1].split(/,\s*|\s+and\s+/)).map(x => x.trim()).filter(Boolean);
  if (!names.length) continue;
  ok(names.every(n => p.credits.some(c => c.startsWith(n))), `${p.id}: tile thanks ${names.join(', ')}`);
}

// 6c. the AutoRun speed-up is on every card, so its thanks is on the page
ok(CAT.shared.some(f => f.path === 'AutoRun.txt' && Buffer.from(f.data, 'base64').toString().startsWith('mem set 0xC04215F4 0xE320F000'))
   && /Andy_hal9000/.test(grab('ui')), 'AutoRun has the no-delay patch, page thanks Andy_hal9000');

// 6d. the zip keeps the loader's (file name) order whatever the page order is
{
  const {files} = compose(CAT.products.map(p => p.id).reverse());
  const sups = files.filter(f => f.p && !f.off).map(f => f.p.file);
  ok(sups.join() === [...sups].sort().join(), 'sups in the zip are in file-name order');
}

// 7. credits survive (author is empty for the site's own sups)
ok(CAT.products.every(p => Array.isArray(p.credits) && typeof p.author === 'string'), 'author/credits fields present');

console.log(fails ? `${fails} FAILED` : 'all passed');
process.exit(fails ? 1 : 0);
