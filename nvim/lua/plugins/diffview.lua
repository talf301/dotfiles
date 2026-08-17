-- View any branch's full changeset from one nvim instance — no worktree
-- switching, no checkout. Complements octo.lua (PR review) and snacks lazygit.
return {
  "sindrets/diffview.nvim",
  cmd = { "DiffviewOpen", "DiffviewFileHistory", "DiffviewClose" },
  keys = {
    -- gv/gV/gH avoid the gg/gl/gL (lazygit) and gh (gitsigns hunks) bindings.
    { "<leader>gv", "<cmd>DiffviewOpen<cr>", desc = "Diffview: open (working tree)" },
    { "<leader>gV", "<cmd>DiffviewClose<cr>", desc = "Diffview: close" },
    { "<leader>gH", "<cmd>DiffviewFileHistory %<cr>", desc = "Diffview: file history" },
  },
  opts = {},
}
