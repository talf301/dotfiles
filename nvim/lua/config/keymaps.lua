-- Keymaps are automatically loaded on the VeryLazy event
-- Default keymaps that are always set: https://github.com/LazyVim/LazyVim/blob/main/lua/lazyvim/config/keymaps.lua
-- Add any additional keymaps here

local review = require("config.review")
vim.keymap.set({ "n", "x" }, "<leader>rc", review.comment, { desc = "Review: comment on line/selection" })
vim.keymap.set("n", "<leader>re", review.edit, { desc = "Review: edit comments file" })
