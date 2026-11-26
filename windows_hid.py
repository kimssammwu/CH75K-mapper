"""CH75K USB discovery and feature reports through the Windows HID stack."""

from contextlib import ExitStack, closing
import ctypes as C
from dataclasses import dataclass, field
from functools import cache
from itertools import count
from pathlib import Path
import sys

from hid_common import CONFIG_PAGE, USB_IDS, VENDOR_COLLECTIONS, HidrawError
from protocol import REPORT_ID


_DWORD = C.c_uint32
_HANDLE = C.c_void_p
_USHORT = C.c_uint16
_INVALID_HANDLE = C.c_void_p(-1).value
_PRESENT_INTERFACES = 0x02 | 0x10
_ERROR_INSUFFICIENT_BUFFER = 122
_ERROR_NO_MORE_ITEMS = 259
_ERROR_SHARING_VIOLATION = 32
_GENERIC_READ_WRITE = 0x80000000 | 0x40000000
_FILE_SHARE_READ_WRITE = 0x01 | 0x02
_OPEN_EXISTING = 3
_DETAIL_HEADER_SIZE = 8 if C.sizeof(C.c_void_p) == 8 else 6
_MAX_DEVICE_ID_LEN = 200
_HIDP_STATUS_SUCCESS = 0x00110000
_HIDP_FEATURE = 2
_USB_DEVICE_IDS = {f"VID_{vid:04X}&PID_{pid:04X}": (vid, pid) for vid, pid in USB_IDS}


class _Guid(C.Structure):
    _fields_ = [("data1", _DWORD), ("data2", _USHORT), ("data3", _USHORT),
                ("data4", C.c_ubyte * 8)]


class _InterfaceData(C.Structure):
    _fields_ = [("size", _DWORD), ("guid", _Guid), ("flags", _DWORD), ("reserved", C.c_size_t)]


class _DevInfoData(C.Structure):
    _fields_ = [("size", _DWORD), ("guid", _Guid), ("devnode", _DWORD), ("reserved", C.c_size_t)]


class _HidAttributes(C.Structure):
    _fields_ = [("size", _DWORD), ("vid", _USHORT), ("pid", _USHORT), ("version", _USHORT)]


class _HidCaps(C.Structure):
    # The nine capability counts and the link-collection count are not needed here.
    _fields_ = [("usage", _USHORT), ("usage_page", _USHORT), ("input_length", _USHORT),
                ("output_length", _USHORT), ("feature_length", _USHORT),
                ("reserved", _USHORT * 17), ("capability_counts", _USHORT * 10)]


@dataclass(frozen=True)
class Device:
    path: Path
    vid: int
    pid: int
    name: str = field(compare=False)
    usage_page: int
    usage: int
    feature_sizes: dict[int, int]
    usb_path: Path
    _devnode: int = field(repr=False)
    _usb_devnode: int = field(repr=False)
    _version: int = field(repr=False)


class _WindowsHid:
    def __init__(self):
        libraries = (
            ("hid.dll", (
                ("HidD_GetHidGuid", None, (C.POINTER(_Guid),)),
                ("HidD_GetAttributes", C.c_ubyte, (_HANDLE, C.POINTER(_HidAttributes))),
                ("HidD_GetProductString", C.c_ubyte, (_HANDLE, C.c_void_p, _DWORD)),
                ("HidD_GetPreparsedData", C.c_ubyte, (_HANDLE, C.POINTER(C.c_void_p))),
                ("HidD_FreePreparsedData", C.c_ubyte, (C.c_void_p,)),
                ("HidD_GetFeature", C.c_ubyte, (_HANDLE, C.c_void_p, _DWORD)),
                ("HidD_SetFeature", C.c_ubyte, (_HANDLE, C.c_void_p, _DWORD)),
                ("HidP_GetCaps", C.c_int32, (C.c_void_p, C.POINTER(_HidCaps))),
                ("HidP_InitializeReportForID", C.c_int32,
                 (C.c_int32, C.c_ubyte, C.c_void_p, C.c_void_p, _DWORD)),
            )),
            ("setupapi.dll", (
                ("SetupDiGetClassDevsW", _HANDLE, (C.POINTER(_Guid), C.c_wchar_p, _HANDLE, _DWORD)),
                ("SetupDiEnumDeviceInterfaces", C.c_int32,
                 (_HANDLE, C.c_void_p, C.POINTER(_Guid), _DWORD, C.POINTER(_InterfaceData))),
                ("SetupDiGetDeviceInterfaceDetailW", C.c_int32, (
                    _HANDLE, C.POINTER(_InterfaceData), C.c_void_p, _DWORD,
                    C.POINTER(_DWORD), C.POINTER(_DevInfoData),
                )),
                ("SetupDiDestroyDeviceInfoList", C.c_int32, (_HANDLE,)),
            )),
            ("cfgmgr32.dll", (
                ("CM_Get_Parent", _DWORD, (C.POINTER(_DWORD), _DWORD, _DWORD)),
                ("CM_Get_Device_IDW", _DWORD, (_DWORD, C.c_wchar_p, _DWORD, _DWORD)),
            )),
            ("kernel32.dll", (
                ("CreateFileW", _HANDLE, (C.c_wchar_p, _DWORD, _DWORD, C.c_void_p, _DWORD, _DWORD, _HANDLE)),
                ("CloseHandle", C.c_int32, (_HANDLE,)),
            )),
        )
        for filename, declarations in libraries:
            # LOAD_LIBRARY_SEARCH_SYSTEM32 prevents loading DLLs beside the portable executable.
            library = C.WinDLL(filename, use_last_error=True, winmode=0x0800)
            for name, result, arguments in declarations:
                function = getattr(library, name)
                function.restype = result
                function.argtypes = arguments
                setattr(self, name, function)

    @staticmethod
    def last_error() -> int:
        return C.get_last_error()

    def open_interface(self, path: Path, *, configuration: bool):
        # A zero-access handle can query attributes even on a keyboard collection.
        # The vendor configuration handle is exclusive during a settings transaction.
        access = _GENERIC_READ_WRITE if configuration else 0
        sharing = 0 if configuration else _FILE_SHARE_READ_WRITE
        return self.CreateFileW(str(path), access, sharing, None, _OPEN_EXISTING, 0, None)


@cache
def _api() -> _WindowsHid:
    if sys.platform != "win32":
        raise HidrawError("Windows HID is available only on Windows.")
    try:
        return _WindowsHid()
    except (AttributeError, OSError, ValueError) as exc:
        raise HidrawError("Windows HID 또는 SetupAPI를 불러올 수 없습니다.") from exc


def _interfaces(api: _WindowsHid):
    guid = _Guid()
    api.HidD_GetHidGuid(C.byref(guid))
    found = api.SetupDiGetClassDevsW(C.byref(guid), None, None, _PRESENT_INTERFACES)
    if found == _INVALID_HANDLE:
        raise HidrawError(f"Windows HID 장치 목록을 열 수 없습니다 (Win32 {api.last_error()}).")
    try:
        for index in count():
            interface = _InterfaceData(size=C.sizeof(_InterfaceData))
            if not api.SetupDiEnumDeviceInterfaces(found, None, C.byref(guid), index, C.byref(interface)):
                code = api.last_error()
                if code != _ERROR_NO_MORE_ITEMS:
                    raise HidrawError(f"Windows HID 장치 목록을 읽을 수 없습니다 (Win32 {code}).")
                break
            required = _DWORD()
            api.SetupDiGetDeviceInterfaceDetailW(found, C.byref(interface), None, 0, C.byref(required), None)
            if (api.last_error() != _ERROR_INSUFFICIENT_BUFFER
                    or not _DETAIL_HEADER_SIZE <= required.value <= 65_536
                    or required.value % 2):
                continue
            detail = (C.c_ubyte * required.value)()
            C.cast(detail, C.POINTER(_DWORD))[0] = _DETAIL_HEADER_SIZE
            device = _DevInfoData(size=C.sizeof(_DevInfoData))
            if api.SetupDiGetDeviceInterfaceDetailW(found, C.byref(interface), detail,
                                                    len(detail), None, C.byref(device)):
                # DevicePath starts immediately after cbSize, even with x64 tail padding.
                path, terminator, _ = bytes(detail[4:]).decode("utf-16-le", "surrogatepass").partition("\0")
                if path and terminator:
                    yield Path(path), device.devnode
    finally:
        api.SetupDiDestroyDeviceInfoList(found)


def _usb_parent(api: _WindowsHid, devnode: int) -> tuple[int, Path, int, int] | None:
    """Group vendor collections by their actual non-interface USB parent."""
    instance = C.create_unicode_buffer(_MAX_DEVICE_ID_LEN)
    for _ in range(16):
        if api.CM_Get_Device_IDW(devnode, instance, len(instance), 0):
            break
        identity = instance.value.upper()
        parts = identity.split("\\", 2)
        if len(parts) == 3 and parts[0] == "USB" and parts[2] and parts[1] in _USB_DEVICE_IDS:
            vid, pid = _USB_DEVICE_IDS[parts[1]]
            return devnode, Path(identity), vid, pid
        parent = _DWORD()
        if api.CM_Get_Parent(C.byref(parent), devnode, 0):
            break
        devnode = parent.value
    return None


def _describe(api: _WindowsHid, handle: int, path: Path, devnode: int,
              parent: tuple[int, Path, int, int]) -> Device | None:
    parent_node, usb_path, vid, pid = parent
    attributes = _HidAttributes(size=C.sizeof(_HidAttributes))
    if not api.HidD_GetAttributes(handle, C.byref(attributes)) or (attributes.vid, attributes.pid) != (vid, pid):
        return None
    preparsed = C.c_void_p()
    if not api.HidD_GetPreparsedData(handle, C.byref(preparsed)) or not preparsed.value:
        return None
    try:
        caps = _HidCaps()
        if api.HidP_GetCaps(preparsed, C.byref(caps)) != _HIDP_STATUS_SUCCESS:
            return None
        page, usage = caps.usage_page, caps.usage
        if (page, usage) not in VENDOR_COLLECTIONS:
            return None
        sizes = {}
        if page == CONFIG_PAGE:
            if not 8 <= caps.feature_length <= 64:
                return None
            report = (C.c_ubyte * caps.feature_length)()
            status = api.HidP_InitializeReportForID(_HIDP_FEATURE, REPORT_ID, preparsed, report, len(report))
            if status != _HIDP_STATUS_SUCCESS or report[0] != REPORT_ID:
                return None
            sizes[REPORT_ID] = len(report)
        name = C.create_unicode_buffer(128)
        api.HidD_GetProductString(handle, name, C.sizeof(name))
        return Device(path, vid, pid, name.value or "CH75K", page, usage, sizes, usb_path,
                      devnode, parent_node, attributes.version)
    finally:
        api.HidD_FreePreparsedData(preparsed)


def discover() -> list[Device]:
    """Inspect USB vendor collections with no read/write access or feature reports."""
    api = _api()
    devices = []
    for path, devnode in _interfaces(api):
        parent = _usb_parent(api, devnode)
        if parent is None:
            continue
        handle = api.open_interface(path, configuration=False)
        if handle == _INVALID_HANDLE:
            continue
        try:
            device = _describe(api, handle, path, devnode, parent)
            if device is not None:
                devices.append(device)
        finally:
            api.CloseHandle(handle)
    return devices


class Hidraw:
    """Expose the shared CH75K feature transport on Windows."""

    def __init__(self, device: Device):
        self.device = device
        self._handle: int | None = None
        self._resources: ExitStack | None = None

    def __enter__(self):
        if self._handle is not None:
            raise HidrawError("CH75K 설정 인터페이스가 이미 열려 있습니다.")
        api = _api()
        known_parent = (self.device._usb_devnode, self.device.usb_path, self.device.vid, self.device.pid)
        with closing(_interfaces(api)) as interfaces:
            entry = next(((path, node) for path, node in interfaces if path == self.device.path), None)
        if (entry is None or entry[1] != self.device._devnode
                or _usb_parent(api, entry[1]) != known_parent):
            raise HidrawError("선택한 CH75K를 다시 연결하고 장치 목록을 새로 고치세요.")
        path, node = entry
        with ExitStack() as resources:
            handle = api.open_interface(path, configuration=self.device.usage_page == CONFIG_PAGE)
            if handle == _INVALID_HANDLE:
                code = api.last_error()
                if code == _ERROR_SHARING_VIOLATION:
                    raise HidrawError("다른 CH75K 도구가 장치를 사용 중입니다.")
                raise HidrawError(f"CH75K USB 접근 실패 (Win32 {code}).")
            resources.callback(api.CloseHandle, handle)
            if (_usb_parent(api, node) != known_parent
                    or _describe(api, handle, path, node, known_parent) != self.device):
                raise HidrawError("선택한 CH75K의 USB 식별 정보가 변경되었습니다. 장치를 다시 선택하세요.")
            self._handle = handle
            self._resources = resources.pop_all()
        return self

    def __exit__(self, *_):
        if self._resources is not None:
            resources = self._resources
            self._handle = None
            self._resources = None
            resources.close()

    def _validate(self, report_id: int, size: int, *, extended: bool = False) -> int:
        if self._handle is None:
            raise HidrawError("CH75K 설정 인터페이스가 닫혀 있습니다.")
        declared = self.device.feature_sizes.get(report_id, 0)
        if report_id != REPORT_ID or not 8 <= declared <= size <= 64 or (size != declared and not extended):
            raise HidrawError("CH75K 기능 보고서가 장치 디스크립터와 일치하지 않습니다.")
        return declared

    def set_feature(self, data: bytes) -> None:
        if self.device.usage_page != CONFIG_PAGE:
            raise HidrawError("CH75K 보조 인터페이스는 읽기 전용입니다.")
        report_id = data[0] if data else 0
        size = self._validate(report_id, len(data))
        buffer = (C.c_ubyte * size).from_buffer_copy(data)
        api = _api()
        if not api.HidD_SetFeature(self._handle, buffer, size):
            raise HidrawError(f"CH75K 기능 보고서 {report_id} 쓰기 실패 (Win32 {api.last_error()}).")

    def _get_feature(self, report_id: int, size: int, *, extended: bool) -> bytes:
        # HidD_GetFeature requires FeatureReportByteLength and returns no byte count.
        # For a larger optional read, return only the report advertised by Windows.
        length = self._validate(report_id, size, extended=extended)
        buffer = (C.c_ubyte * length)(report_id)
        api = _api()
        if not api.HidD_GetFeature(self._handle, buffer, length):
            raise HidrawError(f"CH75K 기능 보고서 {report_id} 읽기 실패 (Win32 {api.last_error()}).")
        if buffer[0] != report_id:
            raise HidrawError(f"CH75K 기능 보고서 {report_id} 응답이 디스크립터와 일치하지 않습니다.")
        return bytes(buffer)

    def get_feature(self, report_id: int, size: int) -> bytes:
        return self._get_feature(report_id, size, extended=False)

    def get_feature_extended(self, report_id: int, size: int) -> bytes:
        return self._get_feature(report_id, size, extended=True)
