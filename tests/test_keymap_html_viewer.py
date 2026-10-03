"""Tests for the one-screen KEYMAP.html viewer (keymap_docgen.write_html).

Run with: python -m pytest tests/test_keymap_html_viewer.py
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import keymap_docgen as kd  # noqa: E402
import vial_keymap_docgen as v  # noqa: E402

SAMPLE_KEYMAP = REPO_ROOT / 'example' / 'sample.keymap'
SAMPLE_LAYOUT = REPO_ROOT / 'example' / 'sample.layout.json'


@pytest.fixture
def sample(monkeypatch):
    """Parsed example/sample.keymap (DEFAULT + LOWER) with the module globals
    set the way main() sets them; monkeypatch restores them afterwards."""
    raw = SAMPLE_KEYMAP.read_text(encoding='utf-8')
    content = kd.expand_defines(kd.strip_comments(raw), kd.parse_defines(raw))
    names = kd.parse_all_layer_names(content)
    monkeypatch.setattr(kd, 'LAYER_NAMES_BY_INDEX', dict(enumerate(names)))
    coords, labels, geom, unit, rowcol = kd.load_physical_layout(SAMPLE_LAYOUT)
    monkeypatch.setattr(kd, 'KEY_LABELS', dict(labels))
    grid, cols = kd.build_grid(coords, rowcol)
    layers = [(n, kd.split_layer_bindings(kd.parse_layer(content, n))) for n in names]
    return {
        'layers': layers, 'behaviors': kd.parse_behaviors(content),
        'macros': kd.parse_macros(content), 'geom': geom, 'unit': unit,
        'grid': grid, 'cols': cols,
    }


def _render(sample, tmp_path, layers=None, name='out.html', **kw) -> str:
    out = tmp_path / name
    kd.write_html(layers if layers is not None else sample['layers'],
                  sample['behaviors'], sample['macros'], out, sample['grid'],
                  sample['cols'], sample['geom'], sample['unit'], **kw)
    return out.read_text(encoding='utf-8')


def _script(html: str) -> str:
    """Body of the viewer script (the last <script> element)."""
    return re.findall(r'<script>\n(.*?)\n</script>', html, re.S)[-1]


def _markup(html: str) -> str:
    """The page markup without the CSS and the viewer script."""
    return html.split('<body>', 1)[1].rsplit('<script>', 1)[0]


def _figure(html: str, view: str, layer: int, op: str = 'main') -> str:
    """Markup of one .kd-fig (view / layer / op)."""
    sec = html.split(f'<section class="kd-view" data-view="{view}">', 1)[1].split('</section>', 1)[0]
    blk = sec.split(f'<div class="kd-layer" data-l="{layer}">', 1)[1]
    blk = blk.split('<div class="kd-layer" ', 1)[0]
    return blk.split(f'<div class="kd-fig" data-op="{op}">', 1)[1].split('<div class="kd-fig" ', 1)[0]


def _keys(fig: str) -> list[str]:
    return re.findall(r'<div class="key[^"]*"[^>]*>', fig)


# --------------------------------------------------------------------------
# layer-jump detection
# --------------------------------------------------------------------------

@pytest.mark.parametrize('action, expected', [
    ('L3', 3), ('⇒L2', 2), ('⇄L1', 1), ('OSL:L4', 4), ('DF:L0', 0), ('L2+⇧', 2),
    ('⇒VIM', 5), ('A ▸ B ▸ ⇒VIM', 5), ('⇧A ▸ ⇒L3', 3),
    ('Q', None), ('L', None), ('LShift', None), ('LCtrl', None), ('', None),
    ('A ▸ L3', None), ('F12', None), ('⇒NOPE', None),
])
def test_layer_jump_target(monkeypatch, action, expected):
    monkeypatch.setattr(kd, 'LAYER_NAMES_BY_INDEX', {0: 'BASE', 5: 'VIM'})
    assert kd._layer_jump_target(action) == expected


def test_layer_jump_target_prefers_layer_names(monkeypatch):
    # A ZMK layer that happens to be named like an index resolves by name.
    monkeypatch.setattr(kd, 'LAYER_NAMES_BY_INDEX', {0: 'L3', 1: 'OTHER'})
    assert kd._layer_jump_target('⇒L3') == 0


def test_layer_label():
    assert kd._layer_label(2, 'VIM_NORMAL') == 'L2 VIM_NORMAL'
    assert kd._layer_label(2, 'Layer 2') == 'L2'
    assert kd._layer_label(2, 'L2') == 'L2'


# --------------------------------------------------------------------------
# page structure
# --------------------------------------------------------------------------

def test_viewer_structure(sample, tmp_path):
    html = _render(sample, tmp_path)
    # Both views, one block per layer each, a main figure per block.
    assert html.count('<section class="kd-view" data-view="key">') == 1
    assert html.count('<section class="kd-view" data-view="path">') == 1
    assert html.count('<h2>レイアウト図</h2>') == 1 and html.count('<h2>経路</h2>') == 1
    for view in ('key', 'path'):
        for layer in (0, 1):
            assert _keys(_figure(html, view, layer))
    # The mod-morph on DEFAULT gets its own Shift+ figure (both views).
    assert 'Mod Morph: Shift+' in _figure(html, 'key', 0, 'Shift+')
    assert 'Mod Morph: Shift+' in _figure(html, 'path', 0, 'Shift+')
    # Tabs, op buttons and view toggle.
    assert '<button type="button" role="tab" data-l="0" title="L0 DEFAULT">' \
           'L0 <span class="kd-tn">DEFAULT</span></button>' in html
    assert '<button type="button" role="tab" data-l="1" ' in html
    assert '<button type="button" data-op="main">タップ / ホールド' in html
    assert '<button type="button" data-op="Shift+" title="Mod Morph: Shift+">Shift+' in html
    assert '<button type="button" data-view="path">経路</button>' in html
    assert '<span class="kd-entry"></span>' in html
    assert '<footer class="kd-detail"></footer>' in html
    # The head one-liner plus the viewer script: exactly two scripts.
    assert html.count('<script') == 2
    assert "<script>document.documentElement.className+=' kd-js'</script>" in html


def test_key_index_and_targets(sample, tmp_path):
    html = _render(sample, tmp_path)
    # Every key carries its binding index, 0..n-1 in each figure.
    for view in ('key', 'path'):
        for layer, op in ((0, 'main'), (1, 'main'), (0, 'Shift+')):
            ks = [int(m) for m in re.findall(r'data-k="(\d+)"', _figure(html, view, layer, op))]
            assert ks == list(range(15)), (view, layer, op)
    default = _figure(html, 'key', 0)
    # &lt 1 SPACE switches while held; &mo 1 is momentary.
    lt = re.search(r'<div class="key"[^>]*data-k="12"[^>]*>', default).group(0)
    assert 'data-to="1" data-via="hold"' in lt
    mo = re.search(r'<div class="key"[^>]*data-k="14"[^>]*>', default).group(0)
    assert 'data-to="1" data-via="mo"' in mo
    # Real (not auto-derived) assignments: the mod-tap's hold, the mod-morph's Shift+.
    mt = re.search(r'<div class="key"[^>]*data-k="13"[^>]*>', default).group(0)
    assert 'data-x="ホールド"' in mt
    mm = re.search(r'<div class="key"[^>]*data-k="9"[^>]*>', default).group(0)
    assert 'data-x="Shift+"' in mm
    plain = re.search(r'<div class="key"[^>]*data-k="0"[^>]*>', default).group(0)
    assert 'data-x' not in plain and 'data-to' not in plain
    # LOWER has no layer keys; the path view links too, but carries no data-x.
    assert 'data-to' not in _figure(html, 'key', 1)
    assert 'data-to="1"' in _figure(html, 'path', 0)
    assert 'data-x' not in _figure(html, 'path', 0)
    # The tooltip stays (used without JavaScript; the viewer moves it).
    assert 'title="タップ: SPACE&#10;ホールド: L1' in default


def test_single_layer_and_real_index(sample, tmp_path):
    # Only DEFAULT: no tabs / 入り方, and no link to the layer that is not drawn.
    html = _markup(_render(sample, tmp_path, layers=sample['layers'][:1]))
    assert '<h1>DEFAULT レイヤー キー割り当て一覧</h1>' in html
    assert 'kd-tabs' not in html and 'kd-entry' not in html
    assert 'data-to' not in html
    # Only LOWER: its block keeps the real layer number.
    html = _render(sample, tmp_path, layers=sample['layers'][1:])
    assert '<div class="kd-layer" data-l="1">' in html
    assert '<h3 class="kd-layer-h">L1 LOWER</h3>' in html


def test_no_path_view(sample, tmp_path):
    html = _markup(_render(sample, tmp_path, show_path=False))
    assert 'data-view="path"' not in html and 'kd-views' not in html
    assert '<h2>経路</h2>' not in html
    assert 'V: キー / 経路' not in html


def test_table_fallback_without_geometry(sample, tmp_path):
    out = tmp_path / 'fallback.html'
    kd.write_html(sample['layers'], sample['behaviors'], sample['macros'], out,
                  sample['grid'], sample['cols'], None, None)
    html = out.read_text(encoding='utf-8')
    assert '<table>' in html and '<h2>経路</h2>' in html
    assert 'kd-view' not in html and '<script' not in html and 'kd-js' not in html


def test_deterministic_lf_and_self_contained(sample, tmp_path):
    a = _render(sample, tmp_path, name='a.html')
    b = _render(sample, tmp_path, name='b.html')
    assert a == b
    raw = (tmp_path / 'a.html').read_bytes()
    assert b'\r\n' not in raw
    # No external resources (CDN, fonts, scripts).
    assert 'http' not in a and ' src=' not in a and '<link' not in a


# --------------------------------------------------------------------------
# the script
# --------------------------------------------------------------------------

def test_script_is_htmlpreview_safe_es5(sample, tmp_path):
    html = _render(sample, tmp_path)
    js = _script(html)
    # htmlpreview.github.io rewrites every '<script' and re-creates inline
    # scripts; '</' or '<!--' would also end / confuse the script element.
    for bad in ('<script', '</', '<!--'):
        assert bad not in js
    # It injects <base href>: no anchors, History API or location tricks.
    for bad in ('href="#', 'pushState', 'replaceState', 'location', 'localStorage',
                'innerHTML', 'application/json'):
        assert bad not in html.split('<script>', 1)[0] + js
    # ES5 only.
    assert '=>' not in js and '`' not in js
    assert not re.search(r'\b(let|const|class)\s', js)
    # The ops are injected from Python's OPS.
    assert 'var OPS = ' + json.dumps(list(kd.OPS), ensure_ascii=False) + ';' in js


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_script_parses(sample, tmp_path):
    js = tmp_path / 'viewer.js'
    js.write_text(_script(_render(sample, tmp_path)), encoding='utf-8')
    subprocess.run(['node', '--check', str(js)], check=True, capture_output=True)


# --------------------------------------------------------------------------
# Vial / QMK: real layer numbers and key-override op buttons
# --------------------------------------------------------------------------

def test_vial_layer_gaps_keep_real_numbers(tmp_path):
    kc = tmp_path / 'keymap.c'
    kc.write_text('const uint16_t keymaps[][1][2] = {\n'
                  '  [0] = LAYOUT(MO(2), TO(1)),\n'
                  '  [2] = LAYOUT(KC_A, KC_B),\n'
                  '};\n')
    layout = tmp_path / 'info.json'
    layout.write_text(json.dumps(
        {'layouts': {'LAYOUT': {'layout': [{'x': 0, 'y': 0}, {'x': 1, 'y': 0}]}}}))
    out = tmp_path / 'out.html'
    assert v.main([str(kc), '--layout', str(layout), '-o', str(out)]) == 0
    html = out.read_text(encoding='utf-8')
    assert '<div class="kd-layer" data-l="2">' in html
    assert 'data-l="1"' not in html
    # Tabs show just the number for 'Layer <n>' names.
    assert '<button type="button" role="tab" data-l="2" title="L2">L2</button>' in html
    layer0 = _figure(html, 'key', 0)
    assert re.search(r'data-k="0" data-to="2" data-via="mo"', layer0)
    # TO(1) targets a layer that is not drawn: no link.
    assert not re.search(r'data-k="1"[^>]*data-to', layer0)


def test_vial_key_override_op_button():
    # Key-override figures come from extra_figures; their op is the caption and
    # the button shows the short op name.
    layers = [('Layer 0', ['KC_A', 'KC_B'])]
    coords, labels, geom, unit, rowcol = v.adapt_qmk_info_layout(
        {'layouts': {'L': {'layout': [{'x': 0, 'y': 0}, {'x': 1, 'y': 0}]}}})
    resolver = v.make_qmk_resolver()

    def extra(name, bindings):
        return [('Key Override: Ctrl+', ['LCTL(KC_BSPACE)', '&none'])]

    entries = kd._layer_figures(layers, {}, {}, geom, unit, resolver=resolver,
                                extra_figures=extra, layer_indices=[0])
    body = '\n'.join(kd._html_viewer_body(layers, entries, [], title='t'))
    assert '<div class="kd-fig" data-op="Key Override: Ctrl+">' in body
    assert ('<button type="button" data-op="Key Override: Ctrl+" '
            'title="Key Override: Ctrl+">Ctrl+') in body
