# keymap-docgen

Generate human-readable key-assignment docs (Excel **.xlsx** + a self-contained
**.html**) from a ZMK `.keymap` file. The tool parses standard ZMK behaviours —
`&kp`, `&mt` (mod-tap), `&lt` (layer-tap), `&mo`/`&to` (layers), mod-morph,
tap-dance and macros — resolves them recursively, and lays every layer out to
match the board's real physical arrangement (including the split gap). The
`.html` renders each layer as a **visual layout figure** — keys positioned by
their real coordinates (so column stagger, the split gap and key rotation all
show), with the tap action on the cap and the hold action below it. Key-cap
text is kept compact without losing information: layer jumps show as `L<n>`
instead of the layer node name, and macro chains are stacked one step per line
with the font size shrinking automatically so the full content fits inside the
key box.

The `.html` is a self-contained **one-screen viewer** (see
[The HTML viewer](#the-html-viewer)): one layer at a time, scaled to fit the
window, switched with tabs or the keyboard, with the Tap Dance / Mod Morph
figures and the **経路 (resolution path) view** — each key cap showing its
behavior-resolution chain (`behavior[index] ▸ … ▸ final binding`) at double key
size — a button away, and a detail panel listing every operation of the key
under the mouse. Without JavaScript the same figures are simply stacked, layer
by layer. When no usable layout geometry is available, the 経路 falls back to
the legacy table form.

The script itself contains **no keyboard-specific data**. Everything particular
to a board — each key's physical position *and its display label* — lives in a
small per-keyboard layout JSON, so the same `keymap_docgen.py` can be vendored
(or used as a git submodule) across multiple keyboards unchanged.

## Requirements

- Python **3.10+** (uses `X | Y` type syntax).
- [`openpyxl`](https://pypi.org/project/openpyxl/) — optional, only for `.xlsx`
  output (`pip install openpyxl`). Without it the `.html` is still generated.

## Usage

```sh
python keymap_docgen.py <keymap_file> [<layer_name> ...] [-l layout.json] [-o output.xlsx]
```

- `<keymap_file>` — path to the `.keymap`.
- `<layer_name> ...` — zero or more layers to render. Omit to render **all**
  layers in definition order.
- `-l, --layout` — path to the physical-layout JSON (see below). If omitted, the
  tool looks for `<keymap_file>`'s name with a `.json` suffix.
- `-o, --output` — output `.xlsx` path. A `.html` of the same base name is always
  written next to it. Defaults to `<layer>_keymap.xlsx` (single layer) or
  `keymap.xlsx`.

```sh
# All layers, explicit layout, both KEYMAP.xlsx and KEYMAP.html:
python keymap_docgen.py config/MyBoard.keymap -l tools/MyBoard.layout.json -o KEYMAP.xlsx

# Only specific layers:
python keymap_docgen.py config/MyBoard.keymap DEFAULT LOWER -l tools/MyBoard.layout.json -o KEYMAP.xlsx
```

## The HTML viewer

The `.html` needs no server and no network: open it from disk, or through
`https://htmlpreview.github.io/?https://github.com/<owner>/<repo>/blob/<branch>/KEYMAP.html`.
It fills the browser window and never scrolls the page:

- **Header** — the layer tabs (`L<n> <name>`), the キー / 経路 view toggle, the op
  toggle (タップ / ホールド, plus ダブルタップ / Shift+ / Ctrl+ — or a Vial
  `Key Override: …` — when a layer has such figures; each button shows how many
  keys are assigned, and is disabled on layers without that figure) and the
  **入り方** line: the keys in other layers that switch to this one (e.g.
  `L0 BASE_QWERTY の SPACE (長押し)`). Keys you keep holding while using the
  layer (`&lt` / `&mo`) are also outlined in green on the figure.
- **Stage** — the one figure for the current layer / view / op, scaled to fit
  (up to 2x; below 0.5x the stage scrolls instead). Keys that switch layer carry
  a `↗` mark.
- **Detail panel** — for the key under the mouse: every operation's action and
  resolution path (auto-derived forms such as `A×2` / `⇧A` greyed), the raw
  binding, and the same physical key on every layer.

| Input | Effect |
|---|---|
| hover a key | show it in the detail panel |
| click a key | pin it in the panel (click again or `Esc` to unpin) |
| click a `↗` key | go to the layer it switches to (touch: first tap pins, second tap goes) |
| `←` / `→` | previous / next layer |
| `0`–`9` | layer with that number |
| `V` | toggle キー / 経路 |
| tab, 入り方 / 各レイヤー chips | go to that layer |

Notes:

- Without JavaScript (or if the script fails) every layer block is stacked —
  the レイアウト図 view, then the 経路 view — with the full per-operation text
  in hover tooltips. Printing from the viewer prints the current figure.
- The page also works on htmlpreview.github.io, which injects a `<base href>`
  and re-creates inline scripts: the script therefore never uses anchors, the
  History API or `location`, and never contains `<script`, `</` or `<!--`.
- The output is deterministic and written with LF line endings on every OS, so
  a local regeneration matches CI byte for byte.

## Layout JSON schema

A ZMK-style physical-layout object. The tool reads the `default_layout` (or the
first layout if there is no `default_layout`):

```json
{
  "layouts": {
    "default_layout": {
      "layout": [
        { "x": 0, "y": 0, "w": 1, "h": 1, "label": "Q" },
        { "x": 1, "y": 0, "w": 1, "h": 1, "label": "W" }
      ]
    }
  }
}
```

Per entry:

| field   | required | meaning                                                            |
|---------|----------|-------------------------------------------------------------------|
| `x`     | yes      | column position (any numeric scale — logical slots or millimetres) |
| `y`     | yes      | row position (smaller = higher)                                    |
| `w`     | no       | key width in the same units (visual figure only; default = unit)   |
| `h`     | no       | key height in the same units (visual figure only; default = unit)  |
| `r`     | no       | key rotation in degrees (visual figure only)                       |
| `rx`/`ry` | no     | rotation origin (visual figure only; default = the key's `x`/`y`)  |
| `fx`/`fy` | no     | figure-only position; the visual figure uses these instead of `x`/`y` (default = `x`/`y`). Lets a clean integer `x`/`y` grid drive the tables while the figure shows the real column stagger. |
| `row`/`col` | no   | logical grid indices; when **every** key has integer `row` and `col` they drive the table rows/columns (preferred over `x`/`y`, which column stagger keeps from grouping into clean rows). The figure still uses `x`/`y`/`fx`/`fy`. |
| `label` | no       | the key's DEFAULT-layer identity shown in the docs (e.g. `Q`)      |

A top-level optional **`unit`** (positive number) sets how many coordinate units equal one key (1u) in the figure. Give it when the coordinates use a 1-per-column scale but the stagger offsets are fractional (e.g. `"unit": 1`), so the figure scale comes from the unit instead of being auto-detected from the smallest coordinate gap (which fractional stagger would otherwise shrink, blowing up the figure).

These map 1:1 onto ZMK's `key_physical_attrs <w h x y r rx ry>`, so a layout JSON
can mirror a board's `zmk,physical-layout` directly.

Rules:

- **Order matters.** `layout[i]` describes keymap binding `i`; the array order
  must match the binding order in every layer.
- **Rows/columns** come from the `row`/`col` fields when every key has them;
  otherwise they are the distinct `y` (top to bottom) and `x` (left to right)
  values — this drives the tables.
- **The visual figure** places every key by its `x`/`y`/`w`/`h` (and optional
  rotation), so it reflects the real board: column stagger, the split gap and
  rotated thumb clusters all appear when the coordinates describe them.
- **The split gap is auto-detected** (in the tables) at the widest horizontal
  gap between adjacent columns and shown as a blank separator column. With
  `row`/`col` the gap is located from each column's representative `x`, so it
  follows the real halves regardless of how the columns are numbered.
- If the layout is missing or its key count doesn't match the bindings, the tool
  falls back to a single keymap-order row and labels default to `pos N`.

See [`example/`](example/) for a complete, runnable 15-key sample:

```sh
cd example
python ../keymap_docgen.py sample.keymap -l sample.layout.json -o /tmp/sample.xlsx
```

## Using it in a keyboard repo

Vendor this folder at `tools/keymap-docgen/` and keep your board's layout JSON as
a **sibling** (outside the folder), e.g. `tools/MyBoard.layout.json`. A GitHub
Action can then regenerate the docs on every keymap change:

```yaml
- run: python tools/keymap-docgen/keymap_docgen.py config/MyBoard.keymap \
         -l tools/MyBoard.layout.json -o KEYMAP.xlsx
```

Link the viewer from the README with
`https://htmlpreview.github.io/?https://github.com/<owner>/<repo>/blob/<branch>/KEYMAP.html`.

## Extract to a standalone repo / git submodule

Because the script is keyboard-agnostic, you can lift this folder into its own
repository and reference it from each keyboard as a submodule:

```sh
# 1) Export this folder, with its history, onto a branch:
git subtree split --prefix=tools/keymap-docgen -b keymap-docgen-export

#    Create an empty GitHub repo (e.g. youruser/zmk-keymap-docgen), then:
git push git@github.com:youruser/zmk-keymap-docgen.git keymap-docgen-export:main

# 2) In each keyboard repo, swap the vendored copy for the submodule:
git rm -r tools/keymap-docgen
git commit -m "chore: replace vendored keymap-docgen with submodule"
git submodule add https://github.com/youruser/zmk-keymap-docgen tools/keymap-docgen
git commit -m "chore: add keymap-docgen submodule"
```

The keyboard's `tools/<board>.layout.json` stays put (it's a sibling, not part of
the submodule), and the workflow already checks out submodules
(`actions/checkout` with `submodules: recursive`), so CI keeps working unchanged.

## ZMK → Vial conversion (`zmk_to_vial.py`)

`zmk_to_vial.py` converts a ZMK `.keymap` into a **Vial keymap for the
[Keyboard Quantizer Mini](https://github.com/sekigon-gonnoc)** (a USB
keyboard converter running vial-qmk), so the same layers / hold-taps /
macros / tap-dances / mod-morphs work when typing on a regular USB keyboard
through the Quantizer.

It reuses this repo's ZMK parser and emits three files:

| output | purpose |
|--------|---------|
| `<name>.vil` | Load into the Vial GUI (`File → Load saved layout...`) — writes layers, macros, tap dances and key overrides to the keyboard. Keycodes are emitted as integers, which the GUI accepts regardless of its keycode-name set. |
| `<name>_vial_defaults.inc` | C include for vial-qmk. `#include` it at the end of the Quantizer's `keymaps/vial/keymap.c` to apply the same configuration as EEPROM defaults — on EEPROM (re)initialisation and on the first boot of every flashed build (vial-qmk resets the VIA region per build) or after a keymap change, keeping later Vial edits otherwise. |
| `<name>_vial_report.md` | Human-readable conversion report: key placement, macro/tap-dance/key-override tables, carrier assignments and warnings. |

### How the conversion works

* **Key placement** — the Quantizer's 32×8 matrix encodes the HID usage code
  of the key pressed on the attached keyboard
  (`HID k → row=(k>>3)+1, col=k&7`; modifiers `0xE0..0xE7 → row 0`).
  Each ZMK position is identified by its **BASE-layer tap keycode**
  (`&kp Q` → the attached keyboard's Q key, `&mt LCTRL A` → the A key).
  Positions without a tap identity (`&mo FUNC` …) are assigned through the
  mapping config.
* **Unmapped keys pass through** — keys of the attached keyboard that don't
  exist in the ZMK keymap keep typing their own character (configurable via
  `"unmapped_keys": "none"`).
* **Behaviours** — `&kp`/`&mt`/`&lt`/`&mo`/`&to`/`&trans`/`&none`/
  `&bootloader`/`&sys_reset`/`&mkp` map to their QMK equivalents; ZMK macros
  become Vial dynamic macros (waits → delays, `&macro_press/release` →
  down/up); tap-dances become Vial tap dance entries; **mod-morphs become
  Vial key overrides**.
* **Mod-morph rules** — if the morph's no-mod branch is a placeable keycode
  (basic key / tap dance / `&to`), it becomes the position's keycode and each
  mod branch becomes a key override triggering on it. If the no-mod branch is
  `&none`, a mod-wrapped keycode (`&kp LC(Z)`) or a macro — none of which can
  act as key-override triggers on this firmware — the position gets a
  **carrier** custom keycode (`QK_KB_3`, `QK_KB_4`, …) and the overrides
  trigger on the carrier.
* **Layer limit** — Vial dynamic keymaps have 8 layers; ZMK layers that make
  no sense on the Quantizer (Bluetooth, trackball mouse layers) are excluded
  via the config and the remaining layer indices are remapped.

### Usage

```sh
python zmk_to_vial.py config/MyBoard.keymap -m tools/MyBoard.vialmap.json \
    --vil MyBoard.vil --inc zmk_keymap_defaults.inc --report MyBoard_vial_report.md
```

- `-m, --mapping` — per-keyboard mapping config (see below).
- `--vil` / `--inc` / `--report` — output paths (default: next to the keymap).
- `--exclude-layers` — comma-separated layer names/indices (overrides config).
- `--strict` — exit non-zero when there are warnings.

### Mapping config schema

```jsonc
{
  "keyboard": "sekigon/keyboard_quantizer/mini",
  "layer_count": 8,
  // ZMK layers to drop (node names, #define aliases or indices)
  "exclude_layers": ["BLUETOOTH", "MOUSE_MOVE", "MOUSE_SCROLL"],
  // physical key for BASE-layer bindings that have no tap keycode;
  // keys are raw binding strings (#define aliases allowed), values are
  // ZMK key names (null = drop the position)
  "identityless_positions": {
    "&mo SYM": "RIGHT_ALT",
    "&mo VIM_BASE": "CAPS_LOCK",
    "&mo FUNC": "APPLICATION",
    "&mo BT": null
  },
  "carrier_start": 3,                    // first QK_KB_n used as a carrier
  "vial_uid": ["0x05", "0xE4", "..."],  // VIAL_KEYBOARD_UID of the firmware
  "layout_options": 0,
  "tapping_term_ms": 150,                // → QMK settings QSID 7 (tapping_term)
  // extra QMK settings ("<QSID>": value). Only the QSIDs listed here are
  // written by the .inc / .vil; anything else is left untouched (the firmware
  // default, or whatever was last set in Vial), so pin every setting that
  // must be deterministic.
  "settings": { "22": 1, "23": 0, "24": 0, "25": 0, "26": 0, "27": 0 },
  "unmapped_keys": "passthrough"         // or "none"
}
```

#### ZMK hold-tap properties → Vial QMK settings

The `&mt` / `&lt` behaviour properties of the ZMK keymap have no direct
counterpart in the keymap itself; they are expressed through Vial's QMK
settings (QSID → value) in the mapping config:

| ZMK `&mt` / `&lt` property | QSID (setting) | value |
|---|---|---|
| `tapping-term-ms = <N>` | 7 `tapping_term` (or `tapping_term_ms`) | N |
| `flavor = "balanced"` | 22 `permissive_hold` / 23 `hold_on_other_key_press` | 1 / 0 |
| `flavor = "hold-preferred"` | 22 / 23 | 0 / 1 |
| `flavor = "tap-preferred"` | 22 / 23 | 0 / 0 |
| `quick-tap-ms = <0>` (or unset) | 25 `quick_tap_term` | 0 (the firmware default is `TAPPING_TERM`, i.e. tap-then-hold repeats the key) |
| `retro-tap` | 24 `retro_tapping` | 1 / 0 |
| `hold-trigger-key-positions` | 26 `chordal_hold` | approximation only (opposite-hand rule) |
| `require-prior-idle-ms = <N>` | 27 `flow_tap_term` | approximation only: N (0 = off) — vial-qmk's flow tap applies only when both the tap keycode and the previous key are letters / Space / `.` `,` `;` `/` and no Ctrl/GUI/Alt is held, whereas ZMK counts any preceding key press |

The C types written into the `.inc` follow vial-qmk's `qmk_settings_t`
(`QMK_SETTINGS` table in `zmk_to_vial.py`: 2 / 4 / 6 / 7 / 9–19 / 25 / 27 are
16-bit, 22 / 23 / 24 / 26 are 0/1 bits, 21 is 32-bit). This matters because
`qmk_settings_set()` silently rejects a value handed over in a buffer smaller
than the setting. Unknown QSIDs are emitted as `uint8_t` with a warning.

### Known limitations

* Key-override trigger conflicts: two different morphs resolving to the same
  trigger keycode on the same layer are reported as warnings.
* ZMK behaviours with no Vial equivalent (`&bt`, `&out`, sticky keys,
  `&macro_pause_for_release`, tap-dances with 3+ taps) are dropped with a
  warning.
* The firmware-side feature set assumed here matches
  [vial-qmk-kq-mini](https://github.com/ryo-aoki-pc/vial-qmk-kq-mini)
  (key overrides may fire Vial macros; key-override layer matching uses the
  active top layer; QMK settings widths follow its `quantum/qmk_settings.c`).

### Tests

```sh
pip install pytest && python -m pytest tests/ -v
```

`tests/test_keymap_html_viewer.py` covers the HTML viewer (page structure, the
key data attributes, layer-jump detection, htmlpreview-safe ES5 script — run
through `node --check` when Node.js is installed).

## Vial / QMK keymaps (`vial_keymap_docgen.py`)

The same physical-layout HTML can be generated for a **QMK/Vial** keymap, so a
Vial keyboard gets a `KEYMAP.html` that looks just like ZMK's. It reuses
`keymap_docgen.py`'s figure renderer through a pluggable resolver, driven by a
QMK keycode resolver instead of the ZMK one.

Two input modes (auto-detected from the extension, override with `--format`):

```sh
# QMK keymap.c (LAYOUT_xxx(...) form) on a QMK info.json physical layout
python vial_keymap_docgen.py path/to/keymaps/<map>/keymap.c \
    --layout path/to/info.json \
    --custom-keycodes example/keyball_custom_keycodes.json \
    -o KEYMAP.html

# Vial .vil (integer layout[layer][row][col]) on a Vial vial.json (KLE) layout
python vial_keymap_docgen.py path/to/KEYMAP.vil --format vil \
    --layout path/to/keymaps/vial/vial.json \
    -o KEYMAP.html
```

* **Keycodes** — basic `KC_*` (short and long spellings), `MT`/`xxx_T` (tap on
  the cap, mod below), `LT`/`MO`/`TO`/`TG`/`DF`/`OSL`/`LM`, `TD(n)`, macros
  (`QK_MACRO_n`), mod-wrappers (`S()/C()/A()/G()/C_S()` …), `QK_BOOT`, and
  custom keycodes by name. `--custom-keycodes` supplies friendly labels for
  firmware-specific enums (e.g. Keyball's `AML_TO`, `CPI_I100`); a Vial
  `vial.json`'s `customKeycodes` is used automatically for `QK_KB_n` carriers.
* **Layouts** — QMK `info.json`/`keyboard.json` (ordered, pairs 1:1 with the
  `keymap.c` `LAYOUT` arguments) or Vial `vial.json` / VIA `via.json` (KLE,
  matrix-indexed; pick the layout option with `--layout-variant`, default the
  `.vil`'s `layout_options`). A `keymap_docgen` physical-layout JSON also works.
* **経路 (path) view** — like the ZMK `KEYMAP.html`, a 経路 view showing each
  key's raw keycode (`MT(MOD_LCTL, KC_A)`, `LT(2, KC_SPACE)`, `TD(1)` …) is
  included by default. It is rendered at the layout key size (compact), since
  QMK tokens are short; pass `--no-path` to omit it (and its toggle), or
  `--path-key-px` to resize.
* **Key overrides** — a `.vil`'s `key_override` entries (which `zmk_to_vial.py`
  produces from ZMK mod-morphs) render like the ZMK mod-morph figures: a per-mod
  `Key Override: Shift+` / `Key Override: Ctrl+` figure is added to each layer (in
  both views, behind the Shift+ / Ctrl+ op buttons), and a carrier key's cap
  shows its no-modifier output.
* **Layer numbers** — tabs, digit keys and the `↗` layer keys use the real QMK
  layer numbers, even when empty `.vil` layers are skipped (passed to
  `keymap_docgen.write_html` as `layer_indices`).
* **Macros & tap dances** — a `.vil`'s `macro` contents are shown in place of the
  `Mn` label (held-modifier spans compressed, e.g. `Home ▸ ⇧End ▸ ⌃X`), and a tap
  dance's double-tap action becomes its own `Tap Dance: ダブルタップ` figure
  (like ZMK), with the on-double-tap action (often a macro) shown there.

The ZMK path is unchanged — the resolver/title/path/extra_figures/layer_indices
parameters on `keymap_docgen.write_html` default to the ZMK behaviour (covered
by a regression test).

## License

MIT — see [LICENSE](LICENSE).
