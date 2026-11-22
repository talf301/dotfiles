-- Keymaps are automatically loaded on the VeryLazy event
-- Default keymaps that are always set: https://github.com/LazyVim/LazyVim/blob/main/lua/lazyvim/config/keymaps.lua
-- Add any additional keymaps here

local review = require("config.review")
vim.keymap.set({ "n", "x" }, "<leader>rc", review.comment, { desc = "Review: comment on line/selection" })
vim.keymap.set("n", "<leader>re", review.edit, { desc = "Review: edit comments file" })
vim.keymap.set("n", "<leader>rs", function()
  review.send()
end, { desc = "Review: send saved comments to Rex" })
vim.keymap.set("n", "<leader>rm", review.mark, { desc = "Review: mark reviewed (the HEAD this diff opened at)" })
vim.keymap.set("n", "<leader>rd", review.open_since_review, { desc = "Review: diff since last review" })

pcall(function()
  require("which-key").add({ { "<leader>r", group = "Review", mode = { "n", "x" } } })
end)

vim.keymap.set("n", "<leader>rb", function()
  require("config.review-table").open()
end, { desc = "Review: wrapped table" })
