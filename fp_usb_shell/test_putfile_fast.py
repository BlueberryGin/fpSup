"""Offline decisions for the binary upload path and its fallback."""
import contextlib
import io
import time
import unittest
import zlib
from unittest import mock

import putfile


class FastUploadTests(unittest.TestCase):
    def setUp(self):
        # put() asks the daemon about MEM1 first; these tests are about UP01
        # and the echo path, and must never reach a live camera.
        patcher = mock.patch.object(putfile, 'mem_caps', return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_old_daemon_or_worker_uses_legacy_path(self):
        with mock.patch.object(putfile, 'upload_request', return_value='ERR unknown'):
            self.assertIsNone(putfile.upload_caps())
        with mock.patch.object(putfile, 'upload_caps', return_value=None), \
             mock.patch.object(putfile, 'upload_request') as sent:
            self.assertFalse(putfile.stage_fast(0x45010000, bytes(512),
                                                'stage', time.time()))
        sent.assert_not_called()

    def test_binary_chunks_use_absolute_addresses_and_crc(self):
        base = 0x45010000
        blob = bytes((i * 19) & 255 for i in range(putfile.UPLOAD_CHUNK + 12))
        calls = []

        def request(header, data=b''):
            self.assertTrue(header.startswith('PUT '))
            _, address, length, checksum = header.split()
            calls.append((int(address, 16), data))
            self.assertEqual(int(length), len(data))
            self.assertEqual(int(checksum, 16), zlib.crc32(data))
            return f'OKU {100 + len(calls)} {address} {length} {checksum}'

        with mock.patch.object(putfile, 'upload_caps',
                               return_value=(putfile.UPLOAD_CHUNK, (0, 0, 0, 0))), \
             mock.patch.object(putfile, 'upload_request', side_effect=request), \
             mock.patch.object(putfile.time, 'sleep') as slept, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertTrue(putfile.stage_fast(base, blob, 'stage', time.time()))
        self.assertEqual(calls, [(base, blob[:putfile.UPLOAD_CHUNK]),
                                 (base + putfile.UPLOAD_CHUNK, blob[putfile.UPLOAD_CHUNK:])])
        slept.assert_not_called()

    def test_lost_ack_is_confirmed_without_resending(self):
        base = 0x45010000
        blob = bytes(range(256)) * 4
        checksum = zlib.crc32(blob)
        caps = [(putfile.UPLOAD_CHUNK, (0, 0, 0, 0)),
                (putfile.UPLOAD_CHUNK, (101, base, len(blob), checksum))]
        with mock.patch.object(putfile, 'upload_caps', side_effect=caps), \
             mock.patch.object(putfile, 'upload_request',
                               return_value='ERR upload uncertain seq=101') as sent, \
             mock.patch.object(putfile.time, 'sleep'), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertTrue(putfile.stage_fast(base, blob, 'stage', time.time()))
        self.assertEqual(sent.call_count, 1)

    def test_unconfirmed_chunk_stops_before_card_file_write(self):
        checks = iter([(putfile.UPLOAD_CHUNK, (0, 0, 0, 0))])

        def first_then_fail():
            value = next(checks, 'failed')
            if value == 'failed':
                raise RuntimeError('no reply')
            return value

        with mock.patch.object(putfile, 'upload_caps',
                               side_effect=first_then_fail), \
             mock.patch.object(putfile, 'upload_request',
                               return_value='ERR upload uncertain'), \
             mock.patch.object(putfile.time, 'sleep'), \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, 'cannot confirm upload'):
                putfile.stage_fast(0x45010000, bytes(512), 'stage', time.time())

    def test_verified_fast_stage_skips_echo_bulk_loader(self):
        blob = bytes(range(256)) * 2
        with mock.patch.object(putfile, '_claims'), \
             mock.patch.object(putfile, 'stage_fast', return_value=True) as fast, \
             mock.patch.object(putfile, 'ensure_bulk') as bulk, \
             mock.patch.object(putfile, 'read_bulk', return_value=blob), \
             contextlib.redirect_stderr(io.StringIO()):
            putfile.put(0x45010000, blob, 'stage', fast=True)
        fast.assert_called_once()
        bulk.assert_not_called()

    def test_unsupported_worker_uses_checked_echo_staging(self):
        blob = bytes(range(256)) * 2
        with mock.patch.dict(putfile.__dict__, {'BULK': 0xC072E200}), \
             mock.patch.object(putfile, '_claims'), \
             mock.patch.object(putfile, 'stage_fast', return_value=False), \
             mock.patch.object(putfile, 'ensure_bulk') as bulk, \
             mock.patch.object(putfile, 'mem_get',
                               return_value=[putfile.ECHO_ORIG]), \
             mock.patch.object(putfile, 'set_echo_handler'), \
             mock.patch.object(putfile, 'stage_bulk', return_value=(0, 0)) as legacy, \
             mock.patch.object(putfile, 'read_bulk', return_value=blob), \
             contextlib.redirect_stderr(io.StringIO()):
            putfile.put(0x45010000, blob, 'stage', fast=True)
        bulk.assert_called_once()
        legacy.assert_called_once()


if __name__ == '__main__':
    unittest.main()
