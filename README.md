# keymap-docgen

Generate human-readable key-assignment docs (Excel **.xlsx** + a self-contained
**.html**) from a ZMK `.keymap` file. The tool parses standard ZMK behaviours —
`&kp`, `&mt` (mod-tap), `&lt` (layer-tap), `&mo`/`&to` (layers), mod-morph,
tap-dance and macros — resolves them recursively, and lays every layer out to
match the board's real physical arrangement (including the split gap). The
`.html` additionally renders each layer as a **visual layout figure** — keys
positioned by their real coordinates (so column stagger, the split gap and key
rotation all show), with the tap action on the cap, the hold action below it,
and every operation in a hover tooltip.

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

## License

MIT — see [LICENSE](LICENSE).
