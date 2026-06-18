-- ~/.config/nvim/lua/plugins/extras.lua
-- Preserved from previous config: lazygit + better floating terminal.
-- LazyVim already ships snacks.nvim, which provides both via snacks.lazygit
-- and snacks.terminal. We just expose the keymaps you had before.
return {
  {
    "folke/snacks.nvim",
    keys = {
      -- LazyGit (replaces kdheepak/lazygit.nvim — snacks has a built-in equivalent)
      { "<leader>gg", function() Snacks.lazygit() end, desc = "Lazygit" },
      { "<leader>gl", function() Snacks.lazygit.log() end, desc = "Lazygit log (cwd)" },
      { "<leader>gL", function() Snacks.lazygit.log_file() end, desc = "Lazygit log (file)" },

      -- Floating terminal (replaces FTerm.nvim — snacks.terminal is the modern equivalent)
      { "<leader>t", function() Snacks.terminal() end, desc = "Toggle floating terminal" },
      -- Match your old terminal-mode escape binding
      { "<C-q>", function() Snacks.terminal() end, mode = "t", desc = "Toggle floating terminal" },
    },
  },
}
