-- Local code review that hands comments back to an agent.
-- The agent launches `REVIEW_FILE=<path> neovide --no-fork -- -c "DiffviewOpen <base>...HEAD"`,
-- blocks until Neovide exits, then reads REVIEW_FILE. An empty file means approved.
local M = {}

local ns = vim.api.nvim_create_namespace("review_comments")

-- Returns the repo-relative path and the revision the buffer shows.
local function location(buf)
  local name = vim.api.nvim_buf_get_name(buf)
  if name:match("^diffview://") then
    local view = require("diffview.lib").get_current_view()
    local prefix = "diffview://" .. view.adapter.ctx.dir .. "/"
    local rev, path = name:sub(#prefix + 1):match("^([^/]+)/(.*)$")
    return path, rev
  end
  return vim.fn.fnamemodify(name, ":."), "working tree"
end

function M.comment()
  local file = vim.env.REVIEW_FILE
  if not file then
    return vim.notify("REVIEW_FILE is not set", vim.log.levels.WARN)
  end
  local buf = vim.api.nvim_get_current_buf()
  local first, last = vim.fn.line("v"), vim.fn.line(".")
  if first > last then
    first, last = last, first
  end
  vim.cmd("normal! \27") -- leave visual mode
  local path, rev = location(buf)
  local code = vim.api.nvim_buf_get_lines(buf, first - 1, last, false)

  vim.ui.input({ prompt = ("Comment %s:%d-%d: "):format(path, first, last) }, function(text)
    if not text or text == "" then
      return
    end
    local entry = {
      ("## %s:%d-%d (%s)"):format(path, first, last, rev),
      "```" .. vim.bo[buf].filetype,
    }
    vim.list_extend(entry, code)
    vim.list_extend(entry, { "```", text, "" })
    local fd = assert(io.open(file, "a"))
    fd:write(table.concat(entry, "\n"), "\n")
    fd:close()
    vim.api.nvim_buf_set_extmark(buf, ns, last - 1, 0, {
      virt_lines = { { { "  💬 " .. text, "DiagnosticInfo" } } },
    })
  end)
end

function M.edit()
  if vim.env.REVIEW_FILE then
    vim.cmd.split(vim.env.REVIEW_FILE)
  end
end

return M
