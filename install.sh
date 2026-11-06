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

# --- retained agent tools ---
for tool in cc_usage.py linear-description linear-upload; do
  link "bin/$tool" "$HOME/.local/bin/$tool"
done

# --- shared instructions and three personal core skills ---
link "agents/AGENTS.md" "$HOME/AGENTS.md"
link "$HOME/AGENTS.md" "$HOME/.claude/CLAUDE.md"
link "$HOME/AGENTS.md" "$HOME/.codex/AGENTS.md"
for skill in work review plannotator; do
  link "agents/skills/$skill" "$HOME/.agents/skills/$skill"
  link "agents/skills/$skill" "$HOME/.claude/skills/$skill"
done

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
