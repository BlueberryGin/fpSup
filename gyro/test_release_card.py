"""Offline checks for the exact files an fpGyroSup release hands to users."""
import contextlib
import hashlib
import io
import pathlib
import sys
import tempfile
import unittest
import zipfile
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import release_card as release


def splash_autorun():
    return ''.join(
        f'display osdfile \\FPSUPUI\\{i}.BIN 0 0 176 56 0\n'
        for i in range(4))


class ReleasePackagingTests(unittest.TestCase):
    def test_finish_frame_is_required_even_when_autorun_only_names_zero_to_three(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)
            (out / 'AutoRun.txt').write_text(splash_autorun())
            ui = out / 'FPSUPUI'
            ui.mkdir()
            for i in range(4):
                (ui / f'{i}.BIN').write_bytes(bytes([i + 1]))
            with self.assertRaisesRegex(SystemExit, '4.BIN'):
                release.package_files(out)
            (ui / '4.BIN').write_bytes(b'finish')
            self.assertEqual(release.package_files(out),
                             release.FILES + release.SPLASH_FILES)

    def test_legacy_text_autorun_does_not_pack_stale_splash_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)
            (out / 'AutoRun.txt').write_text('display text fpSup-Gyro!\n')
            (out / 'FPSUPUI').mkdir()
            (out / 'FPSUPUI/0.BIN').write_bytes(b'stale')
            self.assertEqual(release.package_files(out), release.FILES)

    def test_zip_checksum_and_readme_use_the_built_card_and_all_five_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            here = root / 'gyro'
            here.mkdir()
            source = root / 'version-readme.txt'
            source.write_bytes(b'v1.14.1test\nCamera test pending.\n')
            expected = {
                'AutoRun.txt': splash_autorun().encode(),
                'fpSup.BIN': b'VBIN for mocked packaging test',
                'README.txt': source.read_bytes(),
                **{f'FPSUPUI/{i}.BIN': bytes([i + 1]) * (i + 2)
                   for i in range(5)},
            }

            def build(args, **_kwargs):
                out = pathlib.Path(args[args.index('--out') + 1])
                for name, data in expected.items():
                    if name == 'README.txt':
                        data = b'old generated README'
                    path = out / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                return SimpleNamespace(returncode=0, stdout='built\n', stderr='')

            with mock.patch.object(release, 'HERE', here), \
                 mock.patch.object(release.subprocess, 'run', side_effect=build), \
                 mock.patch.object(release, 'check_sections') as check, \
                 mock.patch.object(sys, 'argv',
                                   ['release_card.py', 'gcsv', 'v1.14.1test',
                                    '--readme', str(source)]), \
                 contextlib.redirect_stdout(io.StringIO()):
                release.main()
            check.assert_called_once_with(here / 'release/gcsv/fpSup.BIN', 'gcsv')

            stem = 'fp-gyro-sup-v1.14.1test'
            archive = here / 'release' / f'{stem}.zip'
            with zipfile.ZipFile(archive) as z:
                self.assertEqual(set(z.namelist()),
                                 {f'{stem}/{name}' for name in expected})
                for name, data in expected.items():
                    self.assertEqual(z.read(f'{stem}/{name}'), data)
            checksums = (here / 'release/SHA256SUMS-v1.14.1test.txt').read_text()
            for name, data in expected.items():
                digest = hashlib.sha256(data).hexdigest()
                self.assertIn(f'{digest}  {name}\n', checksums)
            self.assertIn(f'{hashlib.sha256(archive.read_bytes()).hexdigest()}  '
                          f'{archive.name}\n', checksums)


if __name__ == '__main__':
    unittest.main()
