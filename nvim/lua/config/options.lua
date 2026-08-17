-- Options are automatically loaded before lazy.nvim startup
-- Default options that are always set: https://github.com/LazyVim/LazyVim/blob/main/lua/lazyvim/config/options.lua
-- Add any additional options here

-- localleader = comma (octo's review commands hang off this; far easier to reach
-- than the default backslash). Must be set here, before lazy.nvim loads plugins.
vim.g.maplocalleader = ","
-- Force OSC 52 clipboard provider for SSH sessions.
-- Detects SSH automatically; falls back to default otherwise.
if os.getenv("SSH_TTY") then
  vim.g.clipboard = {
    name = "OSC 52",
    copy = {
      ["+"] = require("vim.ui.clipboard.osc52").copy("+"),
      ["*"] = require("vim.ui.clipboard.osc52").copy("*"),
    },
    paste = {
      ["+"] = require("vim.ui.clipboard.osc52").paste("+"),
      ["*"] = require("vim.ui.clipboard.osc52").paste("*"),
    },
  }
  vim.opt.clipboard = "unnamedplus"
end
