-- Loaded with --cmd into a session's shared docview Neovim (see ./docview). Each
-- document, diff review and ticket draft is a tab page; the tab's own variables
-- say where its review comments go:
--   vim.t.tab_label             "Doc: plan", "Review: dotfiles", "Ticket: draft"
--   vim.t.docview_kind          "doc" | "diff" | "ticket"
--   vim.t.docview_path          the document (doc and ticket tabs)
--   vim.t.diffreview_key        repo and agent pane (diff tabs)
--   vim.t.review_comments_file, vim.t.review_block, vim.t.review_session
-- config.review reads them (vim.b > vim.t) and, because vim.g.docview_shared is
-- set, never falls back to environment variables for the agent pane.
local M = {}
package.loaded.docview_server = M
vim.g.docview_shared = true

local api = vim.api
local group = api.nvim_create_augroup("docview_server", { clear = true })

-- A swap file means the file is open elsewhere (or was, in a crash). Nobody may be
-- looking at this Neovim, so open read-only instead of asking.
api.nvim_create_autocmd("SwapExists", {
  group = group,
  callback = function()
    vim.v.swapchoice = "o"
  end,
})
vim.opt.shortmess:append("A")

-- lazy.nvim fires VeryLazy on UIEnter, so a headless Neovim loads the config's late
-- autocommands and keymaps only when the first viewer attaches. Buffers opened before
-- that missed the FileType autocommands registered then (wrap and spell for prose,
-- and whatever else the config adds): run FileType again for them, once.
api.nvim_create_autocmd("User", {
  group = group,
  pattern = "VeryLazy",
  once = true,
  callback = function()
    vim.schedule(function()
      for _, win in ipairs(api.nvim_list_wins()) do
        local buf = api.nvim_win_get_buf(win)
        local ft = vim.bo[buf].filetype
        if ft ~= "" and vim.bo[buf].buftype == "" then
          pcall(api.nvim_win_call, win, function()
            api.nvim_exec_autocmds("FileType", { pattern = ft, modeline = false })
          end)
        end
      end
    end)
  end,
})

local function tvar(tab, name)
  local ok, v = pcall(api.nvim_tabpage_get_var, tab, name)
  if ok then
    return v
  end
  return nil
end

local function find_tab(name, value)
  for _, tab in ipairs(api.nvim_list_tabpages()) do
    if tvar(tab, name) == value then
      return tab
    end
  end
  return nil
end

local function tab_bufs(tab)
  local seen, bufs = {}, {}
  for _, win in ipairs(api.nvim_tabpage_list_wins(tab)) do
    local b = api.nvim_win_get_buf(win)
    if not seen[b] then
      seen[b] = true
      bufs[#bufs + 1] = b
    end
  end
  return bufs
end

local function shown_elsewhere(buf, tab)
  for _, win in ipairs(vim.fn.win_findbuf(buf)) do
    if api.nvim_win_get_tabpage(win) ~= tab then
      return true
    end
  end
  return false
end

local function is_modified(b)
  return api.nvim_buf_is_valid(b) and vim.bo[b].modified and vim.bo[b].buftype == ""
end

-- The empty tab Neovim starts with: one window on an unnamed, empty, unchanged buffer.
local function pristine(tab)
  if tvar(tab, "tab_label") then
    return false
  end
  local wins = api.nvim_tabpage_list_wins(tab)
  if #wins ~= 1 then
    return false
  end
  local b = api.nvim_win_get_buf(wins[1])
  return api.nvim_buf_get_name(b) == "" and not vim.bo[b].modified and vim.bo[b].buftype == ""
    and api.nvim_buf_line_count(b) == 1 and api.nvim_buf_get_lines(b, 0, 1, false)[1] == ""
end

local function drop_pristine()
  local tabs = api.nvim_list_tabpages()
  for _, tab in ipairs(tabs) do
    if #api.nvim_list_tabpages() > 1 and tab ~= api.nvim_get_current_tabpage() and pristine(tab) then
      pcall(vim.cmd, "tabclose " .. api.nvim_tabpage_get_number(tab))
    end
  end
end

local function labeled()
  local n = 0
  for _, tab in ipairs(api.nvim_list_tabpages()) do
    if tvar(tab, "tab_label") then
      n = n + 1
    end
  end
  return n
end

local function set_review(a)
  if a.comments ~= nil then
    vim.t.review_comments_file = a.comments
  elseif vim.t.review_comments_file == nil then
    vim.t.review_comments_file = a.comments_default
  end
  vim.t.review_block = a.block
  vim.t.review_session = a.session
  if a.label then
    vim.t.tab_label = a.label
  elseif not vim.t.tab_label then
    vim.t.tab_label = a.label_default
  end
end

-- Show a document in its tab (made if needed) and point the tab's comments at a.block.
function M.open_doc(a)
  local tab = find_tab("docview_path", a.path)
  if tab then
    api.nvim_set_current_tabpage(tab)
  else
    local ok, err = pcall(function()
      if pristine(api.nvim_get_current_tabpage()) then
        vim.cmd.edit(vim.fn.fnameescape(a.path))
      else
        vim.cmd("tabedit " .. vim.fn.fnameescape(a.path))
      end
    end)
    if not ok then
      return { error = tostring(err) }
    end
    vim.cmd.tcd(vim.fn.fnameescape(vim.fn.fnamemodify(a.path, ":h")))
    -- Documents are prose: wrap them. The config's own FileType autocmd for this loads
    -- on VeryLazy, which a headless server reaches only once a viewer attaches, so the
    -- first document would otherwise open unwrapped.
    vim.wo.wrap = true
    vim.wo.linebreak = true
  end
  vim.t.docview_path = a.path
  vim.t.docview_kind = a.kind or "doc"
  set_review(a)
  drop_pristine()
  return {
    tab = api.nvim_get_current_tabpage(),
    tabnr = vim.fn.tabpagenr(),
    readonly = vim.bo.readonly,
    tabs = #api.nvim_list_tabpages(),
  }
end

-- Open (or reopen) the diff for one repo and agent pane in its own tab.
function M.open_diff(a)
  local tab = a.key and find_tab("diffreview_key", a.key)
  if tab then
    api.nvim_set_current_tabpage(tab)
    pcall(vim.cmd, "DiffviewClose")
    if api.nvim_tabpage_is_valid(tab) and #api.nvim_list_tabpages() > 1 then
      pcall(vim.cmd, "tabclose " .. api.nvim_tabpage_get_number(tab))
    end
  end
  -- DiffviewOpen makes its own tab and finds the repo from the cwd, which a new tab inherits.
  vim.cmd("tabnew")
  local scratch = api.nvim_get_current_tabpage()
  vim.cmd.tcd(vim.fn.fnameescape(a.top))
  local ok, err = pcall(vim.cmd, { cmd = "DiffviewOpen", args = { a.base } })
  if not ok then
    if api.nvim_tabpage_is_valid(scratch) and #api.nvim_list_tabpages() > 1 then
      pcall(vim.cmd, "tabclose " .. api.nvim_tabpage_get_number(scratch))
    end
    return tostring(err)
  end
  if api.nvim_get_current_tabpage() ~= scratch then
    vim.cmd.tcd(vim.fn.fnameescape(a.top))
  end
  if a.key then
    vim.t.diffreview_key = a.key
  end
  vim.t.docview_kind = "diff"
  vim.t.diffreview_head = a.head
  vim.t.diffreview_dirty = a.dirty
  set_review(a)
  drop_pristine()
  return true
end

function M.detach_uis()
  for _, u in ipairs(api.nvim_list_uis()) do
    pcall(vim.fn.chanclose, u.chan)
  end
  return 1
end

-- With no tab left to show: let the viewer go, and quit if nothing would be lost.
local function retire_if_empty()
  if labeled() > 0 then
    return
  end
  M.detach_uis()
  vim.schedule(function()
    if labeled() == 0 and #api.nvim_list_uis() == 0 and #M.state().modified == 0 then
      vim.cmd("qall")
    end
  end)
end

local function close_tabpage(tab, discard)
  local bufs = tab_bufs(tab)
  api.nvim_set_current_tabpage(tab)
  if tvar(tab, "docview_kind") == "diff" then
    pcall(vim.cmd, "DiffviewClose")
  end
  if api.nvim_tabpage_is_valid(tab) then
    if #api.nvim_list_tabpages() > 1 then
      vim.cmd("tabclose! " .. api.nvim_tabpage_get_number(tab))
    else
      -- The last tab cannot close; empty it instead (modified buffers stay, hidden).
      vim.cmd("silent! only!")
      vim.cmd("enew!")
      for _, k in ipairs({ "tab_label", "docview_kind", "docview_path", "diffreview_key", "diffreview_head",
        "diffreview_dirty", "review_comments_file", "review_block", "review_session", "review_marked" }) do
        pcall(api.nvim_tabpage_del_var, 0, k)
      end
    end
  end
  if discard then
    for _, b in ipairs(bufs) do
      if api.nvim_buf_is_valid(b) and vim.bo[b].buftype == "" and #vim.fn.win_findbuf(b) == 0 then
        pcall(api.nvim_buf_delete, b, { force = true })
      end
    end
  end
end

-- Close the tab whose a.field is a.value. With a.quit, also wipe its buffers, but
-- leave everything as it is when one has unsaved edits.
function M.close_tab(a)
  local tab = find_tab(a.field, a.value)
  if not tab then
    return { found = false, labeled = labeled() }
  end
  local modified = {}
  for _, b in ipairs(tab_bufs(tab)) do
    if is_modified(b) then
      modified[#modified + 1] = api.nvim_buf_get_name(b)
    end
  end
  if a.quit and #modified > 0 then
    return { found = true, closed = false, modified = modified, labeled = labeled() }
  end
  local here = api.nvim_get_current_tabpage()
  close_tabpage(tab, a.quit)
  if here ~= tab and api.nvim_tabpage_is_valid(here) then
    api.nvim_set_current_tabpage(here)
  end
  return { found = true, closed = true, modified = modified, labeled = labeled() }
end

-- Clear review comment marks in the diff tab with this key.
function M.clear_marks(key)
  local tab = find_tab("diffreview_key", key)
  if not tab then
    return 0
  end
  local ok, review = pcall(require, "config.review")
  if not ok then
    return 0
  end
  return review.clear_marks(tab)
end

function M.state()
  local tabs = {}
  local current = api.nvim_get_current_tabpage()
  for i, tab in ipairs(api.nvim_list_tabpages()) do
    local mod = false
    for _, b in ipairs(tab_bufs(tab)) do
      mod = mod or is_modified(b)
    end
    tabs[#tabs + 1] = {
      nr = i,
      label = tvar(tab, "tab_label"),
      kind = tvar(tab, "docview_kind"),
      path = tvar(tab, "docview_path"),
      key = tvar(tab, "diffreview_key"),
      block = tvar(tab, "review_block"),
      session = tvar(tab, "review_session"),
      comments = tvar(tab, "review_comments_file"),
      modified = mod,
      current = tab == current,
    }
  end
  local modified = {}
  for _, b in ipairs(api.nvim_list_bufs()) do
    if is_modified(b) then
      local name = api.nvim_buf_get_name(b)
      modified[#modified + 1] = name ~= "" and name or ("[buffer " .. b .. "]")
    end
  end
  return {
    tabs = tabs,
    labeled = labeled(),
    modified = modified,
    uis = vim.tbl_map(function(u)
      return { u.width, u.height, u.chan }
    end, api.nvim_list_uis()),
    columns = vim.o.columns,
    lines = vim.o.lines,
    readonly = vim.bo.readonly,
  }
end

-- :q on the last window of a tab closes the tab, not the shared Neovim. When no tab
-- is left, the viewer detaches (and Neovim quits if nothing is unsaved). :qa
-- detaches the viewer; :qa! still quits everything.
function M.quit(opts)
  opts = opts or {}
  if opts.write then
    vim.cmd(opts.write)
  end
  if opts.all then
    if opts.bang then
      vim.cmd("qall!")
      return
    end
    M.detach_uis()
    return
  end
  if #api.nvim_tabpage_list_wins(0) > 1 then
    vim.cmd(opts.bang and "close!" or "close")
    return
  end
  if not vim.t.tab_label and #api.nvim_list_tabpages() == 1 then
    M.detach_uis()
    retire_if_empty()
    return
  end
  close_tabpage(api.nvim_get_current_tabpage(), opts.bang)
  retire_if_empty()
end

local function define(name, opts)
  api.nvim_create_user_command(name, function(c)
    M.quit(vim.tbl_extend("force", opts, { bang = c.bang }))
  end, { bang = true, desc = "docview: close this tab, not the shared Neovim" })
end
define("DocviewQuit", {})
define("DocviewWq", { write = "write" })
define("DocviewXit", { write = "update" })
define("DocviewQuitAll", { all = true })
define("DocviewWqAll", { all = true, write = "wall" })

local abbrevs = {
  q = "DocviewQuit", quit = "DocviewQuit",
  wq = "DocviewWq", x = "DocviewXit", xit = "DocviewXit", exit = "DocviewXit",
  qa = "DocviewQuitAll", qall = "DocviewQuitAll", quita = "DocviewQuitAll", quitall = "DocviewQuitAll",
  wqa = "DocviewWqAll", wqall = "DocviewWqAll", xa = "DocviewWqAll", xall = "DocviewWqAll",
}
for from, to in pairs(abbrevs) do
  vim.cmd(("cnoreabbrev <expr> %s getcmdtype() ==# ':' && getcmdline() ==# '%s' ? '%s' : '%s'"):format(from, from, to, from))
end
vim.keymap.set("n", "ZZ", "<cmd>DocviewXit<cr>", { desc = "docview: write and close this tab" })
vim.keymap.set("n", "ZQ", "<cmd>DocviewQuit!<cr>", { desc = "docview: close this tab, discarding edits" })
vim.keymap.set("n", "<C-w>q", "<cmd>DocviewQuit<cr>", { desc = "docview: close window or tab" })
vim.keymap.set("n", "<C-w><C-q>", "<cmd>DocviewQuit<cr>", { desc = "docview: close window or tab" })

-- Tabline: one entry per tab, named by vim.t.tab_label; gt/gT switch.
function M.tabline()
  local parts = {}
  local current = api.nvim_get_current_tabpage()
  for i, tab in ipairs(api.nvim_list_tabpages()) do
    local label = tvar(tab, "tab_label")
    if not label then
      local name = api.nvim_buf_get_name(api.nvim_win_get_buf(api.nvim_tabpage_get_win(tab)))
      label = name ~= "" and vim.fn.fnamemodify(name, ":t") or "[No Name]"
    end
    local mod = false
    for _, b in ipairs(tab_bufs(tab)) do
      mod = mod or is_modified(b)
    end
    parts[#parts + 1] = (tab == current and "%#TabLineSel#" or "%#TabLine#") .. "%" .. i .. "T "
      .. tostring(label):gsub("%%", "%%%%") .. (mod and " +" or "") .. " "
  end
  return table.concat(parts) .. "%#TabLineFill#%T"
end

local TABLINE = "%!v:lua.require'docview_server'.tabline()"
function M.apply_tabline()
  -- bufferline (LazyVim) owns the tabline and hides it with one buffer; keep ours.
  local ok, config = pcall(require, "bufferline.config")
  if ok and type(config.options) == "table" then
    config.options.auto_toggle_bufferline = false
  end
  vim.o.tabline = TABLINE
  vim.o.showtabline = 2
end
api.nvim_create_autocmd("User", {
  group = group,
  pattern = { "VeryLazy", "LazyLoad" },
  callback = function()
    vim.schedule(M.apply_tabline)
  end,
})
api.nvim_create_autocmd({ "VimEnter", "TabNew", "TabEnter" }, {
  group = group,
  callback = function()
    if vim.o.tabline ~= TABLINE or vim.o.showtabline ~= 2 then
      vim.schedule(M.apply_tabline)
    end
  end,
})
M.apply_tabline()

return M
