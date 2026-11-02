#!/usr/bin/env bash
# install.sh — relink this repo's LIVE configs into place on a new machine.
#
# This repo holds the symlink-based configs (nikud handles everything XDG/shell with
# no symlinks; see ~/nikud). Run this after cloning ~/dotfiles to wire those up.
#
#   ./install.sh          # link everything (backs up anything already there)
#   ./install.sh --dry    # print what it WOULD do, touch nothing
#
# Idempotent: a link already pointing at the right target is left alone; anything
# else in the way is renamed to <target>.bak_<n> before linking (never deleted).

set -euo pipefail
REPO="$(cd "$(dirname "$(realpath "$0")")" && pwd)"
DRY=0; [ "${1:-}" = "--dry" ] && DRY=1

link() {  # link <src-in-repo | /absolute/src> <dest>
  local src="$1" dest="$2"; [[ "$src" = /* ]] || src="$REPO/$src"
  if [ ! -e "$src" ]; then echo "skip  $1 (not in repo)"; return; fi
  if [ "$(readlink "$dest" 2>/dev/null)" = "$src" ]; then echo "ok    $dest"; return; fi
  if [ $DRY = 1 ]; then echo "LINK  $dest -> $src${_bak:+  (would back up existing)}"; return; fi
  mkdir -p "$(dirname "$dest")"
  if [ -e "$dest" ] || [ -L "$dest" ]; then
    local n=0; while [ -e "$dest.bak_$n" ] || [ -L "$dest.bak_$n" ]; do n=$((n+1)); done
    mv "$dest" "$dest.bak_$n"; echo "bak   $dest -> $dest.bak_$n"
  fi
  ln -s "$src" "$dest"; echo "LINK  $dest -> $src"
}

# --- agent tools: every executable in bin/ → ~/.local/bin/ ---
for f in "$REPO"/bin/*; do link "bin/$(basename "$f")" "$HOME/.local/bin/$(basename "$f")"; done

# --- Rex CLI (ships inside the app bundle) → ~/.local/bin/rex ---
# Migration note (W20–W22): Rex replaces herdr. Nothing here calls herdr; its binary stays installed until Tal removes it.
link "/Applications/Rex Beta.app/Contents/Helpers/Rex Server.rex/Contents/MacOS/rex" "$HOME/.local/bin/rex"

# --- shared agent skills: Claude and Codex use ~/.agents/skills ---
for skill in implement-and-review first-mate rex-worktree digest dispatch-bead next-bead; do
  link "claude/skills/$skill" "$HOME/.agents/skills/$skill"
  link "$HOME/.agents/skills/$skill" "$HOME/.claude/skills/$skill"
done

# The retired session-board timer is not installed by the Rex workflow.

# --- ClaudeGo.app: opens claudego://<sessionId> links from board.md via iar/go.sh ---
if [ $DRY = 1 ]; then echo "BUILD ~/Applications/ClaudeGo.app"
else
  osacompile -o "$HOME/Applications/ClaudeGo.app" "$REPO/iar/ClaudeGo.applescript"
  plutil -replace CFBundleIdentifier -string local.claudego "$HOME/Applications/ClaudeGo.app/Contents/Info.plist"
  plutil -replace LSBackgroundOnly -bool true "$HOME/Applications/ClaudeGo.app/Contents/Info.plist"
  plutil -replace CFBundleURLTypes -json '[{"CFBundleURLName":"claudego","CFBundleURLSchemes":["claudego"]}]' "$HOME/Applications/ClaudeGo.app/Contents/Info.plist"
  codesign -f -s - "$HOME/Applications/ClaudeGo.app"
  /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$HOME/Applications/ClaudeGo.app"
  echo "BUILD ~/Applications/ClaudeGo.app"
fi

# --- macOS UI configs (these tools don't read nikud's XDG_CONFIG_HOME) ---
link "sketchybar"     "$HOME/.config/sketchybar"
link ".aerospace.toml" "$HOME/.aerospace.toml"

# --- dcg: user config (dcg reads ~/.config/dcg even with nikud's XDG_CONFIG_HOME) ---
link "dcg/config.toml" "$HOME/.config/dcg/config.toml"

# --- nvim: nikud owns the XDG link (~/nikud/xdg_config_home/nvim → ~/dotfiles/nvim).
#     Ensure it exists if nikud is checked out but the link is missing. ---
if [ -d "$HOME/nikud/xdg_config_home" ]; then
  link "nvim" "$HOME/nikud/xdg_config_home/nvim"
else
  echo "note  nvim is linked from ~/nikud/xdg_config_home/nvim — clone nikud first, then rerun"
fi

echo "done."
