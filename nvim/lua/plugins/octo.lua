return {
  "pwntester/octo.nvim",
  cmd = "Octo",
  opts = {
    -- or "fzf-lua" or "telescope" or "default"
    picker = "snacks",
    -- bare Octo command opens picker of commands
    enable_builtin = true,
    -- Ergonomic one-hand navigation inside a review (deep-merged over octo's
    -- defaults, so only these actions move; everything else keeps its keys).
    -- C-j/C-k walk unviewed files, C-n/C-p step through discussion.
    -- File nav stays on ]q/[q brackets; only the unviewed/thread/comment
    -- motions relocate off the bracket keys.
    mappings = {
      review_diff = {
        select_next_unviewed_entry = { lhs = "<C-j>", desc = "next unviewed file" },
        select_prev_unviewed_entry = { lhs = "<C-k>", desc = "prev unviewed file" },
        next_thread = { lhs = "<C-n>", desc = "next thread" },
        prev_thread = { lhs = "<C-p>", desc = "prev thread" },
      },
      review_thread = {
        select_next_unviewed_entry = { lhs = "<C-j>", desc = "next unviewed file" },
        select_prev_unviewed_entry = { lhs = "<C-k>", desc = "prev unviewed file" },
        next_comment = { lhs = "<C-n>", desc = "next comment" },
        prev_comment = { lhs = "<C-p>", desc = "prev comment" },
      },
    },
  },
  keys = {
    {
      "<leader>oi",
      "<CMD>Octo issue list<CR>",
      desc = "List GitHub Issues",
    },
    {
      "<leader>op",
      "<CMD>Octo pr list<CR>",
      desc = "List GitHub PullRequests",
    },
    {
      "<leader>od",
      "<CMD>Octo discussion list<CR>",
      desc = "List GitHub Discussions",
    },
    {
      "<leader>on",
      "<CMD>Octo notification list<CR>",
      desc = "List GitHub Notifications",
    },
    {
      "<leader>or",
      "<CMD>Octo search is:pr is:open review-requested:@me<CR>",
      desc = "PRs awaiting my review",
    },
    {
      "<leader>oa",
      "<CMD>Octo search is:pr is:open author:@me<CR>",
      desc = "My open PRs",
    },
    {
      "<leader>om",
      "<CMD>Octo search is:pr is:open involves:@me<CR>",
      desc = "PRs involving me",
    },
    {
      "<leader>os",
      function()
        require("octo.utils").create_base_search_command({ include_current_repo = true })
      end,
      desc = "Search GitHub",
    },
  },
  dependencies = {
    "nvim-lua/plenary.nvim",
    "folke/snacks.nvim",
    "nvim-tree/nvim-web-devicons", -- optional if file_panel.icons is a function
  },
}
