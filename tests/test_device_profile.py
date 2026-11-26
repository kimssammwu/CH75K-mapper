import unittest

from device_profile import compare_profile, decode_profile
from protocol import ProtocolError
from settings import BUTTONS, MACRO_END, PROFILE_PREFIX, TABLE, parse_profile, plan_changes
from test_settings_link import BASE, factory_image


class DeviceProfileTests(unittest.TestCase):
    def setUp(self):
        self.image = factory_image()[BASE:MACRO_END]

    def write_editor_profile(self, profile):
        for change in plan_changes(BASE, self.image, parse_profile(profile)):
            offset = change.address - BASE
            self.image[offset:offset + len(change.after)] = change.after

    def test_reads_factory_shortcuts_and_compares_semantics(self):
        result = decode_profile(BASE, self.image)
        self.assertFalse(result["unsupported"])
        self.assertEqual(result["profile"]["keys"]["top-left"], "Enter")
        self.assertEqual(result["profile"]["keys"]["bottom-center"], {
            "type": "macro", "sequence": "Ctrl+C", "repeat": "count", "count": 1,
        })
        editor = {"keys": {
            "top-left": "Return", "top-center": "Super+D", "top-right": "ctrl+a",
            "bottom-left": "lctrl+x", "bottom-center": "ctrl+c@48", "bottom-right": "Ctrl+V",
        }}
        self.assertFalse(compare_profile(BASE, self.image, editor)["differences"])

    def test_explicit_defaults_compare_equal_to_factory_macros(self):
        editor = {"keys": {button: {"type": "default"} for button in BUTTONS}}
        self.assertEqual(compare_profile(BASE, self.image, editor)["differences"], [])
        self.image[BUTTONS["bottom-right"]] = 0x68
        self.assertEqual(compare_profile(BASE, self.image, editor)["changes"], {
            "keys": {"bottom-right": {"type": "default"}},
        })

    def test_default_rejects_additional_fields(self):
        with self.assertRaisesRegex(ValueError, "bottom-center"):
            parse_profile({"keys": {"bottom-center": {"type": "default", "action": "F13"}}})

    def test_absent_editor_keys_can_be_loaded_without_writing_a_default(self):
        result = compare_profile(BASE, self.image, {"keys": {"top-left": "Escape"}})
        self.assertEqual(result["differences"][0], {
            "button": "top-left", "device": "Enter", "editor": "Escape",
        })
        self.assertEqual(len(result["differences"]), 6)
        self.assertTrue(all(row["editor"] is None for row in result["differences"][1:]))
        self.assertEqual(result["changes"], {"keys": {"top-left": "Escape"}})
        self.assertEqual(self.image, factory_image()[BASE:MACRO_END])

    def test_decodes_mouse_events_waits_and_all_repeat_modes(self):
        editor = {"keys": {
            "top-left": {"type": "macro", "sequence": "Ctrl+C@1600, @120, MouseLeft, MouseWheel(-2)",
                         "repeat": "count", "count": 65535},
            "top-center": {"type": "macro", "sequence": "MouseMove(-22 6), MouseRight@90",
                           "repeat": "hold"},
            "top-right": {"type": "macro", "sequence": "MouseMiddle, @8, F13@16", "repeat": "toggle"},
            "bottom-left": {"type": "media", "action": "play-pause"},
            "bottom-center": {"type": "system", "action": "sleep"},
            "bottom-right": "KeypadPlus",
        }}
        self.write_editor_profile(editor)
        result = compare_profile(BASE, self.image, editor)
        self.assertEqual(result["unsupported"], {})
        self.assertEqual(result["differences"], [])
        self.assertEqual(result["changes"], {"keys": {}})
        imported = result["profile"]["keys"]
        self.assertEqual(imported["top-left"], editor["keys"]["top-left"])
        self.assertEqual(imported["top-center"]["sequence"], "MouseMove(-22 6), MouseRight@88")
        self.assertEqual(imported["top-right"], editor["keys"]["top-right"])
        self.assertEqual(imported["bottom-left"], editor["keys"]["bottom-left"])
        self.assertEqual(imported["bottom-center"], editor["keys"]["bottom-center"])

    def test_unrecognized_assignments_are_imported_only_when_representable(self):
        self.image[BUTTONS["top-center"]] = 0xB3
        self.image[PROFILE_PREFIX + 3] = 0xF8
        editor = {"keys": {"top-center": "Esc", "bottom-left": "A", "top-left": "F15"}}
        result = compare_profile(BASE, self.image, editor)
        self.assertEqual(set(result["unsupported"]), {"top-center", "bottom-left"})
        self.assertIn("0xB3", result["unsupported"]["top-center"])
        self.assertNotIn("top-center", result["profile"]["keys"])
        self.assertNotIn("bottom-left", result["profile"]["keys"])
        self.assertIn({"button": "top-center", "editor": "Esc", "device": None}, result["differences"])
        self.assertEqual(result["changes"], {"keys": editor["keys"]})

    def test_replacing_an_unreadable_macro_preserves_its_old_slot(self):
        # The opcode lengths are known, but this vendor-specific press timing
        # cannot round-trip through the editor.
        self.image[PROFILE_PREFIX + 2] = 0x84
        original = bytes(self.image[PROFILE_PREFIX:PROFILE_PREFIX + 16])
        editor = {"keys": {"bottom-left": "Ctrl+F1"}}
        result = compare_profile(BASE, self.image, editor)
        self.assertIn("bottom-left", result["unsupported"])
        for change in plan_changes(
            BASE, self.image, parse_profile(result["changes"]),
            preserve_slots_for=frozenset(result["unsupported"]),
        ):
            offset = change.address - BASE
            self.image[offset:offset + len(change.after)] = change.after
        self.assertEqual(self.image[PROFILE_PREFIX:PROFILE_PREFIX + 16], original)
        self.assertEqual(compare_profile(BASE, self.image, editor)["changes"], {"keys": {}})

    def test_an_unrecognized_slot_table_preserves_direct_assignments(self):
        self.image[TABLE] = 0xFF
        result = decode_profile(BASE, self.image)
        self.assertEqual(result["profile"], {"keys": {"top-left": "Enter"}})
        self.assertEqual(len(result["unsupported"]), 5)
        self.image[0x52] = 0
        with self.assertRaises(ProtocolError):
            decode_profile(BASE, self.image)

    def test_invalid_explicit_editor_mapping_is_reported(self):
        with self.assertRaisesRegex(ValueError, "bottom-center"):
            compare_profile(BASE, self.image, {"keys": {"bottom-center": "Ctrl++C"}})


if __name__ == "__main__":
    unittest.main()
