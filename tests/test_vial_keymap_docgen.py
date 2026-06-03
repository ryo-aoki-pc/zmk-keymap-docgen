"""Tests for vial_keymap_docgen.py (QMK/Vial keymap -> physical-layout HTML).

Run with: python -m pytest tests/test_vial_keymap_docgen.py
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import keymap_docgen as kd  # noqa: E402
import zmk_to_vial as zv  # noqa: E402
import vial_keymap_docgen as v  # noqa: E402


# --------------------------------------------------------------------------
# tokenizer
# --------------------------------------------------------------------------

def test_split_top_level_keeps_nested_parens_whole():
    body = "KC_Q , LT(1, KC_SPC) , MT(MOD_LCTL, KC_A) , _______"
    toks = [t.strip() for t in v.split_top_level(body)]
    assert toks == ['KC_Q', 'LT(1, KC_SPC)', 'MT(MOD_LCTL, KC_A)', '_______']


def test_parse_keyball_layer_yields_42_tokens():
    kc = REPO_ROOT.parent / ('keyball/qmk_firmware/keyboards/keyball/keyball39'
                             '/keymaps/via/keymap.c')
    if not kc.is_file():
        pytest.skip('keyball checkout not present')
    layers = v.parse_qmk_keymap_c(kc.read_text(encoding='utf-8', errors='replace'))
    assert len(layers) == 4
    for name, toks in layers:
        assert len(toks) == 42


def test_norm_token_sentinels():
    body = "KC_A, _______, XXXXXXX, KC_TRNS, KC_NO"
    layers = v.parse_qmk_keymap_c("const x[][1][1] = { [0] = LAYOUT(" + body + ") };")
    _, toks = layers[0]
    assert toks == ['KC_A', '&trans', '&none', '&trans', '&none']


# --------------------------------------------------------------------------
# QMK keycode resolver
# --------------------------------------------------------------------------

@pytest.fixture
def resolver():
    return v.make_qmk_resolver(layer_names={0: 'L0', 1: 'L1', 3: 'L3'},
                               custom_labels={'AML_TO': 'AML切替'})


def tap(resolver, tok):
    return resolver(tok, {}, {}, 'タップ')[0]


def hold(resolver, tok):
    return resolver(tok, {}, {}, 'ホールド')[0]


def test_basic_keycodes(resolver):
    assert tap(resolver, 'KC_Q') == 'Q'
    assert tap(resolver, 'KC_SPC') == 'Space'
    assert tap(resolver, 'KC_ENTER') == 'Enter'      # long spelling (.vil decode)
    assert tap(resolver, 'KC_MINS') == '-'
    assert tap(resolver, 'KC_LEFT_CTRL') == 'LCtrl'  # long spelling
    assert tap(resolver, 'KC_LCTL') == 'LCtrl'       # short spelling


def test_mod_tap_and_layer_tap(resolver):
    assert tap(resolver, 'MT(MOD_LCTL, KC_A)') == 'A'
    assert hold(resolver, 'MT(MOD_LCTL, KC_A)') == 'LCtrl'
    assert tap(resolver, 'LSFT_T(KC_LNG2)') == '英数'
    assert hold(resolver, 'LSFT_T(KC_LNG2)') == 'LShift'
    assert tap(resolver, 'LT(1, KC_SPC)') == 'Space'
    assert hold(resolver, 'LT(1, KC_SPC)') == 'L1'


def test_mod_wrappers_and_layer_switches(resolver):
    assert tap(resolver, 'S(KC_6)') == '⇧6'
    assert tap(resolver, 'LCTL(KC_HOME)') == '⌃Home'
    assert tap(resolver, 'MO(3)') == 'L3'
    assert tap(resolver, 'TO(0)') == '⇒L0'
    assert tap(resolver, 'TG(1)') == '⇄L1'
    assert tap(resolver, 'QK_BOOT') == 'BOOT'
    assert tap(resolver, 'TD(2)') == 'TD2'
    assert tap(resolver, 'QK_MACRO_5') == 'M5'


def test_custom_and_sentinels(resolver):
    assert tap(resolver, 'AML_TO') == 'AML切替'
    assert tap(resolver, 'SSNP_HOR') == 'SSNP_HOR'   # unknown custom: bare name (R2)
    # transparent / no-op normalize like ZMK &trans / &none.
    assert resolver('&trans', {}, {}, 'タップ') == kd.resolve('&trans', {}, {}, 'タップ')
    assert resolver('&none', {}, {}, 'タップ') == kd.resolve('&none', {}, {}, 'タップ')


def test_plain_keys_have_no_distinct_extra_op(resolver):
    # Shift+ / Ctrl+ / double-tap must equal tap for plain keys, so the renderer
    # emits no spurious Mod Morph / Tap Dance extra figure.
    for op in ('ダブルタップ', 'Shift+', 'Ctrl+'):
        assert resolver('KC_Q', {}, {}, op)[0] == tap(resolver, 'KC_Q')
        assert resolver('S(KC_6)', {}, {}, op)[0] == tap(resolver, 'S(KC_6)')


# --------------------------------------------------------------------------
# .vil keycode decoding
# --------------------------------------------------------------------------

def test_qmk_int_to_token_roundtrip():
    assert v.qmk_int_to_token(zv.KC_NO) == '&none'
    assert v.qmk_int_to_token(zv.KC_TRNS) == '&trans'
    assert v.qmk_int_to_token(zv.MT(zv.MOD_BIT_LCTL, 0x04)) == 'MT(MOD_LCTL, KC_A)'
    assert v.qmk_int_to_token(zv.LT(1, 0x2C)) == 'LT(1, KC_SPACE)'
    assert v.qmk_int_to_token(zv.MO(3)) == 'MO(3)'
    # QK_KB carrier mapped to a firmware custom-keycode name.
    assert v.qmk_int_to_token(zv.QK_KB + 3, {zv.QK_KB + 3: 'MM_VIM_W'}) == 'MM_VIM_W'


# --------------------------------------------------------------------------
# layout adapters
# --------------------------------------------------------------------------

def test_adapt_qmk_info_layout_order_and_gap():
    info = {'layouts': {'LAYOUT_no_ball': {'layout': [
        {'label': 'L00', 'x': 0, 'y': 0}, {'label': 'L01', 'x': 1, 'y': 0},
        {'label': 'R01', 'x': 7, 'y': 0}, {'label': 'R00', 'x': 8, 'y': 0},
    ]}}}
    coords, labels, geom, unit, rowcol = v.adapt_qmk_info_layout(info)
    assert coords == [(0.0, 0.0), (1.0, 0.0), (7.0, 0.0), (8.0, 0.0)]
    assert labels == {0: 'L00', 1: 'L01', 2: 'R01', 3: 'R00'}
    rows, cols = kd.build_grid(coords, rowcol)
    assert kd.GAP in cols  # split detected between the halves


def test_adapt_vial_kle_layout_variant_and_matrix():
    vial = {'layouts': {'keymap': [
        ['0,0\n\n\n0,0', {'x': 1}, '0,1\n\n\n0,1'],   # JIS key, then US key
        [{'y': 0.1}, '1,0\n\n\n0,0', '1,1\n\n\n0,1'],
    ]}}
    # choice 0 (JIS): keep the *,0 keys
    (coords, _labels, geom, _u, _rc), matrix = v.adapt_vial_kle_layout(vial, 0)
    assert matrix == [(0, 0), (1, 0)]
    assert coords[0] == (0.0, 0.0)
    # choice 1 (US): keep the *,1 keys; second one sits after the {x:1} advance.
    (_c, _l, _g, _u, _rc), matrix1 = v.adapt_vial_kle_layout(vial, 1)
    assert matrix1 == [(0, 1), (1, 1)]


def test_build_vil_layer_bindings_indexes_matrix():
    # 1 layer, 2x2 matrix; figure order = [(0,0),(1,1)]
    vil_layout = [[[0x04, 0x00], [0x00, zv.KC_NO]]]   # (0,0)=KC_A, (1,1)=KC_NO
    binds = v.build_vil_layer_bindings(vil_layout, 0, [(0, 0), (1, 1)])
    assert binds[0] == 'KC_A'
    assert binds[1] == '&none'


# --------------------------------------------------------------------------
# ZMK regression guard: the new resolver default must not change ZMK output
# --------------------------------------------------------------------------

def test_zmk_write_html_default_resolver_unchanged(tmp_path):
    raw = (REPO_ROOT / 'example/sample.keymap').read_text(encoding='utf-8')
    content = kd.expand_defines(kd.strip_comments(raw), kd.parse_defines(raw))
    names = kd.parse_all_layer_names(content)
    kd.LAYER_NAMES_BY_INDEX.clear()
    kd.LAYER_NAMES_BY_INDEX.update(dict(enumerate(names)))
    macros = kd.parse_macros(content)
    behaviors = kd.parse_behaviors(content)
    layers = [(n, kd.split_layer_bindings(kd.parse_layer(content, n))) for n in names]
    coords, labels, geom, unit, rowcol = kd.load_physical_layout(
        REPO_ROOT / 'example/sample.layout.json')
    kd.KEY_LABELS.clear()
    kd.KEY_LABELS.update(labels)
    grid, cols = kd.build_grid(coords, rowcol)

    default_out = tmp_path / 'default.html'
    explicit_out = tmp_path / 'explicit.html'
    kd.write_html(layers, behaviors, macros, default_out, grid, cols, geom, unit)
    kd.write_html(layers, behaviors, macros, explicit_out, grid, cols, geom, unit,
                  resolver=kd.resolve)
    assert default_out.read_text() == explicit_out.read_text()
    assert '<div class="key' in default_out.read_text()
