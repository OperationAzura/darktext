"""Profile-scoped hover decoding for the recorded alchemist screens."""
import re

from .ram_text import BUFFER_OFFSET, BUFFER_SIZE
from .ram_session import context, popup_state


def formula_rows(raw):
    """Preserve character slot and all four formula ordinals per character."""
    end = raw.find(b'\0')
    if end < 0:
        raise ValueError('Unterminated formula buffer')
    groups = raw[:end].split(b'\x14')
    if not 1 <= len(groups) <= 4:
        raise ValueError('Unsupported formula group count')
    rows = []
    for slot, group in enumerate(groups):
        match = re.fullmatch(rb'\x81([ -~]+)\xff\n(.*)', group, flags=re.S)
        if not match:
            raise ValueError('Unsupported character heading')
        character = match[1].decode('ascii')
        parts = match[2].split(b'\x15')
        if parts[0] or len(parts) != 5:
            raise ValueError('Expected four formula slots')
        for index, part in enumerate(parts[1:]):
            match = re.fullmatch(rb' *[\x80-\x8f]([ -~]+)\n*', part)
            if not match:
                raise ValueError('Unsupported formula row')
            rows.append({'character_slot': slot, 'character': character,
                         'formula_slot': index, 'text': match[1].decode('ascii').strip()})
    return rows


def selected_option(segment, cache, base):
    owner = int.from_bytes(segment[0xA88D:0xA88F], 'little')
    # Coverage deliberately restricted to the two screens from the hover test.
    if owner not in (0x1A, 0x59) or cache.context != (base, context(segment)):
        return None
    if cache.pending != cache.processed_key:
        return None
    if cache.popup_quarantined or popup_state(segment) == 'uncertain':
        return None
    count, row = segment[0xE96E:0xE970]
    if not 0 < count <= 20 or row >= count:
        return None  # 0xff is the no-row sentinel, including character headings
    raw_row = segment[0xE9B0 + row]
    if raw_row >= 20 or segment[0xE99C + raw_row] != 1:
        return None
    mode = segment[0xEE42]
    result = {'row': row, 'raw_row': raw_row, 'owner': owner}
    if mode == 1 and popup_state(segment) == 'open' and owner == 0x59:
        try:
            rows = formula_rows(segment[BUFFER_OFFSET:BUFFER_OFFSET + BUFFER_SIZE])
        except ValueError:
            return None
        if raw_row >= len(rows) or count > len(rows):
            return None
        result.update(rows[raw_row], source='formula_popup')
        result['speech_text'] = f"{result['character']}: {result['text']}"
    elif mode == 0 and cache.dialog and cache.parent_usable:
        options = cache.dialog['candidate_options']
        if raw_row >= len(options) or not any(c.isalpha() for c in options[raw_row]):
            return None
        result.update(source='dialog', text=options[raw_row], speech_text=options[raw_row])
    else:
        return None
    return result


class SelectionTracker:
    def __init__(self):
        self.pending = None
        self.emitted = None

    def observe(self, segment, cache, base):
        selection = selected_option(segment, cache, base)
        if selection != self.pending:
            self.pending = selection
            self.emitted = None
            return {'kind': 'selection_cleared', 'selection': None}
        if selection == self.emitted:
            return None
        self.emitted = selection
        return {'kind': 'selection_changed', 'selection': selection}

    def reset(self):
        self.pending = self.emitted = None
