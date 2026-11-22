-- Local code review comments with explicit delivery to a named Rex agent.
local M = {}
-- Programs that read text typed into their pane as a message. Review comments go only to a pane
-- whose foreground process has one of these names (the basename, without a .js-style extension,
-- of its argv0, name, paths or script). Add to it from your config.
M.agents = { "claude", "codex", "jcode" }
local ns = vim.api.nvim_create_namespace("review_comments")
local sending = false

local function nonempty(value)
  if type(value) == "string" and value ~= "" then
    return value
  end
  return nil
end

-- True in a session's shared docview Neovim, where tabs belong to different agents and
-- environment variables say nothing about which one a tab's comments are for.
local function shared()
  return vim.g.docview_shared == true or vim.g.docview_shared == 1
end

-- The agent pane and Rex session that receive the current tab's comments: the buffer's,
-- else the tab's (docview and diffreview set them per tab). Outside a shared docview
-- Neovim, REX_REVIEW_BLOCK and REX_SESSION are the fallback.
function M.target()
  local block = nonempty(vim.b.review_block) or nonempty(vim.t.review_block)
  local session = nonempty(vim.b.review_session) or nonempty(vim.t.review_session)
  if not shared() then
    block = block or nonempty(vim.env.REX_REVIEW_BLOCK)
    session = session or nonempty(vim.env.REX_SESSION)
  end
  return block, session
end

-- The diffview view in the current tab, or nil.
local function diff_view()
  if not package.loaded["diffview"] then
    return nil
  end
  local ok, view = pcall(function()
    return require("diffview.lib").get_current_view()
  end)
  return ok and view or nil
end

local function review_file(buf)
  if vim.b[buf].review_comments_file then
    return vim.b[buf].review_comments_file
  end
  if vim.t.review_comments_file then
    return vim.t.review_comments_file
  end
  if not shared() and vim.env.REVIEW_FILE and vim.env.REVIEW_FILE ~= "" then
    return vim.env.REVIEW_FILE
  end
  local name = vim.api.nvim_buf_get_name(buf)
  local view = diff_view()
  if view then
    -- All buffers of one diff share a comments file.
    name = view.adapter.ctx.toplevel
  end
  if name == "" then
    return nil
  end
  local dir = vim.fn.stdpath("state") .. "/reviews"
  return dir .. "/" .. vim.fn.sha256(vim.fn.fnamemodify(name, ":p")) .. ".md"
end

-- For a buffer shown by diffview: the repo-relative path and a revision label.
-- The old side is labelled as such; line numbers are those of the side shown.
local function diff_location(view, buf)
  local entry = view.cur_entry
  for _, file in ipairs(entry and entry.layout:files() or {}) do
    if file.bufnr == buf then
      local rev = file.rev
      local RevType = require("diffview.vcs.rev").RevType
      local label
      if rev.type == RevType.LOCAL then
        label = "working tree"
      elseif rev.type == RevType.COMMIT then
        label = rev:abbrev(11)
      elseif rev.type == RevType.STAGE then
        label = "index"
      else
        label = tostring(rev.commit or "custom")
      end
      if file.symbol == "a" then
        label = "old side, " .. label
      end
      return file.path, label
    end
  end
  return nil
end

local function location(buf)
  local view = diff_view()
  if view then
    local path, rev = diff_location(view, buf)
    if path then
      return path, rev
    end
  end
  local name = vim.api.nvim_buf_get_name(buf)
  if name:match("^diffview://") then
    return nil
  end
  return vim.fn.fnamemodify(name, ":."), "working tree"
end

local function selection(buf)
  local mode = vim.fn.mode()
  if mode == "\22" then
    vim.notify("Block review selections are not supported", vim.log.levels.WARN)
    return nil
  end
  if mode == "v" or mode == "V" then
    local start, finish = vim.fn.getpos("v"), vim.fn.getpos(".")
    local first, last = math.min(start[2], finish[2]), math.max(start[2], finish[2])
    local opts = { type = mode, exclusive = vim.o.selection == "exclusive" }
    local ok, code = pcall(vim.fn.getregion, start, finish, opts)
    if not ok then
      vim.notify("Could not read the visual selection", vim.log.levels.ERROR)
      return nil
    end
    return first, last, code
  end
  local line = vim.fn.line(".")
  return line, line, vim.api.nvim_buf_get_lines(buf, line - 1, line, false)
end

local function append(path, entry)
  local made, mkdir_err = pcall(vim.fn.mkdir, vim.fn.fnamemodify(path, ":h"), "p")
  if not made then
    return nil, mkdir_err
  end
  local fd, err = io.open(path, "a")
  if not fd then
    return nil, err
  end
  local ok, write_err = fd:write(table.concat(entry, "\n"), "\n")
  local closed, close_err = fd:close()
  return ok and closed, write_err or close_err
end

function M.comment()
  local buf = vim.api.nvim_get_current_buf()
  local file = review_file(buf)
  if not file then
    return vim.notify("Review comments need a named file", vim.log.levels.WARN)
  end
  local first, last, code = selection(buf)
  if not first then
    return
  end
  local tick = vim.api.nvim_buf_get_changedtick(buf)
  local tab = vim.api.nvim_get_current_tabpage()
  local path, rev = location(buf)
  if not path then
    vim.cmd("normal! \27")
    return vim.notify("Comment from a file side of the diff, not this panel", vim.log.levels.WARN)
  end
  local filetype = vim.bo[buf].filetype
  vim.cmd("normal! \27")
  vim.ui.input({ prompt = ("Comment %s:%d-%d: "):format(path, first, last) }, function(text)
    if not text or text == "" then
      return
    end
    if not vim.api.nvim_buf_is_valid(buf) or vim.api.nvim_buf_get_changedtick(buf) ~= tick then
      return vim.notify("Document changed while entering comment; please retry", vim.log.levels.WARN)
    end
    local longest = 2
    for run in table.concat(code, "\n"):gmatch("`+") do
      longest = math.max(longest, #run)
    end
    local fence = string.rep("`", longest + 1)
    local entry = {
      ("## %s:%d-%d (%s)"):format(path, first, last, rev),
      fence .. filetype,
    }
    vim.list_extend(entry, code)
    vim.list_extend(entry, { fence, text, "" })
    local ok, err = append(file, entry)
    if not ok then
      return vim.notify("Could not save review comment: " .. tostring(err), vim.log.levels.ERROR)
    end
    vim.api.nvim_buf_set_extmark(buf, ns, last - 1, 0, {
      virt_lines = { { { "  💬 " .. text, "DiagnosticInfo" } } },
    })
    -- Remember the buffer, so clear_marks() for this tab finds it when it is not shown.
    if vim.api.nvim_tabpage_is_valid(tab) then
      local marked = vim.t[tab].review_marked or {}
      if not vim.tbl_contains(marked, buf) then
        marked[#marked + 1] = buf
        vim.t[tab].review_marked = marked
      end
    end
  end)
end

function M.edit()
  local file = review_file(vim.api.nvim_get_current_buf())
  if file then
    vim.cmd.split(vim.fn.fnameescape(file))
    vim.b.review_comments_file = vim.api.nvim_buf_get_name(0)
  end
end

local shells = { zsh = true, bash = true, sh = true, fish = true, dash = true, ksh = true, tcsh = true, csh = true }

local function base(value)
  if type(value) ~= "string" or value == "" then
    return nil
  end
  return vim.fs.basename((value:gsub("^%-", "")))
end

local function program_name(proc)
  return base(proc.argv0) or ""
end

-- The agent name from M.agents that the foreground process matches, or nil. Rex reports argv0,
-- name, path, invoked_path and, for an interpreter, script. Other arguments never count: a file
-- named after an agent must not make its editor look like one.
local function agent_name(proc)
  local wanted = {}
  for _, a in ipairs(M.agents) do
    wanted[a] = true
  end
  local values = {}
  for _, key in ipairs({ "argv0", "name", "path", "invoked_path", "script" }) do
    values[#values + 1] = proc[key]
  end
  for _, v in ipairs(values) do
    local b = base(v)
    if b then
      b = b:gsub("%.[cm]?[jt]s$", "")
      if wanted[b] then
        return b
      end
    end
  end
  return nil
end

-- Decide from `rex block call ... process` whether text typed into the pane reaches an agent
-- that reads it as a message. Returns "ok" (a known agent), "other" (some other program),
-- "shell", "gone" or "unknown", and a description.
function M.check_target(result)
  if result.code ~= 0 then
    local err = vim.trim(result.stderr or "")
    if err:find("not found", 1, true) then
      return "gone", "the agent pane no longer exists (" .. err .. ")"
    end
    return "unknown", "could not ask Rex what runs in the agent pane: " .. (err ~= "" and err or "unknown error")
  end
  local ok, info = pcall(vim.json.decode, result.stdout or "")
  if not ok or type(info) ~= "table" then
    return "unknown", "Rex gave no readable process information for the agent pane"
  end
  if type(info.last_exit) == "table" then
    return "gone", "the agent pane's program has exited"
  end
  local fg = info.foreground
  if type(fg) ~= "table" then
    return "unknown", "Rex could not tell what runs in the foreground of the agent pane"
  end
  local argv0, name = program_name(fg), type(fg.name) == "string" and fg.name or ""
  if shells[argv0] or shells[name] then
    return "shell", ("the agent pane is at a %s prompt; the agent seems to have exited"):format(shells[argv0] and argv0 or name)
  end
  local agent = agent_name(fg)
  if agent then
    return "ok", agent
  end
  local running = argv0 ~= "" and argv0 or name ~= "" and name or "an unnamed program"
  if base(fg.script) then
    running = running .. " " .. base(fg.script)
  end
  return "other", ("the agent pane runs %s, not a known agent (%s)"):format(running, table.concat(M.agents, ", "))
end

-- Type the saved comments into the agent pane. Sends only when the pane's foreground program is
-- a known agent (M.agents). send({ force = true }) also sends to another program or when Rex
-- cannot tell what runs there; a shell, whose input would run as commands, or a missing or
-- exited pane is refused even then.
function M.send(opts)
  opts = opts or {}
  if sending then
    return vim.notify("Review delivery already in progress", vim.log.levels.WARN)
  end
  local block, session = M.target()
  if not block or not session then
    if shared() then
      return vim.notify("This tab has no agent pane for comments; reopen it with docview or diffreview from the agent's pane",
        vim.log.levels.WARN)
    end
    return vim.notify("REX_SESSION and REX_REVIEW_BLOCK are required", vim.log.levels.WARN)
  end
  local file = review_file(vim.api.nvim_get_current_buf())
  if not file then
    return vim.notify("Review file is unavailable", vim.log.levels.WARN)
  end
  for _, buf in ipairs(vim.api.nvim_list_bufs()) do
    local name = vim.api.nvim_buf_get_name(buf)
    local same = (vim.uv.fs_realpath(name) or name) == (vim.uv.fs_realpath(file) or vim.fn.fnamemodify(file, ":p"))
    if same and vim.bo[buf].modified then
      return vim.notify("Save the comments file before sending", vim.log.levels.WARN)
    end
  end
  local fd = io.open(file, "r")
  if not fd then
    return vim.notify("No saved review comments yet", vim.log.levels.INFO)
  end
  local text = fd:read("*a")
  fd:close()
  if text == "" then
    return vim.notify("No saved review comments yet", vim.log.levels.INFO)
  end
  -- Terminal control bytes in a quoted document must not escape bracketed paste.
  text = text:gsub("[%z\1-\8\11-\31\127]", "")
  local payload = "\27[200~Review comments:\n\n" .. text .. "\27[201~\r"
  sending = true
  local function deliver(program)
    local started, err = pcall(vim.system, { "rex", "block", "call", "-s", session, "-b", block, "write", "-" }, {
      stdin = vim.json.encode({ data = payload }),
      text = true,
    }, function(result)
      sending = false
      vim.schedule(function()
        if result.code == 0 then
          vim.notify("Review sent to Rex agent (" .. program .. ")", vim.log.levels.INFO)
        else
          vim.notify("Could not send review to Rex: " .. (result.stderr or "unknown error"), vim.log.levels.ERROR)
        end
      end)
    end)
    if not started then
      sending = false
      vim.notify("Could not start Rex delivery: " .. tostring(err), vim.log.levels.ERROR)
    end
  end
  local started, err = pcall(vim.system, { "rex", "block", "call", "-s", session, "-b", block, "process" }, {
    text = true,
  }, function(result)
    vim.schedule(function()
      local verdict, what = M.check_target(result)
      if verdict == "ok" then
        return deliver(what)
      elseif (verdict == "unknown" or verdict == "other") and opts.force then
        return deliver("not checked as an agent, forced")
      end
      sending = false
      local force = " To send anyway: :lua require('config.review').send({force=true})"
      local hints = {
        unknown = force,
        other = force,
        shell = " Restart the agent in that pane, then send again.",
        gone = " Reopen the review from the agent's pane, then send again.",
      }
      local hint = hints[verdict]
      vim.notify(("Review not sent: %s.%s"):format(what, hint), vim.log.levels.ERROR)
    end)
  end)
  if not started then
    sending = false
    vim.notify("Could not start Rex delivery: " .. tostring(err), vim.log.levels.ERROR)
  end
end

-- Review since last review: thin wrappers over the diffreview CLI, which owns the marker.
-- Session and agent pane always go as arguments: in a shared docview Neovim the process
-- environment belongs to no tab (docview removes REX_BLOCK from it).
local function diffreview(args, cb, cwd)
  local exe = vim.fn.exepath("diffreview")
  if exe == "" then
    exe = vim.fn.expand("~/.local/bin/diffreview")
  end
  local cmd = vim.list_extend({ exe }, args)
  local ok, err = pcall(vim.system, cmd, { text = true, cwd = cwd or vim.fn.getcwd() }, function(result)
    vim.schedule(function()
      local data = result.code == 0 and pcall(vim.json.decode, result.stdout or "") and vim.json.decode(result.stdout)
      if not data then
        local msg = vim.trim((result.stderr or "") .. (result.stdout or ""))
        return vim.notify("diffreview failed: " .. msg, vim.log.levels.ERROR)
      end
      cb(data)
    end)
  end)
  if not ok then
    vim.notify("Could not run diffreview: " .. tostring(err), vim.log.levels.ERROR)
  end
end

-- Remove inline comment marks from one tab's buffers (default: the current tab): those
-- shown in it and those commented on from it. diffreview calls this after archiving the
-- comments; other tabs of a shared Neovim keep theirs.
function M.clear_marks(tab)
  if not tab or tab == 0 then
    tab = vim.api.nvim_get_current_tabpage()
  end
  local bufs = {}
  for _, win in ipairs(vim.api.nvim_tabpage_list_wins(tab)) do
    bufs[vim.api.nvim_win_get_buf(win)] = true
  end
  for _, b in ipairs(vim.t[tab].review_marked or {}) do
    bufs[b] = true
  end
  for b in pairs(bufs) do
    if vim.api.nvim_buf_is_valid(b) then
      vim.api.nvim_buf_clear_namespace(b, ns, 0, -1)
    end
  end
  vim.t[tab].review_marked = nil
  return 1
end

-- Arguments for `diffreview mark` that mark the HEAD this tab's diff was opened at, or nil.
function M.mark_args()
  local head = vim.t.diffreview_head
  if type(head) ~= "string" or head == "" then
    return nil
  end
  local args = { "mark", "--json", "--opened-head", head }
  if vim.t.diffreview_dirty == true then
    table.insert(args, "--opened-dirty")
  end
  local block, session = M.target()
  if block then
    vim.list_extend(args, { "--agent-block", block })
  end
  if session then
    vim.list_extend(args, { "--session", session })
  end
  return args
end

-- Advance the last-reviewed marker to the HEAD this diff was opened at (not today's HEAD,
-- which may hold commits made after the diff was opened).
function M.mark()
  local args = M.mark_args()
  if not args then
    return vim.notify(
      "No diff opened from diffreview in this tab; open one (Space r d or diffreview open) or run `diffreview mark` in a shell",
      vim.log.levels.WARN
    )
  end
  diffreview(args, function(data)
    M.clear_marks()
    local warn = data.dirty or data.head_moved
    vim.notify(data.message, warn and vim.log.levels.WARN or vim.log.levels.INFO)
  end)
end

-- Open the diff since the last review in this Neovim. Comments go to the session's review file.
function M.open_since_review()
  -- The diff belongs to this tab's agent pane, session and directory, whatever tab is
  -- current when diffreview answers.
  local block, session = M.target()
  local origin = vim.api.nvim_get_current_tabpage()
  local cwd = vim.fn.getcwd()
  if shared() then
    -- In the shared editor, run `diffreview open` like any caller: it takes the session's
    -- docview lock, then picks the tab and places the viewer itself. It talks back to this
    -- Neovim over its socket, so it must run asynchronously (diffreview() never waits).
    if not block or not session then
      return vim.notify("This tab has no agent pane for comments; run `diffreview open` from the agent's pane",
        vim.log.levels.WARN)
    end
    return diffreview({ "open", "--json", "--session", session, "--agent-block", block }, function(data)
      vim.notify(data.message, vim.log.levels.INFO)
    end, cwd)
  end
  local args = { "base", "--json" }
  if session then
    vim.list_extend(args, { "--session", session })
  end
  if block then
    vim.list_extend(args, { "--agent-block", block })
  end
  diffreview(args, function(data)
    if vim.api.nvim_tabpage_is_valid(origin) then
      vim.api.nvim_set_current_tabpage(origin)
    end
    vim.cmd({ cmd = "DiffviewOpen", args = { data.base } })
    vim.t.review_block, vim.t.review_session = block, session
    vim.t.diffreview_key = data.tab_key
    if type(data.comments) == "string" then
      vim.t.review_comments_file = data.comments
    end
    vim.t.diffreview_head = data.head
    vim.t.diffreview_dirty = data.dirty
    vim.notify(data.message, vim.log.levels.INFO)
  end, cwd)
end

return M
