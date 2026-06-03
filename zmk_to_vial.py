#!/usr/bin/env python3
"""
zmk_to_vial.py — Convert a ZMK .keymap into a Vial keymap for the
Keyboard Quantizer Mini (sekigon-gonnoc).

The Quantizer turns any USB keyboard into a QMK/Vial-programmable one.  Its
32x8 matrix encodes the HID usage code of the key pressed on the attached
keyboard:

    HID k (0x00..0xDF)        ->  row = (k >> 3) + 1, col = k & 7
    modifiers (0xE0..0xE7)    ->  row = 0,            col = k & 7

Each ZMK key position is identified by its BASE-layer tap keycode
(&kp Q -> the attached keyboard's Q key).  Positions without a tap identity
(&mo FUNC, &mo SYM, ...) are assigned to physical keys through a mapping
config (JSON).

Outputs
-------
1. ``.vil``      Vial GUI "Load saved layout" file: layers, macros, tap
                 dances and key overrides (keycodes are emitted as integers,
                 which vial-gui accepts regardless of its keycode-name set).
2. ``.inc``      C include for the vial-qmk keymap: applies the same
                 configuration as EEPROM defaults from eeconfig_init_user().
3. ``report.md`` Human-readable conversion report (Japanese) with warnings.

Behaviour conversion
--------------------
=====================  ======================================================
ZMK                    Vial / QMK
=====================  ======================================================
&kp X / &kp LC(X)      KC_X / LCTL(KC_X)  (modifier bits OR-ed)
&mt MOD KEY            MT(mod, kc)
&lt LAYER KEY          LT(layer, kc)      (layer index remapped)
&mo / &to / &trans     MO(n) / TO(n) / KC_TRNS
&none                  KC_NO
&bootloader            QK_BOOT
&sys_reset             QK_RBT
&mkp MBn               QK_MOUSE_BUTTON_n
macros                 Vial dynamic macros (tap/down/up/delay actions)
tap-dance              Vial tap dance entries
mod-morph              Vial key overrides (see below)
=====================  ======================================================

Mod-morph -> Key Override rules
-------------------------------
* Case A — the no-mod branch resolves to a directly placeable keycode
  (basic key, TD(n), TO(n), MO(n)):  the position gets that keycode and each
  mod branch becomes one key override triggering on it.
* Case B — the no-mod branch is &none, a mod-wrapped keycode (QK_MODS) or a
  macro:  those cannot act as key-override triggers on this firmware
  (process_record_via consumes macros and the keymap's QK_MODS hack
  decomposes modified keycodes before key-override processing), so the
  position gets a "carrier" custom keycode (QK_KB_3, QK_KB_4, ...) and the
  overrides trigger on the carrier.
* No-op morphs (mod branch == mods applied to no-mod branch) are skipped.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Reuse the ZMK devicetree parsing helpers from keymap_docgen.py (same repo).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from keymap_docgen import (  # noqa: E402
    strip_comments,
    parse_defines,
    expand_defines,
    parse_all_layer_names,
    parse_layer,
    split_layer_bindings,
    parse_macros,
    parse_behaviors,
)


# ============================================================================
# QMK keycode model (matches vial-qmk, QMK keycodes version 0x7 / 0.22 era)
# ============================================================================

KC_NO = 0x0000
KC_TRNS = 0x0001

QK_BOOT = 0x7C00
QK_RBT = 0x7C01

QK_TO = 0x5200          # TO(layer)  = QK_TO | layer
QK_MOMENTARY = 0x5220   # MO(layer)  = QK_MOMENTARY | layer
QK_TOGGLE_LAYER = 0x5260  # TG(layer) = QK_TOGGLE_LAYER | layer
QK_TAP_DANCE = 0x5700   # TD(n)      = QK_TAP_DANCE | n
QK_MACRO = 0x7700       # QK_MACRO_n = QK_MACRO + n
QK_KB = 0x7E00          # QK_KB_n    = QK_KB + n
QK_MOUSE_BUTTON_1 = 0x00D1

QK_MOD_TAP = 0x2000     # MT(mod5, kc) = QK_MOD_TAP | (mod5 << 8) | kc
QK_LAYER_TAP = 0x4000   # LT(layer, kc) = QK_LAYER_TAP | (layer << 8) | kc

# QK_MODS modifier bits (<< 8 into the keycode); 5-bit field, bit 4 = right hand
MOD_BIT_LCTL = 0x01
MOD_BIT_LSFT = 0x02
MOD_BIT_LALT = 0x04
MOD_BIT_LGUI = 0x08
MOD_BIT_RIGHT = 0x10

# 8-bit modifier masks used by key overrides (real mods)
MOD_MASK_LCTL = 0x01
MOD_MASK_LSFT = 0x02
MOD_MASK_LALT = 0x04
MOD_MASK_LGUI = 0x08
MOD_MASK_RCTL = 0x10
MOD_MASK_RSFT = 0x20
MOD_MASK_RALT = 0x40
MOD_MASK_RGUI = 0x80

# Key override option bits (quantum/vial.h)
KO_OPT_TRIGGER_DOWN = 1 << 0
KO_OPT_REQUIRED_MOD_DOWN = 1 << 1
KO_OPT_NEGATIVE_MOD_UP = 1 << 2
KO_OPT_ONE_MOD = 1 << 3
KO_OPT_NO_REREGISTER = 1 << 4
KO_OPT_NO_UNREGISTER_ON_OTHER = 1 << 5
KO_OPT_ENABLED = 1 << 7
KO_OPTIONS_DEFAULT = (KO_OPT_ENABLED | KO_OPT_TRIGGER_DOWN
                      | KO_OPT_REQUIRED_MOD_DOWN | KO_OPT_NEGATIVE_MOD_UP)

ALL_LAYERS_MASK = 0xFFFF

# Vial macro byte-stream codes (quantum/send_string + quantum/vial.h)
SS_QMK_PREFIX = 1
SS_TAP_CODE = 1
SS_DOWN_CODE = 2
SS_UP_CODE = 3
SS_DELAY_CODE = 4
VIAL_MACRO_EXT_TAP = 5
VIAL_MACRO_EXT_DOWN = 6
VIAL_MACRO_EXT_UP = 7


def MT(mod5: int, kc: int) -> int:
    return QK_MOD_TAP | ((mod5 & 0x1F) << 8) | (kc & 0xFF)


def LT(layer: int, kc: int) -> int:
    return QK_LAYER_TAP | ((layer & 0xF) << 8) | (kc & 0xFF)


def MO(layer: int) -> int:
    return QK_MOMENTARY | (layer & 0x1F)


def TO(layer: int) -> int:
    return QK_TO | (layer & 0x1F)


def TD(n: int) -> int:
    return QK_TAP_DANCE | (n & 0xFF)


def MACRO_KC(n: int) -> int:
    return QK_MACRO + n


def KB_KC(n: int) -> int:
    return QK_KB + n


# ============================================================================
# Quantizer matrix mapping
# ============================================================================

MATRIX_ROWS = 32
MATRIX_COLS = 8


def hid_to_matrix(hid: int) -> tuple[int, int]:
    """Map a HID usage code to the Keyboard Quantizer Mini matrix position."""
    if 0xE0 <= hid <= 0xE7:           # modifiers land on row 0
        return (0, hid & 7)
    return ((hid >> 3) + 1, hid & 7)


def identity_keymap() -> list[list[int]]:
    """The pass-through default keymap (mirrors keymaps[0] in keymap.c)."""
    rows: list[list[int]] = []
    rows.append([0xE0 + c for c in range(8)])              # row 0: modifiers
    for r in range(1, 31):                                 # rows 1..30
        rows.append([((r - 1) * 8 + c) for c in range(8)])
    rows.append([KC_NO] * 8)                               # row 31: message row
    return rows


# ============================================================================
# ZMK keycode tables
# ============================================================================

def _build_zmk_keycodes() -> dict[str, tuple[str, int]]:
    """ZMK key name -> (QMK C name, basic keycode / HID usage value)."""
    t: dict[str, tuple[str, int]] = {}

    def add(qmk_name: str, value: int, *zmk_names: str):
        for n in zmk_names:
            t[n] = (qmk_name, value)

    # Letters
    for i, ch in enumerate('ABCDEFGHIJKLMNOPQRSTUVWXYZ'):
        add(f'KC_{ch}', 0x04 + i, ch)
    # Numbers
    for i, n in enumerate('1234567890'):
        add(f'KC_{n}', 0x1E + i, f'N{n}', f'NUMBER_{n}', n)
    # Control & whitespace
    add('KC_ENTER', 0x28, 'ENTER', 'RETURN', 'RET')
    add('KC_ESCAPE', 0x29, 'ESCAPE', 'ESC')
    add('KC_BACKSPACE', 0x2A, 'BACKSPACE', 'BSPC')
    add('KC_TAB', 0x2B, 'TAB')
    add('KC_SPACE', 0x2C, 'SPACE')
    # Symbols
    add('KC_MINUS', 0x2D, 'MINUS')
    add('KC_EQUAL', 0x2E, 'EQUAL')
    add('KC_LEFT_BRACKET', 0x2F, 'LEFT_BRACKET', 'LBKT')
    add('KC_RIGHT_BRACKET', 0x30, 'RIGHT_BRACKET', 'RBKT')
    add('KC_BACKSLASH', 0x31, 'BACKSLASH', 'BSLH')
    add('KC_NONUS_HASH', 0x32, 'NON_US_HASH', 'NUHS')
    add('KC_SEMICOLON', 0x33, 'SEMICOLON', 'SEMI')
    add('KC_QUOTE', 0x34, 'SINGLE_QUOTE', 'SQT', 'APOSTROPHE', 'APOS')
    add('KC_GRAVE', 0x35, 'GRAVE')
    add('KC_COMMA', 0x36, 'COMMA')
    add('KC_DOT', 0x37, 'PERIOD', 'DOT')
    add('KC_SLASH', 0x38, 'SLASH', 'FSLH')
    add('KC_CAPS_LOCK', 0x39, 'CAPSLOCK', 'CAPS', 'CLCK', 'CAPS_LOCK')
    # Function keys
    for i in range(1, 13):
        add(f'KC_F{i}', 0x3A + i - 1, f'F{i}')
    # Navigation
    add('KC_PRINT_SCREEN', 0x46, 'PRINTSCREEN', 'PSCRN')
    add('KC_SCROLL_LOCK', 0x47, 'SCROLLLOCK', 'SLCK')
    add('KC_PAUSE', 0x48, 'PAUSE_BREAK', 'PAUSE')
    add('KC_INSERT', 0x49, 'INSERT', 'INS')
    add('KC_HOME', 0x4A, 'HOME')
    add('KC_PAGE_UP', 0x4B, 'PAGE_UP', 'PG_UP')
    add('KC_DELETE', 0x4C, 'DELETE', 'DEL')
    add('KC_END', 0x4D, 'END')
    add('KC_PAGE_DOWN', 0x4E, 'PAGE_DOWN', 'PG_DN')
    add('KC_RIGHT', 0x4F, 'RIGHT_ARROW', 'RIGHT')
    add('KC_LEFT', 0x50, 'LEFT_ARROW', 'LEFT')
    add('KC_DOWN', 0x51, 'DOWN_ARROW', 'DOWN')
    add('KC_UP', 0x52, 'UP_ARROW', 'UP')
    # App / menu
    add('KC_APPLICATION', 0x65, 'K_APP', 'K_APPLICATION', 'K_CONTEXT_MENU', 'APPLICATION', 'APP')
    # International / language keys (JIS etc.)
    add('KC_INTERNATIONAL_1', 0x87, 'INT1', 'INT_RO', 'INTERNATIONAL_1')
    add('KC_INTERNATIONAL_2', 0x88, 'INT2', 'INT_KATAKANAHIRAGANA', 'INT_KANA', 'INTERNATIONAL_2')
    add('KC_INTERNATIONAL_3', 0x89, 'INT3', 'INT_YEN', 'INTERNATIONAL_3')
    add('KC_INTERNATIONAL_4', 0x8A, 'INT4', 'INT_HENKAN', 'INTERNATIONAL_4')
    add('KC_INTERNATIONAL_5', 0x8B, 'INT5', 'INT_MUHENKAN', 'INTERNATIONAL_5')
    add('KC_LANGUAGE_1', 0x90, 'LANG1', 'LANG_HANGEUL', 'LANGUAGE_1')
    add('KC_LANGUAGE_2', 0x91, 'LANG2', 'LANG_HANJA', 'LANGUAGE_2')
    # Modifiers
    add('KC_LEFT_CTRL', 0xE0, 'LEFT_CONTROL', 'LCTRL', 'LCTL')
    add('KC_LEFT_SHIFT', 0xE1, 'LEFT_SHIFT', 'LSHIFT', 'LSHFT', 'LSFT')
    add('KC_LEFT_ALT', 0xE2, 'LEFT_ALT', 'LALT')
    add('KC_LEFT_GUI', 0xE3, 'LEFT_GUI', 'LGUI', 'LEFT_WIN', 'LWIN',
        'LEFT_META', 'LMETA', 'LEFT_COMMAND', 'LCMD')
    add('KC_RIGHT_CTRL', 0xE4, 'RIGHT_CONTROL', 'RCTRL', 'RCTL')
    add('KC_RIGHT_SHIFT', 0xE5, 'RIGHT_SHIFT', 'RSHIFT', 'RSHFT', 'RSFT')
    add('KC_RIGHT_ALT', 0xE6, 'RIGHT_ALT', 'RALT')
    add('KC_RIGHT_GUI', 0xE7, 'RIGHT_GUI', 'RGUI', 'RIGHT_WIN', 'RWIN',
        'RIGHT_META', 'RMETA', 'RIGHT_COMMAND', 'RCMD')
    return t


ZMK_KEYCODES = _build_zmk_keycodes()

# ZMK &kp modifier wrapper -> QK_MODS bits (placed at bits 8..12 of the keycode)
ZMK_MOD_FNS = {
    'LC': MOD_BIT_LCTL,
    'LS': MOD_BIT_LSFT,
    'LA': MOD_BIT_LALT,
    'LG': MOD_BIT_LGUI,
    'RC': MOD_BIT_RIGHT | MOD_BIT_LCTL,
    'RS': MOD_BIT_RIGHT | MOD_BIT_LSFT,
    'RA': MOD_BIT_RIGHT | MOD_BIT_LALT,
    'RG': MOD_BIT_RIGHT | MOD_BIT_LGUI,
}

# QMK C macro names for the modifier wrappers (for .inc / report output)
QMK_MOD_FN_NAMES = {
    MOD_BIT_LCTL: 'LCTL',
    MOD_BIT_LSFT: 'LSFT',
    MOD_BIT_LALT: 'LALT',
    MOD_BIT_LGUI: 'LGUI',
    MOD_BIT_RIGHT | MOD_BIT_LCTL: 'RCTL',
    MOD_BIT_RIGHT | MOD_BIT_LSFT: 'RSFT',
    MOD_BIT_RIGHT | MOD_BIT_LALT: 'RALT',
    MOD_BIT_RIGHT | MOD_BIT_LGUI: 'RGUI',
}

# ZMK hold-tap modifier (keycode name) -> 5-bit MT mod field + C name
ZMK_MT_MODS = {
    'LEFT_CONTROL': (MOD_BIT_LCTL, 'MOD_LCTL'), 'LCTRL': (MOD_BIT_LCTL, 'MOD_LCTL'),
    'LCTL': (MOD_BIT_LCTL, 'MOD_LCTL'),
    'LEFT_SHIFT': (MOD_BIT_LSFT, 'MOD_LSFT'), 'LSHIFT': (MOD_BIT_LSFT, 'MOD_LSFT'),
    'LSHFT': (MOD_BIT_LSFT, 'MOD_LSFT'), 'LSFT': (MOD_BIT_LSFT, 'MOD_LSFT'),
    'LEFT_ALT': (MOD_BIT_LALT, 'MOD_LALT'), 'LALT': (MOD_BIT_LALT, 'MOD_LALT'),
    'LEFT_GUI': (MOD_BIT_LGUI, 'MOD_LGUI'), 'LGUI': (MOD_BIT_LGUI, 'MOD_LGUI'),
    'LEFT_WIN': (MOD_BIT_LGUI, 'MOD_LGUI'), 'LCMD': (MOD_BIT_LGUI, 'MOD_LGUI'),
    'RIGHT_CONTROL': (MOD_BIT_RIGHT | MOD_BIT_LCTL, 'MOD_RCTL'),
    'RCTRL': (MOD_BIT_RIGHT | MOD_BIT_LCTL, 'MOD_RCTL'),
    'RCTL': (MOD_BIT_RIGHT | MOD_BIT_LCTL, 'MOD_RCTL'),
    'RIGHT_SHIFT': (MOD_BIT_RIGHT | MOD_BIT_LSFT, 'MOD_RSFT'),
    'RSHIFT': (MOD_BIT_RIGHT | MOD_BIT_LSFT, 'MOD_RSFT'),
    'RSFT': (MOD_BIT_RIGHT | MOD_BIT_LSFT, 'MOD_RSFT'),
    'RIGHT_ALT': (MOD_BIT_RIGHT | MOD_BIT_LALT, 'MOD_RALT'),
    'RALT': (MOD_BIT_RIGHT | MOD_BIT_LALT, 'MOD_RALT'),
    'RIGHT_GUI': (MOD_BIT_RIGHT | MOD_BIT_LGUI, 'MOD_RGUI'),
    'RGUI': (MOD_BIT_RIGHT | MOD_BIT_LGUI, 'MOD_RGUI'),
}

# ZMK mod-morph "mods" property -> 8-bit real-modifier mask
ZMK_MORPH_MODS = {
    'MOD_LCTL': MOD_MASK_LCTL, 'MOD_LSFT': MOD_MASK_LSFT,
    'MOD_LALT': MOD_MASK_LALT, 'MOD_LGUI': MOD_MASK_LGUI,
    'MOD_RCTL': MOD_MASK_RCTL, 'MOD_RSFT': MOD_MASK_RSFT,
    'MOD_RALT': MOD_MASK_RALT, 'MOD_RGUI': MOD_MASK_RGUI,
}

# ZMK mouse buttons (&mkp) -> QMK mouse button keycodes
ZMK_MOUSE_BUTTONS = {
    'MB1': ('MS_BTN1', QK_MOUSE_BUTTON_1 + 0), 'LCLK': ('MS_BTN1', QK_MOUSE_BUTTON_1 + 0),
    'MB2': ('MS_BTN2', QK_MOUSE_BUTTON_1 + 1), 'RCLK': ('MS_BTN2', QK_MOUSE_BUTTON_1 + 1),
    'MB3': ('MS_BTN3', QK_MOUSE_BUTTON_1 + 2), 'MCLK': ('MS_BTN3', QK_MOUSE_BUTTON_1 + 2),
    'MB4': ('MS_BTN4', QK_MOUSE_BUTTON_1 + 3),
    'MB5': ('MS_BTN5', QK_MOUSE_BUTTON_1 + 4),
}

# 8-bit modifier mask -> human label (report)
MOD_MASK_NAMES = [
    (MOD_MASK_LCTL, 'LCtrl'), (MOD_MASK_LSFT, 'LShift'),
    (MOD_MASK_LALT, 'LAlt'), (MOD_MASK_LGUI, 'LGui'),
    (MOD_MASK_RCTL, 'RCtrl'), (MOD_MASK_RSFT, 'RShift'),
    (MOD_MASK_RALT, 'RAlt'), (MOD_MASK_RGUI, 'RGui'),
]


# ============================================================================
# ZMK token parsing
# ============================================================================

class ConvertError(Exception):
    pass


def parse_kp_token(token: str) -> tuple[int, str]:
    """Parse a ZMK &kp argument (e.g. 'Q', 'LC(X)', 'LS(LC(HOME))') into a
    QMK 16-bit keycode value and its C expression string."""
    token = token.strip()
    m = re.fullmatch(r'(\w+)\s*\((.+)\)', token)
    if m:
        fn, inner = m.group(1), m.group(2)
        if fn not in ZMK_MOD_FNS:
            raise ConvertError(f'unknown ZMK modifier function: {fn}({inner})')
        inner_value, inner_name = parse_kp_token(inner)
        mod_bits = ZMK_MOD_FNS[fn]
        value = inner_value | (mod_bits << 8)
        c_name = f'{QMK_MOD_FN_NAMES[mod_bits]}({inner_name})'
        return value, c_name
    if token in ZMK_KEYCODES:
        qmk_name, value = ZMK_KEYCODES[token]
        return value, qmk_name
    raise ConvertError(f'unknown ZMK keycode: {token}')


def keycode_c_expr(value: int) -> str:
    """Best-effort C expression for a QMK keycode value (for .inc / report)."""
    if value == KC_NO:
        return 'KC_NO'
    if value == KC_TRNS:
        return 'KC_TRNS'
    if value == QK_BOOT:
        return 'QK_BOOT'
    if value == QK_RBT:
        return 'QK_RBT'
    basic = value & 0xFF
    basic_name = None
    for name, v in ZMK_KEYCODES.values():
        if v == basic:
            basic_name = name
            break
    for name, v in ZMK_MOUSE_BUTTONS.values():
        if v == value:
            return name
    if QK_TO <= value < QK_TO + 0x20:
        return f'TO({value - QK_TO})'
    if QK_MOMENTARY <= value < QK_MOMENTARY + 0x20:
        return f'MO({value - QK_MOMENTARY})'
    if QK_TOGGLE_LAYER <= value < QK_TOGGLE_LAYER + 0x20:
        return f'TG({value - QK_TOGGLE_LAYER})'
    if QK_TAP_DANCE <= value <= QK_TAP_DANCE + 0xFF:
        return f'TD({value - QK_TAP_DANCE})'
    if QK_MACRO <= value <= QK_MACRO + 0x7F:
        return f'QK_MACRO_{value - QK_MACRO}'
    if QK_KB <= value <= QK_KB + 0x3F:
        return f'QK_KB_{value - QK_KB}'
    if QK_MOD_TAP <= value < QK_LAYER_TAP:
        mod5 = (value >> 8) & 0x1F
        mods = []
        if mod5 & MOD_BIT_LCTL:
            mods.append('MOD_RCTL' if mod5 & MOD_BIT_RIGHT else 'MOD_LCTL')
        if mod5 & MOD_BIT_LSFT:
            mods.append('MOD_RSFT' if mod5 & MOD_BIT_RIGHT else 'MOD_LSFT')
        if mod5 & MOD_BIT_LALT:
            mods.append('MOD_RALT' if mod5 & MOD_BIT_RIGHT else 'MOD_LALT')
        if mod5 & MOD_BIT_LGUI:
            mods.append('MOD_RGUI' if mod5 & MOD_BIT_RIGHT else 'MOD_LGUI')
        return f'MT({" | ".join(mods) or "0"}, {basic_name or hex(basic)})'
    if QK_LAYER_TAP <= value < 0x5000:
        layer = (value >> 8) & 0xF
        return f'LT({layer}, {basic_name or hex(basic)})'
    if 0x0100 <= value <= 0x1FFF:  # QK_MODS
        mod5 = (value >> 8) & 0x1F
        expr = basic_name or hex(basic)
        # wrap in mod functions, innermost first
        right = mod5 & MOD_BIT_RIGHT
        for bit, fn in ((MOD_BIT_LCTL, 'CTL'), (MOD_BIT_LSFT, 'SFT'),
                        (MOD_BIT_LALT, 'ALT'), (MOD_BIT_LGUI, 'GUI')):
            if mod5 & bit:
                expr = f'{"R" if right else "L"}{fn}({expr})'
        return expr
    if basic_name and value == (value & 0xFF):
        return basic_name
    return f'0x{value:04X}'


def mod_mask_label(mask: int) -> str:
    parts = [name for bit, name in MOD_MASK_NAMES if mask & bit]
    return '+'.join(parts) if parts else 'なし'


# ============================================================================
# Data model
# ============================================================================

@dataclass
class VialMacro:
    index: int
    zmk_name: str
    actions: list = field(default_factory=list)  # [["tap", kc], ["delay", ms], ...]


@dataclass
class VialTapDance:
    index: int
    zmk_name: str
    on_tap: int = KC_NO
    on_hold: int = KC_NO
    on_double_tap: int = KC_NO
    on_tap_hold: int = KC_NO
    tapping_term: int = 200


@dataclass
class VialKeyOverride:
    zmk_name: str                 # source mod-morph name
    trigger: int
    replacement: int
    layers: int = 0
    trigger_mods: int = 0
    negative_mod_mask: int = 0
    suppressed_mods: int = 0
    options: int = KO_OPTIONS_DEFAULT
    description: str = ''


@dataclass
class Carrier:
    """A QK_KB_n custom keycode standing in for a morph whose base binding
    cannot act as a key-override trigger."""
    keycode: int
    zmk_name: str
    reason: str


@dataclass
class MorphResult:
    position_keycode: int
    kos: list[VialKeyOverride] = field(default_factory=list)


@dataclass
class Resolved:
    """A single ZMK binding resolved to a QMK keycode."""
    value: int
    kind: str          # basic / mods / mt / lt / mo / to / trans / none /
                       # td / macro / morph / boot / reset / mouse / unsupported
    c_expr: str = ''


# ============================================================================
# Mapping config
# ============================================================================

DEFAULT_CONFIG = {
    'keyboard': 'sekigon/keyboard_quantizer/mini',
    'matrix': {'rows': MATRIX_ROWS, 'cols': MATRIX_COLS},
    'layer_count': 8,
    'exclude_layers': [],
    'identityless_positions': {},
    'carrier_start': 3,            # first QK_KB_n index used for carriers
    'vial_uid': None,              # list of 8 bytes, or None
    'layout_options': -1,
    'settings': {},                # QSID(str) -> value
    'tapping_term_ms': None,       # convenience: fills settings["7"]
    'unmapped_keys': 'passthrough',  # or 'none'
}


def load_config(path: Path | None) -> dict:
    config = dict(DEFAULT_CONFIG)
    if path is not None:
        with open(path, encoding='utf-8') as f:
            user_cfg = json.load(f)
        config.update(user_cfg)
    settings = {str(k): v for k, v in dict(config.get('settings') or {}).items()}
    if config.get('tapping_term_ms') is not None:
        settings.setdefault('7', int(config['tapping_term_ms']))
    config['settings'] = settings
    return config


def vial_uid_to_int(uid_bytes) -> int:
    """VIAL_KEYBOARD_UID bytes -> the integer vial-gui stores in .vil "uid"."""
    if not uid_bytes:
        return 0
    vals = []
    for b in uid_bytes:
        vals.append(int(b, 0) if isinstance(b, str) else int(b))
    return int.from_bytes(bytes(vals), byteorder='little', signed=False)


# ============================================================================
# Converter
# ============================================================================

class Converter:
    def __init__(self, keymap_path: Path, config: dict):
        self.config = config
        self.warnings: list[str] = []

        raw = Path(keymap_path).read_text(encoding='utf-8')
        text = strip_comments(raw)
        self.defines = parse_defines(text)
        self.text = expand_defines(text, self.defines)

        self.zmk_layer_names = parse_all_layer_names(self.text)
        if not self.zmk_layer_names:
            raise ConvertError('no layers found in keymap')

        self.zmk_macros = parse_macros(self.text)
        self.zmk_behaviors = parse_behaviors(self.text)

        self.layer_bindings: dict[int, list[str]] = {}
        for i, name in enumerate(self.zmk_layer_names):
            raw_bindings = parse_layer(self.text, name)
            self.layer_bindings[i] = split_layer_bindings(raw_bindings or '')

        # Outputs
        self.macros: list[VialMacro] = []
        self.tap_dances: list[VialTapDance] = []
        self.key_overrides: list[VialKeyOverride] = []
        self.carriers: list[Carrier] = []
        self.matrix: list[list[list[int]]] = []     # [layer][row][col]
        self.layer_map: dict[int, int] = {}          # zmk idx -> vial idx
        self.kept_layers: list[int] = []             # zmk indices, vial order
        self.position_matrix: dict[int, tuple[int, int] | None] = {}
        self.position_identity: dict[int, str] = {}  # pos -> description

        # Internal allocation state
        self._macro_ids: dict[str, int] = {}
        self._td_ids: dict[str, int] = {}
        self._morph_cache: dict[str, MorphResult] = {}
        self._next_carrier = int(config.get('carrier_start', 3))

        self.tapping_term = int(config['settings'].get('7', 200))

    # ---- helpers ---------------------------------------------------------

    def warn(self, msg: str):
        if msg not in self.warnings:
            self.warnings.append(msg)

    def _expand_cfg_key(self, key: str) -> str:
        """Apply the keymap's #define aliases to a config key like '&mo FUNC'."""
        out = key
        for name in sorted(self.defines, key=len, reverse=True):
            out = re.sub(rf'\b{re.escape(name)}\b', str(self.defines[name]), out)
        return re.sub(r'\s+', ' ', out).strip()

    def _layer_alias(self, zmk_idx: int) -> str:
        """Readable name for a ZMK layer index (#define alias if present)."""
        for name, val in self.defines.items():
            if val == zmk_idx:
                return name
        if 0 <= zmk_idx < len(self.zmk_layer_names):
            return self.zmk_layer_names[zmk_idx]
        return str(zmk_idx)

    # ---- pipeline --------------------------------------------------------

    def convert(self):
        self._build_layer_map()
        self._resolve_identities()
        self._allocate_macros()
        self._allocate_tap_dances()
        self._convert_macros()
        self._convert_tap_dances()
        self._build_matrix()
        self._validate()
        return self

    # ---- layers ----------------------------------------------------------

    def _build_layer_map(self):
        excludes = set()
        cfg_excl = self.config.get('exclude_layers') or []
        for item in cfg_excl:
            if isinstance(item, int):
                excludes.add(item)
                continue
            # accept layer node names, #define aliases and numeric strings
            if item in self.zmk_layer_names:
                excludes.add(self.zmk_layer_names.index(item))
            elif item in self.defines:
                excludes.add(self.defines[item])
            elif str(item).isdigit():
                excludes.add(int(item))
            else:
                self.warn(f'除外レイヤー指定 "{item}" がキーマップ内に見つかりません')

        vial_idx = 0
        for zmk_idx in range(len(self.zmk_layer_names)):
            if zmk_idx in excludes:
                continue
            self.layer_map[zmk_idx] = vial_idx
            self.kept_layers.append(zmk_idx)
            vial_idx += 1

        layer_count = int(self.config.get('layer_count', 8))
        if len(self.kept_layers) > layer_count:
            raise ConvertError(
                f'変換後のレイヤー数 {len(self.kept_layers)} が上限 {layer_count} を超えています。'
                f'exclude_layers で除外するレイヤーを増やしてください。')

    def _map_layer(self, zmk_idx: int, context: str) -> int | None:
        if zmk_idx in self.layer_map:
            return self.layer_map[zmk_idx]
        self.warn(f'{context}: 除外レイヤー {self._layer_alias(zmk_idx)} への参照を KC_NO に置換しました')
        return None

    # ---- identities ------------------------------------------------------

    def _identity_hid(self, binding: str, depth: int = 0) -> int | None:
        """HID usage code that identifies a BASE-layer binding's physical key.

        &kp X            -> X
        &mt MOD X        -> X            (tap keycode)
        &lt LAYER X      -> X            (tap keycode)
        &morph_name      -> identity of its no-mod branch (recursive)
        &td_name         -> identity of its first-tap branch (recursive)
        anything else    -> None (needs the mapping config)
        """
        if depth > 8:
            return None
        parts = binding.split()
        head = parts[0]
        try:
            if head == '&kp' and len(parts) >= 2:
                value, _ = parse_kp_token(' '.join(parts[1:]))
                if value <= 0xFF:                  # plain keycode only
                    return value
            elif head in ('&mt', '&lt') and len(parts) >= 3:
                value, _ = parse_kp_token(' '.join(parts[2:]))
                if value <= 0xFF:
                    return value
            else:
                ref = head[1:]
                beh = self.zmk_behaviors.get(ref)
                if beh:
                    compatible = beh.get('compatible') or ''
                    bindings = beh.get('bindings') or []
                    if compatible in ('zmk,behavior-mod-morph',
                                      'zmk,behavior-tap-dance') and bindings:
                        return self._identity_hid(bindings[0], depth + 1)
        except ConvertError:
            return None
        return None

    def _resolve_identities(self):
        base_idx = self.kept_layers[0]
        base_bindings = self.layer_bindings[base_idx]

        identityless_cfg = {}
        for key, val in (self.config.get('identityless_positions') or {}).items():
            identityless_cfg[self._expand_cfg_key(key)] = val

        owner: dict[tuple[int, int], int] = {}
        for pos, binding in enumerate(base_bindings):
            norm = re.sub(r'\s+', ' ', binding).strip()
            hid = self._identity_hid(binding)
            label = ''
            if hid is None:
                if norm in identityless_cfg:
                    target = identityless_cfg[norm]
                    if target is None:
                        self.position_matrix[pos] = None
                        self.position_identity[pos] = f'{norm} (マップ対象外)'
                        continue
                    if target not in ZMK_KEYCODES:
                        self.warn(f'位置{pos} ({norm}): 設定のキー名 "{target}" が不明です')
                        self.position_matrix[pos] = None
                        self.position_identity[pos] = f'{norm} (設定エラー)'
                        continue
                    qmk_name, hid = ZMK_KEYCODES[target]
                    label = f'{norm} → {qmk_name} (設定による割当)'
                else:
                    self.position_matrix[pos] = None
                    self.position_identity[pos] = f'{norm} (未割当)'
                    # Warn only when the position has real content on kept layers
                    if self._position_has_content(pos):
                        self.warn(
                            f'位置{pos} ({norm}) は BASE レイヤーにキーコードを持たないため、'
                            f'identityless_positions での割当が必要です (現在は未変換)')
                    continue
            else:
                label = norm

            row, col = hid_to_matrix(hid)
            self.position_matrix[pos] = (row, col)
            self.position_identity[pos] = label
            if (row, col) in owner:
                # duplicate identity: allowed, conflicts are resolved per-layer
                pass
            else:
                owner[(row, col)] = pos

    def _basic_name_of(self, hid: int) -> str:
        for zmk_name, (qmk_name, v) in ZMK_KEYCODES.items():
            if v == hid:
                return zmk_name
        return ''

    def _position_has_content(self, pos: int) -> bool:
        """True if any kept layer has a meaningful (non trans/none) binding here."""
        for zmk_idx in self.kept_layers:
            bindings = self.layer_bindings[zmk_idx]
            if pos >= len(bindings):
                continue
            b = bindings[pos].split()[0]
            if b not in ('&trans', '&none'):
                # references to excluded layers don't count as content
                if b in ('&mo', '&to', '&lt'):
                    parts = bindings[pos].split()
                    if len(parts) >= 2 and parts[1].isdigit() and int(parts[1]) not in self.layer_map:
                        continue
                if b in ('&bt', '&out'):
                    continue
                return True
        return False

    # ---- macros ----------------------------------------------------------

    def _allocate_macros(self):
        for name in self.zmk_macros:
            self._macro_ids[name] = len(self._macro_ids)

    def _allocate_tap_dances(self):
        for name, beh in self.zmk_behaviors.items():
            if (beh.get('compatible') or '') == 'zmk,behavior-tap-dance':
                self._td_ids[name] = len(self._td_ids)

    def _convert_macros(self):
        for name, macro_id in self._macro_ids.items():
            actions = self._convert_macro_bindings(name, self.zmk_macros[name]['bindings'])
            self.macros.append(VialMacro(index=macro_id, zmk_name=name, actions=actions))

    def _convert_macro_bindings(self, macro_name: str, binding_groups: list[str]) -> list:
        """ZMK macro bindings -> Vial macro action list."""
        actions: list = []
        mode = 'tap'  # ZMK default activation mode

        def emit_key(kc_value: int, current_mode: str):
            if kc_value > 0xFF and current_mode == 'tap':
                # mod-wrapped keycode: decompose into down(mods) tap(base) up(mods)
                mod5 = (kc_value >> 8) & 0x1F
                basic = kc_value & 0xFF
                if QK_MOD_TAP <= kc_value or not (0x0100 <= kc_value <= 0x1FFF):
                    # non QK_MODS extended keycode (TO(n) etc.): single ext action
                    actions.append(['tap', kc_value])
                    return
                mod_keycodes = self._mod5_to_keycodes(mod5)
                for mk in mod_keycodes:
                    actions.append(['down', mk])
                actions.append(['tap', basic])
                for mk in reversed(mod_keycodes):
                    actions.append(['up', mk])
            else:
                actions.append([current_mode, kc_value])

        for group in binding_groups:
            for binding in split_layer_bindings(group):
                parts = binding.split()
                head = parts[0]
                if head == '&macro_tap':
                    mode = 'tap'
                elif head == '&macro_press':
                    mode = 'down'
                elif head == '&macro_release':
                    mode = 'up'
                elif head == '&macro_wait_time':
                    actions.append(['delay', int(parts[1])])
                elif head == '&macro_pause_for_release':
                    self.warn(f'マクロ {macro_name}: &macro_pause_for_release は未対応のため無視しました')
                elif head == '&kp':
                    try:
                        value, _ = parse_kp_token(' '.join(parts[1:]))
                    except ConvertError as e:
                        self.warn(f'マクロ {macro_name}: {e}')
                        continue
                    emit_key(value, mode)
                elif head == '&to':
                    vial_layer = self._map_layer(int(parts[1]), f'マクロ {macro_name}')
                    if vial_layer is not None:
                        actions.append(['tap', TO(vial_layer)])
                elif head == '&mo':
                    self.warn(f'マクロ {macro_name}: マクロ内の &mo は未対応のため無視しました')
                elif head == '&none':
                    pass
                else:
                    self.warn(f'マクロ {macro_name}: 未対応のマクロ内バインディング "{binding}" を無視しました')

        return self._merge_macro_actions(actions)

    @staticmethod
    def _mod5_to_keycodes(mod5: int) -> list[int]:
        """QK_MODS 5-bit field -> modifier keycodes (for macro decomposition)."""
        right = bool(mod5 & MOD_BIT_RIGHT)
        out = []
        if mod5 & MOD_BIT_LCTL:
            out.append(0xE4 if right else 0xE0)
        if mod5 & MOD_BIT_LSFT:
            out.append(0xE5 if right else 0xE1)
        if mod5 & MOD_BIT_LALT:
            out.append(0xE6 if right else 0xE2)
        if mod5 & MOD_BIT_LGUI:
            out.append(0xE7 if right else 0xE3)
        return out

    @staticmethod
    def _merge_macro_actions(actions: list) -> list:
        """Merge consecutive same-tag key actions: ["up",a],["up",b] -> ["up",a,b]."""
        merged: list = []
        for act in actions:
            tag = act[0]
            if (merged and tag in ('tap', 'down', 'up') and merged[-1][0] == tag):
                merged[-1].extend(act[1:])
            else:
                merged.append(list(act))
        return merged

    # ---- tap dances ------------------------------------------------------

    def _convert_tap_dances(self):
        for name, td_id in self._td_ids.items():
            beh = self.zmk_behaviors[name]
            bindings = beh.get('bindings') or []
            td = VialTapDance(index=td_id, zmk_name=name, tapping_term=self.tapping_term)
            # ZMK tap-dance bindings: [1st tap, 2nd tap, ...]
            slots = ['on_tap', 'on_double_tap']
            for i, b in enumerate(bindings):
                resolved = self.resolve_binding(b, context=f'タップダンス {name}',
                                                allow_morph=False)
                if i < len(slots):
                    setattr(td, slots[i], resolved.value)
                else:
                    self.warn(f'タップダンス {name}: {i + 1}打目以降は Vial 未対応のため無視しました')
            self.tap_dances.append(td)

    # ---- binding resolution ----------------------------------------------

    def resolve_binding(self, binding: str, context: str,
                        allow_morph: bool = True,
                        morph_layer_mask: int = 0) -> Resolved:
        """Convert one ZMK binding string into a QMK keycode."""
        binding = re.sub(r'\s+', ' ', binding).strip()
        parts = binding.split()
        head = parts[0]

        if head == '&trans':
            return Resolved(KC_TRNS, 'trans', 'KC_TRNS')
        if head == '&none':
            return Resolved(KC_NO, 'none', 'KC_NO')
        if head == '&bootloader':
            return Resolved(QK_BOOT, 'boot', 'QK_BOOT')
        if head in ('&sys_reset', '&reset'):
            return Resolved(QK_RBT, 'reset', 'QK_RBT')

        if head == '&kp':
            try:
                value, c_name = parse_kp_token(' '.join(parts[1:]))
            except ConvertError as e:
                self.warn(f'{context}: {e} → KC_NO に置換しました')
                return Resolved(KC_NO, 'unsupported', 'KC_NO')
            kind = 'mods' if value > 0xFF else 'basic'
            return Resolved(value, kind, c_name)

        if head == '&mt' and len(parts) >= 3:
            mod_name = parts[1]
            if mod_name not in ZMK_MT_MODS:
                self.warn(f'{context}: &mt の修飾キー "{mod_name}" が不明です → KC_NO')
                return Resolved(KC_NO, 'unsupported', 'KC_NO')
            mod5, mod_c = ZMK_MT_MODS[mod_name]
            try:
                kc, kc_name = parse_kp_token(' '.join(parts[2:]))
            except ConvertError as e:
                self.warn(f'{context}: {e} → KC_NO に置換しました')
                return Resolved(KC_NO, 'unsupported', 'KC_NO')
            if kc > 0xFF:
                self.warn(f'{context}: &mt のタップ側に修飾付きキーコードは使用できません → KC_NO')
                return Resolved(KC_NO, 'unsupported', 'KC_NO')
            return Resolved(MT(mod5, kc), 'mt', f'MT({mod_c}, {kc_name})')

        if head == '&lt' and len(parts) >= 3:
            vial_layer = self._map_layer(int(parts[1]), context)
            if vial_layer is None:
                return Resolved(KC_NO, 'unsupported', 'KC_NO')
            try:
                kc, kc_name = parse_kp_token(' '.join(parts[2:]))
            except ConvertError as e:
                self.warn(f'{context}: {e} → KC_NO に置換しました')
                return Resolved(KC_NO, 'unsupported', 'KC_NO')
            if kc > 0xFF:
                self.warn(f'{context}: &lt のタップ側に修飾付きキーコードは使用できません → KC_NO')
                return Resolved(KC_NO, 'unsupported', 'KC_NO')
            return Resolved(LT(vial_layer, kc), 'lt', f'LT({vial_layer}, {kc_name})')

        if head == '&mo' and len(parts) >= 2:
            vial_layer = self._map_layer(int(parts[1]), context)
            if vial_layer is None:
                return Resolved(KC_NO, 'unsupported', 'KC_NO')
            return Resolved(MO(vial_layer), 'mo', f'MO({vial_layer})')

        if head == '&to' and len(parts) >= 2:
            vial_layer = self._map_layer(int(parts[1]), context)
            if vial_layer is None:
                return Resolved(KC_NO, 'unsupported', 'KC_NO')
            return Resolved(TO(vial_layer), 'to', f'TO({vial_layer})')

        if head == '&tog' and len(parts) >= 2:
            vial_layer = self._map_layer(int(parts[1]), context)
            if vial_layer is None:
                return Resolved(KC_NO, 'unsupported', 'KC_NO')
            return Resolved(QK_TOGGLE_LAYER | vial_layer, 'tog', f'TG({vial_layer})')

        if head == '&mkp' and len(parts) >= 2:
            btn = parts[1]
            if btn in ZMK_MOUSE_BUTTONS:
                name, value = ZMK_MOUSE_BUTTONS[btn]
                return Resolved(value, 'mouse', name)
            self.warn(f'{context}: 不明なマウスボタン "{btn}" → KC_NO')
            return Resolved(KC_NO, 'unsupported', 'KC_NO')

        if head in ('&bt', '&out'):
            self.warn(f'{context}: "{binding}" は Quantizer に対応機能がないため KC_NO に置換しました')
            return Resolved(KC_NO, 'unsupported', 'KC_NO')

        # custom behaviors / macros referenced by &name
        ref = head[1:]
        if ref in self._macro_ids:
            mid = self._macro_ids[ref]
            return Resolved(MACRO_KC(mid), 'macro', f'QK_MACRO_{mid}')
        if ref in self._td_ids:
            tid = self._td_ids[ref]
            return Resolved(TD(tid), 'td', f'TD({tid})')
        if ref in self.zmk_behaviors:
            compatible = self.zmk_behaviors[ref].get('compatible') or ''
            if compatible == 'zmk,behavior-mod-morph':
                if not allow_morph:
                    self.warn(f'{context}: ネストしたモッドモーフ "{ref}" はこの文脈では未対応です → KC_NO')
                    return Resolved(KC_NO, 'unsupported', 'KC_NO')
                result = self._convert_morph(ref, morph_layer_mask)
                return Resolved(result.position_keycode, 'morph',
                                keycode_c_expr(result.position_keycode))
            self.warn(f'{context}: 未対応のビヘイビア "{binding}" (compatible={compatible}) → KC_NO')
            return Resolved(KC_NO, 'unsupported', 'KC_NO')

        self.warn(f'{context}: 未対応のバインディング "{binding}" → KC_NO')
        return Resolved(KC_NO, 'unsupported', 'KC_NO')

    # ---- mod-morphs -------------------------------------------------------

    def _parse_morph_mods(self, mods_str: str | None, name: str) -> int:
        if not mods_str:
            self.warn(f'モッドモーフ {name}: mods が未定義です')
            return 0
        mask = 0
        for token in re.split(r'[|\s]+', mods_str):
            token = token.strip('()')
            if not token:
                continue
            if token in ZMK_MORPH_MODS:
                mask |= ZMK_MORPH_MODS[token]
            else:
                self.warn(f'モッドモーフ {name}: 不明な modifier "{token}"')
        return mask

    def _alloc_carrier(self, zmk_name: str, reason: str) -> int:
        kc = KB_KC(self._next_carrier)
        self.carriers.append(Carrier(keycode=kc, zmk_name=zmk_name, reason=reason))
        self._next_carrier += 1
        return kc

    @staticmethod
    def _is_noop_morph(mod_mask: int, base_kc: int, replacement_kc: int) -> bool:
        """True when "morph mods held -> replacement" is what a plain keyboard
        would do anyway (e.g. F3 with an LS(F3) shift-morph), so no key
        override is needed."""
        if replacement_kc == base_kc:
            return True
        # Only basic base keycodes with a QK_MODS replacement can be no-ops
        if base_kc > 0xFF or not (0x0100 <= replacement_kc <= 0x1FFF):
            return False
        if (replacement_kc & 0xFF) != base_kc:
            return False
        # Replacement's QK_MODS bits -> 8-bit real-modifier mask
        mod5 = (replacement_kc >> 8) & 0x1F
        right = bool(mod5 & MOD_BIT_RIGHT)
        mask8 = 0
        for bit, left_mask in ((MOD_BIT_LCTL, MOD_MASK_LCTL), (MOD_BIT_LSFT, MOD_MASK_LSFT),
                               (MOD_BIT_LALT, MOD_MASK_LALT), (MOD_BIT_LGUI, MOD_MASK_LGUI)):
            if mod5 & bit:
                mask8 |= (left_mask << 4) if right else left_mask
        # No-op when every replacement modifier is one of the morph's trigger mods
        return (mask8 & ~mod_mask) == 0

    def _convert_morph(self, name: str, layer_mask: int) -> MorphResult:
        """Convert a ZMK mod-morph into a position keycode + key overrides."""
        if name in self._morph_cache:
            cached = self._morph_cache[name]
            for ko in cached.kos:
                ko.layers |= layer_mask
            return cached

        beh = self.zmk_behaviors[name]
        bindings = beh.get('bindings') or []
        if len(bindings) < 2:
            self.warn(f'モッドモーフ {name}: bindings が2つ未満です → KC_NO')
            result = MorphResult(KC_NO)
            self._morph_cache[name] = result
            return result

        mod_mask = self._parse_morph_mods(beh.get('mods'), name)
        base_binding, mod_binding = bindings[0], bindings[1]

        # Resolve the mod branch (replacement)
        mod_resolved = self.resolve_binding(
            mod_binding, context=f'モッドモーフ {name} (修飾側)', allow_morph=False)

        kos: list[VialKeyOverride] = []
        # Resolve the base branch; nested morphs recurse and contribute their KOs
        base_parts = base_binding.split()
        base_ref = base_parts[0][1:] if base_parts else ''
        nested_morph = (base_ref in self.zmk_behaviors and
                        (self.zmk_behaviors[base_ref].get('compatible') or '') == 'zmk,behavior-mod-morph')

        if nested_morph:
            nested = self._convert_morph(base_ref, layer_mask)
            position_kc = nested.position_keycode
            base_kind = 'nested'
        else:
            base_resolved = self.resolve_binding(
                base_binding, context=f'モッドモーフ {name} (無修飾側)', allow_morph=False)
            position_kc = base_resolved.value
            base_kind = base_resolved.kind

        # Determine trigger strategy
        options = KO_OPTIONS_DEFAULT
        # "either left or right modifier" (the common ZMK pattern) needs one_mod
        if bin(mod_mask).count('1') > 1:
            options |= KO_OPT_ONE_MOD

        morph_desc = f'{name}: {mod_mask_label(mod_mask)} で {keycode_c_expr(mod_resolved.value)}'

        if base_kind in ('none', 'mods', 'macro'):
            # Case B: carrier keycode needed
            reasons = {'none': '無修飾側が &none', 'mods': '無修飾側が修飾付きキーコード',
                       'macro': '無修飾側がマクロ'}
            carrier = self._alloc_carrier(name, reasons.get(base_kind, base_kind))
            old_position_kc = position_kc
            position_kc = carrier
            if base_kind == 'none':
                kos.append(VialKeyOverride(
                    zmk_name=name, trigger=carrier, replacement=mod_resolved.value,
                    layers=layer_mask, trigger_mods=mod_mask,
                    suppressed_mods=mod_mask, options=options,
                    description=morph_desc))
            else:
                # KO #1: no morph mods held -> base content
                kos.append(VialKeyOverride(
                    zmk_name=name, trigger=carrier, replacement=old_position_kc,
                    layers=layer_mask, trigger_mods=0,
                    negative_mod_mask=mod_mask, suppressed_mods=0,
                    options=KO_OPTIONS_DEFAULT,
                    description=f'{name}: 修飾なしで {keycode_c_expr(old_position_kc)}'))
                # KO #2: morph mods held -> mod content
                kos.append(VialKeyOverride(
                    zmk_name=name, trigger=carrier, replacement=mod_resolved.value,
                    layers=layer_mask, trigger_mods=mod_mask,
                    suppressed_mods=mod_mask, options=options,
                    description=morph_desc))
        else:
            # Case A: the base keycode itself is the trigger
            if self._is_noop_morph(mod_mask, position_kc, mod_resolved.value):
                # mod branch == mods + base -> standard keyboard behaviour, no KO needed
                pass
            else:
                kos.append(VialKeyOverride(
                    zmk_name=name, trigger=position_kc, replacement=mod_resolved.value,
                    layers=layer_mask, trigger_mods=mod_mask,
                    suppressed_mods=mod_mask, options=options,
                    description=morph_desc))

        self.key_overrides.extend(kos)
        result = MorphResult(position_kc, kos)
        self._morph_cache[name] = result
        return result

    # ---- matrix -----------------------------------------------------------

    def _build_matrix(self):
        layer_count = int(self.config.get('layer_count', 8))
        passthrough = self.config.get('unmapped_keys', 'passthrough') == 'passthrough'

        # Layer 0 default: identity (pass-through) or all KC_NO
        base_default = identity_keymap() if passthrough else \
            [[KC_NO] * MATRIX_COLS for _ in range(MATRIX_ROWS)]
        self.matrix = [
            [row[:] for row in base_default] if v == 0 else
            [[KC_TRNS] * MATRIX_COLS for _ in range(MATRIX_ROWS)]
            for v in range(layer_count)
        ]

        # Strength used to resolve conflicts when two ZMK positions share a
        # physical key: real binding > &trans > &none.
        def strength(kind: str) -> int:
            return {'none': 0, 'trans': 1}.get(kind, 2)

        placed: dict[tuple[int, int, int], tuple[int, str, int]] = {}
        for zmk_idx in self.kept_layers:
            vial_layer = self.layer_map[zmk_idx]
            bindings = self.layer_bindings[zmk_idx]
            base_count = len(self.layer_bindings[self.kept_layers[0]])
            if len(bindings) != base_count:
                self.warn(f'レイヤー {self._layer_alias(zmk_idx)}: バインディング数 '
                          f'{len(bindings)} が BASE ({base_count}) と一致しません')
            for pos, binding in enumerate(bindings):
                mat = self.position_matrix.get(pos)
                if mat is None:
                    continue
                row, col = mat
                ctx = f'レイヤー {self._layer_alias(zmk_idx)} 位置{pos}'
                resolved = self.resolve_binding(binding, context=ctx,
                                                morph_layer_mask=1 << vial_layer)

                key = (vial_layer, row, col)
                new_value, new_kind = resolved.value, resolved.kind
                if key in placed:
                    old_value, old_kind, old_pos = placed[key]
                    if strength(new_kind) <= strength(old_kind):
                        if (strength(new_kind) == strength(old_kind) == 2
                                and new_value != old_value):
                            self.warn(
                                f'{ctx}: 位置{old_pos} と物理キーが重複し、バインディングが異なります '
                                f'({keycode_c_expr(old_value)} を優先、{keycode_c_expr(new_value)} は破棄)')
                        continue
                placed[key] = (new_value, new_kind, pos)

                # KC_TRNS on layer 0 falls through to nothing -> keep identity/KC_NO
                if vial_layer == 0 and new_kind == 'trans':
                    continue
                self.matrix[vial_layer][row][col] = new_value

    # ---- validation --------------------------------------------------------

    def _validate(self):
        limits = [
            (len(self.macros), 16, 'マクロ'),
            (len(self.tap_dances), 32, 'タップダンス'),
            (len(self.key_overrides), 32, 'キーオーバーライド'),
            (self._next_carrier, 32, 'カスタムキーコード (QK_KB_n)'),
            (len(self.kept_layers), int(self.config.get('layer_count', 8)), 'レイヤー'),
        ]
        for count, limit, label in limits:
            if count > limit:
                raise ConvertError(f'{label} の数 {count} が上限 {limit} を超えています')

        # KO trigger conflicts: same trigger keycode used by different morphs
        # on overlapping layers
        seen: dict[int, VialKeyOverride] = {}
        for ko in self.key_overrides:
            prev = seen.get(ko.trigger)
            if prev and prev.zmk_name != ko.zmk_name and (prev.layers & ko.layers) \
                    and prev.trigger_mods == ko.trigger_mods:
                self.warn(f'キーオーバーライドのトリガー衝突: {prev.zmk_name} と {ko.zmk_name} '
                          f'(trigger={keycode_c_expr(ko.trigger)})')
            seen[ko.trigger] = ko

        # Macro byte budget (rough check; EEPROM region is keyboard dependent)
        total = sum(len(macro_bytes(m.actions)) + 1 for m in self.macros)
        if total > 2048:
            self.warn(f'マクロの合計バイト数 {total} が大きすぎる可能性があります (EEPROM領域を確認してください)')


# ============================================================================
# Macro byte encoding (VIA/Vial EEPROM format)
# ============================================================================

def _encode_ext_keycode(kc: int) -> tuple[int, int]:
    """2-byte keycode encoding used by VIAL_MACRO_EXT_*; avoids 0x00 bytes."""
    if (kc & 0xFF) == 0:
        kc = 0xFF00 | (kc >> 8)
    return kc & 0xFF, (kc >> 8) & 0xFF


def macro_bytes(actions: list) -> bytes:
    """Vial macro action list -> EEPROM byte stream (excluding the trailing NUL)."""
    out = bytearray()
    ext_codes = {'tap': VIAL_MACRO_EXT_TAP, 'down': VIAL_MACRO_EXT_DOWN, 'up': VIAL_MACRO_EXT_UP}
    basic_codes = {'tap': SS_TAP_CODE, 'down': SS_DOWN_CODE, 'up': SS_UP_CODE}
    for act in actions:
        tag = act[0]
        if tag == 'delay':
            ms = int(act[1])
            out += bytes([SS_QMK_PREFIX, SS_DELAY_CODE, (ms % 255) + 1, (ms // 255) + 1])
        elif tag == 'text':
            out += act[1].encode('utf-8')
        elif tag in ('tap', 'down', 'up'):
            for kc in act[1:]:
                kc = int(kc)
                if kc > 0xFF:
                    lo, hi = _encode_ext_keycode(kc)
                    out += bytes([SS_QMK_PREFIX, ext_codes[tag], lo, hi])
                else:
                    out += bytes([SS_QMK_PREFIX, basic_codes[tag], kc])
    return bytes(out)


# ============================================================================
# Emitters
# ============================================================================

def emit_vil(conv: Converter) -> dict:
    """Build the .vil JSON structure (vial-gui "Load saved layout" format)."""
    cfg = conv.config
    layer_count = int(cfg.get('layer_count', 8))

    macro_list = [[] for _ in range(16)]
    for m in conv.macros:
        macro_list[m.index] = [list(a) for a in m.actions]

    td_list = [[KC_NO, KC_NO, KC_NO, KC_NO, 200] for _ in range(32)]
    for td in conv.tap_dances:
        td_list[td.index] = [td.on_tap, td.on_hold, td.on_double_tap,
                             td.on_tap_hold, td.tapping_term]

    combo_list = [[KC_NO] * 5 for _ in range(32)]

    ko_list = []
    for ko in conv.key_overrides:
        ko_list.append({
            'trigger': ko.trigger,
            'replacement': ko.replacement,
            'layers': ko.layers if ko.layers else ALL_LAYERS_MASK,
            'trigger_mods': ko.trigger_mods,
            'negative_mod_mask': ko.negative_mod_mask,
            'suppressed_mods': ko.suppressed_mods,
            'options': ko.options,
        })
    while len(ko_list) < 32:
        ko_list.append({
            'trigger': KC_NO, 'replacement': KC_NO, 'layers': ALL_LAYERS_MASK,
            'trigger_mods': 0, 'negative_mod_mask': 0, 'suppressed_mods': 0,
            'options': 0,
        })

    return {
        'version': 1,
        'uid': vial_uid_to_int(cfg.get('vial_uid')),
        'layout': conv.matrix,
        'encoder_layout': [[] for _ in range(layer_count)],
        'layout_options': cfg.get('layout_options', -1),
        'macro': macro_list,
        'vial_protocol': 6,
        'via_protocol': 9,
        'tap_dance': td_list,
        'combo': combo_list,
        'key_override': ko_list,
        'settings': {str(k): v for k, v in (cfg.get('settings') or {}).items()},
    }


def emit_inc(conv: Converter, source_name: str) -> str:
    """Generate the C include applying the conversion as EEPROM defaults."""
    cfg = conv.config
    lines: list[str] = []
    w = lines.append

    w('/*')
    w(' * Generated by zmk_to_vial.py — DO NOT EDIT BY HAND.')
    w(f' * Source ZMK keymap : {source_name}')
    w(' *')
    w(' * EEPROM defaults reproducing the ZMK keymap on the Keyboard Quantizer')
    w(' * Mini.  Applied from eeconfig_init_user() when the EEPROM is')
    w(' * (re)initialised, after dynamic_keymap_reset() has seeded the identity')
    w(' * pass-through keymap; only the cells that differ are overwritten here.')
    w(' *')
    w(' * Include this file at the end of keymaps/vial/keymap.c:')
    w(' *     #include "zmk_keymap_defaults.inc"')
    w(' */')
    w('')
    w('#include "dynamic_keymap.h"')
    w('#include "eeconfig.h"')
    w('#include "vial.h"')
    w('#ifdef QMK_SETTINGS')
    w('#    include "qmk_settings.h"')
    w('#endif')
    w('')

    # --- keymap entries (diff against the default state) ---
    passthrough = cfg.get('unmapped_keys', 'passthrough') == 'passthrough'
    identity = identity_keymap() if passthrough else \
        [[KC_NO] * MATRIX_COLS for _ in range(MATRIX_ROWS)]

    # Collect keymap cells that differ from the post-reset default
    keymap_entries: list[tuple[int, int, int, int]] = []
    for layer, rows in enumerate(conv.matrix):
        for r, row in enumerate(rows):
            for c, kc in enumerate(row):
                default = identity[r][c] if layer == 0 else KC_TRNS
                if kc != default:
                    keymap_entries.append((layer, r, c, kc))

    if keymap_entries:
        w('typedef struct {')
        w('    uint8_t  layer;')
        w('    uint8_t  row;')
        w('    uint8_t  col;')
        w('    uint16_t keycode;')
        w('} zmk_keymap_entry_t;')
        w('')
        w(f'/* {len(keymap_entries)} cells differ from the pass-through default */')
        w('static const zmk_keymap_entry_t zmk_keymap_entries[] = {')
        for layer, r, c, kc in keymap_entries:
            w(f'    {{{layer}, {r}, {c}, {keycode_c_expr(kc)}}},')
        w('};')
        w('')

    # --- tap dances ---
    if conv.tap_dances:
        w('static const vial_tap_dance_entry_t zmk_tap_dance_entries[] = {')
        for td in sorted(conv.tap_dances, key=lambda t: t.index):
            w(f'    /* TD({td.index}) {td.zmk_name} */')
            w(f'    {{{keycode_c_expr(td.on_tap)}, {keycode_c_expr(td.on_hold)}, '
              f'{keycode_c_expr(td.on_double_tap)}, {keycode_c_expr(td.on_tap_hold)}, '
              f'{td.tapping_term}}},')
        w('};')
        w('')

    # --- key overrides ---
    if conv.key_overrides:
        w('static const vial_key_override_entry_t zmk_key_override_entries[] = {')
        for ko in conv.key_overrides:
            w(f'    /* {ko.description} */')
            w('    {')
            w(f'        .trigger           = {keycode_c_expr(ko.trigger)},')
            w(f'        .replacement       = {keycode_c_expr(ko.replacement)},')
            w(f'        .layers            = 0x{(ko.layers or ALL_LAYERS_MASK):04X},')
            w(f'        .trigger_mods      = 0x{ko.trigger_mods:02X},')
            w(f'        .negative_mod_mask = 0x{ko.negative_mod_mask:02X},')
            w(f'        .suppressed_mods   = 0x{ko.suppressed_mods:02X},')
            w(f'        .options           = 0x{ko.options:02X},')
            w('    },')
        w('};')
        w('')

    # --- macros ---
    buf = bytearray()
    for i in range(16):
        macro = next((m for m in conv.macros if m.index == i), None)
        if macro is not None:
            buf += macro_bytes(macro.actions)
        buf.append(0)
    macro_comment = ', '.join(f'M{m.index}={m.zmk_name}' for m in conv.macros)
    w(f'/* Macro buffer: {macro_comment} */')
    w('static const uint8_t zmk_macro_buffer[] = {')
    for i in range(0, len(buf), 12):
        chunk = ', '.join(f'0x{b:02X}' for b in buf[i:i + 12])
        w(f'    {chunk},')
    w('};')
    w('')

    # --- application function ---
    w('void eeconfig_init_user(void) {')
    w('    /* Overriding eeconfig_init_user() suppresses QMK\'s weak default, so')
    w('     * reproduce it: reset the user EEPROM area to blank. */')
    w('#if (EECONFIG_USER_DATA_SIZE) == 0')
    w('    eeconfig_update_user(0);')
    w('#endif')
    w('')
    if keymap_entries:
        w('    /* Keymap cells that differ from the pass-through default */')
        w('    for (size_t i = 0; i < ARRAY_SIZE(zmk_keymap_entries); i++) {')
        w('        dynamic_keymap_set_keycode(zmk_keymap_entries[i].layer,')
        w('                                   zmk_keymap_entries[i].row,')
        w('                                   zmk_keymap_entries[i].col,')
        w('                                   zmk_keymap_entries[i].keycode);')
        w('    }')
        w('')
    if conv.tap_dances:
        w('    /* Tap dances */')
        w('    for (size_t i = 0; i < ARRAY_SIZE(zmk_tap_dance_entries); i++) {')
        w('        dynamic_keymap_set_tap_dance(i, &zmk_tap_dance_entries[i]);')
        w('    }')
        w('')
    if conv.key_overrides:
        w('    /* Key overrides */')
        w('    for (size_t i = 0; i < ARRAY_SIZE(zmk_key_override_entries); i++) {')
        w('        dynamic_keymap_set_key_override(i, &zmk_key_override_entries[i]);')
        w('    }')
        w('')
    w('    /* Macros */')
    w('    dynamic_keymap_macro_set_buffer(0, sizeof(zmk_macro_buffer),')
    w('                                    (uint8_t *)zmk_macro_buffer);')
    w('')
    settings = cfg.get('settings') or {}
    if settings:
        w('#ifdef QMK_SETTINGS')
        w('    /* QMK settings (tapping term etc.) */')
        w('    {')
        for qsid, value in sorted(settings.items(), key=lambda kv: int(kv[0])):
            qsid_i = int(qsid)
            if qsid_i == 7:
                w(f'        uint16_t tapping_term = {int(value)};')
                w('        qmk_settings_set(7, &tapping_term, sizeof(tapping_term));')
            else:
                w(f'        uint8_t qs_{qsid_i} = {int(value)};')
                w(f'        qmk_settings_set({qsid_i}, &qs_{qsid_i}, sizeof(qs_{qsid_i}));')
        w('    }')
        w('#endif')
        w('')
    w('    /* Reload Vial runtime state from the EEPROM we just wrote */')
    w('    vial_init();')
    w('}')
    w('')
    return '\n'.join(lines)


def emit_report(conv: Converter, source_name: str) -> str:
    """Human-readable Markdown conversion report (Japanese)."""
    lines: list[str] = []
    w = lines.append

    w(f'# ZMK → Vial 変換レポート')
    w('')
    w(f'- 変換元: `{source_name}`')
    w(f'- 変換先: {conv.config.get("keyboard", "(不明)")}')
    w('')

    w('## レイヤー対応')
    w('')
    w('| ZMK レイヤー | 別名 | Vial レイヤー |')
    w('|---|---|---|')
    for zmk_idx, name in enumerate(conv.zmk_layer_names):
        alias = conv._layer_alias(zmk_idx)
        if zmk_idx in conv.layer_map:
            w(f'| {name} | {alias} | {conv.layer_map[zmk_idx]} |')
        else:
            w(f'| {name} | {alias} | (除外) |')
    w('')

    w('## キー配置 (BASE レイヤーのアイデンティティ → Quantizer 行列位置)')
    w('')
    w('| ZMK位置 | アイデンティティ | 行列 (row, col) |')
    w('|---|---|---|')
    for pos in sorted(conv.position_identity):
        ident = conv.position_identity[pos]
        mat = conv.position_matrix.get(pos)
        mat_str = f'({mat[0]}, {mat[1]})' if mat else '—'
        w(f'| {pos} | `{ident}` | {mat_str} |')
    w('')

    if conv.macros:
        w('## マクロ')
        w('')
        w('| Vial | ZMK マクロ | アクション |')
        w('|---|---|---|')
        for m in sorted(conv.macros, key=lambda x: x.index):
            acts = []
            for a in m.actions:
                if a[0] == 'delay':
                    acts.append(f'delay {a[1]}ms')
                else:
                    keys = ', '.join(keycode_c_expr(int(kc)) for kc in a[1:])
                    acts.append(f'{a[0]} {keys}')
            w(f'| M{m.index} | {m.zmk_name} | {" → ".join(acts)} |')
        w('')

    if conv.tap_dances:
        w('## タップダンス')
        w('')
        w('| Vial | ZMK | 1打 | 2打 | term |')
        w('|---|---|---|---|---|')
        for td in sorted(conv.tap_dances, key=lambda x: x.index):
            w(f'| TD({td.index}) | {td.zmk_name} | {keycode_c_expr(td.on_tap)} | '
              f'{keycode_c_expr(td.on_double_tap)} | {td.tapping_term}ms |')
        w('')

    if conv.key_overrides:
        w('## キーオーバーライド (モッドモーフ変換)')
        w('')
        w('| # | 元モーフ | トリガー | 条件 | 置換 | レイヤー |')
        w('|---|---|---|---|---|---|')
        for i, ko in enumerate(conv.key_overrides):
            layers = ', '.join(str(b) for b in range(16) if ko.layers & (1 << b)) or '全レイヤー'
            cond = []
            if ko.trigger_mods:
                cond.append(f'{mod_mask_label(ko.trigger_mods)} 押下時')
            if ko.negative_mod_mask:
                cond.append(f'{mod_mask_label(ko.negative_mod_mask)} 非押下時')
            w(f'| {i} | {ko.zmk_name} | {keycode_c_expr(ko.trigger)} | '
              f'{" かつ ".join(cond) or "常時"} | {keycode_c_expr(ko.replacement)} | {layers} |')
        w('')

    if conv.carriers:
        w('## キャリアキーコード割当')
        w('')
        w('モッドモーフの無修飾側がトリガーにできないキーコード (&none / 修飾付き / マクロ) の場合、')
        w('カスタムキーコード (QK_KB_n) を「キャリア」としてキー位置に配置しています。')
        w('')
        w('| キーコード | 元モーフ | 理由 |')
        w('|---|---|---|')
        for c in conv.carriers:
            w(f'| {keycode_c_expr(c.keycode)} | {c.zmk_name} | {c.reason} |')
        w('')
        w('### vial.json customKeycodes 追記用スニペット')
        w('')
        w('Vial GUI 上でキャリアに名前を表示するには、vial.json の `customKeycodes` 配列に')
        w('以下を追記してください (既存の3エントリの後ろに):')
        w('')
        w('```json')
        snippet = []
        for c in conv.carriers:
            kb_index = c.keycode - QK_KB
            snippet.append({
                'name': c.zmk_name.upper(),
                'title': f'ZMK mod-morph carrier: {c.zmk_name}',
                'shortName': f'ZMK\n{kb_index}',
            })
        w(json.dumps(snippet, indent=2, ensure_ascii=False))
        w('```')
        w('')

    w('## リソース使用量')
    w('')
    w('| リソース | 使用 | 上限 |')
    w('|---|---|---|')
    w(f'| レイヤー | {len(conv.kept_layers)} | {conv.config.get("layer_count", 8)} |')
    w(f'| マクロ | {len(conv.macros)} | 16 |')
    w(f'| タップダンス | {len(conv.tap_dances)} | 32 |')
    w(f'| キーオーバーライド | {len(conv.key_overrides)} | 32 |')
    w(f'| カスタムキーコード | {len(conv.carriers)} (QK_KB_{conv.config.get("carrier_start", 3)}〜) | 32 |')
    w('')

    if conv.warnings:
        w('## ⚠️ 警告')
        w('')
        for warning in conv.warnings:
            w(f'- {warning}')
        w('')

    w('## 使い方')
    w('')
    w('1. **Vial GUI**: `File → Load saved layout...` で生成された `.vil` を読み込む')
    w('   (Quantizer Mini を接続した状態で)。レイヤー・マクロ・タップダンス・キーオーバーライドが書き込まれます。')
    w('2. **ファームウェア組込 (任意)**: 生成された `.inc` を vial-qmk の')
    w('   `keyboards/sekigon/keyboard_quantizer/mini/keymaps/vial/` に配置し、keymap.c の末尾で')
    w('   `#include` してビルドすると、EEPROM リセット時のデフォルトとして同じ内容が適用されます。')
    w('')
    return '\n'.join(lines)


# ============================================================================
# CLI
# ============================================================================

def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Convert a ZMK .keymap to a Vial keymap (Keyboard Quantizer Mini)')
    parser.add_argument('keymap', help='input ZMK .keymap file')
    parser.add_argument('-m', '--mapping', help='mapping config JSON '
                        '(identity-less key assignment, excluded layers, ...)')
    parser.add_argument('--vil', help='output .vil path (default: <keymap>.vil)')
    parser.add_argument('--inc', help='output C include path '
                        '(default: <keymap>_vial_defaults.inc)')
    parser.add_argument('--report', help='output Markdown report path '
                        '(default: <keymap>_vial_report.md)')
    parser.add_argument('--exclude-layers', help='comma separated layer names/indices '
                        'to exclude (overrides mapping config)')
    parser.add_argument('--strict', action='store_true',
                        help='treat warnings as errors (exit code 1)')
    args = parser.parse_args(argv)

    keymap_path = Path(args.keymap)
    config = load_config(Path(args.mapping) if args.mapping else None)
    if args.exclude_layers:
        config['exclude_layers'] = [s.strip() for s in args.exclude_layers.split(',')]

    stem = keymap_path.stem
    vil_path = Path(args.vil) if args.vil else keymap_path.with_name(f'{stem}.vil')
    inc_path = Path(args.inc) if args.inc else keymap_path.with_name(f'{stem}_vial_defaults.inc')
    report_path = Path(args.report) if args.report else keymap_path.with_name(f'{stem}_vial_report.md')

    try:
        conv = Converter(keymap_path, config).convert()
    except ConvertError as e:
        print(f'エラー: {e}', file=sys.stderr)
        return 1

    vil = emit_vil(conv)
    vil_path.write_text(json.dumps(vil, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'書き込み: {vil_path}')

    inc = emit_inc(conv, keymap_path.name)
    inc_path.write_text(inc, encoding='utf-8')
    print(f'書き込み: {inc_path}')

    report = emit_report(conv, keymap_path.name)
    report_path.write_text(report, encoding='utf-8')
    print(f'書き込み: {report_path}')

    if conv.warnings:
        print(f'\n警告 ({len(conv.warnings)}件):', file=sys.stderr)
        for warning in conv.warnings:
            print(f'  - {warning}', file=sys.stderr)
        if args.strict:
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
