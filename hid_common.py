"""USB fingerprint and HID report descriptor parsing shared by both hosts."""

USB_IDS = frozenset({(0x30FA, 0x2350), (0x2130, 0x30FA)})
CONFIG_PAGE = 0xFF01
COMPANION_PAGE = 0xFF00
VENDOR_COLLECTIONS = frozenset({(CONFIG_PAGE, 1), (COMPANION_PAGE, 1)})
MAX_DESCRIPTOR = 4096


class HidrawError(RuntimeError):
    """The interface could not be found, verified or used."""


def descriptor_features(descriptor: bytes) -> dict[tuple[int, int], dict[int, int]]:
    """Return top-level application usages and matching feature report lengths."""
    offset = 0
    usage_page = report_size = report_id = report_count = 0
    globals_stack = []
    top_usage = None
    local_usage = None
    depth = 0
    report_bits: dict[int, int] = {}
    collections: dict[tuple[int, int], set[int]] = {}

    while offset < len(descriptor):
        prefix = descriptor[offset]
        offset += 1
        if prefix == 0xFE:
            if offset + 2 > len(descriptor):
                raise ValueError("incomplete long descriptor item")
            offset += 2 + descriptor[offset]
            if offset > len(descriptor):
                raise ValueError("truncated long descriptor item")
            continue

        length = (0, 1, 2, 4)[prefix & 3]
        end = offset + length
        if end > len(descriptor):
            raise ValueError("truncated descriptor item")
        value = int.from_bytes(descriptor[offset:end], "little")
        offset = end
        item_type, tag = (prefix >> 2) & 3, prefix >> 4

        if item_type == 1:
            if tag == 0:
                usage_page = value
            elif tag == 7:
                report_size = value
            elif tag == 8:
                if not 1 <= value <= 255:
                    raise ValueError("invalid report ID")
                report_id = value
            elif tag == 9:
                report_count = value
            elif tag == 10:
                globals_stack.append((usage_page, report_size, report_id, report_count))
            elif tag == 11:
                if not globals_stack:
                    raise ValueError("unbalanced descriptor globals")
                usage_page, report_size, report_id, report_count = globals_stack.pop()
        elif item_type == 2:
            if tag == 0 and local_usage is None:
                local_usage = (value >> 16, value & 0xFFFF) if length == 4 else (usage_page, value)
        elif item_type == 0:
            if tag == 10:
                if depth == 0:
                    top_usage = local_usage if value == 1 else None
                    if top_usage is not None:
                        collections.setdefault(top_usage, set())
                depth += 1
            elif tag == 12:
                depth -= 1
                if depth < 0:
                    raise ValueError("unbalanced descriptor collections")
                if depth == 0:
                    top_usage = None
            elif tag == 11:
                report_bits[report_id] = report_bits.get(report_id, 0) + report_count * report_size
                if top_usage is not None:
                    collections[top_usage].add(report_id)
            local_usage = None

    if depth or globals_stack:
        raise ValueError("unclosed descriptor collection or global state")
    return {
        usage: {number: (report_bits[number] + 7) // 8 + 1 for number in sorted(reports)}
        for usage, reports in collections.items()
    }
