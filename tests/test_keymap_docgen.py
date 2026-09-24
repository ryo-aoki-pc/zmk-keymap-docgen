"""Tests for keymap_docgen.py (ZMK keymap -> KEYMAP.html / KEYMAP.xlsx).

Run with: python -m pytest tests/test_keymap_docgen.py
"""

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import keymap_docgen as kd  # noqa: E402

SAMPLE_KEYMAP = REPO_ROOT / 'example' / 'sample.keymap'
SAMPLE_LAYOUT = REPO_ROOT / 'example' / 'sample.layout.json'


def _generate(out_path: Path) -> None:
    subprocess.run(
        [sys.executable, str(REPO_ROOT / 'keymap_docgen.py'),
         str(SAMPLE_KEYMAP), '-l', str(SAMPLE_LAYOUT), '-o', str(out_path)],
        check=True, capture_output=True)


# --------------------------------------------------------------------------
# deterministic .xlsx output
# --------------------------------------------------------------------------

@pytest.mark.skipif(not kd.HAVE_OPENPYXL, reason='openpyxl not installed')
def test_xlsx_bytes_are_deterministic(tmp_path):
    """Regenerating unchanged docs must not produce a diff.

    openpyxl stamps the current time into docProps/core.xml and into every zip
    entry header, which used to make the CI auto-regeneration job commit a new
    KEYMAP.xlsx on every run even when nothing changed.
    """
    first, second = tmp_path / 'first.xlsx', tmp_path / 'second.xlsx'
    _generate(first)
    _generate(second)
    assert first.read_bytes() == second.read_bytes()


@pytest.mark.skipif(not kd.HAVE_OPENPYXL, reason='openpyxl not installed')
def test_xlsx_zip_timestamps_are_pinned(tmp_path):
    out = tmp_path / 'pinned.xlsx'
    _generate(out)
    with zipfile.ZipFile(out) as zf:
        dates = {info.date_time for info in zf.infolist()}
        core = zf.read(kd._XLSX_CORE_PROPS).decode()
    assert dates == {kd._XLSX_ZIP_DATE_TIME}
    assert core.count(kd._XLSX_EPOCH_W3CDTF) == 2


@pytest.mark.skipif(not kd.HAVE_OPENPYXL, reason='openpyxl not installed')
def test_xlsx_is_still_a_readable_workbook(tmp_path):
    openpyxl = pytest.importorskip('openpyxl')
    out = tmp_path / 'readable.xlsx'
    _generate(out)
    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames == ['動作', '経路']
    assert wb['動作'].max_row > 1


def test_pin_core_properties_rewrites_both_timestamps():
    raw = (b'<cp:coreProperties><dcterms:created xsi:type="dcterms:W3CDTF">'
           b'2026-09-24T16:16:53Z</dcterms:created>'
           b'<dcterms:modified xsi:type="dcterms:W3CDTF">'
           b'2026-09-24T16:16:55Z</dcterms:modified></cp:coreProperties>')
    pinned = kd._pin_core_properties(raw)
    assert b'2026-09-24' not in pinned
    assert pinned.count(kd._XLSX_EPOCH_W3CDTF.encode()) == 2
