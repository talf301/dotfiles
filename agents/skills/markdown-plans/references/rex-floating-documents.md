# Rex floating documents

## How docview works

Each Rex session has one background Neovim (`nvim --headless --listen`, the person's normal config plus `~/dotfiles/agents/docview/server.lua`), started by the first `docview open`, `diffreview open` or `rexwork promote`. Every document, diff and ticket draft is a tab page in it. One floating pane, the viewer, runs `nvim --server SOCKET --remote-ui`. The background Neovim holds the buffers, unsaved edits and each tab's review settings; the pane is disposable.

Neovim sizes itself to the smallest attached viewer. A second viewer in a tab nobody is viewing is 80x24, which caps the document at 24 rows wherever it is shown. So there is only one: `open` from another Rex tab closes the viewer and attaches a new one on the caller's tab.

- State: `~/.local/state/rexwork/sessions/<uuid>/nvim.sock` and `viewer.json` (session, window, block and layer of the viewer). Sessions that rexwork does not know get the directory too.
- Each tab stores `vim.t.tab_label`, `vim.t.review_block`, `vim.t.review_session` and `vim.t.review_comments_file`; Space r s sends that tab's comments to that tab's agent pane.
- `docview toggle --session S --block B` (the Rex action `work_editor`, Ctrl+Opt+Shift+Cmd+E) minimizes a visible float, restores and focuses a minimized one, and moves or reattaches it to B's Rex tab otherwise.
- `:q` on a tab's last window closes the tab; `:qa` detaches the viewer; only `:qa!` quits the shared Neovim. When the last tab closes, the viewer closes and Neovim quits if nothing is unsaved.
- If a file has a swap file, its tab opens read-only rather than waiting on a prompt nobody can see. Commands that get no answer from Neovim within 10 seconds fail with a message instead of hanging.
- `docview gc` quits session Neovims with no tab, no viewer and no unsaved edits, and legacy per-document Neovims (`~/.local/state/docview/<id>/`) with no viewer and no unsaved edits that are more than 12 hours old.

## Manual fallback

Use this only when `docview` is unavailable.

Load the Rex skill and inspect the live session to resolve the originating conversation block and its window. Use `rex api describe session.new_layer --json` and `rex block types --json` if the current server differs from this example.

Create a request file as structured JSON. Replace every placeholder with a verified ID or absolute path. Keep the command as an argument array; do not interpolate document text into shell code.

```json
{
  "session_id": "session:<verified-session>",
  "window_id": "window:<conversation-window>",
  "bounds": [0.06, 0.06, 0.88, 0.88],
  "focus": true,
  "layout": {
    "block": {
      "flavor": "com.superlogical.terminal.shell",
      "label": "Document: <descriptive title>",
      "options": {
        "cwd": "/absolute/document/directory",
        "command": [
          "env",
          "REVIEW_FILE=/absolute/comments.md",
          "REX_REVIEW_BLOCK=block:<verified-conversation>",
          "sh", "-c",
          "nvim --listen \"$1\" \"$2\"; rex focus -s \"$REX_SESSION\" \"$REX_REVIEW_BLOCK\"",
          "sh", "/absolute/unique-review.sock", "/absolute/document.md"
        ]
      }
    }
  }
}
```

Run `rex api call session.new_layer @/absolute/request.json`. Preserve the returned block and layer IDs in the conversation's document record immediately. On timeout, inspect the window before retrying. Verify that the pane is a floating layer in the intended window, Neovim opened the intended file, and its review destination matches the originating conversation. Rex supplies `REX_SESSION`; preserve the normal login-shell configuration and `XDG_CONFIG_HOME`. The trailing `rex focus` returns focus to the conversation pane when Neovim exits; without it, a closed float hands focus to the tab's last pane.

Raise an existing document with `session.raise_layer` and focus its block. Inspect the API schema for arguments. Floating panes belong to their window and remain available when switching back to that conversation.

For an existing document in the same session, `session.move_block` can move it beside the conversation block, then `session.float_block` can float it. This preserves the editor process and unsaved work. Check the source window afterward; moving its last block can close the empty window. Moving between sessions requires a separate plan; do not recreate an editor until its unsaved state is known.

## Retire a superseded document

Verify the replacement is open and the old pane belongs to this conversation and document revision. Through that Neovim instance's RPC socket, inspect all loaded buffers' `modified` flags. If they are clean, close the old document pane with `rex block close`. If any buffer is modified or RPC inspection is unavailable, call `session.detach_block` instead; it removes the pane from view while keeping its process alive. Record whether the pane is closed or detached, and retain paths and replacement identity.

A detached pane can be recovered with `session.move_block` beside the verified conversation block, then `session.float_block`. Never delete the source Markdown or saved feedback as part of retiring a pane.
