-- ~/.config/nvim/lua/plugins/spectre.lua
-- Project-wide find-and-replace with editable diff view.
-- Capital R/W to avoid clobbering LazyVim's <leader>sr (resume) and <leader>sw (grep word).
return {
  {
    "nvim-pack/nvim-spectre",
    cmd = "Spectre",
    opts = { open_cmd = "noswapfile vnew" },
    keys = {
      { "<leader>sR", function() require("spectre").open() end, desc = "Replace in files (Spectre)" },
      { "<leader>sW", function() require("spectre").open_visual({ select_word = true }) end, desc = "Spectre word" },
    },
  },
}
