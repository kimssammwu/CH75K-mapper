"""CH75K USB discovery and synchronous feature reports through macOS IOKit."""

from contextlib import ExitStack
import ctypes as C
from dataclasses import dataclass, field
import fcntl
from functools import cache
import os
from pathlib import Path
import stat
import sys
from tempfile import gettempdir

from hid_common import (
    CONFIG_PAGE, MAX_DESCRIPTOR, USB_IDS, VENDOR_COLLECTIONS, HidrawError, descriptor_features,
)


_CFRef = C.c_void_p
_CFIndex = C.c_long
_CFTypeID = C.c_ulong
_IOReturn = C.c_int32
_IOObject = C.c_uint32
_Options = C.c_uint32
_BytePointer = C.POINTER(C.c_uint8)
_UTF8 = 0x08000100
_SINT32 = 3
_FEATURE = 2
_MAX_REPORT = 64
_SERVICE_PLANE = b"IOService"


@dataclass(frozen=True)
class Device:
    path: Path
    vid: int
    pid: int
    name: str
    usage_page: int
    usage: int
    feature_sizes: dict[int, int]
    usb_path: Path
    _descriptor: bytes = field(repr=False)
    _entry_id: int = field(repr=False)


class _IOKit:
    def __init__(self):
        core = C.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        hid = C.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
        signatures = (
            (core, (
                ("CFRelease", None, (_CFRef,)),
                ("CFStringCreateWithCString", _CFRef, (_CFRef, C.c_char_p, C.c_uint32)),
                ("CFStringGetCString", C.c_bool, (_CFRef, C.POINTER(C.c_char), _CFIndex, C.c_uint32)),
                ("CFStringGetTypeID", _CFTypeID, ()),
                ("CFGetTypeID", _CFTypeID, (_CFRef,)),
                ("CFNumberCreate", _CFRef, (_CFRef, _CFIndex, C.c_void_p)),
                ("CFNumberGetValue", C.c_bool, (_CFRef, _CFIndex, C.c_void_p)),
                ("CFNumberGetTypeID", _CFTypeID, ()),
                ("CFDictionaryCreateMutable", _CFRef, (_CFRef, _CFIndex, C.c_void_p, C.c_void_p)),
                ("CFDictionarySetValue", None, (_CFRef, _CFRef, _CFRef)),
                ("CFSetGetCount", _CFIndex, (_CFRef,)),
                ("CFSetGetValues", None, (_CFRef, C.POINTER(_CFRef))),
                ("CFDataGetTypeID", _CFTypeID, ()),
                ("CFDataGetLength", _CFIndex, (_CFRef,)),
                ("CFDataGetBytePtr", _BytePointer, (_CFRef,)),
            )),
            (hid, (
                ("IOHIDManagerCreate", _CFRef, (_CFRef, _Options)),
                ("IOHIDManagerSetDeviceMatching", None, (_CFRef, _CFRef)),
                ("IOHIDManagerCopyDevices", _CFRef, (_CFRef,)),
                ("IOHIDDeviceGetProperty", _CFRef, (_CFRef, _CFRef)),
                ("IOHIDDeviceGetService", _IOObject, (_CFRef,)),
                ("IOHIDDeviceCreate", _CFRef, (_CFRef, _IOObject)),
                ("IOHIDDeviceOpen", _IOReturn, (_CFRef, _Options)),
                ("IOHIDDeviceClose", _IOReturn, (_CFRef, _Options)),
                ("IOHIDDeviceSetReport", _IOReturn, (_CFRef, C.c_int, _CFIndex, _BytePointer, _CFIndex)),
                ("IOHIDDeviceGetReport", _IOReturn,
                 (_CFRef, C.c_int, _CFIndex, _BytePointer, C.POINTER(_CFIndex))),
                ("IORegistryEntryGetRegistryEntryID", _IOReturn, (_IOObject, C.POINTER(C.c_uint64))),
                ("IORegistryEntryGetParentEntry", _IOReturn, (_IOObject, C.c_char_p, C.POINTER(_IOObject))),
                ("IORegistryEntryCreateCFProperty", _CFRef, (_IOObject, _CFRef, _CFRef, _Options)),
                ("IOObjectConformsTo", C.c_uint, (_IOObject, C.c_char_p)),
                ("IOObjectRelease", _IOReturn, (_IOObject,)),
                ("IORegistryEntryIDMatching", _CFRef, (C.c_uint64,)),
                ("IOServiceGetMatchingService", _IOObject, (_IOObject, _CFRef)),
            )),
        )
        for library, declarations in signatures:
            for name, result, parameters in declarations:
                function = getattr(library, name)
                function.restype = result
                function.argtypes = parameters
                setattr(self, name, function)
        # Pass the address of each exported CFDictionary callback structure.
        self.dictionary_keys = C.byref(C.c_long.in_dll(core, "kCFTypeDictionaryKeyCallBacks"))
        self.dictionary_values = C.byref(C.c_long.in_dll(core, "kCFTypeDictionaryValueCallBacks"))

    def string(self, value: str):
        result = self.CFStringCreateWithCString(None, value.encode("utf-8"), _UTF8)
        if not result:
            raise HidrawError("CoreFoundation could not allocate a USB property key.")
        return result

    def number_value(self, value):
        number = C.c_int32()
        if (value and self.CFGetTypeID(value) == self.CFNumberGetTypeID()
                and self.CFNumberGetValue(value, _SINT32, C.byref(number))):
            return number.value
        return None

    def text_value(self, value):
        text = C.create_string_buffer(512)
        if (value and self.CFGetTypeID(value) == self.CFStringGetTypeID()
                and self.CFStringGetCString(value, text, len(text), _UTF8)):
            return text.value.decode("utf-8")
        return ""

    def property(self, device, key: str):
        key_ref = self.string(key)
        try:
            return self.IOHIDDeviceGetProperty(device, key_ref)
        finally:
            self.CFRelease(key_ref)

    def entry_id(self, service: int) -> int:
        identifier = C.c_uint64()
        if not service or self.IORegistryEntryGetRegistryEntryID(service, C.byref(identifier)):
            return 0
        return identifier.value


@cache
def _api() -> _IOKit:
    if sys.platform != "darwin":
        raise HidrawError("IOKit HID is available only on macOS.")
    try:
        return _IOKit()
    except (AttributeError, OSError, ValueError) as exc:
        raise HidrawError("macOS IOKit 또는 CoreFoundation을 불러올 수 없습니다.") from exc


def _usb_parent(api: _IOKit, service: int, vid: int, pid: int) -> int:
    """Group interfaces using their actual USB device in the IOService tree."""
    current = service
    try:
        for _ in range(16):
            parent = _IOObject()
            if api.IORegistryEntryGetParentEntry(current, _SERVICE_PLANE, C.byref(parent)):
                break
            if current != service:
                api.IOObjectRelease(current)
            current = parent.value
            if not current:
                break
            if not (api.IOObjectConformsTo(current, b"IOUSBHostDevice")
                    or api.IOObjectConformsTo(current, b"IOUSBDevice")):
                continue
            with ExitStack() as releases:
                actual = []
                for name in ("idVendor", "idProduct"):
                    key = api.string(name)
                    releases.callback(api.CFRelease, key)
                    value = api.IORegistryEntryCreateCFProperty(current, key, None, 0)
                    if value:
                        releases.callback(api.CFRelease, value)
                    actual.append(api.number_value(value))
            return api.entry_id(current) if tuple(actual) == (vid, pid) else 0
        return 0
    finally:
        if current and current != service:
            api.IOObjectRelease(current)


def _devices_at_interface(api: _IOKit, device) -> list[Device]:
    vid = api.number_value(api.property(device, "VendorID"))
    pid = api.number_value(api.property(device, "ProductID"))
    if (vid, pid) not in USB_IDS or api.text_value(api.property(device, "Transport")) != "USB":
        return []
    data = api.property(device, "ReportDescriptor")
    if not data or api.CFGetTypeID(data) != api.CFDataGetTypeID():
        return []
    size = api.CFDataGetLength(data)
    if not 0 < size <= MAX_DESCRIPTOR:
        return []
    pointer = api.CFDataGetBytePtr(data)
    if not pointer:
        return []
    descriptor = C.string_at(pointer, size)
    try:
        collections = descriptor_features(descriptor)
    except ValueError:
        return []
    if VENDOR_COLLECTIONS.isdisjoint(collections):
        return []
    service = api.IOHIDDeviceGetService(device)
    identifier = api.entry_id(service)
    parent = _usb_parent(api, service, vid, pid) if identifier else 0
    if not parent:
        return []
    name = api.text_value(api.property(device, "Product")) or "CH75K"
    usb_path = Path(f"iokit-usb-{parent:016x}")
    return [
        Device(Path(f"iokit-{identifier:016x}-{page:04x}-{usage:04x}"), vid, pid, name,
               page, usage, reports, usb_path, descriptor, identifier)
        for (page, usage), reports in collections.items() if (page, usage) in VENDOR_COLLECTIONS
    ]


def discover() -> list[Device]:
    """Enumerate the known USB IDs without opening any HID service."""
    api = _api()
    devices = {}
    for vid, pid in sorted(USB_IDS):
        with ExitStack() as releases:
            manager = api.IOHIDManagerCreate(None, 0)
            if not manager:
                raise HidrawError("macOS HID 관리자에 접근할 수 없습니다.")
            releases.callback(api.CFRelease, manager)
            matching = api.CFDictionaryCreateMutable(None, 0, api.dictionary_keys, api.dictionary_values)
            if not matching:
                raise HidrawError("macOS HID 검색 조건을 만들 수 없습니다.")
            releases.callback(api.CFRelease, matching)
            for key, number in (("VendorID", vid), ("ProductID", pid)):
                key_ref = api.string(key)
                releases.callback(api.CFRelease, key_ref)
                number_ref = api.CFNumberCreate(None, _SINT32, C.byref(C.c_int32(number)))
                if not number_ref:
                    raise HidrawError("macOS HID 검색 조건을 만들 수 없습니다.")
                releases.callback(api.CFRelease, number_ref)
                api.CFDictionarySetValue(matching, key_ref, number_ref)
            key_ref = api.string("Transport")
            releases.callback(api.CFRelease, key_ref)
            transport = api.string("USB")
            releases.callback(api.CFRelease, transport)
            api.CFDictionarySetValue(matching, key_ref, transport)
            api.IOHIDManagerSetDeviceMatching(manager, matching)
            found = api.IOHIDManagerCopyDevices(manager)
            if not found:
                continue
            releases.callback(api.CFRelease, found)
            count = api.CFSetGetCount(found)
            if not count:
                continue
            handles = (_CFRef * count)()
            api.CFSetGetValues(found, handles)
            for handle in handles:
                for entry in _devices_at_interface(api, handle):
                    devices[entry.path] = entry
    return [devices[key] for key in sorted(devices)]


def _lock_configuration() -> int:
    """Serialize configuration across this user's CLI and browser processes."""
    path = Path(gettempdir()) / f".ch75k-iokit-{os.getuid()}.lock"
    fd = None
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, 0o600)
        identity = os.fstat(fd)
        if (not stat.S_ISREG(identity.st_mode) or identity.st_uid != os.getuid()
                or identity.st_nlink != 1 or identity.st_mode & 0o077):
            raise HidrawError("CH75K 잠금 파일의 유형 또는 권한을 확인할 수 없습니다.")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BaseException as exc:
        if fd is not None:
            os.close(fd)
        if isinstance(exc, BlockingIOError):
            raise HidrawError("다른 CH75K 도구가 장치를 사용 중입니다.") from exc
        if isinstance(exc, OSError):
            raise HidrawError("CH75K 설정 잠금 파일을 열 수 없습니다.") from exc
        raise


class Hidraw:
    """Expose the shared HID transport interface over an IOKit device."""

    # With the default pauses, CH75K 30fa:2350 on macOS 26 did not keep written blocks
    # and the read-back check failed. Five times the pauses plus 0.5 s after each save
    # wrote and verified all 19 blocks of a three-key macro profile.
    wait_scale = 5
    save_settle = 0.5

    def __init__(self, device: Device):
        self.device = device
        self._handle = None
        self._resources = None

    def __enter__(self):
        if self._handle is not None:
            raise HidrawError("CH75K 설정 인터페이스가 이미 열려 있습니다.")
        api = _api()
        with ExitStack() as resources:
            if self.device.usage_page == CONFIG_PAGE:
                resources.callback(os.close, _lock_configuration())
            matching = api.IORegistryEntryIDMatching(self.device._entry_id)
            # IOServiceGetMatchingService consumes the matching dictionary.
            service = api.IOServiceGetMatchingService(0, matching) if matching else 0
            if not service:
                raise HidrawError("선택한 CH75K를 다시 연결하고 장치 목록을 새로 고치세요.")
            resources.callback(api.IOObjectRelease, service)
            handle = api.IOHIDDeviceCreate(None, service)
            if not handle:
                raise HidrawError("선택한 CH75K의 USB 설정 인터페이스를 열 수 없습니다.")
            resources.callback(api.CFRelease, handle)
            if self.device not in _devices_at_interface(api, handle):
                raise HidrawError("선택한 CH75K의 USB 식별 정보가 변경되었습니다. 장치를 다시 선택하세요.")
            # Feature reports are synchronous. Do not seize the interface or monitor key events.
            status = api.IOHIDDeviceOpen(handle, 0)
            if status:
                raise HidrawError(f"CH75K USB 접근 실패 (IOKit 0x{status & 0xFFFFFFFF:08X}).")
            resources.callback(api.IOHIDDeviceClose, handle, 0)
            self._handle = handle
            self._resources = resources.pop_all()
        return self

    def __exit__(self, *_):
        if self._resources is not None:
            resources = self._resources
            self._handle = None
            self._resources = None
            resources.close()

    def _validate(self, report_id: int, size: int, *, extended: bool = False):
        if self._handle is None:
            raise HidrawError("CH75K 설정 인터페이스가 닫혀 있습니다.")
        declared = self.device.feature_sizes.get(report_id, 0)
        if not 1 <= report_id <= 255 or not 2 <= size <= _MAX_REPORT:
            raise HidrawError("유효하지 않은 CH75K 보고서 ID 또는 길이입니다.")
        if size != declared and not (extended and 2 <= declared <= size):
            raise HidrawError("CH75K 기능 보고서가 장치 디스크립터와 일치하지 않습니다.")

    def set_feature(self, data: bytes) -> None:
        if self.device.usage_page != CONFIG_PAGE:
            raise HidrawError("CH75K 보조 인터페이스는 읽기 전용입니다.")
        report_id = data[0] if data else 0
        self._validate(report_id, len(data))
        buffer = (C.c_uint8 * len(data)).from_buffer_copy(data)
        status = _api().IOHIDDeviceSetReport(self._handle, _FEATURE, report_id, buffer, len(buffer))
        if status:
            raise HidrawError(f"CH75K 기능 보고서 {report_id} 쓰기 실패 (IOKit 0x{status & 0xFFFFFFFF:08X}).")

    def _get_feature(self, report_id: int, size: int, *, extended: bool = False) -> bytes:
        self._validate(report_id, size, extended=extended)
        buffer = (C.c_uint8 * size)()
        length = _CFIndex(size)
        status = _api().IOHIDDeviceGetReport(self._handle, _FEATURE, report_id, buffer, C.byref(length))
        if status:
            raise HidrawError(f"CH75K 기능 보고서 {report_id} 읽기 실패 (IOKit 0x{status & 0xFFFFFFFF:08X}).")
        if (not 1 <= length.value <= size or (not extended and length.value != size)
                or buffer[0] != report_id):
            raise HidrawError(f"CH75K 기능 보고서 {report_id} 응답이 디스크립터와 일치하지 않습니다.")
        return bytes(buffer[:length.value])

    def get_feature(self, report_id: int, size: int) -> bytes:
        return self._get_feature(report_id, size)

    def get_feature_extended(self, report_id: int, size: int) -> bytes:
        return self._get_feature(report_id, size, extended=True)
