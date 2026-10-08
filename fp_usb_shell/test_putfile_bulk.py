"""Offline tests for the non-idempotent bulk staging cursor."""
import contextlib
import io
import time
import unittest
from unittest import mock

import putfile


class BulkStageTests(unittest.TestCase):
    def stage_with_fault(self, fault, executes, reports_error=True):
        base = 0x1000
        state_addr = 0x2000
        size = putfile.CHUNK * (putfile.BULK_BATCH + 3)
        blob = bytes((i * 37 + 11) & 255 for i in range(size))
        memory = bytearray(size + putfile.CHUNK)
        registers = {state_addr: base, state_addr + 4: 0}
        calls = []

        def mem_set(addr, value):
            registers[addr] = value

        def mem_get(addr, count=1):
            return [registers.get(addr + i * 4) for i in range(count)]

        def sh(line, retries=3):
            self.assertEqual(retries, 0, 'bulk data must never be retried blindly')
            self.assertTrue(line.startswith('echo '))
            piece = bytes.fromhex(line[5:])
            self.assertLessEqual(len(piece), putfile.CHUNK)
            calls.append(piece)
            if len(calls) != fault or executes:
                offset = registers[state_addr] - base
                memory[offset:offset + len(piece)] = piece
                registers[state_addr] += len(piece)
                registers[state_addr + 4] += len(piece)
            return ('ERR timeout' if len(calls) == fault and reports_error
                    else 'ok')

        with mock.patch.dict(putfile.__dict__, {'BULK_STATE': state_addr}), \
             mock.patch.object(putfile, 'mem_set', side_effect=mem_set), \
             mock.patch.object(putfile, 'mem_get', side_effect=mem_get), \
             mock.patch.object(putfile, 'sh', side_effect=sh), \
             contextlib.redirect_stderr(io.StringIO()):
            restarted, retried = putfile.stage_bulk(base, blob, 'test', time.time())
        if reports_error:
            self.assertEqual((restarted, retried),
                             (0, 0) if executes else (0, 1))
        else:
            self.assertEqual((restarted, retried), (1, 0))
        self.assertEqual(memory[:size], blob)
        self.assertEqual(registers[state_addr], base + size)
        self.assertEqual(registers[state_addr + 4], size)
        self.assertLessEqual(len(calls),
                             (size + putfile.CHUNK - 1) // putfile.CHUNK
                             + putfile.BULK_BATCH)

    def test_lost_chunk_resends_only_its_batch(self):
        self.stage_with_fault(fault=5, executes=False)

    def test_lost_reply_after_execution_does_not_duplicate(self):
        self.stage_with_fault(fault=5, executes=True)

    def test_silent_lost_command_is_caught_at_batch_checkpoint(self):
        self.stage_with_fault(fault=5, executes=False, reports_error=False)

    def test_divergent_cursor_stops_before_file_write(self):
        state_addr = 0x2000
        with mock.patch.dict(putfile.__dict__, {'BULK_STATE': state_addr}), \
             mock.patch.object(putfile, 'mem_set'), \
             mock.patch.object(putfile, 'mem_get', side_effect=[[0x1000, 0],
                                                              [0x1000, putfile.CHUNK]]), \
             mock.patch.object(putfile, 'sh', return_value='ok'), \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, 'cursor diverged'):
                putfile.stage_bulk(0x1000, bytes(putfile.CHUNK), 'test', time.time())

    def test_pair_reply_missing_one_word_uses_single_word_read(self):
        state_addr = 0x2000

        def mem_get(addr, count=1, tries=4):
            if count == 2:
                return [0x1000 + putfile.CHUNK, None]
            self.assertEqual(addr, state_addr + 4)
            return [putfile.CHUNK]

        with mock.patch.dict(putfile.__dict__, {'BULK_STATE': state_addr}), \
             mock.patch.object(putfile, 'mem_get', side_effect=mem_get):
            self.assertEqual(putfile.bulk_accepted(0x1000, 'test'), putfile.CHUNK)

    def test_pair_reply_missing_both_words_uses_single_word_reads(self):
        state_addr = 0x2000

        def mem_get(addr, count=1, tries=4):
            if count == 2:
                return [None, None]
            self.assertEqual(tries, 2)
            return [0x1000 + putfile.CHUNK] if addr == state_addr else [putfile.CHUNK]

        with mock.patch.dict(putfile.__dict__, {'BULK_STATE': state_addr}), \
             mock.patch.object(putfile, 'mem_get', side_effect=mem_get):
            self.assertEqual(putfile.bulk_accepted(0x1000, 'test'), putfile.CHUNK)

    def test_repeated_loss_stops_after_bounded_batch_attempts(self):
        state_addr = 0x2000
        registers = {state_addr: 0x1000, state_addr + 4: 0}

        def mem_set(addr, value):
            registers[addr] = value

        def mem_get(addr, count=1):
            return [registers.get(addr + i * 4) for i in range(count)]

        with mock.patch.dict(putfile.__dict__, {'BULK_STATE': state_addr}), \
             mock.patch.object(putfile, 'mem_set', side_effect=mem_set), \
             mock.patch.object(putfile, 'mem_get', side_effect=mem_get), \
             mock.patch.object(putfile, 'sh', return_value='ERR timeout') as sent, \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, 'did not land'):
                putfile.stage_bulk(0x1000, bytes(putfile.CHUNK), 'test', time.time())
        self.assertEqual(sent.call_count, putfile.BULK_RETRIES)


if __name__ == '__main__':
    unittest.main()
