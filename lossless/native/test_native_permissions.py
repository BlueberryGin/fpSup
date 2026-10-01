"""Real compiled C permission port; native registry mocked, not camera proof."""
import ctypes
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
CORE = HERE.parent
CASES = ['exact_names', 'partial_registration', 'single_prepare', 'partial_subscribe',
         'cursor_is_not_preference', 'stale_confirm_rejected', 'recording_locked',
         'still_hidden', 'invalid_cursor', 'close_before_publish', 'echo_suppressed',
         'canonical_failure', 'permission_failure', 'fresh_context_failure',
         'foreign_cursor_event', 'both_subscriptions_drain', 'reentry', 'permission_mapping',
         'begin_facts_failure', 'close_busy_revokes', 'stale_ticket', 'provider_reentry',
         'cursor_changes_during_permission_publish']
SOURCES = [CORE / 'control.c', CORE / 'ui_control.c', CORE / 'binding/binding.c',
           HERE / 'native_port.c', HERE / 'permission_fixture.c']


def compile_program(clang, output, sources, extra):
    subprocess.run([clang, '-std=c11', '-Wall', '-Wextra', '-Werror',
                    '-I', str(CORE), '-I', str(CORE / 'binding'), '-I', str(HERE),
                    *extra, *map(str, sources), '-o', str(output)],
                   check=True, capture_output=True, text=True, timeout=30)


class PermissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.clang = shutil.which('clang')
        if not cls.clang:
            raise RuntimeError('clang required; no skipped execution')
        cls.temp = tempfile.TemporaryDirectory(prefix='fpl-ui-permission-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.out = Path(cls.temp.name)
        compile_program(cls.clang, cls.out / 'permission.dylib', SOURCES,
                        ['-O2', '-shared', '-fPIC'])
        cls.lib = ctypes.CDLL(str(cls.out / 'permission.dylib'))
        cls.lib.permission_case.argtypes = [ctypes.c_uint]
        cls.lib.permission_case.restype = ctypes.c_uint
        runner = cls.out / 'main.c'
        runner.write_text('extern unsigned permission_case(unsigned);\n'
                          'extern int printf(const char *, ...);\n'
                          'int main(void) { unsigned n, v, failed=0; '
                          'for(n=0;n<23;n++){v=permission_case(n); '
                          'printf("%u %u\\n",n,v); failed|=v;} return failed!=0;}\n')
        compile_program(cls.clang, cls.out / 'checked', SOURCES + [runner],
                        ['-O1', '-g', '-fsanitize=address,undefined', '-fno-sanitize-recover=all'])
        checked = subprocess.run([str(cls.out / 'checked')], capture_output=True,
                                 text=True, timeout=30)
        cls.sanitized = {int(i): int(line) for i, line in
                         (row.split() for row in checked.stdout.splitlines())}
        if len(cls.sanitized) != len(CASES) or checked.stderr:
            raise RuntimeError('sanitizer/execution failed: ' + checked.stdout + checked.stderr)

    def test_mutations_detect_permission_and_identity_failures(self):
        source = (HERE / 'native_port.c').read_text()
        mutants = [
            ('cursor-select', 'result = fpl_binding_refresh(p->binding);',
             'result = fpl_binding_notify(p->binding, p->ticket, p->descriptor);', 4),
            ('always-confirm', 'allow = cursor == 0 ? view->off_enabled == 1 : view->on_enabled == 1;',
             'allow = 1;', 4),
            ('no-recording-gate', 'allow = cursor == 0 ? view->off_enabled == 1 : view->on_enabled == 1;',
             'allow = cursor == 0;', 6),
            ('missing-close', 'result = record(p, fp_nv_set_canonical(&p->confirm, 0));\n        if (result != FPL_OK) goto done;',
             '/* broken: leave old confirmation permission enabled */', 9),
        ]
        for name, old, new, case in mutants:
            with self.subTest(mutation=name):
                self.assertEqual(source.count(old), 1)
                path, dylib = self.out / (name + '.c'), self.out / (name + '.dylib')
                path.write_text(source.replace(old, new))
                compile_program(self.clang, dylib,
                                [p if p != HERE / 'native_port.c' else path for p in SOURCES],
                                ['-O2', '-shared', '-fPIC'])
                lib = ctypes.CDLL(str(dylib))
                lib.permission_case.argtypes = [ctypes.c_uint]
                lib.permission_case.restype = ctypes.c_uint
                self.assertNotEqual(lib.permission_case(case), 0, 'unsafe mutation escaped assertion')


def case_test(index):
    def run(self):
        self.assertEqual(self.lib.permission_case(index), 0, 'compiled C assertion line')
        self.assertEqual(self.sanitized[index], 0, 'sanitized C assertion line')
    return run


for index, name in enumerate(CASES):
    setattr(PermissionTests, 'test_%02d_%s' % (index, name), case_test(index))

if __name__ == '__main__':
    unittest.main()
