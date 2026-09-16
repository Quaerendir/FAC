"""Headless tests for the pygame front end (SDL dummy drivers)."""
import json
import os
from pathlib import Path

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')

import pygame  # noqa: E402
import pytest  # noqa: E402

from fac import Engine, Key, Variant, parse_levels  # noqa: E402
from fac.render import (Audio, Charset, Gr0Screen, Pokey, Renderer, Sequencer, Step, _lfsr, atari_rgb,  # noqa: E402
                        death_steps, elevator_steps, intro_steps, level_done_steps, menu_screen, poison_steps,
                        tick_steps, trapdoor_steps, treasure_steps, MS_LOOP_SOUND, TICK_MS)
from fac.play import main  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'fac' / 'data'


@pytest.fixture(scope='module', autouse=True)
def _pygame():
    pygame.init()
    pygame.display.set_mode((64, 64))
    yield
    pygame.quit()


@pytest.fixture
def charset() -> Charset:
    return Charset((DATA / 'charset.bin').read_bytes())


def test_palette():
    assert atari_rgb(0) == (0, 0, 0) and atari_rgb(14) == (238, 238, 238)
    r, g, b = atari_rgb(0x88)
    assert not (r == g == b)


def test_charset_animations(charset):
    hero = charset.glyph(13)
    charset.hero_flip_step(0, 0)
    assert charset.glyph(13)[0] == hero[7] and charset.glyph(13)[7] == hero[0]
    for i in range(1, 8):
        charset.hero_flip_step(0, i)                    # L510..L530: eight swaps = identity
    assert charset.glyph(13) == hero
    charset.hero_death_step(1, 0)                       # L660..L680 on glyph 14
    g = charset.glyph(14)
    orig = charset.original[14 * 8:15 * 8]
    assert g[0] == orig[0] // 2 and g[1] == (orig[1] * 2) & 0xFF and g[2:] == orig[2:]
    for k in range(32):
        charset.restore_hero_byte(k)                    # L695..L715
    assert charset.data[96:128] == charset.original[96:128]


def test_menu_layout():
    cells, cursor = menu_screen(0)
    rows = [bytes(c & 0x7F for c in cells[r * 40:(r + 1) * 40]) for r in range(24)]

    def text(r: int) -> str:
        return ''.join(chr(c + 0x20) if c < 0x40 else chr(c) if c >= 0x60 else '?' for c in rows[r]).rstrip()

    assert text(3).startswith('  WYBIERZ WARIANT (')
    assert text(6) == '    1. bez ograniczenia'
    assert text(9) == '    4. z ograniczonym czasem i krokami'
    assert text(13) == '  "+" - RUCH W LEWO' and text(15) == '  " " - PODSKOK'
    assert text(18).startswith('  ZAGRAJ SOBIE  (') and text(20).startswith('  LUB ZREZYGNUJ (')
    assert cursor == (4, 6) and menu_screen(3)[1] == (4, 9)
    assert cells[3 * 40 + 19] == 0xD9 and cells[3 * 40 + 26] == 0x59     # the bar ends around SELECT
    assert cells[3 * 40 + 20] == 0xB3                                     # inverse 'S'


def test_gr0_wrap_and_position():
    scr = Gr0Screen()
    scr.print(b'A' * 40)
    assert scr.cells[2] == 0x21 and scr.cells[40 + 2] == 0x21 and scr.row == 2
    scr.position(5, 7)
    scr.print(b'\x1d\x1dB', eol=False)
    assert scr.cells[9 * 40 + 5] == 0x22 and (scr.col, scr.row) == (6, 9)


def test_pokey_pure_tone_period():
    p = Pokey(44100)
    p.sound(10, 10, 15)                                 # AUDC = $AF: pure tone, full volume
    samples = p.render(4410)
    assert max(samples) > 10000 and min(samples) < -10000
    crossings = sum(1 for a, b in zip(samples, samples[1:]) if (a < 0) != (b < 0))
    hz = 1_773_447 / 28 / (2 * (10 + 1))
    assert abs(crossings / 2 / 0.1 - hz) < hz * 0.05
    p.sound(0, 0, 0)
    assert all(v == 0 for v in p.render(100))


def test_lfsr_periods():
    for bits, tap in ((4, 3), (5, 3), (17, 12)):
        seq = _lfsr(bits, tap)
        n = len(seq)
        assert n == 2 ** bits - 1 and sum(seq) == 2 ** (bits - 1)
        assert all(seq[(k + bits) % n] == seq[k] ^ seq[(k + tap) % n] for k in range(n))
        assert all(any(seq[(k + p) % n] != seq[k] for k in range(n)) for p in range(1, min(n, 300)))


def test_pokey_noise_continues_across_chunks():
    p = Pokey(44100)
    p.sound(20, 8, 8)                                   # 17-bit poly, half volume
    a = p.render(2000)
    b = p.render(2000)
    assert a != b and len(set(a)) > 2


def test_sequencer_runs_steps_and_audio():
    audio = Audio(enabled=False)
    seq = Sequencer(audio)
    hits: list[str] = []
    seq.add([Step(5.0, (1, 10, 2), lambda: hits.append('a')), Step(30.0, (3, 10, 4), lambda: hits.append('b'))])
    assert seq.busy
    seq.frame()                                         # 20 ms: both actions ran, 15 ms of 'b' left
    assert hits == ['a', 'b'] and audio.last == (3, 10, 4) and seq.busy
    seq.frame()
    assert not seq.busy


def test_sequencer_action_may_replace_the_queue():
    """An action that clears the queue and adds new steps (menu -> START -> level load)."""
    audio = Audio(enabled=False)
    seq = Sequencer(audio)
    hits: list[str] = []

    def restart() -> None:
        seq.clear()
        seq.add([Step(5.0, (7, 10, 7), lambda: hits.append('new'))])

    seq.add([Step(0.0, None, restart), Step(50.0, (1, 10, 1), lambda: hits.append('old'))])
    seq.frame()
    assert hits == ['new'] and not seq.busy and audio.last == (7, 10, 7)
    seq.add([Step(0.0, None, seq.clear)])
    seq.frame()
    assert not seq.busy


def test_step_builders(charset):
    assert len(intro_steps()) == 13 * 16 + 1
    assert len(treasure_steps()) == 3 * 6 + 1 and treasure_steps()[0].sound == (120, 10, 15)
    assert [s.sound[0] for s in elevator_steps()][:3] == [150, 148, 146] and len(elevator_steps()) == 74
    assert len(trapdoor_steps()) == 30 and trapdoor_steps()[14].sound == (0, 8, 15)
    assert len(poison_steps(charset, 0)) == 8 * 12 + 1
    tune = json.loads((DATA / 'meta.json').read_text())['tune_425']
    notes: list[int] = []
    steps = level_done_steps(tune, lambda: notes.append(1), lambda: notes.append(2))
    assert len(steps) == 36 * 17 + 1 and steps[1].sound == (96, 10, 15) and steps[1].ms == MS_LOOP_SOUND
    d = death_steps(charset, 0, lambda: None, lambda: None, lambda: None)
    assert len(d) == 32 + 1 + 32 + 101 + 1 and d[0].sound == (100, 4, 10) and d[-2].sound == (16, 0, 0)
    t = tick_steps([(0, 0, 0), (6, 8, 4)])
    assert len(t) == 2 and abs(sum(s.ms for s in t) - TICK_MS) < 1e-9 and t[-1].sound == (6, 8, 4)


def test_renderer_draws_level_and_menu(charset):
    levels = parse_levels((DATA / 'levels.txt').read_text())
    e = Engine(levels, Variant.TIME_STEPS)
    e.tick(Key.NONE)
    r = Renderer(charset, scale=1)
    surf = pygame.Surface(r.size)
    r.draw_gr18(surf, e.screen, levels[0].colors)
    wall = atari_rgb(138)
    hero = atari_rgb(205)
    assert surf.get_at((0, 0))[:3] == wall               # top wall row
    x, y = levels[0].x * 16, levels[0].y * 16
    assert any(surf.get_at((x + i, y + j))[:3] == hero for i in range(16) for j in range(16))
    r.draw_gr18(surf, e.screen, levels[0].colors, flip=True)
    cells, cursor = menu_screen(1)
    r.draw_gr0(surf, cells, cursor)
    assert surf.get_size() == (320, 192)


def test_play_main_smoke(monkeypatch):
    """Run the real main loop a few frames: intro, menu, START, a couple of ticks, quit."""
    frames = {'n': 0}
    real_flip = pygame.display.flip

    def flip() -> None:
        frames['n'] += 1
        if frames['n'] == 3:
            pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F3, mod=0, unicode='', scancode=0))
        elif frames['n'] == 5:
            pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0, unicode='', scancode=0))
        elif frames['n'] == 40:
            pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_LEFT, mod=0, unicode='', scancode=0))
        elif frames['n'] == 60:
            pygame.event.post(pygame.event.Event(pygame.QUIT))
        real_flip()

    class FastClock:
        def tick(self, fps: int = 0) -> int:
            return 0

    monkeypatch.setattr(pygame.display, 'flip', flip)
    monkeypatch.setattr(pygame.time, 'Clock', FastClock)
    assert main(['--no-intro', '--no-sound', '--scale', '1']) == 0
    assert frames['n'] >= 60
