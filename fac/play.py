"""Play FAC: python -m fac.play [--scale N] [--level N] [--variant 1-4] [--no-sound] [--os-font FILE]

The original keys: '+' left, '*' right, space jump (also arrows / A D W here), HELP = F6 or H
(back to the menu, the level is kept). Menu: SELECT = F3 or Tab, START = F4 or Enter,
OPTION = F2 or Esc (quits, as in the original). Q or the window close quits at any time.
--os-font takes a 1 KB Atari charset dump or an OS ROM image for the exact intro text and
menu; without it a system font stands in.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from enum import Enum
from pathlib import Path

import pygame

from .engine import LEVEL_H, LEVEL_W, Engine, EventKind, Key, Status, Variant, parse_levels
from .render import (FPS, GR18_DEFAULT_COLORS, MS_MENU_CLICK, TICK_MS, Audio, Charset, Renderer, Sequencer, Step,
                     death_steps, elevator_steps, intro_screen, intro_steps, level_done_steps, level_rows,
                     load_os_font, load_steps, menu_screen, poison_steps, tick_steps, trapdoor_steps,
                     treasure_steps)

DATA_DIR = Path(__file__).resolve().parent / 'data'
KEY_REPEAT_DELAY_MS, KEY_REPEAT_MS = 960, 120     # XL/XE OS: KRPDEL = 48 frames, KEYREP = 6 frames (PAL)

GAME_KEYS: dict[int, Key] = {
    pygame.K_PLUS: Key.LEFT, pygame.K_KP_PLUS: Key.LEFT, pygame.K_LEFT: Key.LEFT, pygame.K_a: Key.LEFT,
    pygame.K_ASTERISK: Key.RIGHT, pygame.K_KP_MULTIPLY: Key.RIGHT, pygame.K_RIGHT: Key.RIGHT, pygame.K_d: Key.RIGHT,
    pygame.K_SPACE: Key.JUMP, pygame.K_UP: Key.JUMP, pygame.K_w: Key.JUMP,
    pygame.K_F6: Key.HELP, pygame.K_h: Key.HELP,
}
START_KEYS = {pygame.K_F4, pygame.K_RETURN, pygame.K_KP_ENTER}
SELECT_KEYS = {pygame.K_F3, pygame.K_TAB}
OPTION_KEYS = {pygame.K_F2, pygame.K_ESCAPE}
NOT_A_KEY = {pygame.K_LSHIFT, pygame.K_RSHIFT, pygame.K_LCTRL, pygame.K_RCTRL, pygame.K_LALT, pygame.K_RALT,
             pygame.K_LGUI, pygame.K_RGUI, pygame.K_CAPSLOCK, pygame.K_NUMLOCK, pygame.K_SCROLLLOCK,
             pygame.K_F2, pygame.K_F3, pygame.K_F4, pygame.K_q}


class Screen(Enum):
    INTRO = 'intro'      # L5..L45
    MENU = 'menu'        # L55..L140
    PLAY = 'play'        # L145..


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='FAC (Atari 8-bit, 1992) - pygame front end')
    ap.add_argument('--scale', type=int, default=3)
    ap.add_argument('--level', type=int, default=1, help='start level, 1-based')
    ap.add_argument('--variant', type=int, default=None, choices=[1, 2, 3, 4],
                    help='menu choice: 1 free, 2 time, 3 steps, 4 both (skips the intro and the menu)')
    ap.add_argument('--no-sound', action='store_true')
    ap.add_argument('--no-intro', action='store_true', help='skip the charset-loading intro')
    ap.add_argument('--os-font', type=Path, default=os.environ.get('FAC_OS_FONT'),
                    help='Atari OS charset (1 KB) or OS ROM image, for the intro text and the menu')
    ap.add_argument('--data', type=Path, default=DATA_DIR, help='directory with levels.txt, meta.json, charset.bin')
    args = ap.parse_args(argv)

    meta = json.loads((args.data / 'meta.json').read_text())
    levels = parse_levels((args.data / 'levels.txt').read_text())
    charset = Charset((args.data / 'charset.bin').read_bytes())
    os_font = load_os_font(args.os_font) if args.os_font else None
    tune: list[int] = meta['tune_425']

    pygame.init()
    pygame.key.set_repeat(KEY_REPEAT_DELAY_MS, KEY_REPEAT_MS)
    renderer = Renderer(charset, args.scale, os_font)
    window = pygame.display.set_mode(renderer.size)
    pygame.display.set_caption('FAC - J.B. Wisniewski 1992')
    audio = Audio(enabled=not args.no_sound)
    seq = Sequencer(audio)
    clock = pygame.time.Clock()

    q = 0 if args.variant is None else args.variant - 1        # Q of the menu (L110..L135)
    engine: Engine | None = None
    shown: list[int] = intro_screen()                          # what the GRAPHICS 18 screen displays
    colors: tuple[int, ...] = GR18_DEFAULT_COLORS
    flip = False                                               # CHACT bit 2 (L400)
    bg_flash = False                                           # POKE 712,ABS(PEEK(712)-255) (L700)
    pending_key = Key.NONE                                     # 764 keeps the last key until L295 clears it
    mode = Screen.INTRO
    quit_requested = False

    def show_level() -> None:
        assert engine is not None
        shown[:] = engine.screen

    def start_load() -> None:
        """L145..L195 for the engine's current level."""
        assert engine is not None
        eng = engine
        lv = levels[eng.state.level]
        nonlocal colors, flip, bg_flash
        colors, flip, bg_flash = lv.colors, False, False
        shown[:] = [0] * (LEVEL_W * LEVEL_H)
        rows = list(level_rows(lv))

        def on_row(i: int) -> None:
            shown[i * LEVEL_W:(i + 1) * LEVEL_W] = rows[i]

        def on_done() -> None:
            shown[:] = eng.screen                              # adds the MK digits of L190..L195

        eng.load_level()
        seq.add(load_steps(lv, on_row, on_done))

    def start_game() -> None:
        nonlocal engine, mode, pending_key
        variant = Variant(q)
        if engine is None:
            engine = Engine(levels, variant, start_level=args.level - 1)
        else:
            engine.restart_level(variant)                      # L140 -> L145: same RE, new Q
        pending_key = Key.NONE                                 # POKE 732,0 (L140); 764 was cleared by GRAPHICS
        mode = Screen.PLAY
        seq.clear()
        start_load()

    def to_menu() -> None:
        nonlocal mode
        mode = Screen.MENU
        seq.clear()

    def run_tick() -> None:
        nonlocal pending_key, flip, bg_flash
        assert engine is not None
        eng = engine
        key = pending_key
        events = eng.tick(key)
        if eng.key_consumed:
            pending_key = Key.NONE
        show_level()
        sounds = [ev.value for ev in events if ev.kind is EventKind.SOUND]
        seq.add(tick_steps(sounds))                            # type: ignore[arg-type]
        for ev in events:
            if ev.kind is EventKind.TREASURE:
                seq.add(treasure_steps())
            elif ev.kind is EventKind.ELEVATOR:
                seq.add(elevator_steps())
            elif ev.kind is EventKind.TRAPDOOR:
                seq.add(trapdoor_steps())
            elif ev.kind is EventKind.POISON:
                seq.add(poison_steps(charset, eng.state.facing))
            elif ev.kind is EventKind.DEATH:
                def on_clear() -> None:
                    shown[:] = [0] * (LEVEL_W * LEVEL_H)       # ? #6;"}" (L690)

                def on_flash() -> None:
                    nonlocal bg_flash
                    bg_flash = not bg_flash                    # L700

                seq.add(death_steps(charset, eng.state.facing, on_clear, on_flash, start_load))
            elif ev.kind is EventKind.LEVEL_DONE:
                def on_note() -> None:
                    nonlocal flip
                    flip = not flip                            # POKE 755,ABS(PEEK(755)-8)

                def on_tune_done() -> None:
                    nonlocal flip
                    flip = False
                    shown[:] = [0] * (LEVEL_W * LEVEL_H)       # ? #6;"}" (L415)
                    if eng.state.status is Status.FINISHED:
                        to_menu()                              # L415: NS=0 -> L50
                    else:
                        start_load()                           # L420: RE=NS -> L145

                seq.add(level_done_steps(tune, on_note, on_tune_done))
            elif ev.kind is EventKind.MENU:
                seq.add([Step(TICK_MS, None, to_menu)])

    if args.variant is not None or args.no_intro:
        if args.variant is not None:
            start_game()
        else:
            mode = Screen.MENU
    else:
        seq.add(intro_steps())
        seq.add([Step(0.0, None, to_menu)])

    while not quit_requested:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT or (ev.type == pygame.KEYDOWN and ev.key == pygame.K_q):
                quit_requested = True
            elif ev.type != pygame.KEYDOWN:
                continue
            elif mode is Screen.MENU:
                if ev.key in OPTION_KEYS:                       # L115 -> L120: END
                    quit_requested = True
                elif ev.key in SELECT_KEYS:                     # L135
                    q = (q + 1) % 4
                    seq.add([Step(MS_MENU_CLICK, (50, 10, 6)), Step(0.0, (0, 0, 0))])
                elif ev.key in START_KEYS:                      # L130 -> L140
                    seq.add([Step(MS_MENU_CLICK, (50, 10, 6)), Step(0.0, (0, 0, 0))])
                    start_game()
            elif mode is Screen.PLAY:
                if ev.key in GAME_KEYS:
                    pending_key = GAME_KEYS[ev.key]
                elif ev.key not in NOT_A_KEY:
                    pending_key = Key.OTHER                     # any other key: costs a step (L280)

        if mode is Screen.PLAY and engine is not None and not seq.busy:
            if engine.state.status is Status.PLAYING:
                run_tick()
            elif engine.state.status is Status.MENU:
                to_menu()
        seq.frame()

        if mode is Screen.MENU:
            cells, cursor = menu_screen(q)
            renderer.draw_gr0(window, cells, cursor)
        else:
            pal = list(colors)
            if bg_flash:
                pal[4] = 255 - pal[4]
            renderer.draw_gr18(window, shown, pal, flip=flip, os_charset=(mode is Screen.INTRO))
        pygame.display.flip()
        clock.tick(FPS)

    pygame.quit()
    return 0


if __name__ == '__main__':
    sys.exit(main())
