import json
from contextlib import nullcontext
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import ch75k
from actions import parse_action
from protocol import ProtocolError, SettingsLink
from settings import (
    BUTTONS, KEY_MATRIX, KEY_MATRIX_END, MACRO_END, MACRO_TRIGGERS,
    PROFILE_PREFIX, TABLE, encode_macro, parse_profile, plan_changes,
)


BASE = 0xE0


def factory_image():
    flash = bytearray(b"\xFF" * 0x800)
    flash[0xD0:0xD8] = b"\xe1\x1e\xe0\x00\xf0\x0f\0\0"
    flash[BASE + 0x20] = 0x32
    flash[BASE + 0x52] = 0xB1
    values = [0x28, 0xA8, 0xA9, 0xA5, 0xA6, 0xA7]
    for offset, value in zip(BUTTONS.values(), values):
        flash[BASE + offset] = value
    cursor = BASE + PROFILE_PREFIX
    for index in range(20):
        offset = BASE + TABLE + 5 * index
        words = 4 if index < 5 else 0
        flash[offset:offset + 5] = bytes((7, words, cursor & 255, cursor >> 8, 0))
        if words:
            key = (0x1B, 0x06, 0x19, 0x07, 0x04)[index]
            flash[cursor:cursor + 16] = bytes((1, 0, 0x86, 0xE0, 0x86, key,
                                              6, 0xE0, 6, key)) + bytes(6)
            cursor += 16
    return flash


class FakeUSB:
    def __init__(self, source, fail_address=None, metadata_length=16):
        self.flash = bytearray(source)
        self.mode = False
        self.commands = []
        self.staged = {}
        self.reply = None
        self.fail_address = fail_address
        self.metadata_length = metadata_length

    def set_feature(self, packet):
        assert len(packet) == 8
        report, group, op, index, low, high, value, last = packet
        address = low | high << 8
        assert report == 7
        self.commands.append((group, op, address, index, value, last))
        if group == 0x09 and op == 0x41:
            self.mode = True
        elif group == 0x20 and op == 0:
            self.mode = False
        else:
            assert group == 0x18
            if op == 3:
                self.staged[(address, index)] = value
            elif op == 5:
                assert self.staged[(address, index)] == 0
                self.reply = self.flash[address + index]
            elif op == 9:
                assert self.mode
                if self.fail_address == address:
                    self.fail_address = None
                    self.flash[address] = 0x5A
                    raise OSError("simulated partial USB write")
                self.flash[address:address + last + 1] = bytes(
                    self.staged[(address, i)] for i in range(last + 1))
            elif op in (1, 17):
                self.staged.clear()
            else:
                assert op == 0

    def get_feature(self, report, size):
        assert report == 7 and size == 8 and self.reply is not None
        return bytes((report, self.reply)) + bytes(6)

    def get_feature_extended(self, report, size):
        assert report == 7 and size == 16
        return (bytes((report,)) + bytes(13) + BASE.to_bytes(2, "little"))[:self.metadata_length]


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.original = factory_image()
        self.device = SimpleNamespace(
            vid=0x30FA, pid=0x2350, usb_path=Path("/fake/usb/7"), feature_sizes={7: 8})

    def make_changes(self, profile, source=None):
        keys = parse_profile(profile)
        image = self.original if source is None else source
        return plan_changes(BASE, bytes(image[BASE:MACRO_END]), keys)

    def make_apply(self, fake, changes):
        link = SettingsLink(fake, 8, wait=lambda _: None)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"XDG_STATE_HOME": directory}):
            backup = ch75k._persist_backup(self.device, BASE, changes)
            entries = json.loads(backup.read_text())["blocks"]
            self.assertEqual(bytes.fromhex(entries[0]["before"]), changes[0].before)
            # Windows inherits the user profile's ACL and does not expose POSIX mode bits.
            if os.name != "nt":
                self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
            ch75k._write_and_verify(link, BASE, changes, backup)

    def test_golden_keyboard_macro(self):
        payload, count = encode_macro(parse_action("Ctrl+X"))
        self.assertEqual(count, 4)
        self.assertEqual(payload, bytes.fromhex("01 00 86 e0 86 1b 06 1b 06 e0") + bytes(6))

    def test_long_delay_words(self):
        payload, count = encode_macro(parse_action("A@1600, F13"))
        self.assertEqual(payload[:8], bytes.fromhex("01 00 86 04 7f 04 49 00"))
        self.assertEqual(count, 5)

    def test_standalone_waits_preserve_inline_delays(self):
        payload, count = encode_macro(parse_action("@240, A@40, @16, B"))
        self.assertEqual(count, 6)
        self.assertEqual(payload, bytes.fromhex("01 00 1e 00 86 04 05 04 02 00 86 05 06 05 00 00"))
        for expression in ("@", "@-1", "@65536", "@12@16", "A,,B"):
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                parse_action(expression)

    def test_application_and_dial_edits_are_rejected_before_usb(self):
        profiles = (
            {"keys": {"top-left": {"type": "application", "app": "org.gnome.Nautilus.desktop", "host": "a" * 64}}},
            {"keys": {"top-left": "Ctrl+C"}, "dial": {"dial-0": "scroll-up"}},
            {"keys": {"dial": "Ctrl+C"}},
        )
        for profile in profiles:
            with self.subTest(profile=profile), patch.object(ch75k, "select_device") as select:
                with self.assertRaises(ValueError):
                    ch75k.preview_profile(profile)
                with self.assertRaises(ValueError):
                    ch75k.apply_profile(None, profile)
                select.assert_not_called()

    def test_vendor_mouse_event_stream(self):
        steps = parse_action("MouseLeft, MouseRight@80, MouseMiddle, MouseMove(-12 25), MouseWheel(-2)")
        payload, words = encode_macro(steps)
        self.assertEqual(words, 8)
        self.assertEqual(payload, bytes.fromhex(
            "01 00 86 f1 06 f1 86 f3 0a f3 86 f2 06 f2 06 f6 f4 ff 19 00 06 f7 fe 00"))

    def test_repeat_and_direct_mappings_write_vendor_bytes(self):
        profile = {"keys": {
            "top-left": {"type": "macro", "sequence": "A", "repeat": "count", "count": 1000},
            "top-center": {"type": "macro", "sequence": "A", "repeat": "hold"},
            "top-right": {"type": "macro", "sequence": "A", "repeat": "toggle"},
            "bottom-left": {"type": "media", "action": "previous-track"},
            "bottom-center": {"type": "system", "action": "sleep"},
        }}
        fake = FakeUSB(self.original)
        self.make_apply(fake, self.make_changes(profile))
        for position, low_flag, addr_flag, count in (
            ("top-left", 0, 0, 1000), ("top-center", 0x80, 0, 1), ("top-right", 0, 0x80, 1)
        ):
            trigger = fake.flash[BASE + BUTTONS[position]]
            offset = BASE + TABLE + 5 * MACRO_TRIGGERS.index(trigger)
            marker, low, start_low, start_high, high = fake.flash[offset:offset + 5]
            self.assertEqual((marker, low, high), (7, 2 | low_flag, 0))
            self.assertEqual(start_high & 0xC0, addr_flag)
            address = start_low | ((start_high & 0x3F) << 8)
            self.assertEqual(fake.flash[address:address + 8],
                             count.to_bytes(2, "little") + bytes.fromhex("86 04 06 04 00 00"))
        self.assertEqual(fake.flash[BASE + BUTTONS["bottom-left"]], 0xCD)
        self.assertEqual(fake.flash[BASE + BUTTONS["bottom-center"]], 0xB1)
        self.assertEqual(self.make_changes(profile, fake.flash), [])

    def test_recognizes_variable_length_events_in_last_slot(self):
        address = BASE + PROFILE_PREFIX + 5 * 16
        last = BASE + TABLE + 19 * 5
        self.original[BASE + BUTTONS["top-left"]] = MACRO_TRIGGERS[-1]
        self.original[last:last + 5] = bytes((7, 2, address & 255, address >> 8, 0))
        body, _ = encode_macro(parse_action("MouseMove(4 5), MouseWheel(1)"))
        self.original[address:address + len(body)] = body
        profile = {"keys": {"top-left": {"type": "macro", "sequence": "MouseMove(-7 8), MouseWheel(-3)",
                                        "repeat": "toggle"}}}
        fake = FakeUSB(self.original)
        self.make_apply(fake, self.make_changes(profile))
        self.assertEqual(fake.flash[last + 3], (address >> 8) | 0x80)
        self.assertEqual(fake.flash[address:address + 16],
                         bytes.fromhex("01 00 06 f6 f9 ff 08 00 06 f7 fd 00 00 00 00 00"))
        self.assertEqual(fake.flash[BASE + BUTTONS["top-left"]], MACRO_TRIGGERS[-1])

    def test_rejects_invalid_modes_and_fixed_button(self):
        binding = {"type": "macro", "sequence": "Enter", "repeat": "count"}
        for extra in ({}, {"count": True}, {"count": 65536}, {"repeat": "toggle", "count": 1}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                parse_profile({"keys": {"top-left": binding | extra}})
        with self.assertRaises(ValueError):
            parse_profile({"keys": {"screen-toggle": "Enter"}})

    def test_key_edit_does_not_touch_fixed_controls(self):
        profile = {"keys": {"bottom-center": "F13"}}
        changes = self.make_changes(profile)
        self.assertEqual(changes, self.make_changes(profile | {"dial": {}}))
        self.assertEqual([change.address for change in changes], [BASE + 0xE0])
        self.assertEqual(changes[0].after[2], 0x68)
        self.assertEqual(changes[0].before[:2], changes[0].after[:2])
        self.assertEqual(changes[0].before[3:], changes[0].after[3:])
        fake = FakeUSB(self.original)
        self.make_apply(fake, changes)
        self.assertEqual(fake.flash[BASE + 0x20], self.original[BASE + 0x20])
        self.assertEqual(fake.flash[BASE + 0x52], self.original[BASE + 0x52])

    def test_verify_family_fingerprint(self):
        self.original[BASE + KEY_MATRIX + 1] = 0x1A
        with self.assertRaises(ProtocolError):
            self.make_changes({"keys": {"top-right": "Enter"}})

    def test_macro_reuse_without_other_changes(self):
        changes = self.make_changes({"keys": {"bottom-left": "Ctrl+K"}})
        self.assertEqual([change.address for change in changes],
                         [BASE + PROFILE_PREFIX, BASE + PROFILE_PREFIX + 8])
        fake = FakeUSB(self.original)
        self.make_apply(fake, changes)
        self.assertEqual(fake.flash[BASE + 0x62], MACRO_TRIGGERS[0])
        self.assertEqual(fake.commands[-1][:2], (0x20, 0))
        self.assertEqual(self.make_changes({"keys": {"bottom-left": "Ctrl+K"}}, fake.flash), [])

    def test_macro_allocates_before_key_binding(self):
        changes = self.make_changes({"keys": {"top-left": "Ctrl+K, Ctrl+C@200"}})
        writes = [change.address for change in changes]
        self.assertGreater(writes[0], BASE + TABLE)
        self.assertTrue(all(BASE + TABLE <= item < BASE + PROFILE_PREFIX for item in writes[3:-1]))
        self.assertEqual(writes[-1], BASE + 0x70)
        fake = FakeUSB(self.original, metadata_length=8)
        self.make_apply(fake, changes)
        self.assertEqual(fake.flash[BASE + 0x70], MACRO_TRIGGERS[5])
        self.assertEqual(fake.flash[0xD0:0xD8], self.original[0xD0:0xD8])
        self.assertEqual(fake.flash[:BASE], self.original[:BASE])
        self.assertEqual(self.make_changes({"keys": {"top-left": "Ctrl+K, Ctrl+C@200"}}, fake.flash), [])

    def test_no_overwrite_of_unrecognized_tail(self):
        self.original[BASE + PROFILE_PREFIX + 5 * 16 + 1] = 0xCD
        with self.assertRaises(ProtocolError):
            self.make_changes({"keys": {"top-left": "Ctrl+X"}})

    def test_partial_failure_is_rolled_back(self):
        changes = self.make_changes({"keys": {"top-left": "Ctrl+X"}})
        fake = FakeUSB(self.original, fail_address=BASE + 0x70)
        with self.assertRaisesRegex(ProtocolError, "기존 설정을 복구"):
            self.make_apply(fake, changes)
        self.assertEqual(fake.flash, self.original)
        self.assertFalse(fake.mode)

    def test_snapshot_does_not_commit(self):
        fake = FakeUSB(self.original)
        link = SettingsLink(fake, 8, wait=lambda _: None)
        base, snapshot = ch75k._snapshot(link, False)
        self.assertEqual(base, BASE)
        self.assertEqual(snapshot, self.original[BASE:BASE + PROFILE_PREFIX])
        self.assertFalse(any(command[:2] in {(0x18, 9), (0x09, 0x41)} for command in fake.commands))

    def test_transport_pacing_scales_waits_and_settles_after_save(self):
        waits = []
        SettingsLink(FakeUSB(self.original), 8, wait=waits.append).finish_configuration()
        self.assertEqual(waits, [0.010])
        fake = FakeUSB(self.original)
        fake.wait_scale, fake.save_settle = 5, 0.5
        waits.clear()
        SettingsLink(fake, 8, wait=waits.append).finish_configuration()
        self.assertEqual([round(value, 6) for value in waits], [0.05, 0.5])

    def test_full_apply_callback_and_readback(self):
        fake = FakeUSB(self.original)
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"XDG_STATE_HOME": directory}),
            patch.object(ch75k, "select_device", return_value=self.device),
            patch.object(ch75k, "Hidraw", return_value=nullcontext(fake)),
            patch.object(ch75k, "SettingsLink", side_effect=lambda h, n: SettingsLink(h, n, lambda _: None)),
        ):
            result = ch75k.apply_profile("/dev/hidraw7", {
                "keys": {"top-center": "F16", "bottom-center": "Ctrl+C, F13@100"},
            })
            self.assertIn("USB 설정 블록을 다시 읽어 확인", result)
            report = ch75k.read_profile("/dev/hidraw7")
            self.assertIn("top-center  기기 키 코드 0x6B", report)
            self.assertIn("bottom-center  매크로 슬롯 2", report)
            self.assertEqual(fake.flash[BASE + TABLE + 5 + 1], 6)
            self.assertNotIn("dial-1", report)


if __name__ == "__main__":
    unittest.main()
