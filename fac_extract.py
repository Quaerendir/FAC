#!/usr/bin/env python3
"""
fac_extract.py -- Stage 1 of the FAC (Atari 8-bit, J.B. Wisniewski 1992,
Tajemnice ATARI 2/92) conversion pipeline.

Parses the Atari BASIC listing exactly the way the program's own READ/RESTORE
statements consume it and writes:
  * levels.txt    -- the levels (20x12 rows) with their parameters
  * meta.json     -- colours, parameters, elevator lists, the tune, memory layout
  * charset.bin   -- 208 bytes = 26 glyphs from DATA 1010..1140, POKEd at $9800
                     (line 40: 38912 + 16*I + J), selected with POKE 756,152

Input is FAC.LST: the listing as LISTed by Atari BASIC (ATASCII, EOL = $9B),
taken from 2_92.atr in the archive's 2_92_listingi.zip. The HTML article
(2_92_fac.html) is lossy: it dropped the control characters, the whole
charset (lines 1010..1140) and shows the six inverse treasure characters
$A4..$A9 as CP852-decoded letters. `--check FILE` re-encodes such a text
listing and reports every line that differs from the LST.

Author: Quaerendir
"""

import argparse
import json
import re
import sys
from pathlib import Path

LEVEL_W, LEVEL_H = 20, 12
EOL = 0x9B                       # ATASCII end of line
CHARSET_ADDR = 38912             # $9800, line 40
CHBAS = 152                      # POKE 756,152, line 145
CHARSET_LINES = (1010, 13)       # RESTORE 1010, 13 x READ C$, 16 chars each (lines 25..45)
HERO_GLYPHS = (1070, 2)          # RESTORE 1070, 2 x READ C$ -> glyphs 12..15 (lines 690..715)
TUNE_LINE, TUNE_NOTES = 425, 36  # RESTORE 425, 36 x READ KL (line 395..410)
FIRST_LEVEL = 2000               # RE=2000, line 50
ROW_CHARS = set(b' !"#*+/')      # ATASCII bytes that appear in level rows besides the treasures
TREASURE_LO, TREASURE_HI = 0xA4, 0xA9   # inverse $ % & ' ( ) -> glyphs 4..9 in colour 2 (P in line 545)
# the HTML archive interpreted the six inverse bytes as CP852 and re-encoded them;
# 'Ą'.encode('cp852') == b'\xa4' etc., so cp852 undoes it.
TEXT_TO_ATASCII = {chr(c): c for c in range(0x20, 0x7F)}
TEXT_TO_ATASCII.update({bytes([b]).decode('cp852'): b for b in range(TREASURE_LO, TREASURE_HI + 1)})


def logical_lines_atascii(data: bytes) -> list[tuple[int, bytes]]:
    """FAC.LST: one BASIC line per $9B-terminated record."""
    out: list[tuple[int, bytes]] = []
    for rec in data.split(bytes([EOL])):
        if not rec:
            continue
        m = re.match(rb'^(\d+) ?', rec)
        if m and (not out or int(m.group(1)) > out[-1][0]):
            out.append((int(m.group(1)), rec[m.end():]))
        elif out:
            out[-1] = (out[-1][0], out[-1][1] + rec)          # continuation of a wrapped line
        else:
            raise ValueError(f'listing does not start with a line number: {rec[:20]!r}')
    return out


def logical_lines_text(text: str) -> list[tuple[int, str]]:
    """A text listing wrapped at 38 columns (the HTML archive / FAC.bas): a physical line starts a
    new BASIC line iff it begins with a number greater than the previous line number."""
    out: list[tuple[int, str]] = []
    for phys in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        m = re.match(r'^(\d+) ?', phys)
        if m and (not out or int(m.group(1)) > out[-1][0]):
            out.append((int(m.group(1)), phys[m.end():]))
        elif out and phys:
            out[-1] = (out[-1][0], out[-1][1] + phys)
    return out


class DataReader:
    """Atari BASIC READ/RESTORE over the DATA statements of the listing.

    A numeric READ takes the next comma-separated item; a string READ takes everything
    up to the next comma or the end of the statement (that is how the 16-byte charset
    strings and the 20-byte level rows are consumed: neither contains a comma).
    """

    def __init__(self, lines: list[tuple[int, bytes]]) -> None:
        self.data = [(no, body[5:] if body.startswith(b'DATA ') else body[4:])
                     for no, body in lines if body.startswith(b'DATA')]
        self.line_idx = 0
        self.pos = 0

    def restore(self, line: int) -> None:
        self.line_idx = next((k for k, (no, _) in enumerate(self.data) if no >= line), len(self.data))
        self.pos = 0

    @property
    def line_no(self) -> int:
        return self.data[self.line_idx][0] if self.line_idx < len(self.data) else -1

    def _item(self) -> bytes:
        if self.line_idx >= len(self.data):
            raise EOFError('READ past the last DATA statement')
        no, body = self.data[self.line_idx]
        end = body.find(b',', self.pos)
        if end < 0:
            item = body[self.pos:]
            self.line_idx, self.pos = self.line_idx + 1, 0
        else:
            item = body[self.pos:end]
            self.pos = end + 1
        return item

    def read_str(self) -> bytes:
        return self._item()

    def read_num(self) -> int:
        item = self._item().strip()
        if not re.fullmatch(rb'-?\d+', item):
            raise ValueError(f'line {self.line_no}: numeric READ got {item!r}')
        return int(item)


def row_to_text(row: bytes, no: int) -> str:
    out = []
    for b in row:
        if b in ROW_CHARS:
            out.append(chr(b))
        elif TREASURE_LO <= b <= TREASURE_HI:
            out.append(chr(b - 0x80))              # inverse -> plain: $ % & ' ( )
        else:
            raise ValueError(f'line {no}: unexpected byte ${b:02X} in a level row')
    return ''.join(out)


def extract(lines: list[tuple[int, bytes]]) -> tuple[list[dict], bytes, list[int], list[str]]:
    rd = DataReader(lines)
    warn: list[str] = []

    # charset: lines 25..45
    rd.restore(CHARSET_LINES[0])
    charset = bytearray()
    for _ in range(CHARSET_LINES[1]):
        s = rd.read_str()
        if len(s) < 16:
            raise ValueError(f'charset DATA line {rd.line_no} shorter than 16 bytes')
        charset += s[:16]                          # C$(J+1), J=0..15; the rest is the printed legend
    rd.restore(HERO_GLYPHS[0])
    hero = b''.join(rd.read_str()[:16] for _ in range(HERO_GLYPHS[1]))
    if hero != charset[96:128]:
        raise ValueError('lines 1070/1080 (re-read after death) do not match the charset')

    # tune: lines 395..410
    rd.restore(TUNE_LINE)
    tune = [rd.read_num() for _ in range(TUNE_NOTES)]

    # levels: line 145.. for RE = 2000, NS, NS, ... until NS = 0
    levels: list[dict] = []
    re_line, seen = FIRST_LEVEL, set()
    while re_line:
        if re_line in seen:
            raise ValueError(f'level chain loops back to line {re_line}')
        seen.add(re_line)
        rd.restore(re_line)
        colors = [rd.read_num() for _ in range(5)]                  # lines 150..155 -> 708..712
        rows = []
        for _ in range(LEVEL_H):                                    # lines 160..170
            s = rd.read_str()
            if len(s) != LEVEL_W:
                warn.append(f'line {rd.line_no}: level row of {len(s)} chars, expected {LEVEL_W}')
            rows.append(row_to_text(s[:LEVEL_W].ljust(LEVEL_W), rd.line_no))
        x, y, lc, mc, mk, ns = (rd.read_num() for _ in range(6))   # line 175
        elevators = []
        while True:                                                 # line 450, one READ per use
            wd = rd.read_num()
            if wd == 0:
                break
            elevators.append(wd)
        n = len(levels) + 1
        grid = ''.join(rows)
        treasures = sum(grid.count(c) for c in "$%&'()")
        if treasures != lc:
            warn.append(f'level {n}: {treasures} treasure cells but LC={lc} (the game counts LC)')
        if not (0 <= x < LEVEL_W and 0 <= y < LEVEL_H):
            warn.append(f'level {n}: hero start ({x},{y}) outside the screen')
        elif rows[y][x] != ' ':
            warn.append(f'level {n}: hero start ({x},{y}) is not empty ({rows[y][x]!r})')
        if rows[0] != '/' * LEVEL_W or rows[-1].strip('/#!"') != '':
            warn.append(f'level {n}: top/bottom row is not solid')
        for wd in elevators:
            ex, ey = wd // 10, wd % 10 + 1                          # line 455
            if not (0 <= ex < LEVEL_W):
                warn.append(f'level {n}: elevator target {wd} -> ({ex},{ey}) outside the screen')
        if grid.count('#') and not elevators:
            warn.append(f'level {n}: has elevators but an empty elevator list (first use is deadly)')
        levels.append({'index': n, 'line': re_line, 'colors_708_712': colors, 'rows': rows,
                       'x': x, 'y': y, 'treasures': lc, 'time': mc, 'steps': mk,
                       'next_line': ns, 'elevators': elevators})
        re_line = ns
    return levels, bytes(charset), tune, warn


def check_text_listing(path: Path, lst: list[tuple[int, bytes]]) -> list[str]:
    """Compare a text listing (HTML-derived, UTF-8) with the LST, line by line."""
    text = path.read_text(encoding='utf-8')
    txt = dict(logical_lines_text(text))
    ref = dict(lst)
    report = []
    for no in sorted(set(txt) | set(ref)):
        if no not in ref:
            report.append(f'line {no}: only in {path.name}')
            continue
        if no not in txt:
            report.append(f'line {no}: missing from {path.name}')
            continue
        try:
            enc = bytes(TEXT_TO_ATASCII[c] for c in txt[no].rstrip())
        except KeyError as e:
            report.append(f'line {no}: character {e} has no ATASCII mapping')
            continue
        want = ref[no].rstrip(b' ')
        if enc != want:
            lost = [b for b in want if b < 0x20 or b >= 0x80]
            why = (f'{len(lost)} control/inverse bytes dropped' if lost and len(enc) + len(lost) >= len(want)
                   else 'differs')
            report.append(f'line {no}: {why}')
    return report


def glyph_art(charset: bytes) -> str:
    lines = []
    for g in range(len(charset) // 8):
        lines.append(f'--- glyph {g} (screen code {g}, ATASCII {chr(g + 0x20)!r}) ---')
        for row in charset[g * 8:g * 8 + 8]:
            lines.append(''.join('#' if row & (0x80 >> i) else '.' for i in range(8)))
    return '\n'.join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description='Extract data from FAC.LST')
    ap.add_argument('source', type=Path, nargs='?', default=Path('FAC.LST'))
    ap.add_argument('-o', '--outdir', type=Path, default=Path('.'))
    ap.add_argument('--check', type=Path, metavar='TEXT', help='text listing (FAC.bas) to compare with the LST')
    ap.add_argument('--glyphs', action='store_true', help='print the 26 glyphs as ASCII art')
    args = ap.parse_args()

    lines = logical_lines_atascii(args.source.read_bytes())
    levels, charset, tune, warn = extract(lines)
    for w in warn:
        print(f'!! {w}', file=sys.stderr)

    args.outdir.mkdir(parents=True, exist_ok=True)
    with open(args.outdir / 'levels.txt', 'w') as f:
        f.write('; FAC (C) 1992 J.B. Wisniewski / Tajemnice ATARI 2/92\n')
        f.write("; legend: / * + wall (* right end, + left end)  ! poison carpet  \" trapdoor  # elevator\n")
        f.write("; $ % & ' ( ) treasures (inverse video in the listing, colour 710)  (space) empty\n")
        f.write('; params: x y treasures time steps ; elevators: targets x*10+(y-1), used in order ; colors: 708..712\n')
        for lv in levels:
            f.write(f'\n[level {lv["index"]}]\n')
            f.write(f'params: {lv["x"]} {lv["y"]} {lv["treasures"]} {lv["time"]} {lv["steps"]}\n')
            f.write(f'elevators: {",".join(map(str, lv["elevators"]))}\n')
            f.write(f'colors: {",".join(map(str, lv["colors_708_712"]))}\n')
            for row in lv['rows']:
                f.write(row + '\n')
    (args.outdir / 'charset.bin').write_bytes(charset)
    meta = {
        'source': {
            'listing': args.source.name,
            'article': '2_92_fac.html',
            'archive': 'http://tajemnice.atari8.info/2_92/2_92_listingi.zip -> 2_92.atr -> FAC.LST',
        },
        'screen': {'graphics': 18, 'cols': LEVEL_W, 'rows': LEVEL_H},
        'charset': {'addr': f'${CHARSET_ADDR:04X}', 'chbas': CHBAS, 'size': len(charset),
                    'glyphs': len(charset) // 8, 'file': 'charset.bin',
                    'hero_glyphs': [12, 13, 14], 'digit_glyphs': [16, 25]},
        'keys': {'left': {'code': 6, 'key': '+'}, 'right': {'code': 7, 'key': '*'}, 'jump': {'code': 33, 'key': ' '}},
        'tune_425': tune,
        'levels': [{k: v for k, v in lv.items() if k != 'rows'} for lv in levels],
    }
    (args.outdir / 'meta.json').write_text(json.dumps(meta, indent=2) + '\n')

    print(f'OK: {len(levels)} levels, charset.bin {len(charset)} B ({len(charset) // 8} glyphs at '
          f'${CHARSET_ADDR:04X}), tune {len(tune)} notes'
          + (f', {len(warn)} warnings' if warn else ''))
    if args.check:
        report = check_text_listing(args.check, lines)
        print(f'{args.check.name} vs {args.source.name}: {len(report)} lines differ')
        for r in report:
            print('  ' + r)
    if args.glyphs:
        print(glyph_art(charset))


if __name__ == '__main__':
    main()
