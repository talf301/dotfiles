#!/usr/bin/env zsh
# Second-screen cockpit on AeroSpace workspace 7: Obsidian (board.md) beside a WezTerm window running Claude Code in
# the vault. Reuses the cockpit WezTerm window while it exists, else opens one that continues the last vault
# conversation. Bound to alt-cmd-ctrl-o in .aerospace.toml.

VAULT="$HOME/Library/Mobile Documents/com~apple~CloudDocs/vault"
STATE="$HOME/.local/state/cockpit-wezterm-window"
AS=/opt/homebrew/bin/aerospace
WS=7

windows() { "$AS" list-windows --monitor all --app-bundle-id "$1" --format '%{window-id}' }

obsidian=$(windows md.obsidian | head -1)
[[ -n $obsidian ]] || { open -a Obsidian; sleep 3; obsidian=$(windows md.obsidian | head -1) }
[[ -n $obsidian ]] && "$AS" move-node-to-workspace --window-id "$obsidian" "$WS"

term=$(grep -m1 . "$STATE" 2>/dev/null)
if [[ -z $term ]] || ! windows com.github.wez.wezterm | grep -qx "$term"; then
  before=$(windows com.github.wez.wezterm)
  open -n -a WezTerm --args start --cwd "$VAULT" -- zsh -lic 'claude --continue || claude; exec zsh -l'
  term=""
  for _ in {1..40}; do
    term=$(windows com.github.wez.wezterm | grep -vxF "$before" | head -1)
    [[ -n $term ]] && break
    sleep 0.25
  done
  [[ -n $term ]] && mkdir -p "${STATE:h}" && print -r -- "$term" >| "$STATE"
fi
[[ -n $term ]] && "$AS" move-node-to-workspace --window-id "$term" "$WS"
"$AS" workspace "$WS"
