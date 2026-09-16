"""FAC (Atari 8-bit, J.B. Wisniewski 1992) -- engine and pygame front end, see SPEC.md.

Conversion by Quaerendir. Original game (C) 1992 J.B. Wisniewski / Tajemnice ATARI.
"""
from .engine import (Engine, Event, EventKind, GameState, Glyph, Key, Level, Status, Variant,
                     LEVEL_H, LEVEL_W, MH, TIME_TICKS, parse_levels)

__all__ = ['Engine', 'Event', 'EventKind', 'GameState', 'Glyph', 'Key', 'Level', 'Status', 'Variant',
           'LEVEL_H', 'LEVEL_W', 'MH', 'TIME_TICKS', 'parse_levels']
