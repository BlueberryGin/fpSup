"""put() and read_direct() choose MEM1 only where it is safe to."""
import unittest
from unittest import mock

import putfile

CAPS = {'xfer': 0xFFFC00, 'pool': 0x45000000, 'pool_size': 0x100000,
        'windows': [(0, 0, 0)] * 8}
BLOB = bytes(range(256)) * 4


class Fallback(Exception):
    """Stands in for the old checked path, which talks to the camera."""


def _patches(**kw):
    p = [mock.patch.object(putfile, '_claims'),
         mock.patch.object(putfile, 'mem_caps', return_value=CAPS),
         mock.patch.object(putfile, 'ensure_bulk', side_effect=Fallback)]
    p += [mock.patch.object(putfile, k, **v) for k, v in kw.items()]
    return p


class PutMem1Tests(unittest.TestCase):
    def run_put(self, addr, **kw):
        ps = _patches(**kw)
        mocks = [x.start() for x in ps]
        self.addCleanup(lambda: [x.stop() for x in ps])
        return putfile.put(addr, BLOB, 'test'), mocks

    def test_staging_goes_mem1_and_is_verified(self):
        with mock.patch.object(putfile, 'mem_line') as line, \
             mock.patch.object(putfile, 'mem_read', return_value=BLOB) as rd:
            line.return_value = 'OKW 1024 0.5'
            ps = _patches()
            [x.start() for x in ps]
            try:
                putfile.put(0x45010000, BLOB, 'test')
            finally:
                [x.stop() for x in ps]
        self.assertTrue(line.call_args[0][0].startswith('MEMW 0x45010000 1024 1 /'))
        rd.assert_called_once_with(0x45010000, 1024, True, 'verify')

    def test_readback_mismatch_stops(self):
        with mock.patch.object(putfile, 'mem_line', return_value='OKW 1024 0.5'), \
             mock.patch.object(putfile, 'mem_read', return_value=b'\0' * 1024):
            with self.assertRaises(SystemExit):
                self.run_put(0x45010000)

    def test_cave_address_never_tries_mem1(self):
        with mock.patch.object(putfile, 'mem_write') as mw:
            with self.assertRaises(Fallback):
                self.run_put(0xC072F100)
        mw.assert_not_called()

    def test_refusal_falls_back(self):
        err = RuntimeError('memw  : ERR mem refused at=0x50000000 done=0 cmd6 refused code=4')
        with mock.patch.object(putfile, 'mem_write', side_effect=err):
            with self.assertRaises(Fallback):
                self.run_put(0x50000000)

    def test_uncertain_stops_without_fallback(self):
        err = RuntimeError('memw  : ERR mem uncertain at=0x45010000 done=0 data OUT rc=-7')
        with mock.patch.object(putfile, 'mem_write', side_effect=err):
            with self.assertRaises(SystemExit):
                self.run_put(0x45010000)

    def test_old_worker_uses_old_path(self):
        with mock.patch.object(putfile, 'mem_write') as mw:
            ps = _patches(mem_caps={'return_value': None})
            [x.start() for x in ps]
            try:
                with self.assertRaises(Fallback):
                    putfile.put(0x45010000, BLOB, 'test')
            finally:
                [x.stop() for x in ps]
        mw.assert_not_called()

    def test_read_direct_grants_and_revokes(self):
        with mock.patch.object(putfile, '_claims'), \
             mock.patch.object(putfile, 'mem_caps', return_value=CAPS), \
             mock.patch.object(putfile, 'mem_window') as win, \
             mock.patch.object(putfile, 'mem_read', side_effect=RuntimeError('x')):
            with self.assertRaises(RuntimeError):
                putfile.read_direct(0x5F900982, 10)
        self.assertEqual(win.call_args_list[0].args, (7, 0x5F900980, 12, 1))
        self.assertEqual(win.call_args_list[-1].args, (7, 0, 0, 0))   # even on error


if __name__ == '__main__':
    unittest.main()
