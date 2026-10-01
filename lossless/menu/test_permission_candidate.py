"""Pinned binary checks for opt-in native cursor/confirmation bindings.

This parses real emitted NBU records; it neither renders widgets nor provides
native registration, loader, scheduling, codec readiness or camera access.
"""
import argparse
from copy import deepcopy
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_menu_candidate as builder

SEG0 = Path(os.environ.get('FPLOSSLESS_SEG0',
                           str(Path(__file__).resolve().parents[3] / 'out/seg0_c0000000.bin')))
AUDIT_MODULE = builder.DEFAULT_AUDIT


class PermissionCandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audit = builder.load_audit(AUDIT_MODULE)
        cls.source = SEG0.read_bytes()  # missing canonical input fails; no silent skip
        cls.fixed_page, cls.fixed_pool, cls.fixed = builder.build_candidate(cls.source, cls.audit)
        cls.page, cls.pool, cls.manifest = builder.build_candidate(
            cls.source, cls.audit, 'native-permission-gated')
        cls.strings = builder.PrivatePool(cls.pool)
        cls.binding_rows = cls.manifest['private_permissions']['bindings']

    def records(self, page):
        return {r.offset: r for r in self.audit.records(page, 0, len(page))}

    def clone(self, address, page=None, manifest=None):
        manifest = self.manifest if manifest is None else manifest
        page = self.page if page is None else page
        location = next(row for row in manifest['record_locations']
                        if row['source_record'] == '0x%08X' % address and
                        row['provenance'] == 'cloned_fourth_row_gated')
        return self.records(page)[location['candidate_offset']]

    def decode_binding(self, record):
        self.assertEqual((record.tag, len(record.data), record.word(12),
                          record.word(16), record.word(20), record.word(32)),
                         (0x10006, 58, 6, 0, 0xffffffff, 0x2f))
        self.assertEqual(self.strings.resolve(record.word(8)), 'appVariableEvent')
        self.assertEqual((record.data[44], record.data[53], record.word(54)), (0, 0, 6))
        return (record.word(24), record.word(28), record.word(36),
                self.strings.resolve(record.word(40)), record.word(45),
                self.strings.resolve(record.word(49)))

    def check_bindings(self, page):
        records = self.records(page)
        decoded = [self.decode_binding(records[row['candidate_offset']])
                   for row in self.binding_rows]
        self.assertEqual(decoded, [
            (34497, 10, 1, 'MV_fpLosslessCursor', 3, 'value'),
            (34498, 14, 0, 'MV_fpLosslessConfirm', 10, 'type'),
            (34498, 15, 0, 'MV_fpLosslessConfirm', 12, 'type')])
        actual = [r for r in records.values() if r.tag == 0x10006 and
                  self.strings.resolve(r.word(8)) == 'appVariableEvent' and
                  r.word(24) in (34497, 34498)]
        self.assertEqual(len(actual), 3)

    def check_saved_value(self, page):
        for address, offset in ((0xc216ceb3, 40), (0xc216cee8, 36),
                                (0xc216cf15, 54), (0xc216d8f2, 36), (0xc216d986, 36)):
            record = self.clone(address, page)
            self.assertEqual(self.strings.resolve(record.word(offset)), 'MV_fpLossless')
            self.assertEqual(record.data, self.clone(address, self.fixed_page, self.fixed).data)

    def check_initial_confirm_masks(self, page):
        for address, component, key in ((0xc216d8ca, 10, 0x27), (0xc216d95e, 12, 0x0d)):
            record = self.clone(address, page)
            self.assertEqual((len(record.data), record.word(24), record.word(28),
                              record.word(32), record.word(36), record.word(40)),
                             (44, 34498, component, 9, 0, key))
            old = self.clone(address, self.fixed_page, self.fixed)
            self.assertEqual(record.data[8:32], old.data[8:32])

    def check_budgets(self, page):
        before = self.audit.parse_header(self.records(self.fixed_page)[0], self.strings.resolve)
        after = self.audit.parse_header(self.records(page)[0], self.strings.resolve)
        for kind, count in before['component_counts'].items():
            self.assertEqual(after['component_counts'][kind], count + (3 if kind == 'appVariableEvent' else 0))
        for key in ('groups', 'clip_property_counts', 'property_key_counts', 'trailing_budget'):
            self.assertEqual(after[key], before[key])
        self.assertEqual(after['objects'], before['objects'])
        self.assertEqual(self.clone(0xc216d383, page).word(8), 8)
        self.assertEqual(self.clone(0xc216d5c7, page).word(8), 12)

    def check_navigation(self, page):
        # ESC and Left, Up/Down, stock cursor movement and root escape paths.
        for address in (0xc216ce63, 0xc216d7bc, 0xc216d7e0, 0xc216d817, 0xc216d83f,
                        0xc216d876, 0xc216d89e, 0xc216d9f2, 0xc216da1a):
            self.assertEqual(self.clone(address, page).data,
                             self.clone(address, self.fixed_page, self.fixed).data)

    def test_default_fixed_profile_remains_byte_identical(self):
        self.assertEqual((len(self.fixed_page), builder.digest(self.fixed_page)),
                         (179725, '33946d01b1fd9bca7cba72ef55ef53e16a0469a00e5a29f35896a46e43023633'))
        self.assertEqual((len(self.fixed_pool), builder.digest(self.fixed_pool)),
                         (176240, 'ea773283be884f8c40ca1f08d759d94157d3d58200398a92805ef3f8cd09ad96'))
        self.assertNotIn('private_permissions', self.fixed)
        self.assertNotIn('private_permission_bindings', builder.PROFILES['pure-fixed-gated'])

    def test_exact_native_typed_bindings_and_directions(self):
        self.check_bindings(self.page)
        for row in self.binding_rows:
            self.assertEqual(row['source_template'], '0xC216CEB3')
            self.assertEqual(row['source_template_sha256'],
                             '24e253b51f33c15a0b11f546b5d27d5fdc894ce1008ccde918a9030a69aca62c')

    def test_saved_preference_and_confirmation_actions_stay_separate(self):
        self.check_saved_value(self.page)

    def test_right_enter_fail_closed_without_changing_other_key_fields(self):
        self.check_initial_confirm_masks(self.page)

    def test_escape_left_up_down_and_focus_are_unchanged(self):
        self.check_navigation(self.page)

    def test_real_component_budgets_and_no_new_objects(self):
        self.check_budgets(self.page)
        records = list(self.records(self.page).values())
        objects = self.audit.parse_objects(records, self.strings.resolve)
        self.assertEqual(len(objects), 214)
        self.assertEqual((len(self.page), len(records), records[0].word(4)), (179907, 1541, 3288))
        self.assertEqual(self.manifest['private_permissions']['new_objects'], 0)
        self.assertEqual(self.manifest['private_permissions']['added_components'], {'appVariableEvent': 3})

    def test_component_ids_do_not_collide_and_targets_exist(self):
        old = list(self.records(self.fixed_page).values())
        new = list(self.records(self.page).values())
        for row in self.binding_rows:
            prior_ids = {r.word(28 if r.tag == 0x10006 else 24) for r in old
                         if r.tag in builder.OWNER_OFFSETS and
                         r.word(builder.OWNER_OFFSETS[r.tag]) == row['owner_id']}
            self.assertNotIn(row['event_component_id'], prior_ids)
            targets = [r for r in new if r.tag in builder.OWNER_OFFSETS and
                       r.word(builder.OWNER_OFFSETS[r.tag]) == row['owner_id'] and
                       r.word(28 if r.tag == 0x10006 else 24) == row['target_component_id'] and
                       self.strings.resolve(r.word(8)) == row['target_kind']]
            self.assertEqual(len(targets), 1)
        self.assertEqual(len({(r['owner_id'], r['event_component_id']) for r in self.binding_rows}), 3)

    def test_popup_stays_binary_and_saved_root_max_not_changed(self):
        value = self.clone(0xc216d3eb)
        self.assertEqual(value.data[28:], builder.words(15, 0, 0x3f800000, 0, 1))
        for row in self.manifest['decoded_property_records']:
            if row['component'] == 'controlValue':
                address = int(row['source_record'], 16)
                self.assertEqual(self.clone(address).data,
                                 self.clone(address, self.fixed_page, self.fixed).data)

    def test_no_animation_reenables_confirmation_key_types(self):
        for record in self.records(self.page).values():
            if record.tag == 0x1000a:
                for prop in self.audit.parse_clip(record, self.strings.resolve)['properties']:
                    self.assertFalse(record.word(20) == 34498 and prop['name'] == 'type' and
                                     prop['component_ref'] in (10, 12))

    def test_stock_audio_and_original_records_are_preserved(self):
        records = self.records(self.page)
        changed, audio = [], []
        for row in self.manifest['record_locations']:
            if row['provenance'] not in ('stock_unchanged', 'stock_modified'):
                continue
            offset = int(row['source_record'], 16) - 0xc0000000
            original = self.source[offset:offset + builder.u32(self.source, offset + 4)]
            data = records[row['candidate_offset']].data
            if data != original:
                changed.append(offset)
            if self.audit.DONOR_START <= offset < self.audit.DONOR_END:
                audio.append(data)
        self.assertEqual(changed, [self.audit.PAGE_START, self.audit.MODE_CHANGE, self.audit.B2_OBJECT])
        self.assertEqual(builder.digest(b''.join(audio)), self.audit.DONOR_SHA256)

    def test_only_four_existing_clone_records_change(self):
        changed = []
        for row in self.fixed['record_locations']:
            if row['provenance'] != 'cloned_fourth_row_gated':
                continue
            address = int(row['source_record'], 16)
            if self.clone(address).data != self.clone(address, self.fixed_page, self.fixed).data:
                changed.append(address)
        self.assertEqual(changed, [0xc216d383, 0xc216d5c7, 0xc216d8ca, 0xc216d95e])

    def test_registered_values_and_rendering_are_not_fabricated(self):
        p = self.manifest['private_permissions']
        self.assertEqual(p['variables'], [
            {'name': 'MV_fpLosslessCursor', 'type': 0, 'initial_value': 0},
            {'name': 'MV_fpLosslessConfirm', 'type': 0, 'initial_value': 0}])
        for field in ('registration_performed', 'grey_render_verified',
                      'runtime_permission_verified', 'repeat_cancellation_proven'):
            self.assertFalse(p[field])
        self.assertFalse(self.manifest['deployable'])
        self.assertFalse(self.manifest['camera_accessed'])
        self.assertIn('PERMISSION_RUNTIME_NOT_PROVEN',
                      {x['code'] for x in self.manifest['unresolved']})

    def test_string_prefix_and_opt_in_candidate_are_deterministic(self):
        self.assertEqual(self.pool[:len(self.fixed_pool)], self.fixed_pool)
        self.assertEqual(len(self.pool), 176300)
        rebuilt = builder.build_candidate(self.source, self.audit, 'native-permission-gated')
        self.assertEqual(rebuilt, (self.page, self.pool, self.manifest))

    def test_new_directory_outputs_only_resource_fragments(self):
        with tempfile.TemporaryDirectory(prefix='fpl-permission-candidate-') as directory:
            target = Path(directory) / 'candidate'
            builder.write_artifacts(target, self.page, self.pool, self.manifest)
            self.assertEqual(sorted(p.name for p in target.iterdir()), sorted(builder.NAMES))
            self.assertEqual((target / builder.NAMES[0]).read_bytes(), self.page)
            with self.assertRaises(builder.CandidateError):
                builder.write_artifacts(target, self.page, self.pool, self.manifest)

    def test_bounded_local_component_namespace_refuses_overflow(self):
        profile = builder.PROFILES['native-permission-gated']
        donor = self.audit.records(self.source, profile['start'], profile['end'])
        pool = builder.PrivatePool(self.source[self.audit.POOL:self.audit.POOL_END])
        mapping = {int(old): new for old, new in self.manifest['new_row']['object_id_map'].items()}
        # Inject only a local component ID in a copy, not the pinned input file.
        altered = []
        for r in donor:
            data = bytearray(r.data)
            if r.offset == 0x216d453:
                struct.pack_into('>I', data, 28, 65535)
            altered.append(self.audit.Record(r.offset, bytes(data)))
        with self.assertRaisesRegex(builder.CandidateError, 'collision or overflow'):
            builder.add_permission_bindings(self.source, self.audit, altered, [], mapping, pool,
                                            builder.load_property_schemas(self.source))

    def test_schema_and_native_template_changes_are_rejected(self):
        profile = builder.PROFILES['native-permission-gated']
        donor = self.audit.records(self.source, profile['start'], profile['end'])
        pool = builder.PrivatePool(self.source[self.audit.POOL:self.audit.POOL_END])
        mapping = {int(old): new for old, new in self.manifest['new_row']['object_id_map'].items()}
        schemas = deepcopy(builder.load_property_schemas(self.source))
        schemas['appVariableEvent'][3]['kind'] = 0  # loses the type-11 flag byte
        with self.assertRaisesRegex(builder.CandidateError, 'native schema'):
            builder.add_permission_bindings(self.source, self.audit, donor, [], mapping, pool, schemas)

    def test_eight_isolated_binary_mutations_are_caught(self):
        cursor, confirm, _ = self.binding_rows
        first = self.clone(0xc216d8ca).offset
        saved = self.clone(0xc216ceb3).offset
        object_budget = self.clone(0xc216d383).offset
        left = self.clone(0xc216d9f2).offset
        mutations = [
            ('cursor-not-P2A', cursor['candidate_offset'] + 36, 0, self.check_bindings),
            ('permission-P2A', confirm['candidate_offset'] + 36, 1, self.check_bindings),
            ('wrong-confirm-target', confirm['candidate_offset'] + 45, 12, self.check_bindings),
            ('unsafe-initial-confirm', first + 36, 1, self.check_initial_confirm_masks),
            ('saved-is-cursor', saved + 40,
             self.records(self.page)[cursor['candidate_offset']].word(40), self.check_saved_value),
            ('lost-component-budget', object_budget + 8, 7, self.check_budgets),
            ('changed-left-key', left + 36, 0x27, self.check_navigation),
            ('wrong-property-name', confirm['candidate_offset'] + 49,
             self.records(self.page)[cursor['candidate_offset']].word(49), self.check_bindings),
        ]
        for name, offset, value, checker in mutations:
            with self.subTest(mutation=name):
                checker(self.page)
                changed = bytearray(self.page)
                struct.pack_into('>I', changed, offset, value)
                with self.assertRaises(AssertionError):
                    checker(bytes(changed))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--seg0', type=Path, default=SEG0)
    parser.add_argument('--audit-module', type=Path, default=AUDIT_MODULE)
    args, rest = parser.parse_known_args()
    SEG0, AUDIT_MODULE = args.seg0, args.audit_module
    unittest.main(argv=[sys.argv[0]] + rest)
