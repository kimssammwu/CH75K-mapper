import ctypes
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from hid_common import COMPANION_PAGE, CONFIG_PAGE, HidrawError
import macos_hid


CONFIG = bytes.fromhex("06 01 ff 09 01 a1 01 85 07 75 08 95 07 b1 02 c0")
COMPANION = bytes.fromhex("06 00 ff 09 01 a1 01 85 08 75 08 95 04 81 02 c0")
FOREIGN = bytes.fromhex("05 01 09 06 a1 01 c0")


class IOKitTransportTests(unittest.TestCase):
    def setUp(self):
        self.api = Mock()
        self.api.IORegistryEntryIDMatching.return_value = 34
        self.api.IOServiceGetMatchingService.return_value = 43
        self.api.IOHIDDeviceCreate.return_value = 52
        self.api.IOHIDDeviceOpen.return_value = 0
        self.api.IOHIDDeviceSetReport.return_value = 0
        self.device = macos_hid.Device(
            Path("iokit-0000000000000042-ff01-0001"), 0x30FA, 0x2350, "CH75K",
            CONFIG_PAGE, 1, {7: 8}, Path("iokit-usb-0000000000000066"), CONFIG, 66,
        )
        self.enterContext(patch.object(macos_hid, "_api", return_value=self.api))
        self.fingerprint = self.enterContext(
            patch.object(macos_hid, "_devices_at_interface", return_value=[self.device])
        )
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(macos_hid, "gettempdir", return_value=self.directory))

    def test_report_id_and_payload_use_the_native_numbered_format(self):
        short = b"\x07\x18\x04\x00\x00\x00\x00\x00"
        long = short + b"\x00" * 6 + b"\xe0\x00"

        def get_report(handle, report_type, report_id, buffer, returned):
            self.assertEqual((handle, report_type, report_id), (52, 2, 7))
            self.assertEqual(buffer[0], 0)
            payload = long if len(buffer) == 16 else short
            ctypes.memmove(buffer, payload, len(payload))
            ctypes.cast(returned, ctypes.POINTER(ctypes.c_long))[0] = len(payload)
            return 0

        self.api.IOHIDDeviceGetReport.side_effect = get_report
        with macos_hid.Hidraw(self.device) as hid:
            self.assertEqual(self.api.IOHIDDeviceOpen.call_args.args, (52, 0))
            hid.set_feature(short)
            self.assertEqual(hid.get_feature(7, 8), short)
            self.assertEqual(hid.get_feature_extended(7, 16), long)
            with self.assertRaises(HidrawError):
                hid.get_feature(8, 8)
            with self.assertRaises(HidrawError):
                hid.set_feature(b"\x07" + b"\0" * 15)
        handle, report_type, report_id, buffer, size = self.api.IOHIDDeviceSetReport.call_args.args
        self.assertEqual((handle, report_type, report_id, bytes(buffer), size), (52, 2, 7, short, 8))
        self.api.IOHIDDeviceClose.assert_called_once_with(52, 0)
        self.api.CFRelease.assert_called_once_with(52)
        self.api.IOObjectRelease.assert_called_once_with(43)

    def test_mismatched_device_and_missing_service_cannot_be_opened(self):
        self.fingerprint.return_value = [replace(self.device, usb_path=Path("different-port"))]
        with self.assertRaises(HidrawError), macos_hid.Hidraw(self.device):
            self.fail("Mismatched device opened")
        self.api.IOHIDDeviceOpen.assert_not_called()
        self.api.CFRelease.assert_called_once_with(52)
        self.api.IOObjectRelease.assert_called_once_with(43)
        self.api.IOServiceGetMatchingService.return_value = 0
        with self.assertRaises(HidrawError), macos_hid.Hidraw(self.device):
            self.fail("Removed device opened")

    def test_extended_read_can_return_short_but_rejects_wrong_ids_and_overruns(self):
        def get_report(_handle, _type, _id, buffer, returned):
            buffer[0] = self.reply_id
            ctypes.cast(returned, ctypes.POINTER(ctypes.c_long))[0] = self.reply_length
            return 0

        self.api.IOHIDDeviceGetReport.side_effect = get_report
        with macos_hid.Hidraw(self.device) as hid:
            self.reply_id, self.reply_length = 7, 8
            self.assertEqual(hid.get_feature_extended(7, 16), b"\x07" + b"\0" * 7)
            for self.reply_id, self.reply_length in ((6, 8), (7, 17), (7, 0)):
                with self.subTest(reply_id=self.reply_id, reply_length=self.reply_length):
                    with self.assertRaises(HidrawError):
                        hid.get_feature_extended(7, 16)
            self.reply_id, self.reply_length = 7, 7
            with self.assertRaises(HidrawError):
                hid.get_feature(7, 8)

    def test_configuration_lock_prevents_concurrent_transactions(self):
        with macos_hid.Hidraw(self.device):
            with self.assertRaises(HidrawError), macos_hid.Hidraw(self.device):
                self.fail("Two simultaneous configuration locks")
        with macos_hid.Hidraw(self.device):
            self.assertEqual(self.api.IOHIDDeviceOpen.call_count, 2)


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.api = Mock()
        self.api.CFDataGetTypeID.return_value = 12
        self.api.CFGetTypeID.return_value = 12
        self.api.number_value.side_effect = lambda value: value
        self.api.text_value.side_effect = lambda value: value
        self.data = {
            101: (0x30FA, 0x2350, "USB", CONFIG, 302),
            102: (0x30FA, 0x2350, "USB", COMPANION, 302),
            103: (0x30FA, 0x2350, "USB", FOREIGN, 302),
            104: (0x30FA, 0x2350, "Bluetooth", CONFIG, 305),
            105: (0xAAAA, 0xBBBB, "USB", CONFIG, 305),
            106: (0x30FA, 0x2350, "USB", CONFIG, 306),
        }
        self.buffers = {device: ctypes.create_string_buffer(entry[3]) for device, entry in self.data.items()}
        self.api.CFDataGetLength.side_effect = lambda device: len(self.data[device][3])
        self.api.CFDataGetBytePtr.side_effect = lambda device: self.buffers[device]
        self.api.IOHIDDeviceGetService.side_effect = lambda device: device + 1000
        self.api.entry_id.side_effect = lambda service: service + 10000

        def get_property(device, name):
            vid, pid, transport, _, _ = self.data[device]
            return {"VendorID": vid, "ProductID": pid, "Transport": transport,
                    "ReportDescriptor": device, "Product": "CH75K"}[name]

        self.api.property.side_effect = get_property
        self.enterContext(patch.object(macos_hid, "_api", return_value=self.api))
        self.enterContext(patch.object(macos_hid, "_usb_parent",
                                      side_effect=lambda _api, service, _vid, _pid: self.data[service-1000][4]))

    def test_usb_descriptor_discovery_and_companion_grouping(self):
        self.api.IOHIDManagerCopyDevices.side_effect = [985, None]
        self.api.CFSetGetCount.return_value = len(self.data)

        def set_values(_found, entries):
            for index, value in enumerate(self.data):
                entries[index] = value

        self.api.CFSetGetValues.side_effect = set_values
        config, companion, other = macos_hid.discover()
        self.assertEqual((config.usage_page, config.feature_sizes), (CONFIG_PAGE, {7: 8}))
        self.assertEqual((companion.usage_page, companion.feature_sizes), (COMPANION_PAGE, {}))
        self.assertEqual(config.usb_path, companion.usb_path)
        self.assertNotEqual(config.usb_path, other.usb_path)
        self.assertEqual(self.api.IOHIDManagerSetDeviceMatching.call_count, 2)
        self.api.IOHIDDeviceOpen.assert_not_called()


class RegistryAncestryTests(unittest.TestCase):
    def test_usb_parent_uses_the_physical_registry_and_rechecks_usb_ids(self):
        api = Mock()
        api.IOObjectConformsTo.side_effect = lambda entry, name: (
            entry == 63 and name == b"IOUSBHostDevice"
        )
        api.entry_id.return_value = 1042
        api.string.side_effect = lambda key: key
        api.number_value.side_effect = lambda value: value
        properties = {"idVendor": 0x30FA, "idProduct": 0x2350}
        api.IORegistryEntryCreateCFProperty.side_effect = lambda entry, key, *_: properties[key]

        def parent(entry, plane, address):
            self.assertEqual(plane, b"IOService")
            ctypes.cast(address, ctypes.POINTER(ctypes.c_uint32))[0] = {61: 62, 62: 63}[entry]
            return 0

        api.IORegistryEntryGetParentEntry.side_effect = parent
        self.assertEqual(macos_hid._usb_parent(api, 61, 0x30FA, 0x2350), 1042)
        self.assertEqual([call.args for call in api.IOObjectRelease.call_args_list], [(62,), (63,)])
        properties["idProduct"] = 0xFFFF
        self.assertEqual(macos_hid._usb_parent(api, 61, 0x30FA, 0x2350), 0)


if __name__ == "__main__":
    unittest.main()
