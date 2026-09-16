"""Stage 4: pygame rendering, sound and the timed sequences of FAC.

Everything visible in the original is either the GRAPHICS 18 screen memory (the engine keeps
it), the charset at $9800 (which the animations modify byte by byte: L515, L660..L680,
L710) or the GRAPHICS 0 menu. Sound is POKEY channel 1 driven by SOUND statements; the
multi-statement loops of the listing (jingles, sweeps, the death) are reproduced here as
`Step` sequences with the estimated Atari BASIC statement timings of SPEC.md section 7.
"""
from __future__ import annotations

import array
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Sequence

import pygame

from .engine import LEVEL_H, LEVEL_W, Glyph, Level, atascii_to_screen, row_to_codes

FPS = 50                              # PAL
FRAME_MS = 1000 / FPS
CHAR_PX = 8                           # 8 pixel columns x 8 rows per glyph; GRAPHICS 18 doubles both
SCREEN_W, SCREEN_H = 320, 192         # GRAPHICS 18: 20 x 16 px by 12 x 16 lines; GRAPHICS 0: 40 x 24 chars
GR0_COLS, GR0_ROWS, LMARGN = 40, 24, 2
GR18_DEFAULT_COLORS = (0x28, 0xCA, 0x94, 0x46, 0x00)   # OS defaults for 708..712 (intro screen, L5)
GR0_DEFAULT = {'text': 0xCA, 'bg': 0x94, 'border': 0x00}   # 709, 710, 712 after GRAPHICS 0 (L55)
CHARSET_GLYPHS = 26
HERO_BASE = 13                        # CHR$(13+ZW): glyph 12, 13, 14

# --- timings, all [EST] (SPEC.md section 7): Atari BASIC statement costs in ms
TICK_MS = 100.0                       # one pass of the main loop without a key
MS_STATEMENT = 1.0                    # gap between two SOUND statements of the same pass
MS_LOOP_SOUND = 7.0                   # FOR iteration holding one SOUND
MS_LOOP_POKE = 15.0                   # FOR iteration with POKE ... ASC(C$(J+1)) and a SOUND (L35..L45, L710..L715)
MS_DEATH_STEP = 30.0                  # L660..L680: two PEEK/POKE expressions and a SOUND
MS_ROW_LOAD = 25.0                    # L165..L170: SOUND, READ C$, ? #6;C$
MS_NOTE_EXTRA = 12.0                  # L400..L405: POKE 755, READ KL
MS_FADE_STEP = 5.0                    # L720..L725: SOUND with a fractional STEP loop
MS_MENU_CLICK = 8.0                   # L130: SOUND 1,50,10,6 : SOUND 1,0,0,0

SoundRegs = tuple[int, int, int]      # SOUND 1,f,d,v


@dataclass
class Step:
    """One timed unit of a sequence: apply `action`, set the SOUND registers, hold for `ms`."""
    ms: float
    sound: SoundRegs | None = None
    action: Callable[[], None] | None = None


def atari_rgb(code: int) -> tuple[int, int, int]:
    """GTIA colour code (hue*16 + luminance) -> RGB, a common YIQ approximation."""
    hue, lum = (code >> 4) & 0xF, code & 0xF
    y = lum / 15.0
    if hue == 0:
        v = round(y * 255)
        return v, v, v
    angle = math.radians((hue - 1) * 24.0 - 33.0)
    sat = 0.30
    i, q = sat * math.cos(angle), sat * math.sin(angle)
    r = y + 0.956 * i + 0.621 * q
    g = y - 0.272 * i - 0.647 * q
    b = y - 1.106 * i + 1.703 * q
    return tuple(max(0, min(255, round(v * 255))) for v in (r, g, b))  # type: ignore[return-value]


class Charset:
    """The 64-glyph character set at $9800 as the game leaves it: 26 glyphs from charset.bin,
    the rest is whatever RAM held (zero here). Animations POKE into it; `restore_hero_byte`
    is L710 (glyphs 12..15 re-read from DATA 1070/1080 after a death)."""

    def __init__(self, data: bytes) -> None:
        if len(data) != CHARSET_GLYPHS * 8:
            raise ValueError(f'charset.bin must be {CHARSET_GLYPHS * 8} bytes, got {len(data)}')
        self.original = bytes(data)
        self.data = bytearray(64 * 8)
        self.data[:len(data)] = data

    def glyph(self, n: int) -> bytes:
        return bytes(self.data[n * 8:n * 8 + 8])

    def hero_flip_step(self, zw: int, i: int) -> None:
        """L515..L520: swap bytes I and 7-I of the hero glyph (poison carpet)."""
        base = (HERO_BASE + zw) * 8
        self.data[base + i], self.data[base + 7 - i] = self.data[base + 7 - i], self.data[base + i]

    def hero_death_step(self, zw: int, j: int) -> None:
        """L660..L680: byte 2J halved, byte 2J+1 doubled (mod 256): the hero dissolves."""
        base = (HERO_BASE + zw) * 8
        self.data[base + 2 * j] = self.data[base + 2 * j] // 2
        self.data[base + 2 * j + 1] = (self.data[base + 2 * j + 1] * 2) & 0xFF

    def restore_hero_byte(self, k: int) -> None:
        """L710: POKE 39008+16*I+J = glyph 12 byte 0 onwards, 32 bytes."""
        self.data[96 + k] = self.original[96 + k]


def load_os_font(path: Path) -> bytes:
    """The Atari OS charset (CHBAS=$E0: the intro text and the menu): a 1 KB dump or an OS ROM
    image (16 KB XL/XE: offset $2000; 10 KB 400/800 OS-B: offset $800)."""
    data = path.read_bytes()
    if len(data) == 1024:
        return data
    if len(data) == 16384:
        return data[0x2000:0x2400]
    if len(data) == 10240:
        return data[0x800:0xC00]
    raise ValueError(f'{path}: expected a 1 KB charset or a 10/16 KB OS ROM, got {len(data)} bytes')


class GlyphCache:
    """Surfaces for 8-byte glyphs: `wide` = 2 px per bit and 2 lines per row (GRAPHICS 18)."""

    def __init__(self, scale: int) -> None:
        self.scale = scale
        self._cache: dict[tuple[bytes, tuple[int, int, int], tuple[int, int, int], bool, bool], pygame.Surface] = {}

    def surface(self, rows: bytes, fg: tuple[int, int, int], bg: tuple[int, int, int],
                wide: bool, flip: bool = False) -> pygame.Surface:
        key = (rows, fg, bg, wide, flip)
        surf = self._cache.get(key)
        if surf is not None:
            return surf
        s = self.scale
        px = 2 * s if wide else s
        surf = pygame.Surface((CHAR_PX * px, CHAR_PX * px))
        surf.fill(bg)
        for r in range(8):
            byte = rows[7 - r] if flip else rows[r]
            for c in range(8):
                if byte & (0x80 >> c):
                    surf.fill(fg, (c * px, r * px, px, px))
        self._cache[key] = surf
        return surf


class StandInFont:
    """When no OS charset is given: a system font shaped into 8x8 cells for the OS glyphs."""

    def __init__(self, scale: int) -> None:
        pygame.font.init()
        self.scale = scale
        self._font = pygame.font.SysFont('dejavusansmono,liberationmono,couriernew,monospace', 8 * scale, bold=True)
        self._cache: dict[tuple[int, tuple[int, int, int], tuple[int, int, int], bool], pygame.Surface] = {}

    def surface(self, code: int, fg: tuple[int, int, int], bg: tuple[int, int, int], wide: bool) -> pygame.Surface:
        key = (code, fg, bg, wide)
        surf = self._cache.get(key)
        if surf is not None:
            return surf
        s = self.scale
        px = 2 * s if wide else s
        w = h = CHAR_PX * px
        inverse = bool(code & 0x80)
        glyph = code & 0x7F
        f, b = (bg, fg) if inverse else (fg, bg)
        surf = pygame.Surface((w, h))
        surf.fill(b)
        if glyph == 0x59:                                  # ATASCII $19: left half block (menu bar ends)
            surf.fill(f, (0, 0, w // 2, h))
        elif 0 < glyph < 0x40 or 0x60 <= glyph < 0x7B:      # printable: screen code -> ATASCII
            ch = chr(glyph + 0x20) if glyph < 0x40 else chr(glyph)
            text = self._font.render(ch, False, f, b)
            box = pygame.transform.scale(text, (int(w * 0.85), int(h * 0.95)))
            surf.blit(box, ((w - box.get_width()) // 2, (h - box.get_height()) // 2))
        self._cache[key] = surf
        return surf


class Renderer:
    def __init__(self, charset: Charset, scale: int = 3, os_font: bytes | None = None) -> None:
        self.charset = charset
        self.scale = scale
        self.size = (SCREEN_W * scale, SCREEN_H * scale)
        self.glyphs = GlyphCache(scale)
        self.os_font = os_font
        self.stand_in = StandInFont(scale) if os_font is None else None

    def _os_glyph(self, code: int, fg: tuple[int, int, int], bg: tuple[int, int, int], wide: bool) -> pygame.Surface:
        if self.os_font is not None:
            rows = self.os_font[(code & 0x7F) * 8:(code & 0x7F) * 8 + 8]
            if code & 0x80:
                rows = bytes(b ^ 0xFF for b in rows)
            return self.glyphs.surface(rows, fg, bg, wide)
        assert self.stand_in is not None
        return self.stand_in.surface(code, fg, bg, wide)

    def draw_gr18(self, target: pygame.Surface, screen: Sequence[int], colors: Sequence[int],
                  flip: bool = False, os_charset: bool = False) -> None:
        """GRAPHICS 18: 20x12 codes, colour = bits 6-7 -> 708..711, background 712.
        `flip` = CHACT bit 2 (L400), `os_charset` = CHBAS still at $E0 (the intro, L20)."""
        s = self.scale
        pal = [atari_rgb(c) for c in colors[:4]]
        bg = atari_rgb(colors[4])
        target.fill(bg)
        cell = CHAR_PX * 2 * s
        for i, code in enumerate(screen):
            x, y = (i % LEVEL_W) * cell, (i // LEVEL_W) * cell
            fg = pal[(code >> 6) & 3]
            if os_charset:
                surf = self._os_glyph(code & 0x3F | (0x40 if code & 0x40 else 0), fg, bg, True)
            else:
                surf = self.glyphs.surface(self.charset.glyph(code & 0x3F), fg, bg, True, flip)
            target.blit(surf, (x, y))

    def draw_gr0(self, target: pygame.Surface, screen: Sequence[int], cursor: tuple[int, int] | None,
                 text: int = GR0_DEFAULT['text'], bg: int = GR0_DEFAULT['bg']) -> None:
        """GRAPHICS 0: 40x24 codes; text takes the hue of 710 and the luminance of 709."""
        s = self.scale
        fg = atari_rgb((bg & 0xF0) | (text & 0x0F))
        bgc = atari_rgb(bg)
        target.fill(bgc)
        for i, code in enumerate(screen):
            col, row = i % GR0_COLS, i // GR0_COLS
            if cursor is not None and (col, row) == cursor:
                code ^= 0x80                                  # the OS shows the cursor as inverse video
            target.blit(self._os_glyph(code, fg, bgc, False), (col * CHAR_PX * s, row * CHAR_PX * s))


class Gr0Screen:
    """Just enough of the E: handler for the menu (L55..L110): PRINT with cursor-down
    characters, EOL, and POSITION."""

    def __init__(self) -> None:
        self.cells = [0] * (GR0_COLS * GR0_ROWS)
        self.col, self.row = LMARGN, 0

    def print(self, text: bytes, eol: bool = True) -> None:
        for b in text:
            if b == 0x1D:                                     # cursor down
                self.row = (self.row + 1) % GR0_ROWS
            elif b == 0x9B:
                self.col, self.row = LMARGN, (self.row + 1) % GR0_ROWS
            else:
                self.cells[self.row * GR0_COLS + self.col] = atascii_to_screen(b)
                self.col += 1
                if self.col >= GR0_COLS:
                    self.col, self.row = LMARGN, (self.row + 1) % GR0_ROWS
        if eol:
            self.col, self.row = LMARGN, (self.row + 1) % GR0_ROWS

    def position(self, col: int, row: int) -> None:
        self.col, self.row = col, row


# Lines 60..105 of FAC.LST, byte for byte: $1D = cursor down, $99/$19 = the bar ends around the
# inverse console-key names.
MENU_TEXT: tuple[bytes, ...] = (
    b'\x1d\x1d\x1dWYBIERZ WARIANT (\x99\xd3\xc5\xcc\xc5\xc3\xd4\x19)',                    # L60
    b'\x1d\x1d  1. bez ograniczenia',                                                         # L65
    b'  2. z ograniczonym czasem',                                                            # L70
    b'  3. z ograniczonymi krokami',                                                          # L75
    b'  4. z ograniczonym czasem i krokami\x1d\x1d\x1d',                                      # L80
    b'"+" - RUCH W LEWO',                                                                     # L85
    b'"*" - RUCH W PRAWO',                                                                    # L90
    b'" " - PODSKOK\x1d\x1d',                                                                 # L95
    b'ZAGRAJ SOBIE  (\x99\xd3\xd4\xc1\xd2\xd4\x19)\x1d',                                      # L100
    b'LUB ZREZYGNUJ (\x99\xcf\xd0\xd4\xc9\xcf\xce\x19)\x1d\x1d',                              # L105
)
MENU_CURSOR_COL, MENU_CURSOR_ROW = 4, 6      # L110: POSITION 3,Q+6 : ? " "; leaves the cursor at (4, Q+6)


def menu_screen(q: int) -> tuple[list[int], tuple[int, int]]:
    scr = Gr0Screen()
    for line in MENU_TEXT:
        scr.print(line)
    scr.position(3, q + MENU_CURSOR_ROW)
    scr.print(b' ', eol=False)
    return scr.cells, (MENU_CURSOR_COL, q + MENU_CURSOR_ROW)


def intro_screen() -> list[int]:
    """L5..L20: GRAPHICS 18, POSITION 8,5, ? #6;"fac" in the OS charset."""
    cells = [0] * (LEVEL_W * LEVEL_H)
    for k, b in enumerate(b'fac'):
        cells[5 * LEVEL_W + 8 + k] = atascii_to_screen(b)
    return cells


# ---------------------------------------------------------------- sound: POKEY channel 1
POKEY_CLOCK = 1_773_447               # PAL POKEY input clock (Hz)
POKEY_BASE_DIV = 28                   # AUDCTL=0 (SOUND resets it): channel clocked at 64 kHz


def _lfsr(bits: int, tap: int) -> bytes:
    """Output of the maximal-length shift register x^bits + x^tap + 1 (period 2**bits - 1):
    a[n+bits] = a[n] ^ a[n+tap], register shifted right, output at bit 0."""
    reg, out = (1 << bits) - 1, bytearray()
    for _ in range((1 << bits) - 1):
        out.append(reg & 1)
        bit = (reg ^ (reg >> tap)) & 1
        reg = (reg >> 1) | (bit << (bits - 1))
    return bytes(out)


class Pokey:
    """One POKEY channel under AUDCTL=0, run continuously: `render(n)` continues where the
    previous call stopped, so tones and noise are seamless across chunks. AUDC: bit 7 = skip
    the 5-bit poly gate, bit 6 = 4-bit poly instead of 17-bit, bit 5 = pure tone, bits 0-3 =
    volume. `SOUND 1,f,d,v` writes AUDF1=f and AUDC1=d*16+v."""
    POLY4 = _lfsr(4, 3)               # x^4 + x^3 + 1
    POLY5 = _lfsr(5, 3)               # x^5 + x^3 + 1
    POLY17: bytes | None = None

    def __init__(self, sample_rate: int = 44100, amplitude: int = 12000) -> None:
        self.sample_rate = sample_rate
        self.amplitude = amplitude
        if Pokey.POLY17 is None:
            Pokey.POLY17 = _lfsr(17, 12)          # x^17 + x^12 + 1
        self.audf, self.audc = 0, 0
        self._counter, self._out, self._clock = 0, 0, 0
        self._base_acc = 0.0

    def sound(self, f: int, d: int, v: int) -> None:
        self.audf, self.audc = f & 0xFF, ((d & 0xF) << 4) | (v & 0xF)

    def render(self, n_out: int) -> array.array[int]:
        base_hz = POKEY_CLOCK / POKEY_BASE_DIV
        exact = n_out * base_hz / self.sample_rate + self._base_acc
        n_base = int(exact)
        self._base_acc = exact - n_base
        volume = self.audc & 0x0F
        pure, poly4, skip5 = self.audc & 0x20, self.audc & 0x40, self.audc & 0x80
        p4, p5, p17 = Pokey.POLY4, Pokey.POLY5, Pokey.POLY17 or b''
        counter, out, clock, audf = self._counter, self._out, self._clock, self.audf
        levels = bytearray(n_base)
        for t in range(n_base):
            clock += POKEY_BASE_DIV
            if counter == 0:
                counter = audf
                if skip5 or p5[clock % 31]:
                    if pure:
                        out ^= 1
                    elif poly4:
                        out = p4[clock % 15]
                    else:
                        out = p17[clock % 131071]
            else:
                counter -= 1
            levels[t] = out
        self._counter, self._out, self._clock = counter, out, clock % (31 * 15 * 131071)
        samples = array.array('h')
        gain = self.amplitude * volume / 15
        for k in range(n_out):
            a = k * n_base // n_out
            b = max(a + 1, (k + 1) * n_base // n_out)
            if b > n_base:
                b = n_base
            mean = sum(levels[a:b]) / (b - a) if b > a else out
            samples.append(int((mean * 2 - 1) * gain))
        return samples


class Audio:
    """Feeds the POKEY output to a pygame mixer channel, one chunk per rendered slice."""
    RATE = 44100

    def __init__(self, enabled: bool = True) -> None:
        self.pokey = Pokey(self.RATE)
        self.enabled = False
        self.last: SoundRegs = (0, 0, 0)
        self._pending: list[pygame.mixer.Sound] = []
        self._channel: pygame.mixer.Channel | None = None
        if not enabled:
            return
        try:
            pygame.mixer.init(frequency=self.RATE, size=-16, channels=1, buffer=512)
            self._channel = pygame.mixer.Channel(0)
        except pygame.error:
            return
        self.enabled = True

    def sound(self, regs: SoundRegs) -> None:
        self.last = regs
        self.pokey.sound(*regs)

    def emit(self, ms: float) -> None:
        """Render `ms` of the current registers and queue it."""
        n = int(round(ms * self.RATE / 1000))
        if n <= 0:
            return
        samples = self.pokey.render(n)
        if self.enabled:
            self._pending.append(pygame.mixer.Sound(buffer=samples.tobytes()))

    def pump(self) -> None:
        if self._channel is None:
            return
        while self._pending and self._channel.get_queue() is None:
            snd = self._pending.pop(0)
            if self._channel.get_busy():
                self._channel.queue(snd)
            else:
                self._channel.play(snd)


class Sequencer:
    """Runs Step sequences frame by frame with sub-frame audio resolution."""

    def __init__(self, audio: Audio) -> None:
        self.audio = audio
        self._steps: list[Step] = []
        self._left = 0.0
        self._started = False

    @property
    def busy(self) -> bool:
        return bool(self._steps)

    def add(self, steps: Sequence[Step]) -> None:
        self._steps.extend(steps)

    def clear(self) -> None:
        self._steps, self._left, self._started = [], 0.0, False

    def frame(self) -> None:
        budget = FRAME_MS
        while budget > 1e-6:
            if not self._steps:
                self.audio.emit(budget)
                break
            head = self._steps[0]
            if not self._started:
                self._left, self._started = head.ms, True
                if head.action is not None:
                    head.action()
                    if not self._steps or self._steps[0] is not head:
                        self._started = False              # the action replaced the queue (clear/add)
                        continue
                if head.sound is not None:
                    self.audio.sound(head.sound)
            dt = min(self._left, budget)
            self.audio.emit(dt)
            self._left -= dt
            budget -= dt
            if self._left <= 1e-6:
                if self._steps and self._steps[0] is head:
                    self._steps.pop(0)
                self._started = False
        self.audio.pump()


# ---------------------------------------------------------------- sequences from the listing
def tick_steps(sounds: Sequence[SoundRegs], total_ms: float = TICK_MS) -> list[Step]:
    """The SOUND statements of one pass of the main loop: the last one lasts until the next pass."""
    steps = [Step(MS_STATEMENT, s) for s in sounds[:-1]]
    rest = max(total_ms - MS_STATEMENT * len(steps), MS_STATEMENT)
    steps.append(Step(rest, sounds[-1] if sounds else None))
    return steps


def intro_steps(on_byte: Callable[[int], None] | None = None) -> list[Step]:
    """L25..L45: 13 x 16 POKEs into the charset with a falling tone each."""
    steps = []
    for i in range(13):
        for j in range(16):
            k = 16 * i + j
            steps.append(Step(MS_LOOP_POKE, (255 - 12 * i - 7 * j, 10, i + 1),
                              (lambda k=k: on_byte(k)) if on_byte else None))      # type: ignore[misc]
    steps.append(Step(MS_STATEMENT, (0, 0, 0)))
    return steps


def load_steps(level: Level, on_row: Callable[[int], None], on_done: Callable[[], None]) -> list[Step]:
    """L145..L195: the 12 rows appear one by one with a chirp each."""
    steps = [Step(MS_ROW_LOAD, (11 - i, 8, 15 - i), (lambda i=i: on_row(i))) for i in range(LEVEL_H)]  # type: ignore[misc]
    steps.append(Step(MS_STATEMENT, (0, 0, 0), on_done))
    return steps


def treasure_steps() -> list[Step]:
    """L365..L385: three falling notes."""
    steps = [Step(MS_LOOP_SOUND, (k, 10, j)) for k in (120, 80, 40) for j in range(15, -1, -3)]
    steps.append(Step(MS_STATEMENT, (0, 0, 0)))
    return steps


def level_done_steps(tune: Sequence[int], on_note: Callable[[], None], on_done: Callable[[], None]) -> list[Step]:
    """L395..L415: 36 notes, the screen flipped upside down on every note (POKE 755)."""
    steps: list[Step] = []
    for kl in tune:
        steps.append(Step(MS_NOTE_EXTRA, None, on_note))
        steps.extend(Step(MS_LOOP_SOUND, (kl, 10, j)) for j in range(15, -1, -1))
    steps.append(Step(MS_STATEMENT, None, on_done))
    return steps


def elevator_steps() -> list[Step]:
    """L460..L465: a rising sweep."""
    return [Step(MS_LOOP_SOUND, (i, 10, i // 10)) for i in range(150, 3, -2)]


def trapdoor_steps() -> list[Step]:
    """L490..L500."""
    return [Step(MS_LOOP_SOUND, (abs(i), 8, 15 - abs(i))) for i in range(-14, 16)]


def poison_steps(charset: Charset, zw: int) -> list[Step]:
    """L510..L530: the hero glyph flipped upside down and back, four times."""
    steps: list[Step] = []
    for i in range(8):
        steps.append(Step(0.0, None, (lambda i=i: charset.hero_flip_step(zw, i))))   # type: ignore[misc]
        steps.extend(Step(MS_LOOP_SOUND, (j, 10, 15)) for j in range(100, 111))
    steps.append(Step(MS_STATEMENT, (0, 0, 0)))
    return steps


def death_steps(charset: Charset, zw: int, on_clear: Callable[[], None], on_flash: Callable[[], None],
                on_done: Callable[[], None]) -> list[Step]:
    """L655..L725: the hero dissolves, the screen clears, glyphs 12..15 are rewritten while the
    background flashes, then a fade-out."""
    steps: list[Step] = []
    for _ in range(8):
        for j in range(4):
            steps.append(Step(MS_DEATH_STEP, (100 - 10 * j, 4, 10),
                              (lambda j=j: charset.hero_death_step(zw, j))))       # type: ignore[misc]
    steps.append(Step(MS_STATEMENT, (0, 0, 0), on_clear))

    def restore(k: int, flash: bool) -> Callable[[], None]:
        def action() -> None:
            if flash:                                          # L700, once per outer iteration
                on_flash()
            charset.restore_hero_byte(k)                       # L710
        return action

    for i in range(2):
        for j in range(16):
            steps.append(Step(MS_LOOP_POKE, (j, 0, 10), restore(16 * i + j, j == 0)))
    steps.extend(Step(MS_FADE_STEP, (16, 0, int(v / 10))) for v in range(100, -1, -1))   # I=10 TO 0 STEP -0.1
    steps.append(Step(MS_STATEMENT, None, on_done))
    return steps


def level_rows(level: Level) -> Iterator[list[int]]:
    for row in level.rows:
        yield row_to_codes(row)


__all__ = ['Audio', 'Charset', 'Gr0Screen', 'Pokey', 'Renderer', 'Sequencer', 'Step', 'atari_rgb',
           'death_steps', 'elevator_steps', 'intro_screen', 'intro_steps', 'level_done_steps', 'level_rows',
           'load_os_font', 'load_steps', 'menu_screen', 'poison_steps', 'tick_steps', 'trapdoor_steps',
           'treasure_steps', 'FPS', 'TICK_MS', 'MS_MENU_CLICK', 'GR18_DEFAULT_COLORS', 'Glyph']
