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

link() {  # link <src-in-repo> <dest>
  local src="$REPO/$1" dest="$2"
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

# --- swarm tooling: every executable in bin/ → ~/.local/bin/ ---
for f in "$REPO"/bin/*; do link "bin/$(basename "$f")" "$HOME/.local/bin/$(basename "$f")"; done

# --- IAR skill: cross-agent chain  ~/.claude → ~/.agents → this repo ---
link "claude/skills/implement-and-review" "$HOME/.agents/skills/implement-and-review"
# .claude points at the .agents copy (matches the existing cross-agent convention)
_claude="$HOME/.claude/skills/implement-and-review"
if [ "$(readlink "$_claude" 2>/dev/null)" != "$HOME/.agents/skills/implement-and-review" ]; then
  if [ $DRY = 1 ]; then echo "LINK  $_claude -> ~/.agents/skills/implement-and-review"
  else mkdir -p "$HOME/.claude/skills"
       [ -e "$_claude" ] || [ -L "$_claude" ] && mv "$_claude" "$_claude.bak_0" 2>/dev/null || true
       ln -s "$HOME/.agents/skills/implement-and-review" "$_claude"; echo "LINK  $_claude -> ~/.agents/skills/implement-and-review"
  fi
else echo "ok    $_claude"; fi

# --- macOS UI configs (these tools don't read nikud's XDG_CONFIG_HOME) ---
link "sketchybar"     "$HOME/.config/sketchybar"
link ".aerospace.toml" "$HOME/.aerospace.toml"

# --- nvim: nikud owns the XDG link (~/nikud/xdg_config_home/nvim → ~/dotfiles/nvim).
#     Ensure it exists if nikud is checked out but the link is missing. ---
if [ -d "$HOME/nikud/xdg_config_home" ]; then
  link "nvim" "$HOME/nikud/xdg_config_home/nvim"
else
  echo "note  nvim is linked from ~/nikud/xdg_config_home/nvim — clone nikud first, then rerun"
fi

echo "done."
