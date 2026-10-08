"""Offline regressions for updates over a non-truncating camera file mode."""
import hashlib
import json
import pathlib
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import deploy
import getfile


def vbin(body=b'data'):
    return struct.pack('<4sIII', b'VBIN', 0, 0, len(body)) + body


class DeployTests(unittest.TestCase):
    def test_deploy_does_not_time_out_an_active_transfer(self):
        completed = subprocess.CompletedProcess([], 0, '', '')
        with mock.patch.object(deploy.subprocess, 'run', return_value=completed) as launched:
            self.assertIs(deploy.run('putfile.py', 'local', '\\remote'), completed)
        self.assertNotIn('timeout', launched.call_args.kwargs)

    def test_shorter_bin_is_extended_to_erase_the_previous_tail(self):
        new = vbin()
        old_size = 258_048
        padded = deploy.prepare_bin(new, old_size)
        self.assertEqual(len(padded), old_size)
        self.assertEqual(padded[:len(new)], new)
        self.assertEqual(padded[len(new):], bytes(old_size - len(new)))

    def test_unrepresentable_old_size_stops_before_padding(self):
        with self.assertRaisesRegex(ValueError, 'staging limit'):
            deploy.prepare_bin(vbin(), deploy.MAX_UPLOAD + 1)

    def test_shorter_autorun_uses_complete_comment_lines(self):
        script = b'echo\n'
        padded = deploy.prepare_autorun(script, 141)
        self.assertEqual(len(padded), 141)
        self.assertTrue(padded.startswith(script))
        self.assertTrue(padded.endswith(b'\n'))
        self.assertTrue(all(line.startswith(b'#') for line in padded[len(script):].splitlines()))

    def test_remote_size_requires_a_real_directory_reply(self):
        with mock.patch.object(deploy, 'sh', return_value='ERR timeout'):
            with self.assertRaisesRegex(RuntimeError, 'cannot read card directory'):
                deploy.remote_sizes()
        listing = 'entry 32768 fpSup.BIN\nentry 32772 AutoRun.txt\n'
        with mock.patch.object(deploy, 'sh', return_value=listing):
            self.assertEqual(deploy.remote_sizes(), {'fpSup.BIN': 32768,
                                                      'AutoRun.txt': 32772})

    def test_getfile_rejects_matching_prefix_with_old_tail(self):
        with self.assertRaisesRegex(ValueError, 'file length is 32772'):
            getfile.checked_length(32772, 32768)
        self.assertEqual(getfile.checked_length(32772, 32768, partial=True), 32768)

    def test_getfile_rejects_exact_read_into_caller_buffer_before_camera_claim(self):
        with mock.patch.object(sys, 'argv',
                               ['getfile.py', '\\TEST', '--size', '4', '--buf', '0x1000']), \
             mock.patch('putfile._claims', side_effect=AssertionError('camera claim')):
            with self.assertRaises(SystemExit) as raised:
                getfile.main()
        self.assertEqual(raised.exception.code, 2)

    def test_bin_only_accepts_only_verified_comment_tail(self):
        script = b'echo\n'
        self.assertEqual(deploy.compatible_autorun(script, script + b'###\n'),
                         (True, 4))
        self.assertEqual(deploy.compatible_autorun(script, script + b'mem set 0x1 0x2\n'),
                         (False, 0))
        self.assertEqual(deploy.compatible_autorun(script, b'ping\n###\n'),
                         (False, 0))

    def test_empty_candidate_fpsupui_folder_is_incomplete(self):
        with tempfile.TemporaryDirectory() as td:
            (pathlib.Path(td) / 'FPSUPUI').mkdir()
            with self.assertRaisesRegex(ValueError, 'sidecar must be'):
                deploy.candidate_sidecars(pathlib.Path(td))

    def test_bin_only_shorter_update_checks_autorun_and_full_readback(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            card = root / 'card'
            card.mkdir()
            new = vbin(b'new')
            script = b'echo\n'
            (card / 'fpSup.BIN').write_bytes(new)
            (card / 'AutoRun.txt').write_bytes(script)
            ui = card / 'FPSUPUI'
            ui.mkdir()
            (ui / '0.BIN').write_bytes(b'ui-asset')
            camera = {'fpSup.BIN': vbin(b'old') + b'X' * 100,
                      'AutoRun.txt': script + b'###\n',
                      'FPSUPUI\\0.BIN': b'ui-asset'}
            writes = []
            events = []

            def sizes():
                return {name: len(data) for name, data in camera.items()}

            # getfile args are remote, local, --size, N. Keep the fake transport
            # as strict as getfile's exact-size contract.
            def fake_run(name, *args):
                if name == 'getfile.py':
                    remote = args[0].lstrip('\\')
                    events.append(('read', remote))
                    requested = int(args[3])
                    if len(camera[remote]) != requested:
                        return subprocess.CompletedProcess([], 1, '', 'wrong size')
                    pathlib.Path(args[1]).write_bytes(camera[remote])
                    return subprocess.CompletedProcess([], 0, '', '')
                remote = args[1].lstrip('\\')
                data = pathlib.Path(args[0]).read_bytes()
                before = camera[remote]
                camera[remote] = data + before[len(data):]
                writes.append(remote)
                events.append(('write', remote))
                return subprocess.CompletedProcess([], 0, '', '')

            with mock.patch.object(deploy, 'BACK', root / 'readback'), \
                 mock.patch.object(deploy, 'STATE', root / 'readback' / 'oncard.json'), \
                 mock.patch.object(deploy, 'remote_sizes', side_effect=sizes), \
                 mock.patch.object(deploy, 'run', side_effect=fake_run), \
                 mock.patch.object(deploy.signal, 'signal'):
                self.assertEqual(deploy.main(['--bin-only', str(card)]), 0)
                self.assertEqual(writes, ['fpSup.BIN'])
                self.assertLess(events.index(('read', 'FPSUPUI\\0.BIN')),
                                events.index(('write', 'fpSup.BIN')))
                self.assertEqual(camera['fpSup.BIN'],
                                 new + bytes(len(vbin(b'old') + b'X' * 100) - len(new)))
                state = json.loads(deploy.STATE.read_text())
                self.assertEqual(state['written_files'], ['fpSup.BIN'])
                self.assertEqual(state['files']['fpSup.BIN'],
                                 hashlib.sha256(camera['fpSup.BIN']).hexdigest())
                self.assertEqual(state['files']['AutoRun.txt'],
                                 hashlib.sha256(camera['AutoRun.txt']).hexdigest())
                self.assertEqual(state['sidecars_preflight']['FPSUPUI/0.BIN'],
                                 hashlib.sha256(camera['FPSUPUI\\0.BIN']).hexdigest())

    def test_zero_old_tail_uploads_only_short_bin_then_verifies_full_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            card = root / 'card'
            card.mkdir()
            new = vbin(b'new')
            (card / 'fpSup.BIN').write_bytes(new)
            (card / 'AutoRun.txt').write_bytes(b'echo\n')
            camera = {'fpSup.BIN': vbin(b'old') + bytes(100),
                      'AutoRun.txt': b'echo\n'}
            sent = []
            reads = []

            def fake_run(name, *args):
                if name == 'getfile.py':
                    remote = args[0].lstrip('\\')
                    size = int(args[3])
                    reads.append((remote, size))
                    self.assertEqual(len(camera[remote]), size)
                    pathlib.Path(args[1]).write_bytes(camera[remote])
                    return subprocess.CompletedProcess([], 0, '', '')
                remote = args[1].lstrip('\\')
                data = pathlib.Path(args[0]).read_bytes()
                sent.append(len(data))
                camera[remote] = data + camera[remote][len(data):]
                return subprocess.CompletedProcess([], 0, '', '')

            old_size = len(camera['fpSup.BIN'])
            with mock.patch.object(deploy, 'BACK', root / 'readback'), \
                 mock.patch.object(deploy, 'STATE', root / 'readback' / 'oncard.json'), \
                 mock.patch.object(deploy, 'remote_sizes',
                                   return_value={'fpSup.BIN': old_size,
                                                 'AutoRun.txt': 5}), \
                 mock.patch.object(deploy, 'run', side_effect=fake_run), \
                 mock.patch.object(deploy.signal, 'signal'):
                self.assertEqual(deploy.main(['--bin-only', str(card)]), 0)
            self.assertEqual(sent, [len(new)])
            self.assertEqual(reads, [('AutoRun.txt', 5),
                                     ('fpSup.BIN', old_size),
                                     ('fpSup.BIN', old_size)])
            self.assertEqual(camera['fpSup.BIN'], new + bytes(old_size - len(new)))

    def test_full_deploy_refuses_missing_changed_or_longer_sidecar_before_write(self):
        for remote in (None, b'other', b'asset-old-tail'):
            with self.subTest(remote=remote), tempfile.TemporaryDirectory() as td:
                root = pathlib.Path(td)
                card = root / 'card'
                card.mkdir()
                (card / 'fpSup.BIN').write_bytes(vbin())
                (card / 'AutoRun.txt').write_bytes(b'echo\n')
                ui = card / 'FPSUPUI'
                ui.mkdir()
                (ui / '0.BIN').write_bytes(b'asset')
                back = root / 'readback'
                back.mkdir()
                state = back / 'oncard.json'
                state.write_text('previous pair')
                calls = []

                def fake_run(name, *args):
                    calls.append(name)
                    self.assertEqual(name, 'getfile.py')
                    self.assertEqual(args[0], '\\FPSUPUI\\0.BIN')
                    if remote is None or len(remote) != int(args[3]):
                        return subprocess.CompletedProcess([], 1, '', 'missing or wrong length')
                    pathlib.Path(args[1]).write_bytes(remote)
                    return subprocess.CompletedProcess([], 0, '', '')

                with mock.patch.object(deploy, 'BACK', back), \
                     mock.patch.object(deploy, 'STATE', state), \
                     mock.patch.object(deploy, 'remote_sizes',
                                       return_value={'fpSup.BIN': 20, 'AutoRun.txt': 5}), \
                     mock.patch.object(deploy, 'run', side_effect=fake_run):
                    with self.assertRaisesRegex(SystemExit,
                                                'sidecar must be deployed/verified separately'):
                        deploy.main([str(card)])
                self.assertEqual(calls, ['getfile.py'])
                self.assertEqual(state.read_text(), 'previous pair')

    def test_bin_only_mismatch_does_not_write_or_erase_pair_record(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            card = root / 'card'
            card.mkdir()
            (card / 'fpSup.BIN').write_bytes(vbin())
            (card / 'AutoRun.txt').write_bytes(b'new\n')
            back = root / 'readback'
            back.mkdir()
            state = back / 'oncard.json'
            state.write_text('previous pair')

            def fake_run(name, *args):
                self.assertEqual(name, 'getfile.py')
                pathlib.Path(args[1]).write_bytes(b'old\n')
                return subprocess.CompletedProcess([], 0, '', '')

            with mock.patch.object(deploy, 'BACK', back), \
                 mock.patch.object(deploy, 'STATE', state), \
                 mock.patch.object(deploy, 'remote_sizes',
                                   return_value={'fpSup.BIN': 32, 'AutoRun.txt': 4}), \
                 mock.patch.object(deploy, 'run', side_effect=fake_run):
                with self.assertRaisesRegex(SystemExit, 'AutoRun'):
                    deploy.main(['--bin-only', str(card)])
            self.assertEqual(state.read_text(), 'previous pair')

    def test_failed_write_stops_without_readback_or_retry(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            card = root / 'card'
            card.mkdir()
            (card / 'fpSup.BIN').write_bytes(vbin())
            (card / 'AutoRun.txt').write_bytes(b'echo\n')
            back = root / 'readback'
            back.mkdir()
            state = back / 'oncard.json'
            state.write_text('previous pair')
            calls = []

            def failed_write(name, *args):
                calls.append(name)
                self.assertEqual(name, 'putfile.py')
                # Live putfile output is streamed, not captured by deploy.run.
                return subprocess.CompletedProcess([], 1, None, None)

            with mock.patch.object(deploy, 'BACK', back), \
                 mock.patch.object(deploy, 'STATE', state), \
                 mock.patch.object(deploy, 'remote_sizes',
                                   return_value={'fpSup.BIN': 20, 'AutoRun.txt': 5}), \
                 mock.patch.object(deploy, 'run', side_effect=failed_write), \
                 mock.patch.object(deploy.signal, 'signal'):
                with self.assertRaisesRegex(SystemExit, 'see transfer output above.*DO NOT REBOOT'):
                    deploy.main([str(card)])
            self.assertEqual(calls, ['putfile.py'])
            self.assertFalse(state.exists())

    def test_structural_readback_failure_never_retries_write(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            card = root / 'card'
            card.mkdir()
            (card / 'fpSup.BIN').write_bytes(vbin())
            (card / 'AutoRun.txt').write_bytes(b'echo\n')
            writes = []
            sizes = iter(({'fpSup.BIN': 20, 'AutoRun.txt': 5},
                          {'fpSup.BIN': 30, 'AutoRun.txt': 5}))

            def transfer(name, *args):
                if name == 'putfile.py':
                    writes.append(args[1])
                    return subprocess.CompletedProcess([], 0, '', '')
                return subprocess.CompletedProcess([], 1, '', 'file length is 30')

            with mock.patch.object(deploy, 'BACK', root / 'readback'), \
                 mock.patch.object(deploy, 'STATE', root / 'readback' / 'oncard.json'), \
                 mock.patch.object(deploy, 'remote_sizes', side_effect=lambda: next(sizes)), \
                 mock.patch.object(deploy, 'run', side_effect=transfer), \
                 mock.patch.object(deploy.signal, 'signal'):
                with self.assertRaisesRegex(SystemExit, 'mode 7 cannot truncate'):
                    deploy.main([str(card)])
            self.assertEqual(writes, ['\\fpSup.BIN'])


if __name__ == '__main__':
    unittest.main()
