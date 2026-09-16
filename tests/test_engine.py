"""Acceptance tests from SPEC.md section 9. Every expectation is traced to a line of FAC.LST."""
import random
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from fac import (Engine, EventKind, Glyph, Key, Level, Status, Variant, LEVEL_H, LEVEL_W, MH,
                 TIME_TICKS, parse_levels)
from fac.engine import HERO_COLOUR, TREASURE_COLOUR, atascii_to_screen, row_to_codes

ROOT = Path(__file__).resolve().parent.parent
NONE, LEFT, RIGHT, JUMP, OTHER, HELP = Key.NONE, Key.LEFT, Key.RIGHT, Key.JUMP, Key.OTHER, Key.HELP


def box(*inner: str, x: int = 1, y: int = 10, lc: int | None = None, mc: int = 10, mk: int = 10,
        elevators: tuple[int, ...] = ()) -> Level:
    """Up to 10 interior rows (18 chars, padded) between the wall rows, walls on the sides."""
    rows = ['/' * LEVEL_W]
    for k in range(LEVEL_H - 2):
        r = inner[k] if k < len(inner) else ''
        rows.append('*' + r.ljust(LEVEL_W - 2)[:LEVEL_W - 2] + '+')
    rows.append('/' * LEVEL_W)
    grid = ''.join(rows)
    treasures = (sum(grid.count(c) for c in "$%&'()") or 1) if lc is None else lc   # LC>0: never "done"
    return Level(tuple(rows), x, y, treasures, mc, mk, elevators)


def kinds(events) -> list[EventKind]:
    return [ev.kind for ev in events]


def pos(e: Engine) -> tuple[int, int]:
    return e.state.x, e.state.y


def real_levels() -> list[Level]:
    return parse_levels((ROOT / 'levels.txt').read_text())


# ---------------------------------------------------------------- screen codes
def test_atascii_to_screen():
    assert atascii_to_screen(0x20) == 0 and atascii_to_screen(0x2F) == Glyph.WALL
    assert atascii_to_screen(0x0D) == HERO_COLOUR | Glyph.HERO          # CHR$(13), L205
    assert atascii_to_screen(0xA4) == TREASURE_COLOUR | Glyph.TREASURE0  # inverse '$'
    assert row_to_codes(' !"#$)*+/') == [0, 1, 2, 3, 0x84, 0x89, 10, 11, 15]


# ---------------------------------------------------------------- T1 turning and walking
def test_t1_turn_then_walk():
    e = Engine([box('', '', '', '', '', '', '', '', '', '', x=5, y=10)])
    assert e.state.facing == 0 and e.screen[10 * LEVEL_W + 5] == 0   # drawn by L205, not by the load
    ev = e.tick(LEFT)                                   # L315: turn only
    assert e.screen[10 * LEVEL_W + 5] == HERO_COLOUR | Glyph.HERO
    assert e.state.facing == -1 and pos(e) == (5, 10)
    assert [(v.kind, v.value) for v in ev] == [(EventKind.SOUND, (0, 0, 0)), (EventKind.SOUND, (6, 8, 4))]
    e.tick(LEFT)                                        # L310 -> L555: move
    assert pos(e) == (4, 10)
    assert e.screen[10 * LEVEL_W + 5] == 0              # L645 erased the old cell
    e.tick(NONE)
    assert e.screen[10 * LEVEL_W + 4] == HERO_COLOUR | Glyph.HERO_L   # L205: CHR$(12)
    e.tick(RIGHT)                                       # L335: left -> centre, no move
    assert e.state.facing == 0 and pos(e) == (4, 10)
    e.tick(RIGHT)                                       # centre -> right
    assert e.state.facing == 1 and pos(e) == (4, 10)
    e.tick(RIGHT)
    assert pos(e) == (5, 10) and e.state.sk == 0
    assert e.state.status is Status.PLAYING


# ---------------------------------------------------------------- T2 walking into anything is fatal
@pytest.mark.parametrize('cell', ['/', '*', '+', '!', '"', '#'])
def test_t2_walk_into_solid_is_death(cell):
    e = Engine([box('', '', '', '', '', '', '', '', '', '  ' + cell, x=2, y=10)])   # cell at column 3
    e.tick(RIGHT)
    ev = e.tick(RIGHT)                                  # L615: K>0 -> 650
    assert e.state.status is Status.DEAD and EventKind.DEATH in kinds(ev)
    assert pos(e) == (3, 10)                            # X was already advanced (L570)


# ---------------------------------------------------------------- T3 falling
def _fall_box(floor_rows: int, x: int) -> Level:
    """A ledge on row 6 (x<=4) with the hero on it at row 5, then a pit; the floor is
    `floor_rows` rows below the ledge."""
    rows = ['/' * LEVEL_W]
    for r in range(1, LEVEL_H - 1):
        if r == 6:
            row = '*' + '////' + ' ' * 14 + '+'
        elif r == 6 + floor_rows:
            row = '*' + '////' + '/' * 14 + '+'
        else:
            row = '*' + ' ' * 18 + '+'
        rows.append(row)
    rows.append('/' * LEVEL_W)
    return Level(tuple(rows), x, 5, 1, 10, 10, (), (138, 205, 88, 0, 18))


def test_t3_step_off_ledge_drops_one_row_and_keeps_moving():
    e = Engine([_fall_box(5, 4)])
    e.tick(RIGHT)
    ev = e.tick(RIGHT)                                  # L640: Y+1, H=MH-1, SK=-1
    assert pos(e) == (5, 6) and e.state.h == MH - 1 and e.state.sk == -1
    assert (EventKind.SOUND, (60, 10, 10)) in [(v.kind, v.value) for v in ev]
    e.tick()                                            # L570: X+ZW while H>=0
    assert pos(e) == (6, 7) and e.state.h == 0
    e.tick()
    assert pos(e) == (7, 8) and e.state.h == -1
    e.tick()                                            # H<0: straight down
    assert pos(e) == (7, 9) and e.state.h == -2


@pytest.mark.parametrize('floor_rows,dead', [(1, False), (2, False), (3, True), (4, True)])
def test_t3_fall_height_limit(floor_rows, dead):
    """Landing is fatal iff H<0 (L255): more than two rows below the ledge."""
    e = Engine([_fall_box(floor_rows, 4)])
    e.tick(RIGHT)
    e.tick(RIGHT)
    for _ in range(floor_rows + 2):
        if e.state.status is not Status.PLAYING:
            break
        e.tick()
    assert (e.state.status is Status.DEAD) == dead
    if not dead:
        assert e.state.sk == 0 and e.state.h >= 0 and e.state.y == 5 + floor_rows


# ---------------------------------------------------------------- T4 jumping
def test_t4_jump_arc_facing_right():
    e = Engine([box('', '', '', '', '', '', '', '', '', '', x=2, y=10)])
    e.tick(RIGHT)
    ev = e.tick(JUMP)                                   # L355 -> L570: (X+1, Y-1), H=1, SK=1
    assert pos(e) == (3, 9) and e.state.h == 1 and e.state.sk == 1
    assert [(v.kind, v.value) for v in ev] == [(EventKind.SOUND, (0, 0, 0)), (EventKind.SOUND, (80, 10, 10)),
                                              (EventKind.SOUND, (77, 10, 10))]   # L265, L350, L590
    e.tick(LEFT)                                        # keys are ignored in the air (L250 -> L555)
    assert pos(e) == (4, 8) and e.state.h == 2 and e.state.sk == -1 and e.state.facing == 1
    e.tick()
    assert pos(e) == (5, 9) and e.state.h == 1
    e.tick()
    assert pos(e) == (6, 10) and e.state.h == 0
    e.tick()                                            # floor below: L255, standing
    assert pos(e) == (6, 10) and e.state.sk == 0 and e.state.status is Status.PLAYING


def test_t4_vertical_jump_returns_to_the_floor():
    e = Engine([box('', '', '', '', '', '', '', '', '', '', x=2, y=10)])
    e.tick(JUMP)
    assert pos(e) == (2, 9)
    e.tick()
    assert pos(e) == (2, 8) and e.state.sk == -1
    e.tick()
    assert pos(e) == (2, 9)
    e.tick()
    assert pos(e) == (2, 10) and e.state.h == 0
    e.tick()
    assert e.state.sk == 0 and e.state.status is Status.PLAYING


def test_t4_ceiling_is_fatal():
    e = Engine([box('', '', '', '', '', '', '', '', ' /', '', x=2, y=10)])       # wall at (2,9)
    e.tick(JUMP)                                        # L595: K>0 in the air
    assert e.state.status is Status.DEAD


def test_t4_landing_on_a_corner_diagonally_is_fatal():
    """'Nie skacz okrakiem na narozniki kamieni': a diagonal move into a solid cell (L595)."""
    e = Engine([box('', '', '', '', '', '', '', '', '', '     //', x=2, y=10)])   # walls at (6,10),(7,10)
    # jump right from (2,10): (3,9) (4,8) (5,9) then (6,10) which is a wall
    e.tick(RIGHT)
    e.tick(JUMP)
    e.tick()
    e.tick()
    e.tick()
    assert pos(e) == (6, 10) and e.state.status is Status.DEAD


# ---------------------------------------------------------------- T5 treasures
def test_t5_walk_onto_treasure_collects_and_drops_onto_the_next_row():
    lv = box('', '', '', '', '', '', '', '', '', '/$/', x=1, y=9, lc=1)       # (1..3, 10)
    e = Engine([lv])
    e.tick(RIGHT)
    ev = e.tick(RIGHT)                                  # (2,9): treasure below at (2,10): L635
    assert EventKind.TREASURE in kinds(ev)
    assert e.state.treasures_left == 0 and pos(e) == (2, 10) and e.state.sk == -1
    ev = e.tick()                                       # L235: LC=0 -> level done
    assert EventKind.LEVEL_DONE in kinds(ev) and e.state.status is Status.FINISHED


def test_t5_jump_into_treasure_is_not_fatal():
    e = Engine([box('', '', '', '', '', '', '', '', ' %', '', x=2, y=10)])       # '%' at (2,9)
    ev = e.tick(JUMP)                                   # L575: P -> GOSUB 360 leaves K=0
    assert EventKind.TREASURE in kinds(ev) and e.state.status is Status.PLAYING
    assert pos(e) == (2, 9) and e.state.treasures_left == 0


def test_t5_walk_into_treasure_at_hero_level():
    rows = list(box().rows)
    rows[9] = '*' + ' &' + ' ' * 16 + '+'
    rows[10] = '/' * 20
    e = Engine([Level(tuple(rows), 1, 9, 1, 10, 10, ())])
    e.tick(RIGHT)
    ev = e.tick(RIGHT)                                  # L575 collect, L625 floor -> stays
    assert EventKind.TREASURE in kinds(ev) and pos(e) == (2, 9) and e.state.sk == 0


# ---------------------------------------------------------------- T6 time limit
def test_t6_time_counter():
    lv = box('', '', '', '', '', '', '', '', '', '', x=2, y=10, mc=1)
    e = Engine([lv], Variant.TIME)
    ev = e.tick()                                       # T=9 -> 10 on the first pass (L180/L215)
    assert (EventKind.SHOW_TIME, 1) in [(v.kind, v.value) for v in ev]
    assert e.state.time_left == 0 and e.state.t == 0
    assert e.screen[2:6] == [Glyph.WALL] + row_to_codes('//1')   # POSITION 3,0 (MC<=9): "//1" at 3..5
    for _ in range(TIME_TICKS - 1):
        assert kinds(e.tick()) == [(EventKind.SOUND)]  # nothing but the L265 silence
    ev = e.tick()
    assert (EventKind.SHOW_TIME, 0) in [(v.kind, v.value) for v in ev]
    assert e.state.status is Status.DEAD               # L230: MC<0


def test_t6_time_display_positions():
    e = Engine([box(x=2, y=10, mc=105)], Variant.TIME)
    e.tick()
    assert e.screen[1:6] == row_to_codes('//105')       # POSITION 1,0 for MC>99
    e = Engine([box(x=2, y=10, mc=17)], Variant.TIME)
    e.tick()
    assert e.screen[2:6] == row_to_codes('//17')


def test_t6_no_time_in_free_and_steps_variants():
    for var in (Variant.FREE, Variant.STEPS):
        e = Engine([box(x=2, y=10, mc=0)], var)
        for _ in range(3 * TIME_TICKS):
            e.tick()
        assert e.state.status is Status.PLAYING and e.state.t == 9   # T is never touched (L210)


# ---------------------------------------------------------------- T7 step limit
def test_t7_steps():
    e = Engine([box(x=5, y=10, mk=2)], Variant.STEPS)
    assert e.screen[15:18] == row_to_codes('2//')       # L190: POSITION 15,0 : ? #6;MK
    e.tick(OTHER)                                       # any key costs a step (L280)
    assert e.state.steps_left == 1 and e.screen[13:16] == row_to_codes('//1')
    e.tick(LEFT)
    assert e.state.steps_left == 0 and e.state.facing == -1
    ev = e.tick(LEFT)                                   # MK<0 -> 650 before the move
    assert e.state.status is Status.DEAD and pos(e) == (5, 10)
    assert EventKind.DEATH in kinds(ev)


def test_t7_steps_three_digits():
    e = Engine([box(x=5, y=10, mk=111)], Variant.TIME_STEPS)
    assert e.screen[13:16] == row_to_codes('111')       # POSITION 15-1-1,0
    e.tick(OTHER)
    assert e.screen[11:16] == row_to_codes('//110')
    assert e.variant.time_limited and e.variant.steps_limited


# ---------------------------------------------------------------- T8 trapdoor
def test_t8_trapdoor_never_opens_with_one_treasure_left():
    rows = list(box().rows)
    rows[10] = '*' + '/"/' + ' ' * 15 + '+'
    e = Engine([Level(tuple(rows), 2, 9, 1, 10, 10, ())], rnd=random.Random(1))
    for _ in range(200):
        e.tick()
    assert pos(e) == (2, 9) and e.state.status is Status.PLAYING   # L475: RND < 1 always


def test_t8_trapdoor_always_opens_with_21_treasures():
    rows = list(box().rows)
    rows[10] = '*' + '/"/' + ' ' * 15 + '+'
    e = Engine([Level(tuple(rows), 2, 9, 21, 10, 10, ())], rnd=random.Random(1))
    ev = e.tick(RIGHT)                                  # L260 -> L470 -> L480: opens; the key is never read
    assert EventKind.TRAPDOOR in kinds(ev)
    assert e.screen[10 * LEVEL_W + 2] == Glyph.EMPTY
    assert pos(e) == (2, 10) and e.state.facing == 0 and e.state.sk == -1 and e.state.h == MH - 1
    assert e.screen[9 * LEVEL_W + 2] == Glyph.EMPTY     # L645 erased the old position
    e.tick()                                            # floor at row 11: lands, H=1
    assert e.state.sk == 0 and e.state.status is Status.PLAYING


def test_t8_trapdoor_probability_and_keys_while_shut():
    rows = list(box().rows)
    rows[10] = '*' + '/"/' + ' ' * 15 + '+'
    lv = Level(tuple(rows), 2, 9, 11, 10, 10, ())      # opens with probability (11-1)/20 = 0.5
    opened = 0
    for seed in range(200):
        e = Engine([lv], rnd=random.Random(seed))
        e.tick()
        opened += e.state.y == 10
    assert 70 <= opened <= 130
    class Shut(random.Random):
        def random(self) -> float:
            return 0.01                                 # L475: RND < 0.5 -> stays shut

    e = Engine([lv], rnd=Shut(0))
    e.tick(LEFT)                                        # -> L265 -> L270: the key is honoured
    assert e.state.facing == -1 and pos(e) == (2, 9)


# ---------------------------------------------------------------- T9 elevator
def test_t9_elevator_list_in_order_then_deadly():
    rows = list(box().rows)
    rows[10] = '*' + '/#/' + ' ' * 15 + '+'
    rows[4] = '*' + ' ' * 4 + '/////' + ' ' * 9 + '+'    # floor under (5..9, 3)
    lv = Level(tuple(rows), 2, 9, 5, 10, 10, (52, 82))   # 52 -> (5, 3); 82 -> (8, 3)
    e = Engine([lv])
    ev = e.tick(RIGHT)                                  # standing on '#': L260 -> L445, key unread
    assert (EventKind.ELEVATOR, (5, 3)) in [(v.kind, v.value) for v in ev]
    assert pos(e) == (5, 3) and e.state.facing == 0 and e.state.sk == 0
    assert e.screen[9 * LEVEL_W + 2] == Glyph.EMPTY and e.elevators_left == (82,)
    # walk back onto the elevator: put the hero there directly via a fresh engine instead
    e2 = Engine([lv])
    e2.tick()
    e2.x, e2.y = 2, 9                                   # back on the elevator
    e2.tick()
    assert pos(e2) == (8, 3)
    e2.x, e2.y = 2, 9
    ev = e2.tick()                                      # READ WD -> 0: L505 (poison) -> death
    assert kinds(ev)[-2:] == [EventKind.POISON, EventKind.DEATH]


def test_t9_elevator_into_a_wall_is_fatal_and_treasure_is_collected():
    rows = list(box().rows)
    rows[10] = '*' + '/#/' + ' ' * 15 + '+'
    rows[3] = '*' + ' ' * 5 + '(' + ' ' * 12 + '+'
    rows[4] = '*' + ' ' * 5 + '/' + ' ' * 12 + '+'
    e = Engine([Level(tuple(rows), 2, 9, 1, 10, 10, (62,))])   # 62 -> (6, 3): the treasure
    ev = e.tick()
    assert EventKind.TREASURE in kinds(ev) and pos(e) == (6, 3) and e.state.status is Status.PLAYING
    e = Engine([Level(tuple(rows), 2, 9, 1, 10, 10, (63,))])   # 63 -> (6, 4): the wall
    e.tick()
    assert e.state.status is Status.DEAD


# ---------------------------------------------------------------- T10 poison carpet
def test_t10_poison():
    rows = list(box().rows)
    rows[10] = '*' + '/!/' + ' ' * 15 + '+'
    e = Engine([Level(tuple(rows), 2, 9, 1, 10, 10, ())])
    ev = e.tick()
    assert kinds(ev)[-2:] == [EventKind.POISON, EventKind.DEATH] and e.state.status is Status.DEAD


# ---------------------------------------------------------------- T11 level chain
def test_t11_level_done_next_level_and_finish():
    a = box(x=2, y=10, lc=0)
    b = box(x=3, y=10, lc=0)
    e = Engine([a, b], Variant.STEPS)
    ev = e.tick()
    assert (EventKind.LEVEL_DONE, 0) in [(v.kind, v.value) for v in ev]
    assert e.state.status is Status.LEVEL_DONE and e.load_pending and e.state.level == 1
    ev = e.load_level()
    assert kinds(ev)[0] is EventKind.LEVEL_LOADED and pos(e) == (3, 10) and not e.load_pending
    ev = e.tick()
    assert e.state.status is Status.FINISHED and not e.load_pending
    assert e.tick() == []                               # nothing happens until START (L50 -> menu)
    e.restart_level(Variant.TIME)
    assert e.state.level == 0 and e.variant is Variant.TIME and pos(e) == (2, 10)


# ---------------------------------------------------------------- T12 death restarts the same level
def test_t12_death_restores_the_level():
    rows = list(box().rows)
    rows[10] = '*' + '/"/' + ' ' * 15 + '+'
    lv = Level(tuple(rows), 2, 9, 21, 3, 4, (11,))
    e = Engine([lv], Variant.TIME_STEPS, rnd=random.Random(1))
    e.tick(OTHER)                                       # time shown, trapdoor opens, key unread
    assert e.state.steps_left == 4 and e.state.time_left == 2 and e.state.t == 0
    assert e.screen[10 * LEVEL_W + 2] == Glyph.EMPTY
    e.x, e.y, e.h, e.sk = 3, 9, -5, 0                   # arrange a fatal landing
    e.tick()
    assert e.state.status is Status.DEAD and e.load_pending
    e.load_level()                                      # L725 -> L145
    assert e.state.status is Status.PLAYING and pos(e) == (2, 9)
    assert e.state.steps_left == 4 and e.state.time_left == 3 and e.state.t == 9
    assert e.screen[10 * LEVEL_W + 2] == Glyph.TRAPDOOR and e.elevators_left == (11,)


# ---------------------------------------------------------------- T13 HELP
def test_t13_help_to_menu_keeps_the_level():
    e = Engine([box(x=2, y=10), box(x=3, y=10)], start_level=1)
    ev = e.tick(HELP)
    assert kinds(ev) == [EventKind.MENU] and e.state.status is Status.MENU
    assert e.tick(LEFT) == []
    e.restart_level(Variant.STEPS)
    assert e.state.level == 1 and e.variant is Variant.STEPS and e.state.status is Status.PLAYING


# ---------------------------------------------------------------- the real levels
def test_real_levels_load_and_hero_stands():
    levels = real_levels()
    assert len(levels) == 11
    for k, lv in enumerate(levels):
        e = Engine(levels, start_level=k)
        assert e.state.treasures_left == lv.treasures
        below = e.screen[(lv.y + 1) * LEVEL_W + lv.x] & 0x3F
        assert below > 0 and not (3 < below < 10), f'level {k + 1}: hero not standing'
        assert all(r[0] != ' ' and r[-1] != ' ' for r in lv.rows), f'level {k + 1}: open side'
        e.tick()
        assert e.state.status is Status.PLAYING


def test_real_level_1_first_treasure():
    """Level 1: walk right from (15,10) and collect the '%' at (10,10)? No: go left, drop on the '%'."""
    e = Engine(real_levels())
    e.tick(LEFT)
    for _ in range(4):                                  # (14..11, 10)
        e.tick(LEFT)
    assert pos(e) == (11, 10)
    ev = e.tick(LEFT)                                   # (10,10) is the treasure ')': L575
    assert EventKind.TREASURE in kinds(ev) and pos(e) == (10, 10) and e.state.treasures_left == 8


@settings(max_examples=300, deadline=None)
@given(st.integers(0, 10), st.integers(0, 3), st.lists(st.sampled_from(list(Key)), min_size=1, max_size=120),
       st.integers(0, 2 ** 31))
def test_fuzz_real_levels_never_break(level, variant, keys, seed):
    """Random play on the real levels: the probe never leaves the screen (L540 reads inside EK)."""
    e = Engine(real_levels(), Variant(variant), start_level=level, rnd=random.Random(seed))
    for k in keys:
        e.tick(k)
        s = e.state
        assert 0 <= s.x < LEVEL_W and 0 <= s.y < LEVEL_H
        if s.status is Status.MENU:
            e.restart_level()
