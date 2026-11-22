# show

Put a picture beside the conversation. An agent runs `show FILE` from its Rex
pane; the picture appears in the "Show" pane to the right of that pane. Focus
never moves.

```sh
show diagram.mmd --title "Request flow"
show chart.png
show --enlarge            # latest picture in a large float
show --enlarge --index 2  # picture 2
show --open-pane          # only create or find the Show pane (rexwork start uses it)
show --toggle             # close this tab's Show pane, or reopen it on the latest picture
```

| Input | Conversion |
|---|---|
| `.png` | copied |
| `.jpg` `.jpeg` `.gif` `.webp` `.heic` `.tiff` `.bmp` | `sips` (ImageMagick as fallback); GIF shows its first frame |
| `.svg` | `resvg` or `rsvg-convert` if installed, else macOS QuickLook (`qlmanage`, WebKit). QuickLook draws on a square canvas and crops a wide picture's right side, so `show` first wraps the SVG in a square canvas (side = the longer of its width/height, read from `width`/`height` or `viewBox`) and then crops the known top-left region (ImageMagick, else `sips`); the PNG has the SVG's own aspect ratio, about 2048 px on the long side. Only when the size is unknown is the canvas trimmed instead. ImageMagick's own SVG renderer is the last resort: it fails on text and on Mermaid labels |
| `.mmd` `.mermaid` | `mmdc -s 2 -b white` |
| `.md` `.markdown` | the first mermaid block, through `mmdc`; tables are not supported |

Output is one line of JSON: `image` (the stored PNG), `pane`, `window`,
`index` and `count` (1-based), and `created_pane`. A failed conversion prints
`show: <reason>` on stderr and exits 1.

The caller is `$REX_SESSION` and `$REX_BLOCK`; `--session` and `--block`
override them. If the caller's tab has no Show pane, `show` splits the caller
to the right (40%) with `--focus=false` and starts the viewer there. Each tab
gets its own Show pane; all of them follow the session's one history.

## Keys in the Show pane

| Key | Action |
|---|---|
| `h` `Left` `k` `p` | previous picture |
| `l` `Right` `j` `n` `Space` | next picture |
| `g` / `G` | first / latest |
| `Enter` `z` `f` | enlarge in a float; `q` or `Esc` closes it and focus returns |
| `r` | redraw |
| `q` | close the Show pane (the next `show` reopens it) |

A new picture always jumps to the front. The footer shows index, title and
key hints.

## How it works

- `show` converts the file and appends it to
  `~/.local/state/rexwork/sessions/<UUID>/show/history.json` (UUID is
  `$REX_SESSION` without `session:`). PNGs are kept in `images/`; `state.json`
  records the Show pane of each tab and, per tab, the enlarge float `show` last
  opened there.
- A Show pane is `show`'s own only if it is the block recorded for that tab in
  `state.json`, is still tiled on the tab, and `rex block call ... process`
  reports its foreground program as this `viewer` with cwd this session's
  `show/` dir. A pane that is merely labelled "Show" is never reused, adopted
  or closed; `show` opens a new one instead. If the recorded block is on the
  tab but its `process` query fails twice (0.3 s apart), nothing is created,
  closed or forgotten and `show` exits 75 (retryable); `show FILE` still saves
  the picture to history first and prints its JSON with `"pane": null`.
  Other tools own `session.json` and `objectives.md` in the parent directory.
- The session id must match `^[0-9a-fA-F-]{8,64}$` after `session:` is removed,
  and its directory must be a direct child of `~/.local/state/rexwork/sessions/`;
  otherwise `show` exits 1 before writing anything.
- One per-session lock (`show/lock`) covers numbering, `history.json`, finding
  or creating the Show pane, and `state.json`, so concurrent calls on one tab
  make one Show pane. Conversion runs before the lock. JSON files are written
  through a unique temp file and renamed into place.
- `viewer DIR` runs in the Show pane. It polls `history.json`, draws the
  current PNG with the Kitty graphics protocol and retransmits on resize
  (SIGWINCH), on focus-in and on `r`, so a client that lost the image gets it
  again. Placement: `fit()` takes the pane size from the tty and the cell
  pixel size from `TIOCGWINSZ` (else `rex block call ... size`), and returns a
  box of whole cells inside the rows above the footer, centred. A picture
  larger than the pane is scaled down until one side fills it; a smaller one
  stays at native size (never scaled up). Both `c=` and `r=` are always sent,
  so the terminal never sizes the image from its pixels; the box keeps the
  aspect ratio to within half a cell.
- Enlarge opens a float on the Show pane's tab running rex-view's viewer
  (`~/.config/rex/extensions/plugins/rex-view/.../viewer`), or `viewer --single`
  if rex-view is missing. A new enlarge closes the previous float on the same
  tab only if it is the exact layer and block `show` recorded and that layer holds nothing else;
  other floats, including hand-made ones labelled "Show: enlarged", are left
  alone. The float takes focus only when its tab is the
  session's active tab; the float's command ends with `rex focus` back to the
  pane that had focus.

`--toggle` closes `show`'s own Show pane on the caller's tab if there is one (and
forgets it in `state.json`), else opens one with `--focus=false`; the viewer
starts on the latest picture. Called from the Show pane itself, it closes that
pane and focuses `session.json`'s `main_block` if that block is on the same tab
(otherwise Rex picks the focus). Meant for a key binding.

Install: `ln -s ~/dotfiles/agents/show/show ~/.local/bin/show`.

Tests: `python3 -m unittest test_show.py` (pure parts: fit and placement,
SVG size and square wrapping, toggle with a fake `rex`, state helpers).
