"""CH75K USB detection and Linux hidraw I/O."""

from dataclasses import dataclass, field
from pathlib import Path
import fcntl
import os
import stat
import struct

from hid_common import COMPANION_PAGE, CONFIG_PAGE, USB_IDS, HidrawAccessError, HidrawError
from hid_common import MAX_DESCRIPTOR as _MAX_DESCRIPTOR
from hid_common import VENDOR_COLLECTIONS as _VENDOR_COLLECTIONS
from hid_common import descriptor_features as _descriptor_features


_BUS_USB = 3
_SYS_HIDRAW = Path("/sys/class/hidraw")
_SYS_CHAR = Path("/sys/dev/char")
_DEV = Path("/dev")
_IOC_READ = 2
_IOC_READ_WRITE = 3
_MAX_IOCTL_SIZE = (1 << 14) - 1


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
    _sys_path: Path = field(repr=False)
    _usb_address: str = field(repr=False)


def discover() -> list[Device]:
    """Find matching vendor USB collections without opening any device."""
    devices = []
    for entry in sorted(_SYS_HIDRAW.glob("hidraw*")):
        try:
            interface = (entry / "device").resolve(strict=True)
            properties = dict(
                line.split("=", 1) for line in (interface / "uevent").read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines() if "=" in line
            )
            bus, vid, pid = (int(value, 16) for value in properties.get("HID_ID", "").split(":"))
            if bus != _BUS_USB or (vid, pid) not in USB_IDS:
                continue
            usb_path = next(path for path in interface.parents if (path / "idVendor").is_file())
            usb_vid = int((usb_path / "idVendor").read_text().strip(), 16)
            usb_pid = int((usb_path / "idProduct").read_text().strip(), 16)
            if (usb_vid, usb_pid) != (vid, pid):
                continue
            usb_address = (usb_path / "devnum").read_text().strip()
            descriptor = (interface / "report_descriptor").read_bytes()
            collections = _descriptor_features(descriptor)
            resolved_entry = entry.resolve(strict=True)
            for (page, usage), sizes in collections.items():
                if (page, usage) in _VENDOR_COLLECTIONS:
                    devices.append(Device(
                        _DEV / entry.name, vid, pid, properties.get("HID_NAME", "CH75K"),
                        page, usage, sizes, usb_path, descriptor, resolved_entry, usb_address,
                    ))
        except (OSError, StopIteration, ValueError):
            # Nodes can disappear mid-scan when their device is unplugged.
            continue
    return devices


def _ioctl_number(direction: int, number: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord("H") << 8) | number


def _ioctl(fd: int, direction: int, number: int, data: bytearray) -> int:
    return fcntl.ioctl(fd, _ioctl_number(direction, number, len(data)), data, True)


def _verify_device(fd: int, device: Device) -> dict[int, int]:
    attributes = os.fstat(fd)
    if not stat.S_ISCHR(attributes.st_mode):
        raise HidrawError(f"{device.path} is not a character device")
    major = os.major(attributes.st_rdev)
    minor = os.minor(attributes.st_rdev)
    attached = (_SYS_CHAR / f"{major}:{minor}").resolve(strict=True)
    usb_address = (device.usb_path / "devnum").read_text().strip()
    if attached != device._sys_path or usb_address != device._usb_address:
        raise HidrawError(f"{device.path} changed after discovery")

    identity = bytearray(8)
    _ioctl(fd, _IOC_READ, 0x03, identity)
    if struct.unpack("=IHH", identity) != (_BUS_USB, device.vid, device.pid):
        raise HidrawError(f"{device.path} is no longer the selected CH75K")

    size_data = bytearray(4)
    _ioctl(fd, _IOC_READ, 0x01, size_data)
    size, = struct.unpack("=I", size_data)
    if not 0 < size < _MAX_DESCRIPTOR:
        raise HidrawError(f"{device.path} has an invalid report descriptor length")
    report = bytearray(4 + _MAX_DESCRIPTOR)
    struct.pack_into("=I", report, 0, size)
    _ioctl(fd, _IOC_READ, 0x02, report)
    descriptor = bytes(report[4:4 + size])
    if descriptor != device._descriptor:
        raise HidrawError(f"{device.path} report descriptor changed after discovery")
    sizes = _descriptor_features(descriptor).get((device.usage_page, device.usage))
    if sizes is None:
        raise HidrawError(f"{device.path} does not expose the selected vendor collection")
    return sizes


class Hidraw:
    """Open only a previously discovered interface and validate report lengths."""

    def __init__(self, device: Device):
        self.device = device
        self._fd: int | None = None
        self._sizes: dict[int, int] = {}

    def __enter__(self):
        if self._fd is not None:
            raise HidrawError("the CH75K interface is already open")
        if os.uname().machine not in {"x86_64", "i386", "i686", "aarch64", "armv7l", "riscv64"}:
            raise HidrawError("this CPU architecture's hidraw ioctl ABI is unsupported")
        mode = os.O_RDWR if self.device.usage_page == CONFIG_PAGE else os.O_RDONLY
        fd = None
        try:
            fd = os.open(self.device.path, mode | os.O_NOFOLLOW | os.O_CLOEXEC)
            self._sizes = _verify_device(fd, self.device)
            if mode == os.O_RDWR:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if fd is not None:
                os.close(fd)
            if fd is None and isinstance(exc, PermissionError):
                raise HidrawAccessError(self.device) from exc
            if isinstance(exc, BlockingIOError):
                raise HidrawError(f"{self.device.path}: 다른 CH75K 도구가 사용 중입니다.") from exc
            raise HidrawError(f"{self.device.path}: {exc.strerror or exc}") from exc
        except (HidrawError, ValueError):
            if fd is not None:
                os.close(fd)
            raise
        self._fd = fd
        return self

    def __exit__(self, *_):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
            self._sizes = {}

    def _feature(self, number: int, data: bytearray, *, extended: bool = False) -> bytes:
        if self._fd is None:
            raise HidrawError("the CH75K interface is closed")
        size = len(data)
        if not 2 <= size <= _MAX_IOCTL_SIZE:
            raise HidrawError("feature report does not match the CH75K report descriptor")
        report_id = data[0]
        declared = self._sizes.get(report_id, 0)
        if size != declared and not (extended and 2 <= declared <= size <= 64):
            raise HidrawError("feature report does not match the CH75K report descriptor")
        try:
            length = _ioctl(self._fd, _IOC_READ_WRITE, number, data)
        except OSError as exc:
            raise HidrawError(f"{self.device.path}: feature report {report_id}: {exc.strerror or exc}") from exc
        if not 1 <= length <= size or (not extended and length != size) or data[0] != report_id:
            raise HidrawError(f"{self.device.path}: invalid reply for feature report {report_id}")
        return bytes(data[:length])

    def set_feature(self, data: bytes) -> None:
        if self.device.usage_page != CONFIG_PAGE:
            raise HidrawError("the companion interface is read-only")
        self._feature(0x06, bytearray(data))

    def get_feature(self, report_id: int, size: int) -> bytes:
        if not 0 < report_id <= 255 or not 2 <= size <= _MAX_IOCTL_SIZE:
            raise HidrawError("invalid feature report ID or length")
        return self._feature(0x07, bytearray([report_id]) + bytearray(size - 1))

    def get_feature_extended(self, report_id: int, size: int) -> bytes:
        """Permit the vendor's longer GET request and return only the received bytes."""
        if not 0 < report_id <= 255 or not 2 <= size <= 64:
            raise HidrawError("invalid extended feature report ID or length")
        return self._feature(0x07, bytearray([report_id]) + bytearray(size - 1), extended=True)
