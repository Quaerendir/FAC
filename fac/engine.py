"""FAC engine -- a literal re-implementation of the Atari BASIC game loop in FAC.LST.

Every method mirrors a run of BASIC lines (named in the docstrings and comments: `L205`
means line 205 of the listing). The state is the program's variables (X, Y, ZW, SK, H,
LC, MC, MK, T) plus the GRAPHICS 18 screen memory, because the original reads the terrain
back from the screen (`PEEK(EK+20*Y+X)`, L540) and the hero, the counters and the
opened trapdoors live there too. One `tick()` = one pass of the main loop from L200 to
the next arrival at L200 (or to a death / level-complete exit). Stdlib only, deterministic
given the `random.Random` used for the trapdoors (L475).
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Sequence

LEVEL_W, LEVEL_H = 20, 12
LEVEL_CELLS = LEVEL_W * LEVEL_H
MH = 2               # L180: maximum jump height (also the fall counter start, L640: H=MH-1)
FIRST_T = 9          # L180: T=9, so the first pass of the loop already shows the time (L215)
TIME_TICKS = 10      # L215: the time counter advances every 10 passes of the main loop
TRAPDOOR_DIV = 20    # L475: a trapdoor stays shut with probability 1-(LC-1)/20


class Key(Enum):
    """What PEEK(764) (L270) or PEEK(732) (L240) delivers during one pass of the loop."""
    NONE = 'none'        # 255: no key
    LEFT = 'left'        # code 6, the '+' key (L300)
    RIGHT = 'right'      # code 7, the '*' key (L320)
    JUMP = 'jump'        # code 33, space (L340)
    OTHER = 'other'      # any other key: costs a step (L280) and is then ignored (L340)
    HELP = 'help'        # HELP key, HELPFG at 732 (L240): back to the menu, level kept


class Variant(IntEnum):
    """Q of the menu (L110..L140): KR = Q>1 (steps limited), CZ = Q-2*KR (time limited)."""
    FREE = 0             # 1. bez ograniczenia
    TIME = 1             # 2. z ograniczonym czasem
    STEPS = 2            # 3. z ograniczonymi krokami
    TIME_STEPS = 3       # 4. z ograniczonym czasem i krokami

    @property
    def steps_limited(self) -> bool:
        return self > 1                                   # L140: KR=Q>1

    @property
    def time_limited(self) -> bool:
        return self - 2 * int(self.steps_limited) == 1    # L140: CZ=Q-2*KR


class Glyph(IntEnum):
    """Screen codes without colour bits = glyph index in charset.bin = K of L540..L545."""
    EMPTY = 0            # ' '
    POISON = 1           # '!'  zatruty dywanik (L505)
    TRAPDOOR = 2         # '"'  zapadnia (L470); drawn exactly like WALL
    ELEVATOR = 3         # '#'  winda (L445)
    TREASURE0 = 4        # '$'..')' inverse in the listing: P = K>3 AND K<10 (L545)
    TREASURE5 = 9
    WALL_R = 10          # '*'  right end of a platform (rounded on the right)
    WALL_L = 11          # '+'  left end of a platform
    HERO_L = 12          # CHR$(13+ZW), ZW=-1 (L205)
    HERO = 13
    HERO_R = 14
    WALL = 15            # '/'
    DIGIT0 = 16          # '0'..'9' carved into the top wall (L195, L225, L290)
    DIGIT9 = 25


HERO_COLOUR = 0x40       # CHR$(12..14) are control characters: colour register 709 (bit 6)
TREASURE_COLOUR = 0x80   # inverse video in the DATA rows: colour register 710 (bit 7)


def atascii_to_screen(b: int) -> int:
    """How `? #6;C$` (L170) turns an ATASCII byte into a GRAPHICS 18 screen code."""
    hi, a = b & 0x80, b & 0x7F
    if a < 0x20:
        a += 0x40
    elif a < 0x60:
        a -= 0x20
    return a | hi


def row_to_codes(row: str) -> list[int]:
    """levels.txt row -> screen codes: treasures ($..)) get the inverse/colour-2 bit back."""
    out = []
    for ch in row:
        b = ord(ch)
        if 0x24 <= b <= 0x29:
            b |= 0x80
        out.append(atascii_to_screen(b))
    return out


class EventKind(Enum):
    LEVEL_LOADED = 'level_loaded'    # L145..L195
    SOUND = 'sound'                  # one SOUND 1,f,d,v statement; value = (f, d, v)
    SHOW_TIME = 'show_time'          # L220..L225, value = the MC shown
    SHOW_STEPS = 'show_steps'        # L190..L195 / L285..L290, value = the MK shown
    TREASURE = 'treasure'            # L360..L385 (LC already decremented in the state)
    ELEVATOR = 'elevator'            # L455..L465, value = (x, y) of the destination
    TRAPDOOR = 'trapdoor'            # L480..L500, value = index of the cell that opened
    POISON = 'poison'                # L505..L530, followed by DEATH
    DEATH = 'death'                  # L650..L725: animation, then the same level again (L145)
    LEVEL_DONE = 'level_done'        # L390..L420: tune, then the next level or the menu
    MENU = 'menu'                    # L240 -> L55: HELP key


@dataclass(frozen=True)
class Event:
    kind: EventKind
    value: object = None


class Status(Enum):
    PLAYING = 'playing'
    DEAD = 'dead'            # L650 reached; next tick (or load_level) restarts the level
    LEVEL_DONE = 'level_done'   # L390 reached with NS<>0; next tick loads the next level
    FINISHED = 'finished'    # L390 reached on the last level: L50, back to the menu
    MENU = 'menu'            # HELP (L240 -> L55); restart_level() resumes the same level


@dataclass(frozen=True)
class Level:
    rows: tuple[str, ...]
    x: int
    y: int
    treasures: int           # LC
    time: int                # MC
    steps: int               # MK
    elevators: tuple[int, ...]
    colors: tuple[int, int, int, int, int] = (138, 205, 88, 0, 18)


@dataclass(frozen=True)
class GameState:
    x: int
    y: int
    facing: int              # ZW: -1 left, 0 centre, 1 right
    sk: int                  # 0 standing, 1 rising, -1 falling
    h: int                   # height counter of the jump / fall
    treasures_left: int      # LC
    time_left: int           # MC (meaningful when the variant limits time)
    steps_left: int          # MK
    t: int                   # T, the 0..9 sub-counter of the time
    level: int               # 0-based index
    status: Status
    variant: Variant


def parse_levels(text: str) -> list[Level]:
    """levels.txt (fac_extract.py output) -> levels."""
    levels: list[Level] = []
    lines = [ln.rstrip('\n') for ln in text.splitlines()]
    i = 0
    while i < len(lines):
        m = re.match(r'\[level (\d+)\]\s*$', lines[i])
        if not m:
            i += 1
            continue
        fields: dict[str, str] = {}
        i += 1
        while i < len(lines) and ':' in lines[i] and not lines[i].startswith(('/', '*', '+')):
            key, _, val = lines[i].partition(':')
            fields[key.strip()] = val.strip()
            i += 1
        rows = lines[i:i + LEVEL_H]
        i += LEVEL_H
        if len(rows) != LEVEL_H or any(len(r) != LEVEL_W for r in rows):
            raise ValueError(f'level {m.group(1)}: expected {LEVEL_H} rows of {LEVEL_W} chars')
        x, y, lc, mc, mk = (int(v) for v in fields['params'].split())
        elev = tuple(int(v) for v in fields.get('elevators', '').split(',') if v.strip())
        cols = tuple(int(v) for v in fields.get('colors', '138,205,88,0,18').split(','))
        if len(cols) != 5:
            raise ValueError(f'level {m.group(1)}: expected 5 colours')
        levels.append(Level(tuple(rows), x, y, lc, mc, mk, elev, (cols[0], cols[1], cols[2], cols[3], cols[4])))
    return levels


class Engine:
    """One instance = one game session (menu START -> levels -> menu)."""

    def __init__(self, levels: Sequence[Level], variant: Variant = Variant.FREE, *,
                 start_level: int = 0, rnd: random.Random | None = None) -> None:
        if not levels:
            raise ValueError('need at least one level')
        if not 0 <= start_level < len(levels):
            raise ValueError(f'start_level {start_level} out of range')
        self._levels = list(levels)
        self.variant = Variant(variant)
        self.level = start_level                      # RE, as an index
        self.rnd = rnd or random.Random()
        self.screen: list[int] = [0] * LEVEL_CELLS    # GRAPHICS 18 memory at EK (L10)
        self.x = self.y = 0
        self.zw = self.sk = self.h = self.t = 0
        self.lc = self.mc = self.mk = 0
        self._elevators: list[int] = []
        self._old = 0                                 # cursor set by POSITION X,Y (L560)
        self.key_consumed = False                     # L295 (POKE 764,255) ran during the last tick
        self.status = Status.PLAYING
        self._load_pending = False
        self._events: list[Event] = []
        self._load_level()

    # ----------------------------------------------------------------- public
    @property
    def state(self) -> GameState:
        return GameState(self.x, self.y, self.zw, self.sk, self.h, self.lc, self.mc, self.mk,
                         self.t, self.level, self.status, self.variant)

    @property
    def load_pending(self) -> bool:
        """True after a death or a completed level until the next load (L145)."""
        return self._load_pending

    @property
    def elevators_left(self) -> tuple[int, ...]:
        return tuple(self._elevators)

    def tick(self, key: Key = Key.NONE) -> list[Event]:
        """One pass of the main loop, L200 onwards."""
        self._events = []
        self.key_consumed = False
        if self._load_pending:                        # L725 / L420: GOTO 145
            self._load_level()
        if self.status is not Status.PLAYING:
            return []
        self._draw_hero()                             # L205
        if self.variant.time_limited:                 # L210
            self.t += 1                               # L215
            if self.t >= TIME_TICKS:
                self._show_time()                     # L220..L225
                self.mc -= 1                          # L230
                self.t = 0
                if self.mc < 0:
                    return self._death()
        if self.lc == 0:                              # L235
            return self._level_done()
        if key is Key.HELP:                           # L240
            self.key_consumed = True                  # POKE 732,0 happens at L140
            self.status = Status.MENU
            self._emit(EventKind.MENU)
            return self._events
        k, p = self._terrain(self.x, self.y + 1)      # L245
        if k == 0 or p:                               # L250
            return self._collision(k, key)
        self.sk = 0                                   # L255
        if self.h < 0:
            return self._death()
        if k < 4:                                     # L260
            return self._collision(k, key)
        self._sound_off()                             # L265
        self._keys(key)                               # L270..L355
        return self._events

    def load_level(self) -> list[Event]:
        """Perform a pending load now (lets a front end run the death / tune animation first)."""
        self._events = []
        if self._load_pending:
            self._load_level()
        return self._events

    def restart_level(self, variant: Variant | None = None) -> list[Event]:
        """START in the menu after HELP or after the last level (L140 -> L145): the same RE,
        possibly a new Q. After FINISHED the game is back at level 1 (L50: RE=2000)."""
        self._events = []
        if variant is not None:
            self.variant = Variant(variant)
        if self.status is Status.FINISHED:
            self.level = 0
        self._load_level()
        return self._events

    # ------------------------------------------------------------- internals
    def _emit(self, kind: EventKind, value: object = None) -> None:
        self._events.append(Event(kind, value))

    def _sound(self, f: int, d: int, v: int) -> None:
        self._emit(EventKind.SOUND, (f, d, v))

    def _sound_off(self) -> None:
        self._sound(0, 0, 0)

    def _load_level(self) -> None:
        """L145..L195: draw the level, read its parameters, reset the hero variables."""
        lv = self._levels[self.level]
        self._load_pending = False
        self.screen = [c for row in lv.rows for c in row_to_codes(row)]        # L160..L170
        self.x, self.y = lv.x, lv.y                                             # L175
        self.lc, self.mc, self.mk = lv.treasures, lv.time, lv.steps
        self._elevators = list(lv.elevators)                                    # READ WD (L450) list
        self.zw, self.sk, self.h, self.t = 0, 0, MH, FIRST_T                    # L180
        self.status = Status.PLAYING
        self._emit(EventKind.LEVEL_LOADED, self.level)
        if self.variant.steps_limited:                                          # L185..L195
            self._print_row0(15 - (self.mk > 9) - (self.mk > 99), str(self.mk))
            self._emit(EventKind.SHOW_STEPS, self.mk)

    def _print_row0(self, col: int, text: str) -> None:
        """POSITION col,0 : ? #6;text  -- '/' and digits into the top wall row."""
        for k, ch in enumerate(text):
            self.screen[col + k] = atascii_to_screen(ord(ch))

    def _show_time(self) -> None:
        self._print_row0(3 - (self.mc > 9) - (self.mc > 99), '//' + str(self.mc))   # L220..L225
        self._emit(EventKind.SHOW_TIME, self.mc)

    def _show_steps(self) -> None:
        self._print_row0(13 - (self.mk > 9) - (self.mk > 99), '//' + str(self.mk))  # L285..L290
        self._emit(EventKind.SHOW_STEPS, self.mk)

    def _draw_hero(self) -> None:
        self.screen[self.y * LEVEL_W + self.x] = HERO_COLOUR | (Glyph.HERO + self.zw)   # L205

    def _erase_old(self) -> None:
        self.screen[self._old] = Glyph.EMPTY          # L645: ? #6;" " at the L560 cursor

    def _terrain(self, x: int, y: int) -> tuple[int, bool]:
        """L535..L550 (BAD. TERENU): K = screen code without colour bits, P = treasure."""
        if not (0 <= x < LEVEL_W and 0 <= y < LEVEL_H):
            raise RuntimeError(f'terrain probe outside the screen at ({x},{y})')  # never happens with the 11 levels
        k = self.screen[y * LEVEL_W + x] & 0x3F
        return k, 3 < k < 10

    def _keys(self, key: Key) -> None:
        """L270..L355: the key handler, only reached when standing (or on a shut trapdoor)."""
        if key is Key.NONE:                           # L270: PEEK(764)=255 -> GOTO 200
            return
        self.key_consumed = True                      # L295: POKE 764,255
        if self.variant.steps_limited:                # L275..L290: any key costs a step
            self.mk -= 1
            if self.mk < 0:
                self._death()
                return
            self._show_steps()
        if key is Key.LEFT:                           # L300..L315
            self._sound(6, 8, 4)
            if self.zw == -1:
                self._collision(6, key)
            else:
                self.zw -= 1
        elif key is Key.RIGHT:                        # L320..L335
            self._sound(4, 8, 4)
            if self.zw == 1:
                self._collision(7, key)
            else:
                self.zw += 1
        elif key is Key.JUMP:                         # L340..L355
            self._sound(80, 10, 10)
            self.sk, self.h = 1, 0
            self._collision(33, key)
        # any other key: L340 -> GOTO 200

    def _collision(self, k: int, key: Key) -> list[Event]:
        """L555..L570 (SPR. KOLIZJI). `k` is whatever K holds on entry: the terrain below
        (L250/L260) or a key code (L310/L330/L355); ON K GOTO only reacts to 1..3."""
        self._old = self.y * LEVEL_W + self.x         # L560: POSITION X,Y
        if k == Glyph.POISON:                         # L565
            self._poison()
        elif k == Glyph.TRAPDOOR:
            self._trapdoor(key)
        elif k == Glyph.ELEVATOR:
            self._elevator()
        else:
            if self.h >= 0:                           # L570
                self.x += self.zw
            self.y -= self.sk
            self._arrive()
        return self._events

    def _arrive(self) -> None:
        """L575..L605: the hero is at a new (X,Y); collect, then jump/fall or walk bookkeeping."""
        k, p = self._terrain(self.x, self.y)          # L575
        if p:
            self._collect()
            k = 0                                     # L365..L380 leave K = 0
        if self.sk == 0:                              # L580
            self._horizontal(k)
            return
        self._sound(77 - 10 * self.h, 10, 10)         # L590
        if k > 0:                                     # L595: anything in the way is fatal
            self._death()
            return
        self.h += self.sk                             # L600
        if self.h == MH:
            self.sk = -1
        self._erase_old()                             # L605 -> L645

    def _horizontal(self, k: int) -> None:
        """L610..L645 (RUCH POZIOMY)."""
        self._sound_off()                             # L615
        if k > 0:
            self._death()
            return
        k, p = self._terrain(self.x, self.y + 1)      # L620
        if k > 0 and not p:                           # L625: solid ground
            self._erase_old()
            return
        self._sound(60, 10, 10)                       # L630
        if k > 0:                                     # L635: a treasure below is taken
            self._collect()
        self.y += 1                                   # L640: drop one row, start falling
        self.h, self.sk = MH - 1, -1
        self._erase_old()                             # L645

    def _collect(self) -> None:
        """L360..L385 (PUNKTY)."""
        self.lc -= 1
        self._emit(EventKind.TREASURE, self.lc)

    def _elevator(self) -> None:
        """L445..L465 (WINDA): the next entry of the level's list, 0 (or none left) is deadly."""
        wd = self._elevators.pop(0) if self._elevators else 0    # L450: READ WD
        if wd == 0:
            self._poison()
            return
        self.zw = 0                                   # L455
        self.x, self.y = wd // 10, wd % 10 + 1
        self._emit(EventKind.ELEVATOR, (self.x, self.y))          # L460..L465
        self._arrive()                                # GOTO 575

    def _trapdoor(self, key: Key) -> None:
        """L470..L500 (ZAPADNIA)."""
        if self.rnd.random() < 1 - (self.lc - 1) / TRAPDOOR_DIV:   # L475: stays shut
            self._sound_off()                         # -> L265
            self._keys(key)
            return
        below = (self.y + 1) * LEVEL_W + self.x
        self.screen[below] = Glyph.EMPTY              # L480
        self.zw = 0                                   # L485
        if self.h >= 0:
            self.h = MH
        self._emit(EventKind.TRAPDOOR, below)         # L490..L500
        self.y += 1                                   # L640
        self.h, self.sk = MH - 1, -1
        self._erase_old()                             # L645

    def _poison(self) -> None:
        """L505..L530 (ZATRUTY DYWANIK): the flip animation, then death."""
        self._emit(EventKind.POISON)
        self._death()

    def _death(self) -> list[Event]:
        """L650..L725 (SMIERC): animation, then the same level again."""
        self.status = Status.DEAD
        self._load_pending = True
        self._emit(EventKind.DEATH)
        return self._events

    def _level_done(self) -> list[Event]:
        """L390..L420 (ZALICZENIE)."""
        self._emit(EventKind.LEVEL_DONE, self.level)
        if self.level + 1 < len(self._levels):        # L420: RE=NS
            self.level += 1
            self.status = Status.LEVEL_DONE
            self._load_pending = True
        else:                                         # L415: NS=0 -> L50 (RE=2000) -> menu
            self.status = Status.FINISHED
        return self._events
