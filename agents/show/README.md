# show

Put a picture beside the conversation. An agent runs `show FILE` from its Rex
pane; the picture appears in that agent's own "Show" pane, to the right of its
pane. Each agent pane has its own Show pane and its own history, so two agents
on one tab have two Show panes. Focus never moves. Under rexchat the picture is
drawn inline in the chat instead (see rexchat below).

```sh
show diagram.mmd --title "Request flow"
show chart.png
show --enlarge            # this pane's latest picture in a large float
show --enlarge --index 2  # this pane's picture 2
show --open-pane          # only create or find this pane's Show pane (rexwork start uses it)
show --toggle             # close this pane's Show pane, or reopen it on the latest picture
```

| Input | Conversion |
|---|---|
| `.png` | copied |
| `.jpg` `.jpeg` `.gif` `.webp` `.heic` `.tiff` `.bmp` | `sips` (ImageMagick as fallback); GIF shows its first frame |
| `.svg` | `resvg` or `rsvg-convert` if installed, else macOS QuickLook (`qlmanage`, WebKit). QuickLook draws on a square canvas and crops a wide picture's right side, so `show` first wraps the SVG in a square canvas (side = the longer of its width/height, read from `width`/`height` or `viewBox`) and then crops the known top-left region (ImageMagick, else `sips`); the PNG has the SVG's own aspect ratio, about 2048 px on the long side. Only when the size is unknown is the canvas trimmed instead. ImageMagick's own SVG renderer is the last resort: it fails on text and on Mermaid labels |
| `.mmd` `.mermaid` | `mmdc -s 2 -b white` |
| `.md` `.markdown` | the first mermaid block, through `mmdc`; tables are not supported |

Output is one line of JSON: `image` (the stored PNG), `pane`, `window`,
`index` and `count` (1-based, within this pane's history), `created_pane`, and
`closed_orphans` when it closed Show panes whose agent pane is gone. A failed
conversion prints `show: <reason>` on stderr and exits 1.

The caller is `$REX_SESSION` and `$REX_BLOCK`; `--session` and `--block`
override them. If the caller has no Show pane, `show` splits the caller to the
right (40%) with `--focus=false` and starts the viewer there on the caller's
history.

`--toggle`, `--enlarge` and `--open-pane` act on the caller's own Show pane and
history. From a key binding the caller is the focused pane, so it is resolved:
a Show pane means its owner; a pane with its own Show pane or history means
itself; otherwise the pane on the same tab whose history changed last; failing
that, the old shared session history (below), if any. The JSON names the result
in `owner` and `resolved` (`caller`, `show pane`, `latest on tab`,
`legacy show pane`, `legacy history`). `--open-pane` always uses the caller.

## rexchat

Under rexchat (`~/src/rexchat`) pictures are drawn inline in the chat, not in
a Show pane. `show` delivers inline when:

1. `REXCHAT_INBOX` is set (the rexchat launcher sets it for its agent), or
2. the calling pane (`--block` or `$REX_BLOCK`) runs a live rexchat: its control
   socket `~/.local/state/rexchat/<block>.sock` (or under `$REXCHAT_STATE_DIR`)
   accepts a connection and answers. `show` sends `{}`, which rexchat refuses
   without doing anything, and takes rexchat's pid from the socket peer. The
   inbox is the one the launcher made: `<tmp>/rexchat/<block>/inbox-<pid>/`
   (`<tmp>` is the per-user temp directory), else the older
   `pictures/<block>/inbox-<pid>/` under rexchat's state directory. A status
   or socket file left by a killed rexchat does not answer, so `show` opens
   the normal Show pane. A live rexchat with no usable inbox is an error.

A picture is written only into an inbox that is a real directory owned by
you and not writable by others, as is its parent and, under `<tmp>/rexchat`,
that directory too (no symlinks): another user cannot plant one. Missing
directories (for `REXCHAT_INBOX`) are made with mode 0700.

Inline, `show FILE` converts the picture, writes `<ns>.json` (`title`,
`source`) and then `<ns>.png` into the inbox, and prints
`{"image": ..., "inline": true, "note": "drawn inline in the chat"}`. The pane
subcommands print `{"inline": true, "note": "pictures are drawn inline in this
chat; there is no Show pane"}`. rexchat's `shim/show` only runs this `show`.
This does not depend on the shim being first on PATH: Codex runs commands
through zsh, whose startup files put `~/.local/bin` first. If the inbox cannot
be written (Codex's sandbox allows writes only to its workspace and temp
dirs, so the older inbox under `~/.local/state` fails there), `show` exits 1 with `cannot write to rexchat's picture inbox`, so the
agent can rerun it with more permissions.

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

- State lives in `~/.local/state/rexwork/sessions/<UUID>/show/` (UUID is
  `$REX_SESSION` without `session:`):

  ```
  show/
    lock                      one lock for the whole session
    state.json                owners: {agent block: its Show pane block}
                              enlarge: {tab: the float show last opened there}
                              panes: {tab: Show pane} (old shared panes only)
    panes/<BLOCK>/            one per agent pane (BLOCK is the id without "block:")
      history.json  images/   that pane's pictures, numbered from 1
    history.json  images/     the old shared session history (read-only)
  ```

  `show FILE` appends to `panes/<BLOCK>/history.json`.
- Old state: a session's shared `history.json` from before per-pane Show panes
  is never rewritten or deleted. `--enlarge` and `--toggle` reach it when the
  tab has no per-pane history, and an old Show pane (recorded in `panes`)
  still toggles and enlarges on it. Every older `state.json` shape loads.
- A Show pane is `show`'s own only if it is the block recorded for its owner
  in `state.json`, is still tiled on the owner's tab, and `rex block call ...
  process` reports its foreground program as this `viewer` with cwd that
  owner's `panes/<BLOCK>/` dir. A pane that is merely labelled "Show" is never
  reused, adopted or closed; `show` opens a new one instead. If the recorded
  block is on the tab but its `process` query fails twice (0.3 s apart),
  nothing is created, closed or forgotten and `show` exits 75 (retryable);
  `show FILE` still saves the picture to history first and prints its JSON
  with `"pane": null`. Other tools own `session.json` and `objectives.md` in
  the parent directory.
- Cleanup: each `show FILE`, `--open-pane` and `--toggle` closes the Show panes
  whose owner block is no longer in the session, if they pass the ownership
  check above, and forgets them. Their histories stay.
- The session id must match `^[0-9a-fA-F-]{8,64}$` after `session:` is removed,
  and its directory must be a direct child of `~/.local/state/rexwork/sessions/`;
  otherwise `show` exits 1 before writing anything.
- One per-session lock (`show/lock`) covers numbering, the histories, finding
  or creating Show panes, and `state.json`, so concurrent calls from one pane
  make one Show pane and calls from several panes do not lose state. Conversion runs before the lock. JSON files are written
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
- Enlarge opens a float on the caller's tab running rex-view's viewer
  (`~/.config/rex/extensions/plugins/rex-view/.../viewer`), or `viewer --single`
  if rex-view is missing. A new enlarge closes the previous float on the same
  tab only if it is the exact layer and block `show` recorded and that layer holds nothing else;
  other floats, including hand-made ones labelled "Show: enlarged", are left
  alone. The float takes focus only when its tab is the
  session's active tab; the float's command ends with `rex focus` back to the
  pane that had focus.

`--toggle` closes the resolved owner's Show pane if there is one (and forgets
it in `state.json`), else opens one beside the owner with `--focus=false`; the
viewer starts on the latest picture. Called from a Show pane, it closes that
pane and focuses its owner (for an old shared Show pane: `session.json`'s
`main_block`) if that block is on the same tab; otherwise Rex picks the focus.
Bound to Cmd-I in the Rex config (`work_show_toggle`, which passes the focused
pane as `--block`).

Install: `ln -s ~/dotfiles/agents/show/show ~/.local/bin/show`.

Tests: `python3 -m unittest test_show.py` (fit and placement, SVG size and
square wrapping; per-pane panes, histories, toggle, enlarge, orphan cleanup,
old state and concurrent calls with a fake `rex`; rexchat detection with a
fake rexchat socket).
