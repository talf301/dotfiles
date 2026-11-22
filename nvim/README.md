# Neovim document review in Rex

Read internal plans and documents in normal Neovim, in floating panes attached to their conversation's Rex tab. Keep current documents available there; retire superseded panes while retaining their Markdown and saved feedback.

## Review controls

| Keys | Action |
| --- | --- |
| Space r t | Toggle the heading sidebar |
| Space r b | Open the table under the cursor with wrapped cells |
| Space r c | Comment on the current line or visual selection |
| Space r e | Edit saved comments |
| Space r s | Send saved comments to the originating agent |
| Space r i | Preview an image or diagram |
| Space r d | Open the diff since the last review (diffview) |
| Space r m | Mark HEAD reviewed |
| Space u m | Toggle Markdown rendering |
| gt / gT | Next / previous document, diff or ticket tab (editor float) |
| Ctrl+Opt+Shift+Cmd+E | In Rex: hide the editor float, or show it on this tab |

The table popup supports j/k and Ctrl-d/u. Press q or Escape to return to the original position. Enter comments in the source document. Inline Markdown is shown literally inside the table popup.

The comment keys also work in a diffview diff. A comment quotes the repo-relative path and the line numbers of the side it was made on; comments on the old side say `old side, <commit>`. See `~/dotfiles/agents/diffreview` for the review-since-last-review flow.

## Rendering

Headings, lists, checkboxes, and tables use render-markdown.nvim. Wide inline tables can wrap awkwardly in Neovim; Space r b provides a separate view with text wrapping inside each cell.

Mermaid uses Snacks images, Mermaid CLI (`mmdc`), and ImageMagick. Diagrams conceal their source while reading and expose it when the cursor enters. Rex image recovery resends cached image data and placements when the terminal reports a missing image. Markdownlint diagnostics and formatting are disabled for Markdown; the other configured formatters remain available.

## Conversation documents

Each Rex session has one background Neovim (`docview`, `~/dotfiles/agents/docview`) shown in one floating pane, the editor float. Every document (`docview open`), diff review (`diffreview open`) and ticket draft (`rexwork promote`) is a tab page in it, labelled in the tabline (`Doc: plan`, `Review: dotfiles`, `Ticket: draft`); gt and gT switch tabs. Opening from another Rex tab moves the float there. Saved comments belong to that document revision.

Each tab records where its comments go: `vim.t.review_block`, `vim.t.review_session` and `vim.t.review_comments_file`, set by the command that opened it. `config.review` reads the buffer's value, then the tab's; in the shared editor (`vim.g.docview_shared`) it never falls back to `REX_REVIEW_BLOCK` or `REVIEW_FILE`, so comments from one tab never go to another tab's agent. `clear_marks()` clears only the current tab's comment marks.

`:q` on a tab's last window closes the tab, not the shared Neovim; unsaved buffers stay loaded and `docview ls` lists them. Closing the last tab closes the float, and Neovim quits when nothing is unsaved. `:qa` closes the float only; `:qa!` still quits everything. Closing the float pane never loses edits.

## Configuration and recovery

The installed configuration lives in `~/dotfiles/nvim`. The complete configuration, plugin lockfile, review code, and tests are preserved on the local dotfiles branch `u/tfriedman/markdown-visual-fixes`. The branch includes the installed baseline and subsequent rendering and table fixes. It has not been pushed to GitHub.

To inspect the saved configuration without switching the active checkout:

```sh
git -C ~/dotfiles show u/tfriedman/markdown-visual-fixes:nvim/README.md
git -C ~/dotfiles diff master..u/tfriedman/markdown-visual-fixes -- nvim
```

Normal Neovim configuration remains selected through `XDG_CONFIG_HOME`; the current installation uses `~/nikud/xdg_config_home/nvim`. Plugins are pinned by `lazy-lock.json`. External requirements are Neovim, Rex, `mmdc`, and ImageMagick on PATH, plus the Markdown Tree-sitter parsers installed through the existing Neovim setup.

The `markdown-plans` skill contains the document-writing and pane-lifecycle workflow. Requested outward-facing artifacts use the separate `claude-artifacts` skill.

## Checks

`tests/review_table_spec.lua` runs headlessly and checks cell wrapping, Unicode, escaped pipes, narrow widths, and content preservation. `tests/review_table_live.py` tests a table in a running Neovim instance, including navigation, resize handling, source preservation, and closing.

`tests/markdown_rendering_smoke.py` checks the rendering fixture in Rex. `tests/rex_image_recovery.py` checks an actual missing-image response and acknowledged recovery. `tests/review_spec.lua` covers review comment handling, including per-tab targets in the shared editor; run it from `nvim/` with `nvim --headless -u NONE -l tests/review_spec.lua`. The shared editor itself is tested by `~/dotfiles/agents/docview/test_docview.py`.
