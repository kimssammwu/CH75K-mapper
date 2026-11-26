import ctypes
from pathlib import Path
import unittest
from unittest.mock import Mock, call, patch

from hid_common import COMPANION_PAGE, CONFIG_PAGE, HidrawError
import windows_hid as hid


def _pointee(pointer, structure):
    return ctypes.cast(pointer, ctypes.POINTER(structure)).contents


class WindowsHidTests(unittest.TestCase):
    def setUp(self):
        self.api = Mock()
        self.enterContext(patch.object(hid, "_api", return_value=self.api))
        self.error = 0
        self.api.last_error.side_effect = lambda: self.error
        self.api.SetupDiGetClassDevsW.return_value = 200
        self.api.SetupDiEnumDeviceInterfaces.side_effect = self.enumerate_interface
        self.api.SetupDiGetDeviceInterfaceDetailW.side_effect = self.interface_detail
        self.api.CM_Get_Device_IDW.side_effect = self.device_id
        self.api.CM_Get_Parent.side_effect = self.parent_node
        self.api.open_interface.side_effect = self.open_interface
        self.api.HidD_GetAttributes.side_effect = self.attributes
        self.api.HidD_GetPreparsedData.side_effect = self.preparsed
        self.api.HidP_GetCaps.side_effect = self.capabilities
        self.api.HidP_InitializeReportForID.side_effect = self.initialize_report
        self.api.HidD_GetFeature.side_effect = self.read_report
        self.api.HidD_SetFeature.return_value = True
        self.config_path = Path(r"\\?\hid#config")
        self.companion_path = Path(r"\\?\hid#companion")
        self.physical_id = r"USB\VID_30FA&PID_2350\1&AB&0&3"
        self.entries = [self.config_path, self.companion_path, Path(r"\\?\hid#keyboard"),
                        Path(r"\\?\hid#unrelated")]
        self.identities = {
            40: (r"HID\VID_30FA&PID_2350&MI_00&COL01\1", 48),
            41: (r"HID\VID_30FA&PID_2350&MI_00&COL02\1", 48),
            42: (r"HID\VID_30FA&PID_2350&MI_00&COL03\1", 48),
            43: (r"HID\VID_30FA&PID_2350\1", 50),
            48: (r"USB\VID_30FA&PID_2350&MI_00\1&AB&0&0000", 49),
            49: (self.physical_id, None),
            50: (r"BTHENUM\VID_30FA&PID_2350\1", None),
        }
        self.caps = {40: (CONFIG_PAGE, 8), 41: (COMPANION_PAGE, 0), 42: (1, 8)}
        self.version = 0x0100
        self.has_report = True
        self.reply = b"\x07\x31" + b"\0" * 6

    def enumerate_interface(self, handle, _, guid, index, data):
        self.assertEqual(handle, 200)
        self.assertEqual(_pointee(data, hid._InterfaceData).size, ctypes.sizeof(hid._InterfaceData))
        if index == len(self.entries):
            self.error = hid._ERROR_NO_MORE_ITEMS
            return False
        _pointee(data, hid._InterfaceData).flags = index
        return True

    def interface_detail(self, handle, interface, data, length, required, info):
        index = _pointee(interface, hid._InterfaceData).flags
        encoded = (str(self.entries[index]) + "\0").encode("utf-16-le")
        if data is None:
            _pointee(required, hid._DWORD).value = len(encoded) + 4
            self.error = hid._ERROR_INSUFFICIENT_BUFFER
            return False
        self.assertEqual(length, len(encoded) + 4)
        self.assertEqual(_pointee(data, hid._DWORD).value, hid._DETAIL_HEADER_SIZE)
        self.assertEqual(_pointee(info, hid._DevInfoData).size, ctypes.sizeof(hid._DevInfoData))
        ctypes.memmove(ctypes.addressof(data) + 4, encoded, len(encoded))
        _pointee(info, hid._DevInfoData).devnode = 40 + index
        return True

    def device_id(self, node, buffer, length, flags):
        buffer.value = self.identities[node][0]
        return 0

    def parent_node(self, parent, node, flags):
        target = self.identities[node][1]
        if target is None:
            return 1
        _pointee(parent, hid._DWORD).value = target
        return 0

    def open_interface(self, path, *, configuration):
        return 40 + self.entries.index(path)

    def attributes(self, handle, data):
        result = _pointee(data, hid._HidAttributes)
        self.assertEqual(result.size, 12)
        result.vid, result.pid, result.version = 0x30FA, 0x2350, self.version
        return True

    def preparsed(self, handle, data):
        _pointee(data, ctypes.c_void_p).value = handle
        return True

    def capabilities(self, preparsed, data):
        result = _pointee(data, hid._HidCaps)
        self.assertEqual(ctypes.sizeof(result), 64)
        result.usage_page, result.feature_length = self.caps[preparsed.value]
        result.usage = 1
        return hid._HIDP_STATUS_SUCCESS

    def initialize_report(self, kind, report_id, preparsed, buffer, size):
        self.assertEqual((kind, report_id, preparsed.value, size), (2, 7, 40, 8))
        buffer[0] = report_id
        return hid._HIDP_STATUS_SUCCESS if self.has_report else 0xC0110010

    def read_report(self, handle, buffer, size):
        self.assertEqual((handle, size, buffer[0]), (40, 8, 7))
        ctypes.memmove(buffer, self.reply, size)
        return True

    def test_discovery_requires_real_usb_vendor_collections_and_report_id(self):
        config, companion = hid.discover()
        self.assertEqual((config.path, config.feature_sizes), (self.config_path, {7: 8}))
        self.assertEqual((companion.path, companion.feature_sizes), (self.companion_path, {}))
        self.assertEqual(config.usb_path, companion.usb_path)
        self.assertEqual(config.usb_path, Path(self.physical_id))
        self.assertEqual(self.api.open_interface.call_args_list, [
            call(path, configuration=False) for path in self.entries[:3]
        ])
        self.assertEqual(self.api.CloseHandle.call_args_list, [call(40), call(41), call(42)])
        self.api.SetupDiDestroyDeviceInfoList.assert_called_once_with(200)
        self.api.HidD_GetFeature.assert_not_called()
        self.api.HidD_SetFeature.assert_not_called()
        self.has_report = False
        self.assertEqual([device.path for device in hid.discover()], [self.companion_path])

    def test_report_lengths_and_ids_are_checked_before_feature_io(self):
        config, companion = hid.discover()
        request = b"\x07\x18\x05" + b"\0" * 5
        with hid.Hidraw(config) as transport:
            self.api.open_interface.assert_called_with(self.config_path, configuration=True)
            transport.set_feature(request)
            handle, report, size = self.api.HidD_SetFeature.call_args.args
            self.assertEqual((handle, bytes(report), size), (40, request, 8))
            self.assertEqual(transport.get_feature(7, 8), self.reply)
            self.assertEqual(transport.get_feature_extended(7, 16), self.reply)
            with self.assertRaises(HidrawError):
                transport.set_feature(b"\x07" + b"\0" * 15)
            with self.assertRaises(HidrawError):
                transport.get_feature(8, 8)
            self.reply = b"\x06" + b"\0" * 7
            with self.assertRaises(HidrawError):
                transport.get_feature(7, 8)
        self.api.CloseHandle.assert_called_with(40)
        with self.assertRaises(HidrawError):
            transport.get_feature(7, 8)
        with hid.Hidraw(companion) as transport:
            self.api.open_interface.assert_called_with(self.companion_path, configuration=False)
            with self.assertRaises(HidrawError):
                transport.set_feature(request)
        self.api.HidD_SetFeature.assert_called_once()

    def test_configuration_rejects_a_removed_or_changed_device(self):
        config, _ = hid.discover()
        self.api.open_interface.reset_mock()
        self.identities[49] = (r"USB\VID_30FA&PID_2350\1&AB&0&4", None)
        with self.assertRaises(HidrawError), hid.Hidraw(config):
            self.fail("Changed USB parent was accepted")
        self.api.open_interface.assert_not_called()

        self.identities[49] = (self.physical_id, None)
        self.version += 1
        with self.assertRaises(HidrawError), hid.Hidraw(config):
            self.fail("Changed HID fingerprint was accepted")
        self.api.CloseHandle.assert_called_with(40)
        self.api.HidD_SetFeature.assert_not_called()

    def test_configuration_uses_an_exclusive_handle(self):
        hid._WindowsHid.open_interface(self.api, self.config_path, configuration=True)
        self.api.CreateFileW.assert_called_once_with(str(self.config_path), 0xC0000000, 0, None, 3, 0, None)


if __name__ == "__main__":
    unittest.main()
