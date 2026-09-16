#!/usr/bin/env python3
"""Render docs/*.png headlessly: the menu, the levels as the game shows them (with the hero
after its first pass of the loop, counters on), and a mid-death frame.

    python3 tools/screenshots.py [--os-font FILE]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pygame  # noqa: E402

from fac import Engine, Key, Variant, parse_levels  # noqa: E402
from fac.render import Charset, Renderer, load_os_font, menu_screen, intro_screen, GR18_DEFAULT_COLORS  # noqa: E402

DATA = ROOT / 'fac' / 'data'


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--os-font', type=Path, default=os.environ.get('FAC_OS_FONT'))
    ap.add_argument('--scale', type=int, default=2)
    ap.add_argument('-o', '--outdir', type=Path, default=ROOT / 'docs')
    args = ap.parse_args()
    pygame.init()
    pygame.display.set_mode((64, 64))
    charset = Charset((DATA / 'charset.bin').read_bytes())
    os_font = load_os_font(args.os_font) if args.os_font else None
    r = Renderer(charset, args.scale, os_font)
    levels = parse_levels((DATA / 'levels.txt').read_text())
    args.outdir.mkdir(exist_ok=True)
    surf = pygame.Surface(r.size)

    r.draw_gr18(surf, intro_screen(), GR18_DEFAULT_COLORS, os_charset=True)
    pygame.image.save(surf, str(args.outdir / 'intro.png'))
    cells, cursor = menu_screen(1)
    r.draw_gr0(surf, cells, cursor)
    pygame.image.save(surf, str(args.outdir / 'menu.png'))

    for k in range(len(levels)):
        e = Engine(levels, Variant.TIME_STEPS, start_level=k)
        e.tick(Key.NONE)                                   # L205: hero drawn, MC shown, MK shown
        r.draw_gr18(surf, e.screen, levels[k].colors)
        pygame.image.save(surf, str(args.outdir / f'level{k + 1}.png'))

    e = Engine(levels)                                     # walk left off the start of level 1, drop, die?
    for key in (Key.RIGHT, Key.RIGHT, Key.JUMP):
        e.tick(key)
    for _ in range(3):
        e.tick()                                           # into the wall at (19,10): death
    for j in range(4):
        charset.hero_death_step(e.state.facing, j)
        charset.hero_death_step(e.state.facing, j)
    r.draw_gr18(surf, e.screen, levels[0].colors)
    pygame.image.save(surf, str(args.outdir / 'death.png'))
    print(f'written to {args.outdir}: intro, menu, level1..{len(levels)}, death')


if __name__ == '__main__':
    main()
