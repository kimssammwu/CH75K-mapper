"""Request active-seat access to a verified Linux CH75K hidraw interface."""

from pathlib import Path
import os
import re
import stat
import subprocess

from hid_common import CONFIG_PAGE, USB_IDS, HidrawError
from hidraw import Device


_RULE = (
    b'# Active local users can open supported USB HID interfaces through hidraw.\n'
    b'SUBSYSTEM=="hidraw", KERNEL=="hidraw*", ATTRS{idVendor}=="30fa", ATTRS{idProduct}=="2350", TAG+="uaccess"\n'
    b'SUBSYSTEM=="hidraw", KERNEL=="hidraw*", ATTRS{idVendor}=="2130", ATTRS{idProduct}=="30fa", TAG+="uaccess"\n'
)
_UDEVADM = ("/usr/bin/udevadm", "/usr/sbin/udevadm", "/bin/udevadm", "/sbin/udevadm")
_DEVICE_CHANGED = "선택한 CH75K 연결이 바뀌었습니다. 장치를 다시 선택해 주세요."

# Only system Python runs as root. This fixed program receives the discovered
# interface identity as data. It never reads a rule or a script from the bundle.
_ROOT_PROGRAM = (
    f"RULE = {_RULE!r}\n"
    f"USB_IDS = {sorted(USB_IDS)!r}\n"
    f"UDEVADM = {_UDEVADM!r}\n"
    + r"""
from pathlib import Path
import os
import re
import stat
import subprocess
import sys
import tempfile

node, expected_sys, expected_usb, expected_address, vid, pid = sys.argv[1:]

def verify_device():
    if not re.fullmatch(r"hidraw[0-9]+", node):
        raise ValueError("unexpected device node")
    device_path = Path("/dev") / node
    node_stat = device_path.lstat()
    if not stat.S_ISCHR(node_stat.st_mode):
        raise ValueError("not a character device")
    syspath = (Path("/sys/class/hidraw") / node).resolve(strict=True)
    kernel_dev = f"{os.major(node_stat.st_rdev)}:{os.minor(node_stat.st_rdev)}"
    if (str(syspath) != expected_sys or not syspath.is_relative_to("/sys/devices")
            or (Path("/sys/dev/char") / kernel_dev).resolve(strict=True) != syspath):
        raise ValueError("device identity changed")
    usb = Path(expected_usb)
    if usb not in syspath.parents:
        raise ValueError("USB identity changed")
    usb_ids = tuple(int((usb / key).read_text().strip(), 16) for key in ("idVendor", "idProduct"))
    if usb_ids != (int(vid), int(pid)) or usb_ids not in USB_IDS:
        raise ValueError("unsupported USB identity")
    if (usb / "devnum").read_text().strip() != expected_address:
        raise ValueError("USB connection changed")
    return syspath

def trusted_mode(entry, predicate):
    return predicate(entry.st_mode) and entry.st_uid == 0 and not entry.st_mode & 0o022

def install_rule():
    directory = Path("/etc/udev/rules.d")
    for parent in (Path("/etc"), directory.parent):
        if not trusted_mode(parent.lstat(), stat.S_ISDIR):
            raise OSError("unsafe udev directory")
    directory.mkdir(mode=0o755, exist_ok=True)
    if not trusted_mode(directory.lstat(), stat.S_ISDIR):
        raise OSError("unsafe udev rules directory")
    target = directory / "70-ch75k-native.rules"

    def existing_rule():
        try:
            fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return False
        except OSError:
            raise SystemExit(3)
        with os.fdopen(fd, "rb") as rule:
            if not trusted_mode(os.fstat(rule.fileno()), stat.S_ISREG):
                raise SystemExit(3)
            if rule.read(len(RULE) + 1) != RULE:
                raise SystemExit(3)
        return True

    if existing_rule():
        return
    fd, filename = tempfile.mkstemp(prefix=".ch75k-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as rule:
            rule.write(RULE)
            rule.flush()
            os.fchmod(rule.fileno(), 0o644)
            os.fsync(rule.fileno())
        try:
            # An atomic link preserves an administrator's file if it appeared
            # between our check and installation.
            os.link(filename, target)
        except FileExistsError:
            if not existing_rule():
                raise
    finally:
        os.unlink(filename)

def reload_rules():
    for pathname in UDEVADM:
        try:
            entry = os.stat(pathname)
        except OSError:
            continue
        if trusted_mode(entry, stat.S_ISREG) and entry.st_mode & 0o111:
            subprocess.run([pathname, "control", "--reload-rules"], check=True, timeout=8)
            return pathname
    raise FileNotFoundError("udevadm")

if os.geteuid() != 0:
    raise SystemExit(5)
try:
    verify_device()
except (OSError, ValueError):
    raise SystemExit(2)
try:
    install_rule()
except OSError:
    raise SystemExit(5)
try:
    udevadm = reload_rules()
except (OSError, subprocess.SubprocessError):
    raise SystemExit(4)
try:
    syspath = verify_device()
except (OSError, ValueError):
    raise SystemExit(2)
try:
    subprocess.run([udevadm, "trigger", "--action=add", "--subsystem-match=hidraw",
                    "--sysname-match=" + node, "--settle", str(syspath)], check=True, timeout=12)
except (OSError, subprocess.SubprocessError):
    raise SystemExit(4)
try:
    verify_device()
except (OSError, ValueError):
    raise SystemExit(2)
"""
)


def _system_executable(paths: tuple[str, ...]) -> str | None:
    for candidate in paths:
        try:
            info = os.stat(candidate)
        except OSError:
            continue
        if (stat.S_ISREG(info.st_mode) and info.st_uid == 0
                and not info.st_mode & 0o022 and info.st_mode & 0o111):
            return candidate
    return None


def _require_current_device(device: Device, descriptor: int | None = None):
    try:
        info = device.path.lstat() if descriptor is None else os.fstat(descriptor)
        if not stat.S_ISCHR(info.st_mode):
            raise ValueError
        number = f"{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}"
        sys_path = (Path("/sys/dev/char") / number).resolve(strict=True)
        usb_address = (device.usb_path / "devnum").read_text().strip()
        if sys_path != device._sys_path or usb_address != device._usb_address:
            raise ValueError
    except (OSError, ValueError) as exc:
        raise HidrawError(_DEVICE_CHANGED) from exc


def _can_open(device: Device) -> bool:
    try:
        fd = os.open(device.path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    except PermissionError:
        return False
    except OSError as exc:
        raise HidrawError(f"{device.path}을(를) 열 수 없습니다. 장치를 다시 연결해 주세요.") from exc
    try:
        _require_current_device(device, fd)
    finally:
        os.close(fd)
    return True


def request_hid_access(device: Device) -> None:
    """Ask the desktop to install the CH75K rule, then verify user access."""
    if (not re.fullmatch(r"/dev/hidraw[0-9]+", str(device.path))
            or device.usage_page != CONFIG_PAGE or (device.vid, device.pid) not in USB_IDS):
        raise HidrawError(_DEVICE_CHANGED)
    _require_current_device(device)
    if _can_open(device):
        return

    pkexec = _system_executable(("/usr/bin/pkexec", "/bin/pkexec"))
    python = _system_executable(("/usr/bin/python3",))
    if not pkexec or not python or not _system_executable(_UDEVADM):
        raise HidrawError("시스템 권한 도구(pkexec, Python 3 또는 udevadm)가 없습니다. "
                          "README의 Linux 권한 설정을 사용해 주세요.")

    command = [
        pkexec, "--disable-internal-agent", python, "-I", "-S", "-c", _ROOT_PROGRAM,
        device.path.name, str(device._sys_path), str(device.usb_path), device._usb_address,
        str(device.vid), str(device.pid),
    ]
    try:
        result = subprocess.run(command, cwd="/", stdin=subprocess.DEVNULL, capture_output=True,
                                check=False, timeout=105)
    except subprocess.TimeoutExpired as exc:
        raise HidrawError("CH75K 권한 요청 시간이 초과됐습니다. 다시 시도해 주세요.") from exc
    except OSError as exc:
        raise HidrawError("시스템 인증을 시작하지 못했습니다. README의 Linux 권한 설정을 확인해 주세요.") from exc

    errors = {
        2: _DEVICE_CHANGED,
        3: "기존 70-ch75k-native.rules 파일이 설치 규칙과 다릅니다. 기존 파일을 직접 확인해 주세요.",
        4: "udev 규칙을 다시 적용하지 못했습니다. CH75K를 다시 연결해 주세요.",
        5: "CH75K udev 규칙을 설치하지 못했습니다. README의 Linux 권한 설정을 확인해 주세요.",
        126: "CH75K 권한 요청이 취소됐습니다.",
        127: "관리자 인증을 완료하지 못했습니다. 데스크톱 인증 서비스와 관리자 권한을 확인해 주세요.",
    }
    if result.returncode:
        raise HidrawError(errors.get(result.returncode, "CH75K 권한을 설정하지 못했습니다."))
    _require_current_device(device)
    if not _can_open(device):
        raise HidrawError("CH75K 권한이 현재 사용자에게 적용되지 않았습니다. "
                          "활성 데스크톱 세션에서 CH75K를 다시 연결해 주세요.")
