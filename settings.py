"""CH75K profile verification, keyboard macro encoding, and write planning."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from struct import pack

from actions import Step, parse_action
from protocol import BLOCK_SIZE, ProtocolError

BUTTONS = {
    "top-left": 0x70, "top-center": 0x78, "top-right": 0x80,
    "bottom-left": 0x62, "bottom-center": 0xE2, "bottom-right": 0xDA,
}
KEY_MATRIX = 0x48
KEY_MATRIX_END = 0xE8
EXTENDED_KEYS_END = 0xF4
TABLE = 0xF8
SLOT_COUNT = 20
ENTRY_SIZE = 5
MACRO_END = 0x7B0
MACRO_TRIGGERS = tuple(range(0xA5, 0xB0)) + tuple(range(0xB8, 0xC1))
MOUSE_MOVE = 0xF6
MOUSE_WHEEL = 0xF7
MOUSE_BUTTONS = {"left": 0xF1, "right": 0xF3, "middle": 0xF2}
MEDIA_ACTIONS = {
    "play-pause": 0xC9, "volume-down": 0xCA, "volume-up": 0xCB,
    "next-track": 0xCC, "previous-track": 0xCD, "player": 0xCE,
    "mail": 0xCF, "browser-forward": 0xD0, "browser-stop": 0xD1,
    "browser-back": 0xD2, "browser-reload": 0xD3, "browser-search": 0xD4,
    "browser-favorites": 0xD5, "mute": 0xD6, "computer": 0xD7,
    "stop": 0xD8, "calculator": 0xD9, "browser": 0xDA,
}
SYSTEM_ACTIONS = {"power": 0xB0, "sleep": 0xB1}
DIRECT_ACTIONS = {"media": MEDIA_ACTIONS, "system": SYSTEM_ACTIONS}
MAX_REPEAT_COUNT = 65535
# Hold is the high bit of the low length byte. Toggle is the high bit of the address byte.
REPEAT_FLAGS = {"count": 0, "hold": 0x0080, "toggle": 0x8000}


def _align8(value: int) -> int:
    return (value + 7) & ~7


PROFILE_PREFIX = _align8(TABLE + SLOT_COUNT * ENTRY_SIZE)


@dataclass(frozen=True)
class Action:
    text: str
    key: int | None
    body: bytes = b""
    words: int = 0
    flags: int = 0
    kind: str = "keyboard"


@dataclass(frozen=True)
class Change:
    address: int
    before: bytes
    after: bytes


def encode_macro(steps: tuple[Step, ...], count: int = 1) -> tuple[bytes, int]:
    body = bytearray(count.to_bytes(2, "little"))
    words = 0

    def emit(opcode: int, delay: int, payload: bytes = b"", *, down: bool = False):
        nonlocal words
        body.extend((min(delay, 127) | (0x80 if down else 0), opcode))
        body.extend(payload)
        words += 1
        delay -= 127
        while delay > 0:
            body.extend((min(delay, 127), 0))
            words += 1
            delay -= 127

    for step in steps:
        delay = max(1, step.delay_ms // 8)
        if step.mouse is not None:
            kind, args = step.mouse
            if kind == "move":
                emit(MOUSE_MOVE, delay, pack("<hh", *args))
            elif kind == "wheel":
                emit(MOUSE_WHEEL, delay, bytes((args[0] & 255, 0)))
            else:
                code = MOUSE_BUTTONS[kind]
                emit(code, 6, down=True)
                emit(code, delay)
            continue
        if not step.usages:
            emit(0, delay)
            continue
        for usage in step.usages:
            emit(usage, 6, down=True)  # 48 ms at key-down, as in the stock shortcuts.
        for usage in reversed(step.usages[1:]):
            emit(usage, 6)
        emit(step.usages[0], delay)
    if not 1 <= words <= 127:
        raise ValueError("매크로는 대기 단계를 포함해 1~127개의 이벤트를 저장할 수 있습니다.")
    return bytes(body).ljust(_align8(len(body)), b"\x00"), words


def _parse_binding(value: str | dict) -> Action | None:
    if isinstance(value, str):
        steps = parse_action(value)
        if not steps:
            return None
        if len(steps) == 1 and len(steps[0].usages) == 1 and "@" not in value:
            return Action(value, steps[0].usages[0])
        body, words = encode_macro(steps)
        return Action(value, None, body, words, kind="macro")

    if not isinstance(value, dict):
        raise ValueError("키 입력 또는 동작 설정이 필요합니다.")
    kind = value.get("type")
    if isinstance(kind, str) and kind in DIRECT_ACTIONS:
        name = value.get("action")
        if value.keys() != {"type", "action"} or not isinstance(name, str) or name not in DIRECT_ACTIONS[kind]:
            raise ValueError("알 수 없는 미디어 또는 시스템 동작입니다.")
        return Action(name, DIRECT_ACTIONS[kind][name], kind=kind)

    if kind != "macro" or value.keys() - {"type", "sequence", "repeat", "count"}:
        raise ValueError("지원하지 않는 동작 설정입니다.")
    repeat = value.get("repeat")
    if not isinstance(repeat, str) or repeat not in REPEAT_FLAGS:
        raise ValueError("매크로 반복 방식을 선택하세요.")
    count = 1
    if repeat == "count":
        count = value.get("count")
        if type(count) is not int or not 1 <= count <= MAX_REPEAT_COUNT:
            raise ValueError(f"반복 횟수는 1~{MAX_REPEAT_COUNT} 사이의 정수여야 합니다.")
        repeat_label = f"{count}회"
    else:
        if "count" in value:
            raise ValueError("횟수 지정 방식에서만 count를 설정할 수 있습니다.")
        repeat_label = {"hold": "누르는 동안 반복", "toggle": "다시 누를 때까지 반복"}[repeat]
    sequence = value.get("sequence")
    steps = parse_action(sequence)
    if not steps:
        raise ValueError("매크로 동작을 하나 이상 입력하세요.")
    body, words = encode_macro(steps, count)
    return Action(f"{sequence} · {repeat_label}", None, body, words,
                  flags=REPEAT_FLAGS[repeat], kind="macro")


def parse_profile(profile: dict) -> dict[str, Action]:
    if not isinstance(profile, dict) or profile.keys() - {"keys", "dial"}:
        raise ValueError("프로필의 keys 항목에 버튼 설정을 넣으세요.")
    if profile.get("dial", {}) != {}:
        raise ValueError("다이얼은 고정 볼륨 기능입니다. 이전 프로필은 입체 매핑 화면에서 불러오세요.")
    keys = profile.get("keys", {})
    if not isinstance(keys, dict) or keys.keys() - BUTTONS.keys():
        raise ValueError("알 수 없는 버튼 위치입니다.")
    parsed = {}
    for button, value in keys.items():
        try:
            action = _parse_binding(value)
            if action is not None:
                parsed[button] = action
        except ValueError as exc:
            raise ValueError(f"{button}: {exc}") from exc
    return parsed


def profile_base(header: bytes) -> int:
    if len(header) != 8 or not header.startswith(b"\xe1\x1e") or header[4:] != b"\xf0\x0f\0\0":
        raise ProtocolError("CH75K 프로필 헤더를 찾지 못했습니다. 설정을 쓰지 않았습니다.")
    base = int.from_bytes(header[2:4], "little")
    if base < 0xD8 or base % BLOCK_SIZE or base + PROFILE_PREFIX >= MACRO_END:
        raise ProtocolError("CH75K 프로필 시작 주소가 예상 범위를 벗어났습니다.")
    return base


def verify_matrix(data: bytes):
    visible = set(BUTTONS.values())
    if len(data) < PROFILE_PREFIX or data[0x52] != 0xB1:
        raise ProtocolError("CH75K에 해당하는 6버튼 설정 구조를 확인하지 못했습니다.")
    if any(data[offset] != 0xFF for offset in range(KEY_MATRIX, KEY_MATRIX_END)
           if offset not in visible and offset != 0x52):
        raise ProtocolError("연결된 키보드의 버튼 배열이 CH75K 설정과 다릅니다.")


@dataclass
class Slot:
    address: int
    words: int
    flags: int
    end: int = 0

    def entry(self) -> bytes:
        return bytes((7, (self.words & 127) | (self.flags & 128),
                      self.address & 255, (self.address >> 8) | (self.flags >> 8),
                      self.words >> 7))


def _slots(base: int, data: bytes) -> list[Slot]:
    slots = []
    minimum = base + PROFILE_PREFIX
    for i in range(SLOT_COUNT):
        offset = TABLE + i * ENTRY_SIZE
        marker, low, address_low, address_high, high = data[offset:offset + ENTRY_SIZE]
        address = address_low | ((address_high & 63) << 8)
        words = (low & 127) | (high << 7)
        flags = (low & 128) | ((address_high & 192) << 8)
        if marker != 7 or address % 8 or not minimum <= address <= MACRO_END:
            raise ProtocolError("기존 매크로 슬롯 표를 확인하지 못했습니다.")
        slots.append(Slot(address, words, flags))
    for current, following in zip(slots, slots[1:]):
        current.end = following.address
        minimum_size = _align8(2 + current.words * 2) if current.words else 0
        if current.end < current.address + minimum_size or (not current.words and current.end != current.address):
            raise ProtocolError("기존 매크로 저장 범위가 겹치거나 예상 구조와 다릅니다.")
    last = slots[-1]
    last.end = last.address
    if last.words:
        minimum_end = last.address + _align8(2 + last.words * 2)
        if minimum_end > MACRO_END:
            raise ProtocolError("마지막 매크로가 저장 한계를 벗어납니다.")
        last.end = _macro_end(base, data, last) or MACRO_END
    return slots


def macro_storage_end(base: int, prefix: bytes) -> int:
    """Use the end of allocated macro data when the slot table establishes it."""
    try:
        return _slots(base, prefix)[-1].end
    except ProtocolError:
        return MACRO_END


def _macro_end(base: int, data: bytes, slot: Slot) -> int | None:
    cursor = slot.address - base + 2
    for _ in range(slot.words):
        if cursor + 2 > len(data):
            return None
        opcode = data[cursor + 1]
        cursor += 2
        if opcode == MOUSE_MOVE:
            cursor += 4
        elif opcode == MOUSE_WHEEL:
            cursor += 2
        elif opcode > MOUSE_WHEEL:
            return None  # Vendor extensions can reference auxiliary blocks after the event stream.
    end = _align8(base + cursor)
    return end if end <= base + len(data) else None


def plan_changes(base: int, original: bytes, keys: dict[str, Action], *,
                 preserve_slots_for: frozenset[str] = frozenset()) -> list[Change]:
    verify_matrix(original)
    edited = bytearray(original)
    phases = {}

    def set_bytes(address: int, value: bytes, phase: int):
        offset = address - base
        if offset < 0 or offset + len(value) > len(edited):
            raise ProtocolError("설정 변경이 읽어 둔 영역을 벗어납니다.")
        edited[offset:offset + len(value)] = value
        for block in range(offset & ~7, _align8(offset + len(value)), BLOCK_SIZE):
            phases[block] = phase

    macros = any(action.key is None for action in keys.values())
    slots = _slots(base, original) if macros else []
    read_end = base + len(original)
    if slots and not slots[-1].end <= read_end <= MACRO_END:
        raise ProtocolError("기존 매크로 저장 영역을 읽은 뒤 다시 시도하세요.")
    references = Counter(original[KEY_MATRIX:EXTENDED_KEYS_END])
    for button, action in keys.items():
        offset = BUTTONS[button]
        current = original[offset]
        if action.key is not None:
            set_bytes(base + offset, bytes((action.key,)), 2)
            continue
        existing = MACRO_TRIGGERS.index(current) if current in MACRO_TRIGGERS else None
        matches = next((i for i, slot in enumerate(slots)
                        if slot.words == action.words and slot.flags == action.flags
                        and slot.end - slot.address == len(action.body)
                        and edited[slot.address - base:slot.address - base + len(action.body)] == action.body),
                       None)
        if matches is not None:
            slot_id = matches
        elif (button not in preserve_slots_for and existing is not None and references[current] == 1
              and slots[existing].words
              and slots[existing].flags & 0x4000 == 0
              and _macro_end(base, edited, slots[existing]) == slots[existing].end
              and slots[existing].end - slots[existing].address == len(action.body)):
            slot_id = existing
            slot = slots[slot_id]
            set_bytes(slot.address, action.body, 0)
            slot.words, slot.flags = action.words, action.flags
        else:
            slot_id = next((i for i in range(SLOT_COUNT)
                            if not any(slot.words for slot in slots[i:])), None)
            if slot_id is None:
                raise ProtocolError("기존 매크로를 보존하며 추가할 수 있는 슬롯이 없습니다.")
            slot = slots[slot_id]
            end = slot.address + len(action.body)
            if end > read_end or any(value not in (0, 255) for value in edited[slot.address - base:end - base]):
                raise ProtocolError("기존 매크로를 보존할 수 있는 빈 저장 영역이 부족합니다.")
            set_bytes(slot.address, action.body, 0)
            slot.words, slot.flags, slot.end = action.words, action.flags, end
            for empty in slots[slot_id + 1:]:
                empty.address = empty.end = end
        selected = MACRO_TRIGGERS[slot_id]
        set_bytes(base + offset, bytes((selected,)), 2)
        if selected != current:
            references[current] -= 1
            references[selected] += 1

    for slot_id, slot in enumerate(slots):
        set_bytes(base + TABLE + slot_id * ENTRY_SIZE, slot.entry(), 1)
    return [Change(base + offset, original[offset:offset + BLOCK_SIZE],
                   bytes(edited[offset:offset + BLOCK_SIZE]))
            for offset in sorted(phases, key=lambda block: (phases[block], block))
            if original[offset:offset + BLOCK_SIZE] != edited[offset:offset + BLOCK_SIZE]]
