#!/usr/bin/env python3
"""Native Linux configurator for the inote CH75K macro keypad."""

from __future__ import annotations

import argparse
from contextlib import suppress
from datetime import datetime
import errno
import json
import os
from pathlib import Path
import sys

from hid_common import COMPANION_PAGE, CONFIG_PAGE, HidrawError
from protocol import BLOCK_SIZE, PROFILE_POINTER, REPORT_ID, ProtocolError, SettingsLink
from settings import (
    BUTTONS, DIRECT_ACTIONS, MACRO_END, MACRO_TRIGGERS, PROFILE_PREFIX,
    Change, parse_profile, plan_changes, profile_base, verify_matrix,
)

if sys.platform == "darwin":
    from macos_hid import Device, Hidraw, discover
elif sys.platform.startswith("linux"):
    from hidraw import Device, Hidraw, discover
else:
    raise RuntimeError("CH75K requires macOS or Linux.")


def candidates() -> list[Device]:
    devices = discover()
    companions = {device.usb_path for device in devices if device.usage_page == COMPANION_PAGE}
    return [device for device in devices if device.usage_page == CONFIG_PAGE
            and device.usb_path in companions and 8 <= device.feature_sizes.get(REPORT_ID, 0) <= 64]


def find_devices() -> list[tuple[str, str]]:
    return [(str(device.path), f"{device.name} ({device.vid:04x}:{device.pid:04x})")
            for device in candidates()]


def select_device(selector: str | None) -> Device:
    devices = [
        device for device in candidates()
        if selector is None or selector in (str(device.path), device.path.name)
    ]
    if not devices:
        raise ProtocolError("CH75K 후보가 없습니다. 키보드를 USB-C로 직접 연결하세요.")
    if len(devices) != 1:
        raise ProtocolError("USB 장치가 여러 개입니다. 사용할 CH75K를 선택하세요.")
    return devices[0]


def preview_profile(profile: dict) -> str:
    keys = parse_profile(profile)
    lines = []
    for button, action in keys.items():
        if action.kind in DIRECT_ACTIONS:
            category = "미디어" if action.kind == "media" else "시스템"
            detail = f"{category} 키 · 장치 코드 0x{action.key:02X}"
        elif action.key is not None:
            detail = f"HID 키 0x{action.key:02X}"
        else:
            detail = f"매크로 {action.words}개 이벤트 · {len(action.body)}바이트"
        lines.append(f"{button}  {action.text}  ({detail})")
    return "\n".join(lines) or "변경할 설정을 입력하세요. 빈 칸은 기존 설정을 유지합니다."


def _read_span(link: SettingsLink, address: int, count: int) -> bytes:
    return b"".join(link.read_block(address + i, min(BLOCK_SIZE, count - i))
                    for i in range(0, count, BLOCK_SIZE))


def _snapshot(link: SettingsLink, include_macros: bool) -> tuple[int, bytes]:
    base = profile_base(link.read_block(PROFILE_POINTER))
    data = _read_span(link, base, PROFILE_PREFIX)
    verify_matrix(data)
    if include_macros:
        tail = base + len(data)
        data += _read_span(link, tail, MACRO_END - tail)
    return base, data


def read_profile(selector: str | Device | None) -> str:
    device = selector if isinstance(selector, Device) else select_device(selector)
    with Hidraw(device) as hid:
        base, data = _snapshot(SettingsLink(hid, device.feature_sizes[REPORT_ID]), False)
    lines = [f"CH75K 버튼 배열 확인 · 프로필 주소 0x{base:04X}"]
    direct_names = {code: name for actions in DIRECT_ACTIONS.values() for name, code in actions.items()}
    for button, offset in BUTTONS.items():
        value = data[offset]
        detail = (f"매크로 슬롯 {MACRO_TRIGGERS.index(value) + 1}"
                  if value in MACRO_TRIGGERS else direct_names.get(value, f"기기 키 코드 0x{value:02X}"))
        lines.append(f"{button}  {detail}")
    return "\n".join(lines)


def state_directory() -> Path:
    default_state = (Path.home() / "Library" / "Application Support" if sys.platform == "darwin"
                     else Path.home() / ".local" / "state")
    return Path(os.environ.get("XDG_STATE_HOME") or default_state) / "ch75k-native"


def _persist_backup(device: Device, base: int, changes: list[Change]) -> Path:
    root = state_directory() / "backups"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = root / f"{datetime.now():%Y%m%d-%H%M%S-%f}-{os.getpid()}.json"
    payload = {
        "format": 1, "device": f"{device.vid:04x}:{device.pid:04x}",
        "usb_path": str(device.usb_path), "base": base,
        "blocks": [{"address": change.address, "before": change.before.hex(),
                    "after": change.after.hex()} for change in changes],
    }
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    directory = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_CLOEXEC)
    try:
        try:
            os.fsync(directory)
        except OSError as exc:
            if sys.platform != "darwin" or exc.errno not in (errno.EINVAL, errno.ENOTSUP):
                raise
    finally:
        os.close(directory)
    return path


def _verify_blocks(link: SettingsLink, changes: list[Change], *, restored: bool = False):
    for change in changes:
        expected = change.before if restored else change.after
        if link.read_block(change.address) != expected:
            raise ProtocolError(f"설정을 다시 읽었을 때 블록 0x{change.address:04X} 값이 다릅니다.")


def _write_and_verify(link: SettingsLink, base: int, changes: list[Change], backup: Path):
    attempted = []
    try:
        link.begin_configuration(base)
        for change in changes:
            attempted.append(change)
            link.write_block(change.address, change.after)
        link.finish_configuration()
        _verify_blocks(link, changes)
    except BaseException as original:
        if not attempted:
            with suppress(Exception):
                link.release()
            raise
        try:
            link.begin_configuration(base)
            for change in reversed(attempted):
                link.write_block(change.address, change.before)
            link.finish_configuration()
            _verify_blocks(link, attempted, restored=True)
        except BaseException as recovery:
            raise ProtocolError(
                f"적용과 자동 복구를 확인하지 못했습니다. 백업 {backup}\n{recovery}") from original
        finally:
            with suppress(Exception):
                link.release()
        raise ProtocolError(f"적용에 실패해 기존 설정을 복구했습니다. 백업 {backup}\n{original}") from original
    try:
        link.close_configuration()
    except Exception as exc:
        raise ProtocolError(
            f"저장과 읽기 검증을 마쳤지만 USB 설정 모드를 종료하지 못했습니다. 백업 {backup}\n{exc}") from exc


def apply_profile(selector: str | Device | None, profile: dict) -> str:
    keys = parse_profile(profile)
    if not keys:
        raise ValueError("변경할 버튼 설정을 입력하세요.")
    device = selector if isinstance(selector, Device) else select_device(selector)
    with Hidraw(device) as hid:
        link = SettingsLink(hid, device.feature_sizes[REPORT_ID])
        base, original = _snapshot(link, any(action.key is None for action in keys.values()))
        changes = plan_changes(base, original, keys)
        if not changes:
            return "현재 장치에 같은 설정이 저장되어 있습니다."
        backup = _persist_backup(device, base, changes)
        _write_and_verify(link, base, changes, backup)
    return f"적용 후 {len(changes)}개 USB 설정 블록을 다시 읽어 확인했습니다.\n백업 {backup}"


def _load_profile(path: Path) -> dict:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CH75K 매크로 설정 도구")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("gui", help="그래픽 설정 창 열기")
    commands.add_parser("devices", help="HID 설정 인터페이스 목록")
    preview = commands.add_parser("preview", help="키보드 없이 JSON 프로필 검사")
    preview.add_argument("profile", type=Path)
    read = commands.add_parser("read", help="연결된 CH75K의 키 설정 읽기")
    read.add_argument("--device", help="hidraw 장치 선택")
    apply = commands.add_parser("apply", help="CH75K에 변경 사항 저장하고 다시 읽기")
    apply.add_argument("profile", type=Path)
    apply.add_argument("--device", help="hidraw 장치 선택")
    apply.add_argument("--confirm-ch75k", action="store_true", help="실제로 연결한 모델이 CH75K임을 확인")
    args = parser.parse_args(argv)
    try:
        if args.command in (None, "gui"):
            from desktop_app import main as open_desktop
            return open_desktop([])
        elif args.command == "devices":
            print("\n".join(f"{path}  {name}" for path, name in find_devices()) or "CH75K 후보 없음")
        elif args.command == "preview":
            print(preview_profile(_load_profile(args.profile)))
        elif args.command == "read":
            print(read_profile(args.device))
        elif args.command == "apply":
            if not args.confirm_ch75k:
                parser.error("연결된 장치가 CH75K일 때 --confirm-ch75k를 추가하세요.")
            print(apply_profile(args.device, _load_profile(args.profile)))
    except (HidrawError, ProtocolError, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except ImportError as exc:
        print(f"CH75K 매퍼를 열지 못했습니다. {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
