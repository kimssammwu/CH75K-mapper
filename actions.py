"""Parse local CH75K keyboard and mouse macro steps."""

from dataclasses import dataclass
import re


DEFAULT_DELAY_MS = 50
MAX_DELAY_MS = 65535
MAX_MOUSE_MOVE = 32767
MAX_MOUSE_WHEEL = 127


class ActionError(ValueError):
    """The requested action is not a valid CH75K input expression."""


@dataclass(frozen=True)
class Step:
    usages: tuple[int, ...]
    delay_ms: int = DEFAULT_DELAY_MS
    mouse: tuple[str, tuple[int, ...]] | None = None


# USB HID Usage Tables, Keyboard/Keypad page (0x07).
KEYS = {
    "enter": 0x28, "return": 0x28,
    "esc": 0x29, "escape": 0x29,
    "backspace": 0x2A, "tab": 0x2B, "space": 0x2C, "spacebar": 0x2C,
    "minus": 0x2D, "-": 0x2D, "hyphen": 0x2D,
    "equal": 0x2E, "equals": 0x2E, "=": 0x2E,
    "leftbracket": 0x2F, "[": 0x2F, "rightbracket": 0x30, "]": 0x30,
    "backslash": 0x31, "\\": 0x31,
    "semicolon": 0x33, ";": 0x33, "apostrophe": 0x34, "'": 0x34, "quote": 0x34,
    "grave": 0x35, "`": 0x35, "backtick": 0x35,
    "comma": 0x36, "period": 0x37, "dot": 0x37, ".": 0x37,
    "slash": 0x38, "/": 0x38, "capslock": 0x39,
    "printscreen": 0x46, "scrolllock": 0x47, "pause": 0x48,
    "insert": 0x49, "ins": 0x49, "home": 0x4A,
    "pageup": 0x4B, "pgup": 0x4B, "delete": 0x4C, "del": 0x4C, "end": 0x4D,
    "pagedown": 0x4E, "pgdn": 0x4E, "right": 0x4F, "left": 0x50,
    "down": 0x51, "up": 0x52, "numlock": 0x53,
    "keypadslash": 0x54, "keypadasterisk": 0x55, "keypadminus": 0x56,
    "keypadplus": 0x57, "keypadenter": 0x58,
    "keypadperiod": 0x63, "menu": 0x65,
    "ctrl": 0xE0, "control": 0xE0, "lctrl": 0xE0, "leftctrl": 0xE0,
    "lshift": 0xE1, "leftshift": 0xE1, "shift": 0xE1,
    "alt": 0xE2, "lalt": 0xE2, "leftalt": 0xE2,
    "option": 0xE2, "loption": 0xE2, "leftoption": 0xE2,
    "win": 0xE3, "windows": 0xE3, "meta": 0xE3, "super": 0xE3,
    "gui": 0xE3, "lwin": 0xE3, "leftwin": 0xE3,
    "cmd": 0xE3, "command": 0xE3, "lcmd": 0xE3, "leftcommand": 0xE3,
    "rctrl": 0xE4, "rightctrl": 0xE4, "rightcontrol": 0xE4,
    "rshift": 0xE5, "rightshift": 0xE5,
    "ralt": 0xE6, "rightalt": 0xE6, "altgr": 0xE6,
    "roption": 0xE6, "rightoption": 0xE6,
    "rwin": 0xE7, "rightwin": 0xE7, "rmeta": 0xE7, "rightmeta": 0xE7,
    "rcmd": 0xE7, "rightcommand": 0xE7,
}
KEYS.update({letter: 4 + index for index, letter in enumerate("abcdefghijklmnopqrstuvwxyz")})
KEYS.update({digit: 0x1E + index for index, digit in enumerate("1234567890")})
KEYS.update({f"f{index}": 0x39 + index for index in range(1, 13)})
KEYS.update({f"f{index}": 0x68 + (index - 13) for index in range(13, 25)})
KEYS.update({f"keypad{digit}": 0x59 + index for index, digit in enumerate("1234567890")})


def _usage(token: str) -> int:
    name = token.strip().casefold()
    if name not in KEYS:
        name = re.sub(r"[\s_-]+", "", name)
    try:
        return KEYS[name]
    except KeyError:
        raise ActionError(f"Unknown key {token.strip()!r}") from None


def _mouse_action(text: str, number: int) -> tuple[str, tuple[int, ...]]:
    if text in {"mouseleft", "mouseright", "mousemiddle"}:
        return text.removeprefix("mouse"), ()

    move = re.fullmatch(r"mousemove\s*\(\s*([+-]?[0-9]{1,5})\s+([+-]?[0-9]{1,5})\s*\)", text)
    if move:
        dx, dy = map(int, move.groups())
        if not (-MAX_MOUSE_MOVE <= dx <= MAX_MOUSE_MOVE and -MAX_MOUSE_MOVE <= dy <= MAX_MOUSE_MOVE):
            raise ActionError(f"Step {number} mouse coordinates must be −{MAX_MOUSE_MOVE}–{MAX_MOUSE_MOVE}")
        return "move", (dx, dy)

    wheel = re.fullmatch(r"mousewheel\s*\(\s*([+-]?[0-9]{1,5})\s*\)", text)
    if wheel:
        distance = int(wheel[1])
        if not -MAX_MOUSE_WHEEL <= distance <= MAX_MOUSE_WHEEL:
            raise ActionError(f"Step {number} mouse wheel must be −{MAX_MOUSE_WHEEL}–{MAX_MOUSE_WHEEL}")
        return "wheel", (distance,)

    raise ActionError(f"Step {number} must use MouseLeft, MouseRight, MouseMiddle, MouseMove(x y), or MouseWheel(n)")


def parse_action(text: str) -> tuple[Step, ...]:
    """Parse comma-separated chords, mouse actions, or @wait in milliseconds."""
    if not isinstance(text, str):
        raise ActionError("An action must be text")
    if not text.strip():
        return ()

    steps = []
    for number, expression in enumerate(text.split(","), start=1):
        command, has_delay, delay_text = expression.partition("@")
        delay = DEFAULT_DELAY_MS
        if has_delay:
            delay_text = delay_text.strip()
            if not re.fullmatch(r"[0-9]{1,5}", delay_text):
                raise ActionError(f"Step {number} delay must be 0–{MAX_DELAY_MS} ms")
            delay = int(delay_text)
            if delay > MAX_DELAY_MS:
                raise ActionError(f"Step {number} delay must be 0–{MAX_DELAY_MS} ms")

        name = command.strip().casefold()
        if not name and has_delay:
            steps.append(Step((), delay))
            continue
        if name.startswith("mouse"):
            steps.append(Step((), delay, _mouse_action(name, number)))
            continue

        tokens = command.split("+")
        if any(not token.strip() for token in tokens):
            raise ActionError(f"Step {number} has an empty key")
        usages = tuple(_usage(token) for token in tokens)
        if len(set(usages)) != len(usages):
            raise ActionError(f"Step {number} repeats a key")
        usages = tuple(sorted(usages, key=lambda usage: usage < 0xE0))
        steps.append(Step(usages, delay))
    return tuple(steps)
