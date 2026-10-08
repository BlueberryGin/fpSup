import struct
import unittest

from build_observer import initial_state, NONCE
from observer_decode import decode_snapshot, SOURCES


def fixture(source=1, count=1, capacity=2):
    state = bytearray(initial_state(capacity))
    struct.pack_into("<I", state, 24, count)
    struct.pack_into("<I", state, 28, int(count == capacity))
    records = bytearray(capacity * 256)
    for index in range(count):
        base, size = index * 256, SOURCES[source][1]
        struct.pack_into("<12I", records, base, 0x31524E46, 1, source, index + 1, NONCE,
                         0x100000, 0xABC, 0xA80F0000, size, 0, 0xFFFFFFFC, 9)
        records[base + 64:base + 64 + size] = bytes((i * 3) % 256 for i in range(size))
        struct.pack_into("<I", records, base + 252, index + 1)
    return bytes(state), bytes(records)


class ObserverDecodeTests(unittest.TestCase):
    def test_four_sources_preserve_exact_raw_payloads_and_unknowns(self):
        for source in SOURCES:
            state, records = fixture(source, count=2)
            decoded = decode_snapshot(state, records, source_id=source)
            self.assertEqual(decoded["count"], 2)
            self.assertFalse(decoded["geometry_eligible"])
            self.assertFalse(decoded["source_ordering_verified"])
            for i, row in enumerate(decoded["records"]):
                self.assertEqual(bytes.fromhex(row["bytes_hex"]), records[i * 256:(i + 1) * 256])
                self.assertIsNone(row["source_time_ms"])
                self.assertIsNone(row["episode_generation"])
                self.assertFalse(row["confirmation_authorized"])
                self.assertFalse(row["motion_authorized"])
                if source in (3, 4):
                    self.assertFalse(row["subject"]["geometry_eligible"])
            if source == 1:
                self.assertEqual(decoded["records"][0]["af_raw"]["manager_position"], -4)

    def test_active_or_busy_snapshot_rejected(self):
        state, records = fixture()
        for offset in (20, 32):
            data = bytearray(state)
            struct.pack_into("<I", data, offset, 1)
            with self.assertRaisesRegex(ValueError, "disabled producer"):
                decode_snapshot(bytes(data), records, source_id=1)

    def test_commit_identity_nonce_length_and_padding_tamper(self):
        state, records = fixture()
        for offset in (0, 4, 8, 12, 16, 32, 48, 240, 252):
            data = bytearray(records)
            data[offset] ^= 0x80
            with self.assertRaises(ValueError):
                decode_snapshot(state, bytes(data), source_id=1)

    def test_count_capacity_and_header_tamper(self):
        state, records = fixture()
        for offset, value in ((0, 0), (4, 2), (8, 128), (12, 0), (12, 1025),
                              (16, 0), (24, 3), (28, 1), (28, 2), (36, 2)):
            data = bytearray(state)
            struct.pack_into("<I", data, offset, value)
            with self.assertRaises(ValueError):
                decode_snapshot(bytes(data), records, source_id=1)

    def test_loss_preserved_without_inventing_missing_records(self):
        state, records = fixture(count=2)
        data = bytearray(state)
        struct.pack_into("<I", data, 28, 1)
        struct.pack_into("<I", data, 36, 1)
        decoded = decode_snapshot(bytes(data), records, source_id=1)
        self.assertTrue(decoded["full"])
        self.assertTrue(decoded["loss_observed"])
        self.assertEqual(len(decoded["records"]), 2)

    def test_empty_snapshot_no_fabricated_sample(self):
        state, records = fixture(count=0)
        self.assertEqual(decode_snapshot(state, records, source_id=1)["records"], [])

    def test_full_capacity_requires_consistent_full_flag(self):
        state, records = fixture(count=2)
        state = bytearray(state)
        struct.pack_into("<I", state, 28, 0)
        with self.assertRaisesRegex(ValueError, "full/loss"):
            decode_snapshot(bytes(state), records, source_id=1)

    def test_truncation_type_and_wrong_source(self):
        state, records = fixture()
        with self.assertRaises(TypeError):
            decode_snapshot(bytearray(state), records, source_id=1)
        for bad in (True, 0, 5):
            with self.assertRaises(ValueError):
                decode_snapshot(state, records, source_id=bad)
        for left, right in ((state[:-1], records), (state, records[:-1]), (state, records + b"x")):
            with self.assertRaises(ValueError):
                decode_snapshot(left, right, source_id=1)
        with self.assertRaises(ValueError):
            decode_snapshot(state, records, source_id=2)


if __name__ == "__main__":
    unittest.main()
