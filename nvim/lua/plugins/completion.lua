-- ~/.config/nvim/lua/plugins/completion.lua
-- LazyVim ships with blink.cmp + LSP + mason. This just makes accept/navigate
-- feel like VSCode and ensures common language servers auto-install.
return {
  {
    "saghen/blink.cmp",
    opts = {
      keymap = {
        preset = "default",
        ["<Tab>"] = { "select_next", "snippet_forward", "fallback" },
        ["<S-Tab>"] = { "select_prev", "snippet_backward", "fallback" },
        ["<CR>"] = { "accept", "fallback" },
        ["<C-Space>"] = { "show", "show_documentation", "hide_documentation" },
      },
      completion = {
        accept = { auto_brackets = { enabled = true } },
        menu = { draw = { treesitter = { "lsp" } } },
        documentation = { auto_show = true, auto_show_delay_ms = 200 },
        ghost_text = { enabled = true },
      },
      signature = { enabled = true },
    },
  },
  -- Auto-install LSPs you likely want. Add/remove as needed.
  {
    "mason-org/mason-lspconfig.nvim", -- was "williamboman/mason-lspconfig.nvim"
    opts = {
      ensure_installed = {
        "pyright",
        "ruff",
        "ts_ls",
        "lua_ls",
        "rust_analyzer",
        "bashls",
        "jsonls",
      },
    },
  },
}
