#!/usr/bin/env python3
"""The shared Quick Set recipe (QS_SHARE.md) against the two camera-proven
references, and the properties the nesting relies on.

    python3 -B -m unittest test_qs

  - N = 3, one layer (OpenGate's row 2): every record = the FP3K plan's bytes
    (camera-proven through OG3K), aliases F2/F3/F4 standing for the markers;
  - N = 8, six layers (Jose's rows 2..7): every record = what Jose's core
    hands the stock parser (his table, his kind-0 7.0, his deltas);
  - any order of layers gives the same bytes; a layer applied twice = once;
  - a record changed by someone else is left alone (conflict);
  - the generator refuses a hard-coded alias id (QS_SHARE.md §1.1).
"""
import itertools
import json
import pathlib
import struct
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ui import qs                                  # noqa: E402
import gen_qs_recipe as gen                        # noqa: E402

SHARED = {0: 0xFFFFFFF2, 1: 0xFFFFFFF3, 2: 0xFFFFFFF4}
JOSE_OWN = {2: 0xFFFFFFF3, 3: 0xFFFFFFE1, 4: 0xFFFFFFE4, 5: 0xFFFFFFE7, 6: 0xFFFFFFEA, 7: 0xFFFFFFED}


def ids_for(k, own):
    d = dict(SHARED)
    d[3] = own
    return d


class Data:
    plans = None
    jose = None

    @classmethod
    def load(cls):
        if cls.plans is None:
            cls.plans = gen.fp3k_plans()
            cls.jose = gen.jose_deltas(gen.jose_layout())
        return cls


def rows():
    return qs.recipe()['records']


def addr(row):
    return int(row['address'], 16)


class TestReferences(unittest.TestCase):
    def test_recipe_is_current(self):
        self.assertEqual(json.dumps(gen.build(), indent=1) + '\n', qs.RECIPE.read_text())

    def test_n3_is_the_fp3k_plan(self):
        d = Data.load()
        layer = (3, 2, ids_for(2, 0xFFFFFFF3))
        n = 0
        for row in rows():
            a = addr(row)
            got, seen = qs.chain(row, [layer])
            if a in d.plans:
                self.assertEqual(got, bytes.fromhex(d.plans[a]['new']), row['address'])
                n += 1
            else:   # rows 3..7 and the N = 8 footer: untouched at N = 3
                self.assertEqual(got, qs.stock(row), row['address'])
        self.assertEqual(n, 359)

    def test_n8_is_jose(self):
        d = Data.load()
        layers = [(8, k, ids_for(k, JOSE_OWN[k])) for k in range(2, 8)]
        for row in rows():
            a = addr(row)
            got, _ = qs.chain(row, layers)
            old = qs.stock(row)
            if a in d.jose:
                want = d.jose[a]
            elif row['rule'] == 'qs_max':       # Jose's kind 0: 7.0, mask 0x1A
                want = bytearray(old[:32] + bytes.fromhex('40e00000') + old[32:])
                want[4:8] = struct.pack('>I', len(want))
                want[31] = 0x1A
                want = bytes(want)
            else:                               # clone / end: his rows = OpenGate's
                want = bytes.fromhex(d.plans[a]['new'])
            self.assertEqual(got, want, row['address'])


class TestNesting(unittest.TestCase):
    def layers(self, n, ks):
        return [(n, k, ids_for(k, 0x40000000 + 16 * k)) for k in ks]

    def test_order_does_not_matter(self):
        for n in (3, 4, 8):
            ks = list(range(2, n))
            base = None
            for perm in itertools.islice(itertools.permutations(ks), 24):
                out = [qs.chain(row, self.layers(n, perm))[0] for row in rows()]
                if base is None:
                    base = out
                self.assertEqual(out, base, f'N={n} order {perm}')

    def test_twice_is_once(self):
        for n in (3, 8):
            ls = self.layers(n, range(2, n))
            for row in rows():
                once = qs.chain(row, ls)[0]
                twice = qs.chain(row, ls + ls)[0]
                self.assertEqual(once, twice, row['address'])

    def test_every_rule_reached_and_reported(self):
        ls = self.layers(8, range(2, 8))
        outcomes = set()
        for row in rows():
            _, seen = qs.chain(row, ls)
            outcomes.update(seen)
            self.assertEqual(seen.count(qs.CONFLICT), 0, row['address'])
        self.assertEqual(outcomes, {qs.WROTE, qs.DONE, qs.SKIP})

    def test_foreign_change_is_a_conflict(self):
        row = next(r for r in rows() if r['rule'] == 'clone')
        cur = bytearray(qs.stock(row))
        cur[20] ^= 1                                    # someone else's edit
        got, what = qs.layer(bytes(cur), row, 3, 2, ids_for(2, 1))
        self.assertEqual((got, what), (bytes(cur), qs.CONFLICT))

    def test_footer_label_only_for_its_row(self):
        for row in rows():
            if row['rule'] != 'footer_label':
                continue
            for k in range(2, 8):
                _, seen = qs.chain(row, self.layers(8, [k]))
                self.assertEqual(seen, [qs.WROTE if k == row['k'] else qs.SKIP])

    def test_unsupported_n_refused(self):
        with self.assertRaises(qs.QsError):
            qs.fn(9)
        self.assertTrue(qs.check_n(3) and qs.check_n(8))
        self.assertFalse(any(qs.check_n(n) for n in (4, 5, 6, 7)))

    def test_growth_fits_the_copy_buffer(self):
        self.assertLess(qs.max_growth(), 1024 - 32)


class TestNoHardcodedIds(unittest.TestCase):
    def test_recipe_has_no_alias_words(self):
        for row in rows():
            for e in row.get('edits', []):
                if isinstance(e[2], dict):
                    continue
                b = bytes.fromhex(e[2])
                for i in range(len(b) - 3):
                    self.assertLess(struct.unpack('>I', b[i:i + 4])[0], 0xF0000000, row['address'])

    def test_check_catches_the_fp3k_literal(self):
        """The bug of 2026-10-06: FFFFFFF3 copied from the FP3K plan."""
        d = Data.load()
        a = 0xC23535A3
        raw = gen.delta(bytes.fromhex(d.plans[a]['old']), bytes.fromhex(d.plans[a]['new']))
        with self.assertRaises(gen.RecipeError):
            gen.forbid_alias_words(raw, hex(a))


if __name__ == '__main__':
    unittest.main()
