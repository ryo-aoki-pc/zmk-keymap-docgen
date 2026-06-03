# keymap-docgen

Generate human-readable key-assignment docs (Excel **.xlsx** + a self-contained
**.html**) from a ZMK `.keymap` file. The tool parses standard ZMK behaviours —
`&kp`, `&mt` (mod-tap), `&lt` (layer-tap), `&mo`/`&to` (layers), mod-morph,
tap-dance and macros — resolves them recursively, and lays every layer out to
match the board's real physical arrangement (including the split gap). The
`.html` additionally renders each layer as a **visual layout figure** — keys
positioned by their real coordinates (so column stagger, the split gap and key
rotation all show), with the tap action on the cap, the hold action below it,
and every operation in a hover tooltip. The figures are collected into a single
table: one row per layer, with the layer name in the left header cell and every
figure belonging to that layer — the main figure plus its Tap Dance / Mod Morph
figures — stacked together in the right cell. Key-cap text on the figure is kept
compact without losing information: layer jumps show as `L<n>` instead of the
layer node name, and macro chains are stacked one step per line with the font
size shrinking automatically so the full content fits inside the key box.

The HTML's **経路 (resolution path) section** uses the same physical-layout
figure style: each key cap shows its behavior-resolution chain
(`behavior[index] ▸ … ▸ final binding`) at double key size so the longer path
strings stay readable, with extra figures for operations whose path diverges
(Tap Dance / Mod Morph) and the full per-operation paths in hover tooltips.
When no usable layout geometry is available, the section falls back to the
legacy table form.

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
| `<name>_vial_defaults.inc` | C include for vial-qmk. `#include` it at the end of the Quantizer's `keymaps/vial/keymap.c` to apply the same configuration as EEPROM defaults on every EEPROM (re)initialisation. |
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
  "tapping_term_ms": 150,                // → QMK settings QSID 7
  "settings": { "22": 1 },               // extra QMK settings (22 = permissive hold)
  "unmapped_keys": "passthrough"         // or "none"
}
```

### Known limitations

* Key-override trigger conflicts: two different morphs resolving to the same
  trigger keycode on the same layer are reported as warnings.
* ZMK behaviours with no Vial equivalent (`&bt`, `&out`, sticky keys,
  `&macro_pause_for_release`, tap-dances with 3+ taps) are dropped with a
  warning.
* The firmware-side feature set assumed here matches
  [vial-qmk-kq-mini](https://github.com/ryo-aoki-pc/vial-qmk-kq-mini)
  (key overrides may fire Vial macros; key-override layer matching uses the
  active top layer).

### Tests

```sh
pip install pytest && python -m pytest tests/ -v
```

## License

MIT — see [LICENSE](LICENSE).
