#!/usr/bin/env python3
"""Generate a physical-layout KEYMAP-vial.html from a QMK/Vial keymap.

This reuses keymap_docgen.py's figure renderer (so the output looks/behaves like
the ZMK KEYMAP.html: every layer drawn on the real physical layout, tap on the
key cap, hold below, full operation list in the hover tooltip), but drives it
with a QMK/Vial keycode resolver instead of the ZMK one.

Two keymap input modes (auto-detected from the file extension, override with
--format):
  * QMK keymap.c   `[n] = LAYOUT_xxx(KC_Q, LT(1,KC_SPC), ...)`   --format keymap_c
  * Vial .vil      integer `layout[layer][row][col]`             --format vil

Two physical-layout sources (auto-detected from the JSON shape, see
choose_layout):
  * QMK info.json / keyboard.json  `layouts.<name>.layout` (ordered x/y) — pairs
    1:1 with the keymap.c LAYOUT argument order.
  * Vial vial.json / VIA via.json  `layouts.keymap` (KLE format, matrix-indexed)
    — each key declares its (row,col), used to index the .vil layout.
  * A keymap_docgen physical-layout JSON (`layouts.default_layout`) also works.

Both layout adapters return keymap_docgen's (coords, labels, geom, unit, rowcol)
tuple, so build_grid() and write_html() are reused unchanged.
"""

import argparse
import json
import re
import sys
from pathlib import Path

import keymap_docgen as kd
import zmk_to_vial as zv


# ============================================================================
# QMK keycode -> display label
# ============================================================================

# Transparent / no-op tokens (mapped to the renderer's universal sentinels so
# its .key.trans / .key.none styling and ▽ / empty cells light up unchanged).
TRANS_TOKENS = {'KC_TRNS', 'KC_TRANSPARENT', '_______', '___', '&trans', '▽'}
NONE_TOKENS = {'KC_NO', 'XXXXXXX', '&none'}

# Stripped (no "KC_") basic keycode name -> compact display label. Both the
# short spellings used in hand-written keymap.c (KC_ENT, KC_MINS, KC_LCTL, …)
# and the long spellings emitted when decoding a .vil (KC_ENTER, KC_MINUS,
# KC_LEFT_CTRL, …) are covered.
QMK_BASIC_LABELS = {
    # whitespace / editing / control
    'SPC': 'Space', 'SPACE': 'Space', 'ENT': 'Enter', 'ENTER': 'Enter',
    'BSPC': 'BS', 'BSPACE': 'BS', 'BACKSPACE': 'BS',
    'TAB': 'Tab', 'ESC': 'Esc', 'ESCAPE': 'Esc',
    'DEL': 'Del', 'DELETE': 'Del', 'INS': 'Ins', 'INSERT': 'Ins',
    'CAPS': 'Caps', 'CAPS_LOCK': 'Caps', 'CAPSLOCK': 'Caps', 'APP': 'Menu',
    'APPLICATION': 'Menu', 'PSCR': 'PrtSc', 'PRINT_SCREEN': 'PrtSc',
    # arrows / navigation
    'LEFT': '←', 'RGHT': '→', 'RIGHT': '→', 'UP': '↑', 'DOWN': '↓',
    'PGUP': 'PgUp', 'PAGE_UP': 'PgUp', 'PGDN': 'PgDn', 'PAGE_DOWN': 'PgDn',
    'HOME': 'Home', 'END': 'End',
    # punctuation
    'MINS': '-', 'MINUS': '-', 'EQL': '=', 'EQUAL': '=',
    'LBRC': '[', 'LEFT_BRACKET': '[', 'RBRC': ']', 'RIGHT_BRACKET': ']',
    'BSLS': '\\', 'BACKSLASH': '\\', 'SCLN': ';', 'SEMICOLON': ';',
    'QUOT': "'", 'QUOTE': "'", 'GRV': '`', 'GRAVE': '`',
    'COMM': ',', 'COMMA': ',', 'DOT': '.', 'SLSH': '/', 'SLASH': '/',
    # international (JIS)
    'NUHS': '#', 'NONUS_HASH': '#', 'NUBS': '\\', 'NONUS_BACKSLASH': '\\',
    'INT1': 'ろ', 'INTERNATIONAL_1': 'ろ', 'INT3': '¥', 'INTERNATIONAL_3': '¥',
    'LNG1': 'かな', 'LANGUAGE_1': 'かな', 'LNG2': '英数', 'LANGUAGE_2': '英数',
    # shifted-symbol aliases (KC_EXLM == S(KC_1), etc.)
    'EXLM': '!', 'AT': '@', 'HASH': '#', 'DLR': '$', 'PERC': '%', 'CIRC': '^',
    'AMPR': '&', 'ASTR': '*', 'LPRN': '(', 'RPRN': ')', 'UNDS': '_',
    'PLUS': '+', 'LCBR': '{', 'RCBR': '}', 'PIPE': '|', 'COLN': ':',
    'DQUO': '"', 'TILD': '~', 'LABK': '<', 'RABK': '>', 'QUES': '?',
    # modifiers (as plain keys)
    'LCTL': 'LCtrl', 'LEFT_CTRL': 'LCtrl', 'LEFT_CONTROL': 'LCtrl',
    'RCTL': 'RCtrl', 'RIGHT_CTRL': 'RCtrl', 'RIGHT_CONTROL': 'RCtrl',
    'LSFT': 'LShift', 'LEFT_SHIFT': 'LShift', 'RSFT': 'RShift', 'RIGHT_SHIFT': 'RShift',
    'LALT': 'LAlt', 'LEFT_ALT': 'LAlt', 'RALT': 'RAlt', 'RIGHT_ALT': 'RAlt',
    'LGUI': 'LWin', 'LEFT_GUI': 'LWin', 'RGUI': 'RWin', 'RIGHT_GUI': 'RWin',
    # mouse buttons
    'BTN1': '🖱左', 'MS_BTN1': '🖱左', 'BTN2': '🖱右', 'MS_BTN2': '🖱右',
    'BTN3': '🖱中', 'MS_BTN3': '🖱中', 'BTN4': '🖱4', 'BTN5': '🖱5',
}

# Mod-tap modifier constant -> label (for MT(MOD_x | MOD_y, kc)).
MOD_TAP_LABELS = {
    'MOD_LCTL': 'LCtrl', 'MOD_RCTL': 'RCtrl', 'MOD_LSFT': 'LShift',
    'MOD_RSFT': 'RShift', 'MOD_LALT': 'LAlt', 'MOD_RALT': 'RAlt',
    'MOD_LGUI': 'LWin', 'MOD_RGUI': 'RWin', 'MOD_HYPR': 'Hyper', 'MOD_MEH': 'Meh',
}

# Short mod-tap prefix (XXX in XXX_T(kc)) -> hold label.
MOD_TAP_PREFIX = {
    'LCTL': 'LCtrl', 'RCTL': 'RCtrl', 'LSFT': 'LShift', 'RSFT': 'RShift',
    'LALT': 'LAlt', 'RALT': 'RAlt', 'LGUI': 'LWin', 'RGUI': 'RWin',
    'C': 'LCtrl', 'S': 'LShift', 'A': 'LAlt', 'G': 'LWin',
    'MEH': 'Meh', 'HYPR': 'Hyper', 'ALL': 'Hyper', 'LCA': '⌃⌥',
    'LSG': '⇧⌘', 'LCAG': '⌃⌥⌘', 'C_S': '⌃⇧', 'SGUI': '⇧⌘', 'SCMD': '⇧⌘',
}

# Mod-wrapper function (XXX in XXX(kc)) -> glyph prefix.
MOD_WRAP_GLYPH = {
    'S': '⇧', 'LSFT': '⇧', 'RSFT': '⇧',
    'C': '⌃', 'LCTL': '⌃', 'RCTL': '⌃',
    'A': '⌥', 'LALT': '⌥', 'RALT': '⌥',
    'G': '⌘', 'LGUI': '⌘', 'RGUI': '⌘',
    'C_S': '⌃⇧', 'SGUI': '⇧⌘', 'SCMD': '⇧⌘',
    'LCA': '⌃⌥', 'LSA': '⇧⌥', 'LCG': '⌃⌘', 'LAG': '⌥⌘', 'LCAG': '⌃⌥⌘',
    'MEH': '⌃⇧⌥', 'HYPR': '⌃⇧⌥⌘', 'ALL': '⌃⇧⌥⌘',
}


def qmk_basic_label(name: str) -> str:
    """'KC_Q' -> 'Q', 'KC_SPC'/'KC_SPACE' -> 'Space', 'KC_F1' -> 'F1'."""
    n = name.strip()
    if n.startswith('KC_'):
        n = n[3:]
    return QMK_BASIC_LABELS.get(n, n)


def mod_mask_label(mods: str) -> str:
    """'MOD_LCTL | MOD_LSFT' -> 'LCtrl+LShift'."""
    parts = [MOD_TAP_LABELS.get(p.strip(), p.strip().replace('MOD_', ''))
             for p in mods.split('|') if p.strip()]
    return '+'.join(parts) if parts else 'Mod'


def make_qmk_resolver(layer_names=None, custom_labels=None):
    """Build a keymap_docgen resolver (binding, behaviors, macros, op) ->
    (action, path) for QMK/Vial keycode expressions.

    Plain keycodes return the SAME value for every non-hold operation so the
    renderer finds nothing distinct and emits no spurious Tap Dance / Mod Morph
    extra figure (unlike ZMK's &kp, which deliberately returns ⇧X / X×2). Only
    mod-tap / layer-tap produce a distinct hold face."""
    layer_names = layer_names or {}
    custom_labels = custom_labels or {}

    def layer_label(n):
        try:
            return layer_names.get(int(n), f'L{n}')
        except (TypeError, ValueError):
            return f'L{n}'

    def split_fn(b):
        """'FN(inner)' -> ('FN', 'inner') with balanced parens, else None."""
        m = re.match(r'([A-Za-z_]\w*)\((.*)\)$', b)
        if not m:
            return None
        fn, inner = m.group(1), m.group(2)
        # Verify the captured inner is balanced (re is greedy: guards nested fns).
        depth = 0
        for ch in inner:
            depth += (ch == '(') - (ch == ')')
            if depth < 0:
                return None
        return (fn, inner) if depth == 0 else None

    def faces(b, depth=0):
        """Return (tap_label, hold_label) for a QMK keycode expression."""
        b = b.strip()
        if depth > 8:
            return (b, '')
        parsed = split_fn(b)
        if parsed:
            fn, inner = parsed
            args = [a.strip() for a in split_top_level(inner)]
            # mod-tap: MT(mods, kc)
            if fn in ('MT', 'MOD_T') and len(args) == 2:
                return (faces(args[1], depth + 1)[0], mod_mask_label(args[0]))
            # short mod-tap: LSFT_T(kc), C_S_T(kc), …
            if fn.endswith('_T') and len(args) == 1:
                pref = fn[:-2]
                return (faces(args[0], depth + 1)[0],
                        MOD_TAP_PREFIX.get(pref, pref))
            # layer-tap: LT(layer, kc)  /  LTn(kc)
            if fn == 'LT' and len(args) == 2:
                return (faces(args[1], depth + 1)[0], layer_label(args[0]))
            m = re.fullmatch(r'LT(\d+)', fn)
            if m and len(args) == 1:
                return (faces(args[0], depth + 1)[0], layer_label(m.group(1)))
            # layer-mod: LM(layer, mod)
            if fn == 'LM' and len(args) == 2:
                return (f'{layer_label(args[0])}+{mod_mask_label(args[1])}', '')
            # layer switches that take an argument
            if fn in ('MO', 'TG', 'TT', 'DF', 'OSL', 'PDF') and len(args) == 1:
                pre = {'MO': '', 'TG': '⇄', 'TT': '⇄', 'DF': 'DF:',
                       'OSL': 'OSL:', 'PDF': 'DF:'}[fn]
                return (f'{pre}{layer_label(args[0])}', '')
            if fn == 'TO' and len(args) == 1:
                return (f'⇒{layer_label(args[0])}', '')
            if fn == 'TD' and len(args) == 1:
                return (f'TD{args[0]}', '')
            # mod-wrapper: S()/C()/LCTL()/C_S()/…  (recurse into inner)
            if fn in MOD_WRAP_GLYPH and len(args) == 1:
                return (f'{MOD_WRAP_GLYPH[fn]}{faces(args[0], depth + 1)[0]}', '')
            # unknown function: show compactly
            return (f'{fn}({", ".join(args)})', '')

        # Bare tokens
        if b in custom_labels:
            return (custom_labels[b], '')
        m = re.fullmatch(r'QK_MACRO_(\d+)', b) or re.fullmatch(r'MACRO_(\d+)', b)
        if m:
            return (f'M{m.group(1)}', '')
        m = re.fullmatch(r'M(\d+)', b)
        if m:
            return (f'M{m.group(1)}', '')
        if b in ('QK_BOOT', 'RESET'):
            return ('BOOT', '')
        if b in ('QK_RBT', 'QK_REBOOT'):
            return ('REBOOT', '')
        if b.startswith('KC_') or re.fullmatch(r'[A-Z0-9]', b):
            return (qmk_basic_label(b), '')
        # Custom / unknown enum keycode: show its name (never crash — R2).
        return (b, '')

    def resolve(binding, behaviors, macros, op):
        b = binding.strip()
        if b in TRANS_TOKENS:
            return kd.resolve('&trans', {}, {}, op)
        if b in NONE_TOKENS:
            return kd.resolve('&none', {}, {}, op)
        tap, hold = faces(b)
        return (hold if op == 'ホールド' else tap, b)

    return resolve


# ============================================================================
# Tokenizer + QMK keymap.c parser  (Target A)
# ============================================================================

def split_top_level(s: str, sep: str = ',') -> list[str]:
    """Split on `sep` at paren/bracket depth 0 (keeps LT(1,KC_SPC) whole)."""
    out, depth, cur = [], 0, []
    for ch in s:
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        if ch == sep and depth == 0:
            out.append(''.join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append(''.join(cur))
    return out


def _extract_paren_body(text: str, open_idx: int) -> tuple[str, int]:
    """Given the index of a '(', return (body, index_after_matching_close)."""
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == '(':
            depth += 1
        elif text[i] == ')':
            depth -= 1
            if depth == 0:
                return text[open_idx + 1:i], i + 1
    raise ValueError('unbalanced parentheses in keymap.c LAYOUT(...)')


def parse_qmk_keymap_c(text: str) -> list[tuple[str, list[str]]]:
    """Parse `[n] = LAYOUT_xxx(tok, tok, ...)` blocks into
    [(layer_name, [token,...]), ...] in layer-index order. Transparent / no-op
    tokens are normalised to the '&trans' / '&none' sentinels."""
    text = kd.strip_comments(text)
    layers: dict[int, list[str]] = {}
    for m in re.finditer(r'\[(\d+)\]\s*=\s*[A-Za-z_]\w*\s*\(', text):
        idx = int(m.group(1))
        body, _ = _extract_paren_body(text, m.end() - 1)
        toks = [t.strip() for t in split_top_level(body)]
        toks = [t for t in toks if t]
        layers[idx] = [_norm_token(t) for t in toks]
    if not layers:
        raise ValueError('no `[n] = LAYOUT...(...)` layers found in keymap.c')
    return [(f'Layer {i}', layers[i]) for i in sorted(layers)]


def _norm_token(tok: str) -> str:
    if tok in TRANS_TOKENS:
        return '&trans'
    if tok in NONE_TOKENS:
        return '&none'
    return tok


def detect_layout_macro_name(text: str) -> str | None:
    """The LAYOUT macro name used by the keymap (`[0] = LAYOUT_xxx(...)`)."""
    m = re.search(r'\[\d+\]\s*=\s*([A-Za-z_]\w*)\s*\(', kd.strip_comments(text))
    return m.group(1) if m else None


def _extract_brace_body(text: str, open_idx: int) -> str:
    """Return the content between a '{' at open_idx and its matching '}'."""
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return text[open_idx + 1:i]
    raise ValueError('unbalanced braces in LAYOUT macro body')


def parse_layout_macro(h_text: str, macro_name: str) -> dict[int, tuple[int, int]]:
    """Parse a QMK `#define LAYOUT_xxx(args) { {cells}, ... }` macro into
    {arg_index: (matrix_row, matrix_col)} by matching each argument name to its
    cell position in the matrix body. Follows simple aliases
    (`#define LAYOUT_a LAYOUT_b`)."""
    text = kd.strip_comments(h_text)
    text = re.sub(r'\\[ \t]*\r?\n', ' ', text)  # join `\`-continued #define lines
    name = macro_name
    for _ in range(8):  # follow `#define ALIAS CONCRETE`
        m = re.search(r'#\s*define\s+' + re.escape(name) + r'\s+([A-Za-z_]\w*)\s*$',
                      text, re.M)
        if not m:
            break
        name = m.group(1)
    m = re.search(r'#\s*define\s+' + re.escape(name) + r'\s*\(', text)
    if not m:
        raise ValueError(f'LAYOUT macro {macro_name!r} not found in the .h file')
    args_body, after = _extract_paren_body(text, m.end() - 1)
    args = [a.strip() for a in split_top_level(args_body) if a.strip()]
    arg_index = {a: i for i, a in enumerate(args)}
    body = _extract_brace_body(text, text.index('{', after))
    arg_to_matrix: dict[int, tuple[int, int]] = {}
    for r, rowm in enumerate(re.finditer(r'\{([^{}]*)\}', body)):
        for c, cell in enumerate(s.strip() for s in split_top_level(rowm.group(1))):
            if cell in arg_index:
                arg_to_matrix[arg_index[cell]] = (r, c)
    if not arg_to_matrix:
        raise ValueError(f'LAYOUT macro {name!r} had no mappable argument cells')
    return arg_to_matrix


# ============================================================================
# Vial .vil keymap  (Target B)
# ============================================================================

def qmk_int_to_token(value: int, custom_by_int: dict | None = None) -> str:
    """Decode a 16-bit Vial keycode int into the same expression grammar the
    resolver understands, reusing zmk_to_vial.keycode_c_expr. QK_KB_n carriers
    are mapped to the firmware's custom-keycode name when known."""
    custom_by_int = custom_by_int or {}
    if value in custom_by_int:
        return custom_by_int[value]
    expr = zv.keycode_c_expr(value)
    if expr in ('KC_TRNS',):
        return '&trans'
    if expr in ('KC_NO',):
        return '&none'
    return expr


def build_vil_layer_bindings(vil_layout, layer: int, matrix: list[tuple[int, int]],
                             custom_by_int: dict | None = None) -> list[str]:
    """Bindings for one .vil layer in figure order (matrix[i] -> layout[layer][r][c])."""
    rows = vil_layout[layer]
    out = []
    for (r, c) in matrix:
        try:
            out.append(qmk_int_to_token(rows[r][c], custom_by_int))
        except (IndexError, TypeError):
            out.append('&none')
    return out


# ============================================================================
# Physical-layout adapters  -> keymap_docgen (coords, labels, geom, unit, rowcol)
# ============================================================================

def adapt_qmk_info_layout(info: dict, layout_name: str | None = None):
    """QMK info.json / keyboard.json `layouts.<name>.layout` (ordered {x,y[,w,h,
    label]}) -> keymap_docgen layout 5-tuple. Order matches keymap.c LAYOUT args."""
    layouts = info.get('layouts') or {}
    if not layouts:
        raise ValueError('info.json has no "layouts"')
    if layout_name and layout_name in layouts:
        chosen = layouts[layout_name]
    else:
        chosen = layouts.get('LAYOUT') or next(iter(layouts.values()))
    entries = chosen.get('layout') or []
    out_entries = []
    for e in entries:
        ent = {'x': e['x'], 'y': e['y']}
        for k in ('w', 'h', 'r', 'rx', 'ry'):
            if k in e:
                ent[k] = e[k]
        if e.get('label') is not None:
            ent['label'] = e['label']
        out_entries.append(ent)
    # QMK info.json x/y are already in key units; pin unit so fractional
    # column-stagger offsets aren't mistaken for the key unit.
    return kd.parse_physical_layout(
        {'layouts': {'default_layout': {'layout': out_entries}}, 'unit': 1.0})


def parse_kle(keymap_rows: list, choice: int):
    """Walk a VIA/Vial KLE `layouts.keymap` (the kle-serial cursor model:
    x/y/w/h offsets and r/rx/ry rotation). Keep keys whose layout-option tag is
    absent or equals `0,<choice>`. Returns a list of
    {'x','y','w','h','r','rx','ry','matrix':(row,col)} dicts."""
    keys = []
    cur = {'x': 0.0, 'y': 0.0, 'w': 1.0, 'h': 1.0, 'r': 0.0, 'rx': 0.0, 'ry': 0.0}
    for row in keymap_rows:
        for item in row:
            if isinstance(item, dict):
                if 'r' in item:
                    cur['r'] = float(item['r'])
                if 'rx' in item:
                    cur['rx'] = float(item['rx']); cur['x'] = cur['rx']; cur['y'] = cur['ry']
                if 'ry' in item:
                    cur['ry'] = float(item['ry']); cur['x'] = cur['rx']; cur['y'] = cur['ry']
                if 'x' in item:
                    cur['x'] += float(item['x'])
                if 'y' in item:
                    cur['y'] += float(item['y'])
                if 'w' in item:
                    cur['w'] = float(item['w'])
                if 'h' in item:
                    cur['h'] = float(item['h'])
                continue
            # key string: "row,col\n...\nG,O"
            lines = str(item).split('\n')
            mm = re.match(r'\s*(\d+)\s*,\s*(\d+)\s*$', lines[0])
            tag = lines[3].strip() if len(lines) > 3 else ''
            keep = True
            if tag:
                t = re.match(r'(\d+)\s*,\s*(\d+)$', tag)
                if t:
                    keep = int(t.group(2)) == choice
            if mm and keep:
                keys.append({'x': cur['x'], 'y': cur['y'], 'w': cur['w'], 'h': cur['h'],
                             'r': cur['r'], 'rx': cur['rx'], 'ry': cur['ry'],
                             'matrix': (int(mm.group(1)), int(mm.group(2)))})
            cur['x'] += cur['w']
            cur['w'] = 1.0
            cur['h'] = 1.0
        cur['y'] += 1.0
        cur['x'] = cur['rx']
    return keys


def adapt_vial_kle_layout(vial: dict, choice: int):
    """Vial vial.json / VIA via.json `layouts.keymap` -> (layout 5-tuple,
    matrix list) where matrix[i] is the (row,col) of figure key i (to index a
    keymap source). Coordinates are normalised so the figure starts at the
    origin, and unit is pinned to 1.0 (KLE keys are 1u; fractional offsets must
    not be mistaken for the key unit)."""
    keymap_rows = (vial.get('layouts') or {}).get('keymap')
    if not keymap_rows:
        raise ValueError('vial.json has no "layouts.keymap"')
    keys = parse_kle(keymap_rows, choice)
    if not keys:
        raise ValueError(f'no keys for layout variant {choice} in vial.json')
    min_x = min(k['x'] for k in keys)
    min_y = min(k['y'] for k in keys)
    entries = []
    for k in keys:
        e = {'x': k['x'] - min_x, 'y': k['y'] - min_y, 'w': k['w'], 'h': k['h']}
        if k['r']:
            e['r'] = k['r']
            e['rx'] = k['rx'] - min_x
            e['ry'] = k['ry'] - min_y
        entries.append(e)
    five = kd.parse_physical_layout(
        {'layouts': {'default_layout': {'layout': entries}}, 'unit': 1.0})
    matrix = [k['matrix'] for k in keys]
    return five, matrix


# ============================================================================
# CLI
# ============================================================================

def _load_json(path: Path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _build_custom_maps(vial: dict | None, custom_kc_path: Path | None):
    """Return (custom_labels {name->label}, custom_by_int {int->name}).
    Labels come from --custom-keycodes JSON and/or vial.json customKeycodes
    (shortName). custom_by_int maps QK_KB+n -> the n-th customKeycode name."""
    labels: dict[str, str] = {}
    by_int: dict[int, str] = {}
    if vial and isinstance(vial.get('customKeycodes'), list):
        for i, ck in enumerate(vial['customKeycodes']):
            name = ck.get('name')
            if not name:
                continue
            by_int[zv.QK_KB + i] = name
            short = (ck.get('shortName') or '').replace('\n', ' ').strip()
            labels.setdefault(name, short or ck.get('title') or name)
    if custom_kc_path:
        for k, v in _load_json(custom_kc_path).items():
            labels[k] = v
    return labels, by_int


def choose_layout(layout_json: dict, variant: int, want_matrix: bool):
    """Pick the right adapter from the layout JSON shape.
    Returns (five_tuple, matrix_or_None)."""
    layouts = layout_json.get('layouts') if isinstance(layout_json, dict) else None
    if isinstance(layouts, dict) and 'keymap' in layouts:          # VIA/Vial KLE
        five, matrix = adapt_vial_kle_layout(layout_json, variant)
        return five, matrix
    if isinstance(layouts, dict) and 'default_layout' in layouts:  # docgen layout
        return kd.parse_physical_layout(layout_json), None
    # QMK info.json / keyboard.json
    return adapt_qmk_info_layout(layout_json), None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description='Generate a physical-layout KEYMAP-vial.html from a QMK '
                    'keymap.c or a Vial .vil, reusing the ZMK docgen renderer.')
    p.add_argument('input', help='QMK keymap.c or Vial .vil file')
    p.add_argument('layers', nargs='*',
                   help='Optional layer indices to include (default: all).')
    p.add_argument('-o', '--output', default='KEYMAP.html',
                   help='Output HTML path (default: KEYMAP.html)')
    p.add_argument('-l', '--layout', required=True,
                   help='Physical-layout JSON: QMK info.json / Vial vial.json / '
                        'VIA via.json / docgen layout JSON.')
    p.add_argument('-f', '--format', choices=('keymap_c', 'vil'),
                   help='Input format (default: from extension; .c=keymap_c, else vil)')
    p.add_argument('--custom-keycodes',
                   help='JSON map of custom keycode name -> display label.')
    p.add_argument('--layout-variant', type=int, default=None,
                   help='KLE layout option for Vial/VIA layouts '
                        '(default: the .vil layout_options, else 0).')
    p.add_argument('--layout-name', help='Which info.json layouts.<name> to use.')
    p.add_argument('--layout-macro',
                   help='QMK .h file with the LAYOUT macro, required to map a '
                        'keymap.c onto a matrix-indexed (KLE) vial.json/via.json '
                        'layout (e.g. the real staggered/trackball layout).')
    p.add_argument('--layout-macro-name',
                   help='LAYOUT macro name in --layout-macro (default: the macro '
                        'used by the keymap.c).')
    p.add_argument('--with-path', action='store_true',
                   help='Also emit the 経路 (resolution-path) section. Off by '
                        'default for QMK/Vial output (the raw keycode is in the '
                        'tooltip) to keep the page compact.')
    p.add_argument('--title', help='HTML page title / heading.')
    args = p.parse_args(argv)

    in_path = Path(args.input)
    if not in_path.is_file():
        print(f'error: input not found: {in_path}', file=sys.stderr)
        return 1
    fmt = args.format or ('keymap_c' if in_path.suffix == '.c' else 'vil')
    layout_json = _load_json(Path(args.layout))

    vial_json = layout_json if (isinstance(layout_json, dict)
                                and 'customKeycodes' in layout_json) else None
    custom_labels, custom_by_int = _build_custom_maps(
        vial_json, Path(args.custom_keycodes) if args.custom_keycodes else None)

    if fmt == 'keymap_c':
        # keymap.c may carry Shift-JIS comments (non-UTF-8); they are stripped
        # before parsing, so decode tolerantly rather than failing.
        text = in_path.read_text(encoding='utf-8', errors='replace')
        layers_tokens = parse_qmk_keymap_c(text)
        five, matrix = choose_layout(layout_json, args.layout_variant or 0, False)
        if matrix is None:
            # Ordered layout (QMK info.json): keymap.c LAYOUT args pair 1:1.
            layers_data = layers_tokens
        else:
            # Matrix-indexed layout (KLE vial.json/via.json): map each LAYOUT arg
            # to its (row,col) via the LAYOUT macro, then place it where the
            # vial.json puts that matrix cell (real staggered/trackball layout).
            if not args.layout_macro:
                print('error: a matrix-indexed (KLE) layout with a keymap.c needs '
                      '--layout-macro <keyboard>.h to map LAYOUT args to the '
                      'matrix.', file=sys.stderr)
                return 1
            macro_name = args.layout_macro_name or detect_layout_macro_name(text)
            arg_to_matrix = parse_layout_macro(
                Path(args.layout_macro).read_text(encoding='utf-8', errors='replace'),
                macro_name)
            layers_data = []
            for name, toks in layers_tokens:
                cell = {arg_to_matrix[i]: t for i, t in enumerate(toks)
                        if i in arg_to_matrix}
                layers_data.append((name, [cell.get(rc, '&none') for rc in matrix]))
            print(f'keymap.c: {len(layers_tokens)} layers on {len(matrix)} matrix '
                  f'keys (variant {args.layout_variant or 0})')
    else:
        vil = _load_json(in_path)
        vil_layout = vil['layout']
        variant = (args.layout_variant if args.layout_variant is not None
                   else (vil.get('layout_options') if isinstance(vil.get('layout_options'), int)
                         and vil.get('layout_options') >= 0 else 0))
        five, matrix = choose_layout(layout_json, variant, True)
        if matrix is None:
            print('error: --format vil needs a matrix-indexed layout '
                  '(Vial vial.json / VIA via.json).', file=sys.stderr)
            return 1
        layers_data = []
        for layer in range(len(vil_layout)):
            bindings = build_vil_layer_bindings(vil_layout, layer, matrix, custom_by_int)
            if all(b in ('&trans', '&none') for b in bindings):
                continue  # skip fully empty layers
            layers_data.append((f'Layer {layer}', bindings))
        print(f'.vil: {len(vil_layout)} layers, variant {variant}, '
              f'{len(matrix)} keys, {len(layers_data)} non-empty layers')

    coords, labels, geom, unit, rowcol = five
    total = len(layers_data[0][1]) if layers_data else 0
    if not coords or len(coords) != total:
        print(f'error: layout key count ({len(coords) if coords else 0}) != '
              f'bindings ({total}).', file=sys.stderr)
        return 1

    # Optional layer subset by index.
    if args.layers:
        want = set(args.layers)
        layers_data = [(n, b) for i, (n, b) in enumerate(layers_data)
                       if str(i) in want or n in want]

    layer_names = {i: f'L{i}' for i in range(len(layers_data))}
    qmk_resolver = make_qmk_resolver(layer_names=layer_names, custom_labels=custom_labels)

    kd.KEY_LABELS.clear()
    kd.KEY_LABELS.update(labels)
    kd.LAYER_NAMES_BY_INDEX.clear()
    kd.LAYER_NAMES_BY_INDEX.update(layer_names)
    grid, display_cols = kd.build_grid(coords, rowcol)

    out_path = Path(args.output)
    kd.write_html(layers_data, {}, {}, out_path, grid, display_cols, geom, unit,
                  resolver=qmk_resolver, title=args.title or out_path.stem,
                  show_path=args.with_path)
    print(f'saved: {out_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
