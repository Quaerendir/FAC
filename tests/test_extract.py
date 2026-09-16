"""Stage 1: the extractor reproduces the shipped data files from FAC.LST."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import fac_extract  # noqa: E402


@pytest.fixture(scope='module')
def extracted():
    lines = fac_extract.logical_lines_atascii((ROOT / 'FAC.LST').read_bytes())
    return fac_extract.extract(lines)


def test_listing_lines(extracted):
    lines = fac_extract.logical_lines_atascii((ROOT / 'FAC.LST').read_bytes())
    numbers = [no for no, _ in lines]
    assert numbers == sorted(numbers) and numbers[0] == 5 and numbers[-1] == 4160
    assert len(numbers) == 347


def test_levels(extracted):
    levels, charset, tune, warn = extracted
    assert warn == []
    assert len(levels) == 11
    assert [lv['line'] for lv in levels] == [2000 + 200 * k for k in range(11)]
    assert [lv['treasures'] for lv in levels] == [9, 5, 3, 6, 10, 12, 5, 4, 2, 3, 5]
    assert [lv['time'] for lv in levels] == [17, 11, 18, 20, 24, 24, 30, 15, 105, 28, 16]
    assert [lv['steps'] for lv in levels] == [111, 43, 94, 86, 118, 114, 101, 23, 80, 23, 75]
    assert levels[3]['elevators'] == [13, 35, 27, 139, 139, 139, 181, 13]
    assert levels[10]['elevators'] == [160, 160] and levels[10]['next_line'] == 0
    assert all(lv['colors_708_712'] == [138, 205, 88, 0, 18] for lv in levels)
    for lv in levels:
        assert len(lv['rows']) == 12 and all(len(r) == 20 for r in lv['rows'])
        assert sum(''.join(lv['rows']).count(c) for c in "$%&'()") == lv['treasures']


def test_charset(extracted):
    _, charset, tune, _ = extracted
    assert len(charset) == 208
    assert charset[:8] == bytes(8)                                  # glyph 0 = space
    assert charset[8 * 2:8 * 3] == charset[8 * 15:8 * 16] == b'\xff' * 8   # trapdoor looks like wall
    assert charset[8 * 13:8 * 14] == bytes.fromhex('3c7edbffc37e24e7')     # hero, CHR$(13)
    assert hashlib.sha256(charset).hexdigest() == hashlib.sha256((ROOT / 'charset.bin').read_bytes()).hexdigest()
    assert tune[:8] == [96, 91, 81, 91, 96, 108, 121, 128] and len(tune) == 36


def test_cli_reproduces_shipped_files(tmp_path):
    subprocess.run([sys.executable, str(ROOT / 'fac_extract.py'), str(ROOT / 'FAC.LST'), '-o', str(tmp_path)],
                   check=True, capture_output=True)
    for name in ('levels.txt', 'charset.bin', 'meta.json'):
        assert (tmp_path / name).read_bytes() == (ROOT / name).read_bytes(), name
    for name in ('levels.txt', 'charset.bin', 'meta.json'):
        assert (ROOT / 'fac' / 'data' / name).read_bytes() == (ROOT / name).read_bytes(), name
    meta = json.loads((tmp_path / 'meta.json').read_text())
    assert meta['charset']['addr'] == '$9800' and meta['charset']['chbas'] == 152


def test_html_listing_differs_only_where_expected():
    lines = fac_extract.logical_lines_atascii((ROOT / 'FAC.LST').read_bytes())
    report = fac_extract.check_text_listing(ROOT / 'FAC.bas', lines)
    numbers = sorted(int(r.split()[1].rstrip(':')) for r in report)
    charset_lines = [1010, 1020, 1030, 1040, 1050, 1060, 1070, 1080, 1100, 1110, 1120, 1130, 1140]
    assert numbers == [50, 60, 65, 80, 95, 100, 105] + charset_lines
