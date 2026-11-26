"""Read CH75K assignments that the editor can represent without guessing."""

from __future__ import annotations

from dataclasses import dataclass
from struct import unpack

from actions import DEFAULT_DELAY_MS, KEYS, MAX_DELAY_MS, parse_action
from protocol import ProtocolError
from settings import (
    BUTTONS, DIRECT_ACTIONS, MACRO_TRIGGERS, MOUSE_BUTTONS, MOUSE_MOVE,
    MOUSE_WHEEL, REPEAT_FLAGS, Slot, _slots, encode_macro, parse_profile, verify_matrix,
)


_KEY_NAMES = {usage: name.capitalize() for name, usage in reversed(tuple(KEYS.items()))} | {
    0xE1: "Shift", 0xE4: "RCtrl", 0xE5: "RShift", 0xE6: "RAlt", 0xE7: "RWin",
}
_DIRECT_NAMES = {code: (kind, name) for kind, actions in DIRECT_ACTIONS.items()
                 for name, code in actions.items()}
_BUTTON_NAMES = {code: f"Mouse{name.title()}" for name, code in MOUSE_BUTTONS.items()}
_REPEAT_NAMES = {value: name for name, value in REPEAT_FLAGS.items()}
_STOCK_SHORTCUTS = {KEYS[key] for key in "acdvx"}


@dataclass(frozen=True)
class _Event:
    opcode: int
    ticks: int
    pressed: bool
    arguments: bytes = b""


def _read_events(image: bytes, start: int, end: int, count: int) -> tuple[list[_Event], int]:
    events = []
    cursor = start
    for _ in range(count):
        if cursor + 2 > end:
            raise ValueError("incomplete macro event")
        flag, opcode = image[cursor:cursor + 2]
        cursor += 2
        width = 4 if opcode == MOUSE_MOVE else 2 if opcode == MOUSE_WHEEL else 0
        if cursor + width > end:
            raise ValueError("incomplete macro payload")
        events.append(_Event(opcode, flag & 0x7F, bool(flag & 0x80), image[cursor:cursor + width]))
        cursor += width
    if any(byte not in (0, 255) for byte in image[cursor:end]):
        raise ValueError("unrecognized trailing macro data")
    return events, cursor


def _stock_shortcut(events: list[_Event]) -> str | None:
    # Factory clipboard shortcuts release Ctrl before the letter. The editor
    # models the same shortcut as a chord and preserves the stored bytes on equality.
    if len(events) != 4 or events[1].opcode not in _STOCK_SHORTCUTS:
        return None
    ctrl, key = KEYS["ctrl"], events[1].opcode
    if events == [_Event(ctrl, 6, True), _Event(key, 6, True),
                  _Event(ctrl, 6, False), _Event(key, 6, False)]:
        return f"Ctrl+{_KEY_NAMES[key]}"
    return None


def _decode_step(events: list[_Event], index: int) -> tuple[str, int, int]:
    event = events[index]
    next_index = index + 1
    if not event.pressed:
        if event.opcode == 0:
            return "", event.ticks, next_index
        if event.opcode == MOUSE_MOVE:
            x, y = unpack("<hh", event.arguments)
            return f"MouseMove({x} {y})", event.ticks, next_index
        if event.opcode == MOUSE_WHEEL and event.arguments[1] == 0:
            distance = int.from_bytes(event.arguments[:1], "little", signed=True)
            return f"MouseWheel({distance})", event.ticks, next_index
        raise ValueError("unmatched macro release")

    if event.opcode in _BUTTON_NAMES:
        if next_index >= len(events) or event.ticks != 6:
            raise ValueError("unmatched mouse press")
        release = events[next_index]
        if release.pressed or release.opcode != event.opcode:
            raise ValueError("unmatched mouse release")
        return _BUTTON_NAMES[event.opcode], release.ticks, next_index + 1

    usages = []
    while index < len(events) and events[index].pressed:
        event = events[index]
        if event.ticks != 6 or event.opcode not in _KEY_NAMES:
            raise ValueError("unsupported keyboard press")
        usages.append(event.opcode)
        index += 1
    releases = events[index:index + len(usages)]
    if len(releases) != len(usages) or any(
        release.pressed or release.opcode != usage or (i < len(usages) - 1 and release.ticks != 6)
        for i, (release, usage) in enumerate(zip(releases, reversed(usages)))
    ):
        raise ValueError("unsupported keyboard release order")
    return "+".join(_KEY_NAMES[usage] for usage in usages), releases[-1].ticks, index + len(usages)


def _decode_sequence(events: list[_Event]) -> str:
    steps = []
    index = 0
    while index < len(events):
        name, ticks, index = _decode_step(events, index)
        if not ticks:
            raise ValueError("unsupported zero-delay event")
        chunk = ticks
        while chunk == 127 and index < len(events):
            next_event = events[index]
            if (next_event.opcode or next_event.pressed or not next_event.ticks
                    or (ticks + next_event.ticks) * 8 > MAX_DELAY_MS):
                break
            chunk = next_event.ticks
            ticks += chunk
            index += 1
        if not name or ticks != DEFAULT_DELAY_MS // 8:
            name += f"@{ticks * 8}"
        steps.append(name)
    return ", ".join(steps)


def _decode_macro(base: int, image: bytes, slot: Slot) -> dict:
    repeat = _REPEAT_NAMES.get(slot.flags)
    if repeat is None or not 1 <= slot.words <= 127:
        raise ValueError("unsupported macro slot")
    start, end = slot.address - base, slot.end - base
    if not 0 <= start < start + 2 <= end <= len(image):
        raise ValueError("invalid macro boundaries")
    count = int.from_bytes(image[start:start + 2], "little")
    if not count or (repeat != "count" and count != 1):
        raise ValueError("unsupported macro repeat count")
    events, used_end = _read_events(image, start + 2, end, slot.words)
    sequence = _stock_shortcut(events) if repeat == "count" and count == 1 else None
    if sequence is None:
        sequence = _decode_sequence(events)
        encoded, words = encode_macro(parse_action(sequence), count)
        stored = image[start:used_end]
        if len(sequence) > 2048 or words != slot.words or encoded[:len(stored)] != stored:
            raise ValueError("macro cannot be represented by the editor")
    action = {"type": "macro", "repeat": repeat, "sequence": sequence}
    if repeat == "count":
        action["count"] = count
    return action


def decode_profile(base: int, image: bytes) -> dict:
    """Return importable assignments and labels for unsupported stored bytes."""
    verify_matrix(image)
    slots = None
    if any(image[offset] in MACRO_TRIGGERS for offset in BUTTONS.values()):
        try:
            slots = _slots(base, image)
        except ProtocolError:
            # Keep the direct key readback even if the whole slot table is unrecognized.
            pass
    keys = {}
    unsupported = {}
    for button, offset in BUTTONS.items():
        code = image[offset]
        if code in MACRO_TRIGGERS:
            index = MACRO_TRIGGERS.index(code)
            if slots is not None:
                try:
                    keys[button] = _decode_macro(base, image, slots[index])
                    continue
                except ValueError:
                    pass
            unsupported[button] = f"매크로 슬롯 {index + 1} · 편집 형식 미지원"
        elif code in _DIRECT_NAMES:
            kind, name = _DIRECT_NAMES[code]
            keys[button] = {"type": kind, "action": name}
        elif code in _KEY_NAMES:
            keys[button] = _KEY_NAMES[code]
        else:
            unsupported[button] = f"장치 동작 코드 0x{code:02X} · 편집 형식 미지원"
    return {"profile": {"keys": keys}, "unsupported": unsupported}


def compare_profile(base: int, image: bytes, draft: dict) -> dict:
    """Compare encoded actions and select only explicit, safe hardware updates."""
    editor = parse_profile(draft)
    decoded = decode_profile(base, image)
    device_values = decoded["profile"]["keys"]
    device = parse_profile(decoded["profile"])
    draft_values = draft.get("keys", {})
    differences = []
    changes = {}
    for button in BUTTONS:
        previous, desired = device.get(button), editor.get(button)
        if previous is None and desired is None:
            continue
        if previous is not None and desired is not None and (
            previous.key, previous.body, previous.words, previous.flags
        ) == (desired.key, desired.body, desired.words, desired.flags):
            continue
        differences.append({
            "button": button,
            "device": device_values.get(button),
            "editor": draft_values[button] if desired is not None else None,
        })
        if desired is not None:
            changes[button] = draft_values[button]
    return {**decoded, "differences": differences, "changes": {"keys": changes}}
