"""Tests for zmk_to_vial.py (ZMK keymap -> Vial keymap converter)."""

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import zmk_to_vial as z  # noqa: E402
from zmk_to_vial import (  # noqa: E402
    Converter,
    ConvertError,
    hid_to_matrix,
    identity_keymap,
    parse_kp_token,
    keycode_c_expr,
    load_config,
    vial_uid_to_int,
    macro_bytes,
    emit_vil,
    emit_inc,
    emit_report,
    MT, LT, MO, TO, TD, MACRO_KC, KB_KC,
    KC_NO, KC_TRNS, QK_BOOT, QK_RBT,
)

EXAMPLE = REPO_ROOT / 'example'


# ----------------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------------

def make_keymap(tmp_path: Path, body: str) -> Path:
    path = tmp_path / 'test.keymap'
    path.write_text(body, encoding='utf-8')
    return path


def make_config(tmp_path: Path, **overrides) -> dict:
    config = load_config(None)
    config.update(overrides)
    return config


SAMPLE_CONFIG = {
    'identityless_positions': {'&mo 1': 'RIGHT_ALT'},
    'vial_uid': [0x05, 0xE4, 0xA1, 0x7F, 0xDC, 0x87, 0xCB, 0x2A],
}


@pytest.fixture
def sample_conv(tmp_path):
    """The bundled example/sample.keymap converted with its sample config."""
    config = load_config(EXAMPLE / 'sample.vialmap.json')
    return Converter(EXAMPLE / 'sample.keymap', config).convert()


# ----------------------------------------------------------------------------
# Keycode tables and matrix mapping
# ----------------------------------------------------------------------------

class TestKeycodeTables:
    def test_hid_to_matrix_letters(self):
        assert hid_to_matrix(0x04) == (1, 4)    # A
        assert hid_to_matrix(0x14) == (3, 4)    # Q
        assert hid_to_matrix(0x29) == (6, 1)    # Escape

    def test_hid_to_matrix_modifiers(self):
        assert hid_to_matrix(0xE0) == (0, 0)    # LCtrl
        assert hid_to_matrix(0xE6) == (0, 6)    # RAlt

    def test_identity_keymap_shape(self):
        m = identity_keymap()
        assert len(m) == 32
        assert all(len(row) == 8 for row in m)
        assert m[0] == [0xE0, 0xE1, 0xE2, 0xE3, 0xE4, 0xE5, 0xE6, 0xE7]
        assert m[3][4] == 0x14                  # Q at its HID position
        assert m[31] == [KC_NO] * 8             # message row

    def test_parse_plain_keycode(self):
        value, name = parse_kp_token('Q')
        assert value == 0x14 and name == 'KC_Q'

    def test_parse_modified_keycode(self):
        value, name = parse_kp_token('LC(X)')
        assert value == 0x011B
        assert name == 'LCTL(KC_X)'

    def test_parse_nested_modifiers(self):
        value, name = parse_kp_token('LS(LC(HOME))')
        assert value == 0x0300 | 0x4A
        assert name == 'LSFT(LCTL(KC_HOME))'

    def test_parse_right_hand_modifier(self):
        value, _ = parse_kp_token('RC(A)')
        assert value == 0x1104

    def test_parse_unknown_raises(self):
        with pytest.raises(ConvertError):
            parse_kp_token('NOT_A_KEY')

    def test_keycode_constructors(self):
        assert MT(0x01, 0x04) == 0x2104          # LCTL_T(KC_A)
        assert MT(0x11, 0x2D) == 0x312D          # RCTL_T(KC_MINUS)
        assert LT(2, 0x2C) == 0x422C             # LT(2, KC_SPACE)
        assert MO(1) == 0x5221
        assert TO(0) == 0x5200
        assert TD(3) == 0x5703
        assert MACRO_KC(5) == 0x7705
        assert KB_KC(3) == 0x7E03

    def test_keycode_c_expr(self):
        assert keycode_c_expr(KC_NO) == 'KC_NO'
        assert keycode_c_expr(KC_TRNS) == 'KC_TRNS'
        assert keycode_c_expr(0x14) == 'KC_Q'
        assert keycode_c_expr(MO(3)) == 'MO(3)'
        assert keycode_c_expr(TD(1)) == 'TD(1)'
        assert keycode_c_expr(MT(0x02, 0x28)) == 'MT(MOD_LSFT, KC_ENTER)'
        assert keycode_c_expr(LT(1, 0x2C)) == 'LT(1, KC_SPACE)'
        assert keycode_c_expr(0x011B) == 'LCTL(KC_X)'
        assert keycode_c_expr(0x034A) == 'LSFT(LCTL(KC_HOME))'

    def test_keycode_to_vial_string(self):
        from zmk_to_vial import keycode_to_vial_string as v
        # basic v6 spellings (differ from the long C names)
        assert v(KC_NO) == 'KC_NO'
        assert v(KC_TRNS) == 'KC_TRNS'
        assert v(0x04) == 'KC_A'
        assert v(0x2A) == 'KC_BSPACE'          # not KC_BACKSPACE
        assert v(0x33) == 'KC_SCOLON'          # not KC_SEMICOLON
        assert v(0x2F) == 'KC_LBRACKET'
        assert v(0x4B) == 'KC_PGUP'
        assert v(0x4E) == 'KC_PGDOWN'
        assert v(0xE0) == 'KC_LCTRL'           # not KC_LEFT_CTRL
        assert v(0xE1) == 'KC_LSHIFT'
        # parametric
        assert v(MO(1)) == 'MO(1)'
        assert v(TO(4)) == 'TO(4)'
        assert v(TD(0)) == 'TD(0)'
        assert v(MACRO_KC(3)) == 'M3'
        assert v(KB_KC(3)) == 'USER03'         # carrier custom keycode
        assert v(KB_KC(10)) == 'USER10'
        assert v(MT(0x01, 0x04)) == 'LCTL_T(KC_A)'
        assert v(MT(0x02, 0x28)) == 'LSFT_T(KC_ENTER)'
        assert v(MT(0x11, 0x2D)) == 'RCTL_T(KC_MINUS)'
        assert v(LT(2, 0x2C)) == 'LT2(KC_SPACE)'
        # modifier-wrapped
        assert v(0x011B) == 'LCTL(KC_X)'
        assert v(0x024D) == 'LSFT(KC_END)'
        assert v(0x034A) == 'C_S(KC_HOME)'     # ctrl+shift combo
        assert v(QK_BOOT) == 'QK_BOOT'
        assert v(QK_RBT) == 'QK_REBOOT'

    def test_vial_uid(self):
        uid = vial_uid_to_int([0x05, 0xE4, 0xA1, 0x7F, 0xDC, 0x87, 0xCB, 0x2A])
        assert uid == 0x2ACB87DC7FA1E405
        assert vial_uid_to_int(['0x05', '0xE4', '0xA1', '0x7F',
                                '0xDC', '0x87', '0xCB', '0x2A']) == uid
        assert vial_uid_to_int(None) == 0


# ----------------------------------------------------------------------------
# Sample keymap end-to-end
# ----------------------------------------------------------------------------

class TestSampleKeymap:
    def test_layers(self, sample_conv):
        assert sample_conv.kept_layers == [0, 1]
        assert sample_conv.layer_map == {0: 0, 1: 1}

    def test_base_layer_keycodes(self, sample_conv):
        m = sample_conv.matrix
        r, c = hid_to_matrix(0x14)
        assert m[0][r][c] == 0x14                          # &kp Q
        r, c = hid_to_matrix(0x2C)
        assert m[0][r][c] == LT(1, 0x2C)                   # &lt 1 SPACE
        r, c = hid_to_matrix(0x28)
        assert m[0][r][c] == MT(0x02, 0x28)                # &mt LSHIFT ENTER
        r, c = hid_to_matrix(0xE6)
        assert m[0][r][c] == MO(1)                         # &mo 1 -> RALT (config)

    def test_lower_layer_keycodes(self, sample_conv):
        m = sample_conv.matrix
        r, c = hid_to_matrix(0x14)                          # Q key
        assert m[1][r][c] == 0x1E                           # &kp N1
        r, c = hid_to_matrix(0x36)                          # COMMA key (mm_comma identity)
        assert m[1][r][c] == MACRO_KC(0)                    # &macro_hi
        r, c = hid_to_matrix(0x2C)                          # SPACE key
        assert m[1][r][c] == KC_TRNS                        # &trans

    def test_unmapped_keys_passthrough(self, sample_conv):
        m = sample_conv.matrix
        r, c = hid_to_matrix(0x3A)                          # F1: not in the keymap
        assert m[0][r][c] == 0x3A                           # passthrough on base
        assert m[1][r][c] == KC_TRNS                        # transparent above

    def test_mod_morph_to_key_override(self, sample_conv):
        # mm_comma: COMMA normally, SEMICOLON when shift is held
        assert len(sample_conv.key_overrides) == 1
        ko = sample_conv.key_overrides[0]
        assert ko.trigger == 0x36                            # KC_COMMA
        assert ko.replacement == 0x33                        # KC_SEMICOLON
        assert ko.trigger_mods == 0x22                       # either shift
        assert ko.suppressed_mods == 0x22
        assert ko.layers == 0b01                             # DEFAULT layer only
        assert ko.options & 0x80                             # enabled
        assert ko.options & 0x08                             # one_mod (either shift)

    def test_macro_conversion(self, sample_conv):
        assert len(sample_conv.macros) == 1
        macro = sample_conv.macros[0]
        assert macro.zmk_name == 'macro_hi'
        assert macro.actions == [['tap', 0x0B, 0x0C]]        # H, I (merged)

    def test_vil_structure(self, sample_conv):
        vil = emit_vil(sample_conv)
        assert vil['version'] == 1
        assert vil['uid'] == 0x2ACB87DC7FA1E405
        assert vil['vial_protocol'] == 6
        assert vil['via_protocol'] == 9
        assert len(vil['layout']) == 8
        assert all(len(layer) == 32 for layer in vil['layout'])
        assert all(len(row) == 8 for layer in vil['layout'] for row in layer)
        assert len(vil['macro']) == 16
        assert len(vil['tap_dance']) == 32
        assert vil['combo'] == []                            # no combos generated
        assert len(vil['key_override']) == 32                # emitted by default
        assert len(vil['encoder_layout']) == 8
        # layout cells stay ints (restore_layout normalises via serialize(deserialize))
        for layer in vil['layout']:
            for row in layer:
                for code in row:
                    assert isinstance(code, int)
        json.dumps(vil)                                      # must be serialisable

    def test_vil_keycode_sections_are_strings(self, sample_conv):
        """tap_dance / key_override / combo / macro keycodes must be qmk_id
        strings — vial-gui stores them verbatim and crashes on raw ints."""
        vil = emit_vil(sample_conv)
        # tap dance: keycode fields strings, tapping term int (no real TDs here,
        # so check the KC_NO fillers)
        for entry in vil['tap_dance']:
            assert entry[0:4] == ['KC_NO', 'KC_NO', 'KC_NO', 'KC_NO']
            assert isinstance(entry[4], int)
        # key override: trigger/replacement strings, masks ints
        ko = vil['key_override'][0]                          # mm_comma
        assert ko['trigger'] == 'KC_COMMA'
        assert ko['replacement'] == 'KC_SCOLON'              # v6 spelling
        assert isinstance(ko['trigger_mods'], int)
        for ko in vil['key_override']:
            assert isinstance(ko['trigger'], str)
            assert isinstance(ko['replacement'], str)
        # macro: action keycodes are strings, delays ints
        macro = vil['macro'][0]                              # macro_hi: H, I
        assert macro == [['tap', 'KC_H', 'KC_I']]

    def test_vil_omit_key_override(self, tmp_path):
        """With vil_emit_key_override=False the .vil carries no key overrides
        (they come from the firmware EEPROM defaults instead), so a vial-gui
        build that mishandles key-override import does not crash."""
        config = load_config(EXAMPLE / 'sample.vialmap.json')
        config['vil_emit_key_override'] = False
        conv = Converter(EXAMPLE / 'sample.keymap', config).convert()
        vil = emit_vil(conv)
        assert vil['key_override'] == []
        # the .inc still applies the key overrides on the firmware side
        assert 'dynamic_keymap_set_key_override' in emit_inc(conv, 'sample.keymap')

    def test_inc_compiles_shape(self, sample_conv):
        inc = emit_inc(sample_conv, 'sample.keymap')
        assert 'void eeconfig_init_user(void)' in inc
        assert 'static void zmk_apply_keymap_defaults(void)' in inc
        # version-checked auto-apply entry point + a concrete version constant
        assert 'void zmk_keymap_apply_if_outdated(void)' in inc
        assert re.search(r'#define ZMK_KEYMAP_VERSION 0x[0-9A-F]{4}u', inc)
        assert 'eeconfig_read_user()' in inc
        assert 'eeconfig_update_user(' in inc
        assert 'dynamic_keymap_set_keycode' in inc
        assert 'dynamic_keymap_set_key_override' in inc
        assert 'dynamic_keymap_macro_set_buffer' in inc
        assert 'vial_init();' in inc
        assert 'MT(MOD_LSFT, KC_ENTER)' in inc
        assert 'LT(1, KC_SPACE)' in inc
        # no empty C array initialisers (invalid C)
        assert '= {\n};' not in inc

    def test_inc_version_changes_with_keymap(self, tmp_path):
        """The keymap version hash must change when the converted keymap changes
        (so reflashing a modified keymap re-applies it) and be stable otherwise."""
        def version_of(conv):
            return re.search(r'ZMK_KEYMAP_VERSION (0x[0-9A-F]{4})u',
                             emit_inc(conv, 'x.keymap')).group(1)

        config = load_config(EXAMPLE / 'sample.vialmap.json')
        v1 = version_of(Converter(EXAMPLE / 'sample.keymap', config).convert())
        # same input -> same version (deterministic across runs)
        v1b = version_of(Converter(EXAMPLE / 'sample.keymap', config).convert())
        assert v1 == v1b
        # a changed keymap -> different version
        km = make_keymap(tmp_path, KEYMAP_TEMPLATE.format(
            macros='', behaviors='',
            layers='DEFAULT { bindings = <&kp Q &kp Z>; };'))
        v2 = version_of(Converter(km, make_config(tmp_path)).convert())
        assert v2 != v1

    def test_report_contains_sections(self, sample_conv):
        report = emit_report(sample_conv, 'sample.keymap')
        assert '## レイヤー対応' in report
        assert '## マクロ' in report
        assert '## キーオーバーライド' in report
        assert 'mm_comma' in report


# ----------------------------------------------------------------------------
# Behaviour-specific cases (synthetic keymaps)
# ----------------------------------------------------------------------------

KEYMAP_TEMPLATE = '''
/ {{
    macros {{
        {macros}
    }};

    behaviors {{
        {behaviors}
    }};

    keymap {{
        compatible = "zmk,keymap";
        {layers}
    }};
}};
'''


class TestModMorphCases:
    def _convert(self, tmp_path, macros='', behaviors='', layers='', **cfg):
        keymap = make_keymap(tmp_path, KEYMAP_TEMPLATE.format(
            macros=macros, behaviors=behaviors, layers=layers))
        config = make_config(tmp_path, **cfg)
        return Converter(keymap, config).convert()

    def test_case_a_tap_dance_base(self, tmp_path):
        """Nested morph -> TD: position gets TD(0), KOs trigger on TD(0)."""
        conv = self._convert(
            tmp_path,
            macros='''
                macro_x: macro_x {
                    compatible = "zmk,behavior-macro";
                    #binding-cells = <0>;
                    bindings = <&kp HOME>;
                };
            ''',
            behaviors='''
                td_a: td_a {
                    compatible = "zmk,behavior-tap-dance";
                    #binding-cells = <0>;
                    bindings = <&none>, <&macro_x>;
                };
                mm_inner: mm_inner {
                    compatible = "zmk,behavior-mod-morph";
                    bindings = <&td_a>, <&kp END>;
                    #binding-cells = <0>;
                    mods = <(MOD_LSFT|MOD_RSFT)>;
                };
                mm_outer: mm_outer {
                    compatible = "zmk,behavior-mod-morph";
                    bindings = <&mm_inner>, <&kp PAGE_DOWN>;
                    #binding-cells = <0>;
                    mods = <(MOD_LCTL|MOD_RCTL)>;
                };
            ''',
            layers='''
                DEFAULT { bindings = <&kp Q &mm_outer>; };
            ''',
            # the morph chain resolves to &none -> no identity -> explicit mapping
            identityless_positions={'&mm_outer': 'D'})
        # Both nested morphs' overrides are produced, all triggering on TD(0)
        assert len(conv.key_overrides) == 2
        shift_ko = next(ko for ko in conv.key_overrides if ko.trigger_mods == 0x22)
        ctrl_ko = next(ko for ko in conv.key_overrides if ko.trigger_mods == 0x11)
        assert shift_ko.trigger == TD(0)
        assert ctrl_ko.trigger == TD(0)
        assert shift_ko.replacement == 0x4D                  # KC_END
        assert ctrl_ko.replacement == 0x4E                   # KC_PAGE_DOWN
        # The D key position holds the innermost tap dance keycode
        r, c = hid_to_matrix(0x07)
        assert conv.matrix[0][r][c] == TD(0)
        # Tap dance entry references the macro
        assert conv.tap_dances[0].on_tap == KC_NO
        assert conv.tap_dances[0].on_double_tap == MACRO_KC(0)

    def test_case_b_none_base(self, tmp_path):
        """&none base -> carrier keycode + 1 KO."""
        conv = self._convert(
            tmp_path,
            behaviors='''
                mm_x: mm_x {
                    compatible = "zmk,behavior-mod-morph";
                    bindings = <&none>, <&kp END>;
                    #binding-cells = <0>;
                    mods = <(MOD_LSFT|MOD_RSFT)>;
                };
            ''',
            layers='''
                DEFAULT { bindings = <&kp Q &kp W>; };
                UPPER   { bindings = <&kp Q &mm_x>; };
            ''')
        assert len(conv.carriers) == 1
        assert conv.carriers[0].keycode == KB_KC(3)
        assert len(conv.key_overrides) == 1
        ko = conv.key_overrides[0]
        assert ko.trigger == KB_KC(3)
        assert ko.replacement == 0x4D
        assert ko.layers == 0b10                             # UPPER layer
        # W key position on UPPER holds the carrier
        r, c = hid_to_matrix(0x1A)
        assert conv.matrix[1][r][c] == KB_KC(3)

    def test_case_b_qk_mods_base(self, tmp_path):
        """QK_MODS base (LC(Z)) -> carrier + 2 KOs (negative + positive)."""
        conv = self._convert(
            tmp_path,
            behaviors='''
                mm_u: mm_u {
                    compatible = "zmk,behavior-mod-morph";
                    bindings = <&kp LC(Z)>, <&kp PAGE_UP>;
                    #binding-cells = <0>;
                    mods = <(MOD_LCTL|MOD_RCTL)>;
                };
            ''',
            layers='''
                DEFAULT { bindings = <&kp Q &mm_u>; };
            ''',
            identityless_positions={'&mm_u': 'U'})
        assert len(conv.carriers) == 1
        carrier = conv.carriers[0].keycode
        assert len(conv.key_overrides) == 2
        neg_ko = next(ko for ko in conv.key_overrides if ko.negative_mod_mask)
        pos_ko = next(ko for ko in conv.key_overrides if ko.trigger_mods)
        assert neg_ko.trigger == carrier
        assert neg_ko.negative_mod_mask == 0x11
        assert neg_ko.replacement == 0x011D                  # LCTL(KC_Z)
        assert pos_ko.trigger_mods == 0x11
        assert pos_ko.replacement == 0x4B                    # KC_PAGE_UP
        # The U key gets the carrier (identityless config mapping)
        r, c = hid_to_matrix(0x18)
        assert conv.matrix[0][r][c] == carrier

    def test_case_b_macro_base(self, tmp_path):
        """Macro base -> carrier + 2 KOs (macro can't be a KO trigger)."""
        conv = self._convert(
            tmp_path,
            macros='''
                macro_a: macro_a {
                    compatible = "zmk,behavior-macro";
                    #binding-cells = <0>;
                    bindings = <&kp END>;
                };
                macro_b: macro_b {
                    compatible = "zmk,behavior-macro";
                    #binding-cells = <0>;
                    bindings = <&kp HOME>;
                };
            ''',
            behaviors='''
                mm_o: mm_o {
                    compatible = "zmk,behavior-mod-morph";
                    bindings = <&macro_a>, <&macro_b>;
                    #binding-cells = <0>;
                    mods = <(MOD_LSFT|MOD_RSFT)>;
                };
            ''',
            layers='''
                DEFAULT { bindings = <&kp Q &mm_o>; };
            ''',
            identityless_positions={'&mm_o': 'O'})
        assert len(conv.carriers) == 1
        assert len(conv.key_overrides) == 2
        neg_ko = next(ko for ko in conv.key_overrides if ko.negative_mod_mask)
        pos_ko = next(ko for ko in conv.key_overrides if ko.trigger_mods)
        assert neg_ko.replacement == MACRO_KC(0)
        assert pos_ko.replacement == MACRO_KC(1)

    def test_noop_morph_skipped(self, tmp_path):
        """F3 / LS(F3) shift-morph needs no key override."""
        conv = self._convert(
            tmp_path,
            behaviors='''
                mm_n: mm_n {
                    compatible = "zmk,behavior-mod-morph";
                    bindings = <&kp F3>, <&kp LS(F3)>;
                    #binding-cells = <0>;
                    mods = <(MOD_LSFT|MOD_RSFT)>;
                };
            ''',
            layers='''
                DEFAULT { bindings = <&kp Q &mm_n>; };
            ''')
        assert len(conv.key_overrides) == 0
        assert len(conv.carriers) == 0
        # The position still gets KC_F3
        r, c = hid_to_matrix(0x3C)
        assert conv.matrix[0][r][c] == 0x3C

    def test_morph_on_multiple_layers(self, tmp_path):
        """A morph used on two layers gets both bits in its KO layer mask."""
        conv = self._convert(
            tmp_path,
            behaviors='''
                mm_x: mm_x {
                    compatible = "zmk,behavior-mod-morph";
                    bindings = <&kp COMMA>, <&kp SEMICOLON>;
                    #binding-cells = <0>;
                    mods = <(MOD_LSFT|MOD_RSFT)>;
                };
            ''',
            layers='''
                DEFAULT { bindings = <&kp Q &mm_x>; };
                UPPER   { bindings = <&kp W &mm_x>; };
            ''')
        assert len(conv.key_overrides) == 1
        assert conv.key_overrides[0].layers == 0b11


class TestMacros:
    def _convert(self, tmp_path, macros, layers, **cfg):
        keymap = make_keymap(tmp_path, KEYMAP_TEMPLATE.format(
            macros=macros, behaviors='', layers=layers))
        return Converter(keymap, make_config(tmp_path, **cfg)).convert()

    def test_macro_with_waits_and_mods(self, tmp_path):
        """ZMK macro with LS()-wrapped keys and waits -> decomposed actions."""
        conv = self._convert(
            tmp_path,
            macros='''
                macro_dd: macro_dd {
                    compatible = "zmk,behavior-macro";
                    #binding-cells = <0>;
                    bindings = <&kp HOME>, <&macro_wait_time 100>, <&kp LS(END)>,
                               <&macro_wait_time 100>, <&kp LC(X)>;
                };
            ''',
            layers='DEFAULT { bindings = <&kp Q &macro_dd>; };',
            identityless_positions={'&macro_dd': 'D'})
        macro = conv.macros[0]
        assert macro.actions == [
            ['tap', 0x4A],            # HOME
            ['delay', 100],
            ['down', 0xE1], ['tap', 0x4D], ['up', 0xE1],     # LS(END) decomposed
            ['delay', 100],
            ['down', 0xE0], ['tap', 0x1B], ['up', 0xE0],     # LC(X) decomposed
        ]

    def test_macro_press_release_modes(self, tmp_path):
        """&macro_release / &macro_tap mode switches."""
        conv = self._convert(
            tmp_path,
            macros='''
                macro_v: macro_v {
                    compatible = "zmk,behavior-macro";
                    #binding-cells = <0>;
                    bindings = <&macro_release>, <&kp LSHIFT &kp RSHIFT>,
                               <&macro_tap>, <&kp RIGHT>;
                };
            ''',
            layers='DEFAULT { bindings = <&kp Q &macro_v>; };',
            identityless_positions={'&macro_v': 'V'})
        macro = conv.macros[0]
        assert macro.actions == [
            ['up', 0xE1, 0xE5],        # release both shifts (merged)
            ['tap', 0x4F],             # RIGHT
        ]

    def test_macro_layer_switch(self, tmp_path):
        """&to inside a macro -> extended TO(n) tap action with layer remap."""
        conv = self._convert(
            tmp_path,
            macros='''
                macro_t: macro_t {
                    compatible = "zmk,behavior-macro";
                    #binding-cells = <0>;
                    bindings = <&kp HOME>, <&to 1>;
                };
            ''',
            layers='''
                DEFAULT { bindings = <&kp Q &macro_t>; };
                UPPER   { bindings = <&kp W &trans>; };
            ''',
            identityless_positions={'&macro_t': 'T'})
        macro = conv.macros[0]
        # consecutive taps are merged into one action; TO(1) stays an
        # extended keycode (layer index remapped)
        assert macro.actions == [['tap', 0x4A, TO(1)]]

    def test_macro_bytes_encoding(self):
        """Vial EEPROM byte encoding of macro actions."""
        actions = [['tap', 0x4A], ['delay', 100], ['down', 0xE1],
                   ['tap', TO(4)], ['up', 0xE1]]
        data = macro_bytes(actions)
        expected = bytes([
            1, 1, 0x4A,                # SS_QMK_PREFIX, SS_TAP_CODE, HOME
            1, 4, 101, 1,              # delay 100 -> (100%255)+1, (100//255)+1
            1, 2, 0xE1,                # down LSHIFT
            1, 5, 0x04, 0x52,          # ext tap TO(4)=0x5204, little-endian
            1, 3, 0xE1,                # up LSHIFT
        ])
        assert data == expected

    def test_macro_bytes_ext_encoding_non_zero_low(self):
        # LSFT(KC_END) = 0x024D: low byte != 0 -> stored directly little-endian
        data = macro_bytes([['tap', 0x024D]])
        assert data == bytes([1, 5, 0x4D, 0x02])

    def test_macro_bytes_ext_encoding_zero_low(self):
        # TO(0) = 0x5200: low byte == 0 would terminate the macro string, so it
        # is stored as 0xFF52 (the firmware's decode_keycode reverses this)
        data = macro_bytes([['tap', TO(0)]])
        assert data == bytes([1, 5, 0x52, 0xFF])


class TestLayerHandling:
    def _convert(self, tmp_path, layers, **cfg):
        keymap = make_keymap(tmp_path, KEYMAP_TEMPLATE.format(
            macros='', behaviors='', layers=layers))
        return Converter(keymap, make_config(tmp_path, **cfg)).convert()

    THREE_LAYERS = '''
        BASE  { bindings = <&kp Q &mo 1 &mo 2>; };
        NAV   { bindings = <&kp W &trans &trans>; };
        EXTRA { bindings = <&kp E &trans &trans>; };
    '''

    def test_exclude_by_name(self, tmp_path):
        conv = self._convert(tmp_path, self.THREE_LAYERS,
                             exclude_layers=['EXTRA'],
                             identityless_positions={'&mo 1': 'RIGHT_ALT',
                                                     '&mo 2': 'CAPS_LOCK'})
        assert conv.kept_layers == [0, 1]
        # &mo 2 references an excluded layer -> KC_NO + warning
        assert any('除外レイヤー' in w for w in conv.warnings)
        r, c = hid_to_matrix(0x39)                           # CAPS_LOCK
        assert conv.matrix[0][r][c] == KC_NO

    def test_exclude_by_index(self, tmp_path):
        conv = self._convert(tmp_path, self.THREE_LAYERS,
                             exclude_layers=[2],
                             identityless_positions={'&mo 1': 'RIGHT_ALT', '&mo 2': None})
        assert conv.kept_layers == [0, 1]

    def test_layer_remap_after_exclusion(self, tmp_path):
        """Excluding a middle layer shifts later layer indices down."""
        layers = '''
            BASE  { bindings = <&kp Q &mo 2>; };
            SKIP  { bindings = <&kp W &trans>; };
            KEEP  { bindings = <&kp E &trans>; };
        '''
        conv = self._convert(tmp_path, layers,
                             exclude_layers=['SKIP'],
                             identityless_positions={'&mo 2': 'RIGHT_ALT'})
        assert conv.layer_map == {0: 0, 2: 1}
        # &mo 2 (KEEP) must be remapped to MO(1)
        r, c = hid_to_matrix(0xE6)
        assert conv.matrix[0][r][c] == MO(1)

    def test_too_many_layers_raises(self, tmp_path):
        layers = '\n'.join(
            f'L{i} {{ bindings = <&kp Q>; }};' for i in range(9))
        with pytest.raises(ConvertError):
            self._convert(tmp_path, layers)

    def test_duplicate_identity_consistent(self, tmp_path):
        """Two positions with the same identity and same bindings merge silently."""
        layers = '''
            BASE  { bindings = <&kp Q &kp SPACE &kp SPACE>; };
            UPPER { bindings = <&kp W &kp N1 &kp N1>; };
        '''
        conv = self._convert(tmp_path, layers)
        assert not any('重複' in w for w in conv.warnings)

    def test_duplicate_identity_conflict_warns(self, tmp_path):
        """Same identity but different bindings -> warning, stronger binding wins."""
        layers = '''
            BASE  { bindings = <&kp Q &kp SPACE &kp SPACE>; };
            UPPER { bindings = <&kp W &kp N1 &kp N2>; };
        '''
        conv = self._convert(tmp_path, layers)
        assert any('重複' in w for w in conv.warnings)

    def test_strict_unmapped_keys(self, tmp_path):
        conv = self._convert(tmp_path, 'BASE { bindings = <&kp Q>; };',
                             unmapped_keys='none')
        m = conv.matrix
        r, c = hid_to_matrix(0x3A)          # F1 not in keymap
        assert m[0][r][c] == KC_NO


class TestBindings:
    def _convert_single(self, tmp_path, binding, **cfg):
        layers = f'DEFAULT {{ bindings = <&kp Q {binding}>; }};'
        keymap = make_keymap(tmp_path, KEYMAP_TEMPLATE.format(
            macros='', behaviors='', layers=layers))
        cfg.setdefault('identityless_positions', {}).setdefault('&kp Q', 'Q')
        return Converter(keymap, make_config(tmp_path, **cfg)).convert()

    def test_bootloader_and_reset(self, tmp_path):
        conv = self._convert_single(
            tmp_path, '&bootloader',
            identityless_positions={'&bootloader': 'B'})
        r, c = hid_to_matrix(0x05)
        assert conv.matrix[0][r][c] == QK_BOOT

        conv = self._convert_single(
            tmp_path, '&sys_reset',
            identityless_positions={'&sys_reset': 'R'})
        r, c = hid_to_matrix(0x15)
        assert conv.matrix[0][r][c] == QK_RBT

    def test_bt_becomes_noop_with_warning(self, tmp_path):
        conv = self._convert_single(
            tmp_path, '&bt BT_SEL 0',
            identityless_positions={'&bt BT_SEL 0': 'B'})
        r, c = hid_to_matrix(0x05)
        assert conv.matrix[0][r][c] == KC_NO
        assert any('Quantizer に対応機能がない' in w for w in conv.warnings)

    def test_mouse_buttons(self, tmp_path):
        conv = self._convert_single(
            tmp_path, '&mkp MB1',
            identityless_positions={'&mkp MB1': 'B'})
        r, c = hid_to_matrix(0x05)
        assert conv.matrix[0][r][c] == z.QK_MOUSE_BUTTON_1


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

class TestCli:
    def test_cli_end_to_end(self, tmp_path):
        out_vil = tmp_path / 'out.vil'
        out_inc = tmp_path / 'out.inc'
        out_report = tmp_path / 'out.md'
        rc = z.main([
            str(EXAMPLE / 'sample.keymap'),
            '-m', str(EXAMPLE / 'sample.vialmap.json'),
            '--vil', str(out_vil),
            '--inc', str(out_inc),
            '--report', str(out_report),
        ])
        assert rc == 0
        vil = json.loads(out_vil.read_text())
        assert vil['uid'] == 0x2ACB87DC7FA1E405
        assert out_inc.read_text().startswith('/*')
        assert '変換レポート' in out_report.read_text()

    def test_cli_strict_with_warnings(self, tmp_path):
        # A keymap with an unconvertible binding (&bt) on a mapped key
        keymap = make_keymap(tmp_path, KEYMAP_TEMPLATE.format(
            macros='', behaviors='',
            layers='DEFAULT { bindings = <&kp Q &kp W>; };\n'
                   'UPPER   { bindings = <&bt BT_CLR &trans>; };'))
        rc = z.main([str(keymap), '--strict',
                     '--vil', str(tmp_path / 'o.vil'),
                     '--inc', str(tmp_path / 'o.inc'),
                     '--report', str(tmp_path / 'o.md')])
        assert rc == 1
