"""The C03A33C8 hook body, with the whole REC chain linked in on the host.

rec_hook.c + rec_workspace.c + raw_workspace.c + workspace_layout.c +
producer_facts.c + control.c + frame_pipeline.c, compiled together. Only the
original C03A2438, the RAW allocator and the producer's firmware services are
substituted (rec_hook_fixture.c). No camera, USB, card or installer.

What these hold up:
  * every request that is not event 3 reaches the original with the same
    camera and the SAME request object, and its result comes back unchanged,
    with nothing in our state or the allocator touched -- including when our
    state is broken, because C03A2438 gates far more than recording;
  * event 3 goes through the workspace adapter, which calls the original
    itself, first, exactly once;
  * with an honest provider and no readiness proofs, REC is REFUSED and the
    reservation is given back. That is the direct build's contract, and it is
    what this card does today: no adapter yet proves codec, writer, header,
    playback, storage or REC-gate readiness.
"""
import ctypes as ct
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
OK, INVALID, BUSY, UNSUPPORTED, NOT_READY, FAULT = range(6)
READY_CAPTURE, BLOCK_REC = 0x7E, 1 << 31
NULL_REQUEST, OURS, NULL_HOOK = 0xFFFFFFFF, 0, 1
(ORIGINAL_CALLS, SAME_CAMERA, SAME_REQUEST, GETS, FREES, QUERIES, RELEASES,
 BAD_CALL, CALLS, PASSED, ADMITTED, REFUSED, LAST_RESULT, HELD, ACTIVE,
 READS_READY, SEEN_REQUEST) = range(17)
SOURCES = ('rec_hook.c', 'rec_workspace.c', 'raw_workspace.c', 'workspace_layout.c',
           'producer_facts.c', 'rec_hook_fixture.c')
CORE = ('control.c', 'frame_pipeline.c')
OTHER_EVENTS = (0, 1, 2, 4, 0xF, 0x14, 0x24, 0x26, 0x2D)


def build(directory, replace=None, name='hook.dylib'):
    sources = [HERE / s for s in SOURCES] + [HERE.parent / s for s in CORE]
    if replace:
        path, old, new = replace
        text = (HERE / path).read_text()
        if text.count(old) != 1:
            raise AssertionError(f'mutation seam not found once in {path}')
        mutated = Path(directory) / f'{name}-{path}'
        mutated.write_text(text.replace(old, new))
        sources[SOURCES.index(path)] = mutated
    out = Path(directory) / name
    subprocess.run([shutil.which('clang') or 'clang', '-shared', '-fPIC', '-O2', '-std=c11',
                    '-Wall', '-Wextra', '-Werror', '-DFPL_REC_HOOK_HOST_TEST',
                    '-DFPL_REC_WORKSPACE_HOST_TEST', '-DFPL_RAW_WORKSPACE_HOST_TEST',
                    '-DFPL_PRODUCER_FACTS_HOST_TEST', '-I', str(HERE)]
                   + [str(s) for s in sources] + ['-o', str(out)],
                   check=True, capture_output=True, text=True, timeout=60)
    lib = ct.CDLL(str(out))
    for fn, count, returns in (('reset', 1, True), ('call', 2, True), ('get', 1, True),
                               ('reinit', 0, True), ('original_returns', 1, False),
                               ('break_hook', 0, False), ('rewire_setup', 0, False)):
        f = getattr(lib, 'fpl_fixture_' + fn)
        f.argtypes = [ct.c_uint32] * count
        f.restype = ct.c_uint32 if returns else None
    return lib


class HookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='fpl-rec-hook-')
        cls.lib = build(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.assertEqual(self.lib.fpl_fixture_reset(0), OK)

    def get(self, field):
        return self.lib.fpl_fixture_get(field)

    def untouched(self):
        for field in (GETS, FREES, QUERIES, RELEASES, ADMITTED, REFUSED, HELD, ACTIVE):
            self.assertEqual(self.get(field), 0, field)
        self.assertEqual(self.get(BAD_CALL), 0)

    # ---- everything that is not ours ----------------------------------
    def test_other_events_reach_the_original_with_the_same_arguments(self):
        for event in OTHER_EVENTS:
            for result in (0, 1, 7):
                with self.subTest(event=event, result=result):
                    self.lib.fpl_fixture_reset(READY_CAPTURE)
                    self.lib.fpl_fixture_original_returns(result)
                    self.assertEqual(self.lib.fpl_fixture_call(event, OURS), result)
                    self.assertEqual(self.get(ORIGINAL_CALLS), 1)
                    self.assertEqual(self.get(SAME_CAMERA), 1)
                    self.assertEqual(self.get(SAME_REQUEST), 1)
                    self.assertEqual(self.get(PASSED), 1)
                    self.untouched()

    def test_other_events_pass_even_when_our_state_is_broken(self):
        for breakage in ('magic', 'setup', 'null'):
            for event in OTHER_EVENTS:
                with self.subTest(breakage=breakage, event=event):
                    self.lib.fpl_fixture_reset(READY_CAPTURE)
                    if breakage == 'magic':
                        self.lib.fpl_fixture_break_hook()
                    elif breakage == 'setup':
                        self.lib.fpl_fixture_rewire_setup()
                    which = NULL_HOOK if breakage == 'null' else OURS
                    self.assertEqual(self.lib.fpl_fixture_call(event, which), 1)
                    self.assertEqual(self.get(ORIGINAL_CALLS), 1)
                    self.assertEqual(self.get(SAME_REQUEST), 1)
                    self.untouched()

    def test_a_null_request_is_handed_on_as_it_came(self):
        self.assertEqual(self.lib.fpl_fixture_call(NULL_REQUEST, OURS), 1)
        self.assertEqual(self.get(ORIGINAL_CALLS), 1)
        self.assertEqual(self.get(SEEN_REQUEST), 0)
        self.untouched()

    # ---- event 3 ------------------------------------------------------
    def test_event_3_with_no_readiness_is_refused_before_any_memory_is_taken(self):
        """The adapter admits the facts BEFORE reserving: with no readiness
        proofs it refuses without asking the allocator for a single byte."""
        self.assertEqual(self.lib.fpl_fixture_call(3, OURS), 0)
        self.assertEqual(self.get(ORIGINAL_CALLS), 1)     # inside prepare, once
        self.assertEqual(self.get(GETS), 0)
        self.assertEqual(self.get(FREES), 0)
        self.assertEqual(self.get(HELD), 0)
        self.assertEqual(self.get(REFUSED), 1)
        self.assertEqual(self.get(LAST_RESULT), NOT_READY)
        self.assertEqual(self.get(QUERIES), self.get(RELEASES))
        self.assertEqual(self.get(BAD_CALL), 0)

    def test_event_3_with_every_capture_proof_is_admitted_and_holds(self):
        self.lib.fpl_fixture_reset(READY_CAPTURE)
        self.assertEqual(self.lib.fpl_fixture_call(3, OURS), 1)
        self.assertEqual(self.get(ORIGINAL_CALLS), 1)
        self.assertEqual(self.get(ADMITTED), 1)
        self.assertEqual(self.get(HELD), 1)
        self.assertEqual(self.get(ACTIVE), 1)
        self.assertEqual(self.get(FREES), 0)
        self.assertGreaterEqual(self.get(READS_READY), 1, 'readiness never saw the grant')
        self.assertEqual(self.get(BAD_CALL), 0)

    def test_the_movie_button_event_is_admitted_like_event_3(self):
        self.lib.fpl_fixture_reset(READY_CAPTURE)
        self.assertEqual(self.lib.fpl_fixture_call(0x23, OURS), 1)
        self.assertEqual(self.get(ADMITTED), 1)
        self.assertEqual(self.get(HELD), 1)
        self.assertEqual(self.get(ORIGINAL_CALLS), 1)

    def test_a_second_start_while_a_take_holds_is_refused(self):
        self.lib.fpl_fixture_reset(READY_CAPTURE)
        self.assertEqual(self.lib.fpl_fixture_call(3, OURS), 1)
        self.assertEqual(self.lib.fpl_fixture_call(3, OURS), 0)
        self.assertEqual(self.get(LAST_RESULT), BUSY)
        self.assertEqual(self.get(GETS), 1)

    def test_a_blocking_port_refuses_even_with_every_proof(self):
        self.lib.fpl_fixture_reset(READY_CAPTURE | BLOCK_REC)
        self.assertEqual(self.lib.fpl_fixture_call(3, OURS), 0)
        self.assertEqual(self.get(HELD), 0)

    def test_the_original_refusing_event_3_is_respected(self):
        self.lib.fpl_fixture_reset(READY_CAPTURE)
        self.lib.fpl_fixture_original_returns(0)
        self.assertEqual(self.lib.fpl_fixture_call(3, OURS), 0)
        self.assertEqual(self.get(ORIGINAL_CALLS), 1)
        self.assertEqual(self.get(GETS), 0)

    def test_event_3_with_broken_state_is_refused_without_touching_anything(self):
        for breakage in ('magic', 'setup', 'null'):
            with self.subTest(breakage=breakage):
                self.lib.fpl_fixture_reset(READY_CAPTURE)
                if breakage == 'magic':
                    self.lib.fpl_fixture_break_hook()
                elif breakage == 'setup':
                    self.lib.fpl_fixture_rewire_setup()
                which = NULL_HOOK if breakage == 'null' else OURS
                self.assertEqual(self.lib.fpl_fixture_call(3, which), 0)
                self.assertEqual(self.get(ORIGINAL_CALLS), 0)
                self.untouched()

    def test_storage_must_be_fresh(self):
        self.assertEqual(self.lib.fpl_fixture_reinit(), INVALID)


class HookMutationTests(unittest.TestCase):
    """Each defect, in an isolated copy, must be caught by HookTests."""

    MUTATIONS = {
        'passes other events to prepare': (
            'rec_hook.c', 'if (!request || !FPL_REC_IS_START(request[0])) {',
            'if (!request) {'),
        'refuses other events when state is broken': (
            'rec_hook.c', '        if (hook_valid(h)) { h->calls++; h->passed_through++; }\n'
                          '        return original_prepare(camera, request);',
            '        if (!hook_valid(h)) return 0;\n'
            '        h->calls++; h->passed_through++;\n'
            '        return original_prepare(camera, request);'),
        'calls the original twice on event 3': (
            'rec_hook.c', '    dispatch = fpl_rec_workspace_prepare(',
            '    (void)original_prepare(camera, request);\n'
            '    dispatch = fpl_rec_workspace_prepare('),
        'falls back to the original on broken event 3': (
            'rec_hook.c', '         * touched, so there is nothing to count into. */\n        return 0;',
            '         * touched, so there is nothing to count into. */\n'
            '        return original_prepare(camera, request);'),
        'returns dispatch regardless': (
            'rec_hook.c', '    return dispatch;\n}', '    (void)dispatch;\n    return 1;\n}'),
        'does not check the setup wiring': (
            'rec_hook.c', '           h->setup.context == &h->facts;', '           1;'),
    }

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='fpl-rec-hook-mut-')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @staticmethod
    def against(lib):
        """HookTests, run on a given build. A subclass, because a suite run
        calls setUpClass, which would quietly rebuild the REAL module and test
        that instead of the mutant."""
        class Against(HookTests):
            @classmethod
            def setUpClass(cls):
                cls.lib = lib

            @classmethod
            def tearDownClass(cls):
                pass
        result = unittest.TestResult()
        unittest.defaultTestLoader.loadTestsFromTestCase(Against).run(result)
        return result

    def test_the_real_build_passes_the_same_harness(self):
        result = self.against(build(self.tmp.name, name='real.dylib'))
        self.assertTrue(result.wasSuccessful(), result.failures + result.errors)
        self.assertGreater(result.testsRun, 5)

    def test_every_mutation_fails_the_hook_tests(self):
        for index, (name, seam) in enumerate(self.MUTATIONS.items()):
            with self.subTest(mutation=name):
                result = self.against(build(self.tmp.name, seam, f'mutant{index}.dylib'))
                self.assertFalse(result.wasSuccessful(), f'{name} was not caught')


class ArmCompileTests(unittest.TestCase):
    def test_the_hook_chain_builds_freestanding_for_the_camera(self):
        clang = shutil.which('clang') or 'clang'
        nm = shutil.which('llvm-nm') or shutil.which('nm') or 'nm'
        with tempfile.TemporaryDirectory(prefix='fpl-rec-hook-arm-') as tmp:
            for source in ('rec_hook.c', 'producer_facts.c'):
                out = Path(tmp) / (source + '.o')
                subprocess.run([clang, '--target=armv7a-none-eabi', '-mcpu=cortex-a9',
                                '-mthumb', '-mfloat-abi=soft', '-mfpu=none',
                                '-ffreestanding', '-fno-builtin', '-nostdlib', '-O2',
                                '-std=c11', '-Wall', '-Wextra', '-Werror', '-c',
                                '-I', str(HERE), str(HERE / source), '-o', str(out)],
                               check=True, capture_output=True, text=True, timeout=30)
                undefined = subprocess.run([nm, '-u', str(out)], capture_output=True,
                                           text=True).stdout
                for forbidden in ('memcpy', 'memset', '__aeabi', 'fpl_test_'):
                    self.assertNotIn(forbidden, undefined, f'{source}: {undefined}')


if __name__ == '__main__':
    unittest.main()
