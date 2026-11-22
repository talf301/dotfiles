-- Run from nvim/: nvim --headless -u NONE -l tests/review_spec.lua
vim.opt.runtimepath:prepend(vim.fn.getcwd())
local review = require("config.review")
local dir = vim.fn.tempname()
vim.fn.mkdir(dir, "p")
local file = dir .. "/nested/comments.md"
vim.env.REVIEW_FILE = file
local notices = {}
vim.notify = function(text)
  notices[#notices + 1] = text
end
local buf = vim.api.nvim_create_buf(true, false)
vim.api.nvim_set_current_buf(buf)
vim.api.nvim_buf_set_name(buf, dir .. "/source.md")
vim.bo[buf].filetype = "markdown"
vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "first", "é猫 target", "last", "```mermaid", "A --> B", "```" })
local function contents()
  return table.concat(vim.fn.readfile(file), "\n")
end
vim.ui.input = function(_, cb)
  cb("check this")
end
vim.api.nvim_win_set_cursor(0, { 1, 0 })
vim.cmd("normal! vj\27")
vim.api.nvim_win_set_cursor(0, { 3, 0 })
review.comment()
assert(contents():find("source.md:3-3", 1, true), contents())
assert(contents():find("```markdown\nlast\n```", 1, true), contents())

vim.fn.writefile({}, file)
vim.api.nvim_win_set_cursor(0, { 2, 0 })
vim.cmd("normal! vl")
review.comment()
assert(contents():find("```markdown\né猫\n```", 1, true), contents())
assert(not contents():find("target", 1, true))

vim.fn.writefile({}, file)
vim.api.nvim_win_set_cursor(0, { 4, 0 })
vim.cmd("normal! Vjj")
review.comment()
assert(contents():find("````markdown\n```mermaid\nA --> B\n```\n````", 1, true), contents())
local before = contents()
vim.ui.input = function(_, cb)
  cb(nil)
end
review.comment()
assert(contents() == before)
vim.ui.input = function(_, cb)
  vim.api.nvim_buf_set_lines(buf, 0, 1, false, { "changed" })
  cb("stale comment")
end
review.comment()
assert(contents() == before)

-- A failed save must not create an apparent inline comment.
vim.env.REVIEW_FILE = dir
local ns = vim.api.nvim_get_namespaces().review_comments
local count = #vim.api.nvim_buf_get_extmarks(buf, ns, 0, -1, {})
vim.ui.input = function(_, cb)
  cb("cannot save")
end
review.comment()
assert(#vim.api.nvim_buf_get_extmarks(buf, ns, 0, -1, {}) == count)
vim.env.REVIEW_FILE = file

vim.env.REX_SESSION = "session:test"
vim.env.REX_REVIEW_BLOCK = "block:test"
-- What `rex block call ... process` reports for a pane running an agent, and at a shell prompt.
local function proc(fg, extra)
  return { code = 0, stdout = vim.json.encode(vim.tbl_extend("force", { last_exit = vim.NIL, foreground = fg }, extra or {})) }
end
local agent = proc({ pid = 1, user_id = 501, name = "2.1.330", argv0 = "claude" })
local shell = proc({ pid = 2, user_id = 501, name = "zsh", argv0 = "-zsh" })
local process = agent
local calls, writes, pending = {}, {}, nil
local function fake_system(args, opts, cb)
  calls[#calls + 1] = { args = args, opts = opts }
  assert(args[5] == "session:test" and args[7] == "block:test", vim.inspect(args))
  if args[8] == "process" then
    cb(process)
  else
    writes[#writes + 1] = { args = args, opts = opts }
    pending = cb
  end
  return {}
end
vim.system = fake_system
local function settle()
  vim.wait(20, function()
    return false
  end)
end
review.send()
review.send()
settle()
assert(#writes == 1, "duplicate in-flight send")
assert(calls[1].args[8] == "process", "did not check the pane before typing into it")
assert(writes[1].args[8] == "write" and writes[1].args[9] == "-")
local payload = vim.json.decode(writes[1].opts.stdin).data
assert(payload:sub(1, 6) == "\27[200~" and payload:sub(-7) == "\27[201~\r")
pending({ code = 1, stderr = "missing target" })
settle()
assert(contents() == before, "failed delivery lost comments")
review.send()
settle()
assert(#writes == 2)
pending({ code = 0, stderr = "" })
settle()

-- A pane at a shell prompt would run the review as commands: refuse, even when forced.
local function refused(opts, expect)
  local n = #writes
  notices = {}
  review.send(opts)
  settle()
  assert(#writes == n, "sent to an unsafe pane")
  assert(notices[#notices] and notices[#notices]:find(expect, 1, true), vim.inspect(notices))
end
process = shell
refused(nil, "zsh prompt")
refused({ force = true }, "zsh prompt")
process = proc({ pid = 3, user_id = 501, name = "bash", argv0 = "/bin/bash" })
refused(nil, "bash prompt")
process = { code = 3, stderr = 'Error: block target "block:test" not found' }
refused({ force = true }, "no longer exists")
process = proc({ pid = 1, user_id = 501, name = "zsh" }, { last_exit = { exit_code = 0 } })
refused({ force = true }, "has exited")
-- Unknown foreground: refuse unless forced.
process = proc(vim.NIL)
refused(nil, "force=true")
review.send({ force = true })
settle()
assert(#writes == 3, "force did not send to an undetermined pane")
pending({ code = 0 })
settle()
process = { code = 1, stderr = "unknown method" }
refused(nil, "could not ask Rex")
-- Agents as Rex reports them on this machine (claude's name is its version), or under node.
assert(review.check_target(agent) == "ok")
assert(review.check_target(proc({ pid = 4, user_id = 501, name = "node", argv0 = "node", script = "/opt/codex/bin/codex.js" })) == "ok")
assert(review.check_target(proc({ pid = 5, user_id = 501, name = "codex", argv0 = "codex" })) == "ok")
local codex_bin = "/n/lib/node_modules/@openai/codex/vendor/aarch64-apple-darwin/bin/codex"
assert(review.check_target(proc({ pid = 7, user_id = 501, name = "codex", argv0 = codex_bin, path = codex_bin })) == "ok")
assert(review.check_target(proc({ pid = 8, user_id = 501, name = "2.1.329", argv0 = "claude", path = "/v/2.1.329" })) == "ok")
assert(review.check_target(proc({ pid = 9, user_id = 501, name = "node", argv0 = "node", script = "/x/bin/claude" })) == "ok")
-- A file argument named after an agent does not make its editor an agent.
assert(review.check_target(proc({ pid = 9, user_id = 501, name = "nvim", argv0 = "nvim", argv = { "nvim", "/tmp/codex" } })) == "other")
assert(review.check_target(proc({ pid = 10, user_id = 501, name = "jcode", argv0 = "jcode" })) == "ok")
assert(review.check_target(proc({ pid = 6, user_id = 501, name = "fish", argv0 = "-fish" })) == "shell")
-- Any other program (a Python REPL left after the agent exited) is refused unless forced.
local python = proc({ pid = 11, user_id = 501, name = "Python", argv0 = "python3", path = "/usr/bin/python3" })
assert(review.check_target(python) == "other")
assert(review.check_target(proc({ pid = 12, user_id = 501, name = "node", argv0 = "node", script = "/x/server.js" })) == "other")
process = python
refused(nil, "runs python3, not a known agent")
refused(nil, "force=true")
review.send({ force = true })
settle()
assert(#writes == 4, "force did not send to another program")
pending({ code = 0 })
settle()
-- The list is configurable.
table.insert(review.agents, "python3")
assert(review.check_target(python) == "ok")
table.remove(review.agents)
assert(review.check_target(python) == "other")
process = agent

vim.system = function()
  error("cannot spawn rex")
end
review.send()
local recovered = false
vim.system = function(args, _, cb)
  if args[8] == "process" then
    cb(agent)
  else
    recovered = true
    cb({ code = 0 })
  end
  return {}
end
review.send()
settle()
assert(recovered, "spawn error left delivery locked")

-- Opening comments without REVIEW_FILE must retain the original file identity.
vim.env.REVIEW_FILE = nil
vim.api.nvim_set_current_buf(buf)
review.edit()
local comments = vim.api.nvim_buf_get_name(0)
assert(vim.b.review_comments_file == comments)
vim.api.nvim_buf_set_lines(0, 0, -1, false, { "edited feedback" })
recovered = false
review.send()
assert(not recovered, "sent an unsaved edit")
vim.fn.mkdir(vim.fn.fnamemodify(comments, ":h"), "p")
vim.cmd.write()
review.send()
settle()
assert(recovered, "send from comments buffer used the wrong file")
vim.fn.delete(comments)

-- Comments in a diffview buffer quote the repo path and say which side they are on.
vim.env.REVIEW_FILE = file
vim.fn.writefile({}, file)
local RevType = { LOCAL = 1, COMMIT = 2, STAGE = 3 }
local old = vim.api.nvim_create_buf(true, false)
vim.api.nvim_buf_set_name(old, "diffview:///repo/.git/abc1234def0/src/app.py")
vim.api.nvim_buf_set_lines(old, 0, -1, false, { "x = 1", "y = 2" })
vim.bo[old].filetype = "python"
local new = vim.api.nvim_create_buf(true, false)
vim.api.nvim_buf_set_name(new, dir .. "/src/app.py")
vim.api.nvim_buf_set_lines(new, 0, -1, false, { "x = 10", "y = 2", "z = 3" })
vim.bo[new].filetype = "python"
local files = {
  { bufnr = old, path = "src/app.py", symbol = "a", rev = { type = RevType.COMMIT, abbrev = function() return "abc1234def0" end } },
  { bufnr = new, path = "src/app.py", symbol = "b", rev = { type = RevType.LOCAL } },
}
package.loaded["diffview"] = {}
package.loaded["diffview.vcs.rev"] = { RevType = RevType }
package.loaded["diffview.lib"] = {
  get_current_view = function()
    return { adapter = { ctx = { toplevel = dir } }, cur_entry = { layout = { files = function() return files end } } }
  end,
}
vim.ui.input = function(_, cb)
  cb("diff note")
end
vim.api.nvim_set_current_buf(new)
vim.api.nvim_win_set_cursor(0, { 2, 0 })
vim.cmd("normal! Vj")
review.comment()
assert(contents():find("## src/app.py:2-3 (working tree)\n```python\ny = 2\nz = 3\n```\ndiff note", 1, true), contents())
vim.api.nvim_set_current_buf(old)
vim.api.nvim_win_set_cursor(0, { 1, 0 })
review.comment()
assert(contents():find("## src/app.py:1-1 (old side, abc1234def0)\n```python\nx = 1\n```", 1, true), contents())
-- The file panel is not a file side; refuse instead of writing a wrong path.
local panel = vim.api.nvim_create_buf(true, false)
vim.api.nvim_buf_set_name(panel, "diffview:///panels/0/DiffviewFilePanel")
vim.api.nvim_set_current_buf(panel)
before = contents()
review.comment()
assert(contents() == before, "commented from the file panel")
-- Without REVIEW_FILE, all buffers of one diff share a comments file.
vim.env.REVIEW_FILE = nil
local shared = {}
for _, b in ipairs({ old, new }) do
  vim.api.nvim_set_current_buf(b)
  review.edit()
  shared[#shared + 1] = vim.api.nvim_buf_get_name(0)
  vim.cmd.close()
end
assert(shared[1] == shared[2], "diff buffers use different comments files")
assert(review.clear_marks() == 1)
assert(#vim.api.nvim_buf_get_extmarks(new, ns, 0, -1, {}) == 0)

-- <leader>rm marks the HEAD recorded when the diff was opened, never "whatever HEAD is now".
vim.t.diffreview_head = nil
assert(review.mark_args() == nil)
local ran = false
vim.system = function()
  ran = true
  return {}
end
notices = {}
review.mark()
assert(not ran and notices[1]:find("No diff opened", 1, true), "marked without a recorded HEAD")
vim.t.diffreview_head = "aaaa1111"
vim.t.diffreview_dirty = true
vim.env.REX_REVIEW_BLOCK = "block:test"
assert(vim.deep_equal(review.mark_args(),
  { "mark", "--json", "--opened-head", "aaaa1111", "--opened-dirty", "--agent-block", "block:test",
    "--session", "session:test" }),
  vim.inspect(review.mark_args()))
vim.t.diffreview_dirty = false
vim.env.REX_REVIEW_BLOCK = nil
assert(vim.deep_equal(review.mark_args(), { "mark", "--json", "--opened-head", "aaaa1111", "--session", "session:test" }))
vim.env.REX_SESSION = nil
assert(vim.deep_equal(review.mark_args(), { "mark", "--json", "--opened-head", "aaaa1111" }))

-- A shared docview Neovim: each tab sends to its own agent pane; environment variables,
-- which every open would overwrite, are never the target.
vim.g.docview_shared = true
vim.env.REX_SESSION = "session:stale"
vim.env.REX_REVIEW_BLOCK = "block:stale"
vim.env.REVIEW_FILE = file
package.loaded["diffview"] = nil
vim.cmd("tabonly!")
vim.cmd("enew!")
local tabs = {}
for i, name in ipairs({ "one", "two" }) do
  if i > 1 then
    vim.cmd("tabnew")
  end
  local f = dir .. "/" .. name .. ".comments.md"
  vim.fn.writefile({ "## " .. name }, f)
  vim.t.review_comments_file = f
  vim.t.review_block = "block:" .. name
  vim.t.review_session = "session:" .. name
  local b = vim.api.nvim_get_current_buf()
  vim.api.nvim_buf_set_name(b, dir .. "/" .. name .. ".md")
  vim.api.nvim_buf_set_lines(b, 0, -1, false, { name .. " line" })
  tabs[name] = { tab = vim.api.nvim_get_current_tabpage(), buf = b, file = f }
end
local sent = {}
vim.system = function(args, opts, cb)
  if args[8] == "process" then
    cb(agent)
  else
    sent[#sent + 1] = { session = args[5], block = args[7], data = vim.json.decode(opts.stdin).data }
    cb({ code = 0 })
  end
  return {}
end
for _, name in ipairs({ "one", "two", "one" }) do
  vim.api.nvim_set_current_tabpage(tabs[name].tab)
  review.send()
  settle()
  local s = sent[#sent]
  assert(s.block == "block:" .. name and s.session == "session:" .. name, vim.inspect(s))
  assert(s.data:find("## " .. name, 1, true), "sent another tab's comments")
end
-- A tab without a target refuses instead of using the environment.
vim.cmd("tabnew")
local n = #sent
notices = {}
review.send()
settle()
assert(#sent == n and notices[1]:find("no agent pane", 1, true), vim.inspect(notices))
vim.t.diffreview_head = "bbbb2222"
assert(vim.deep_equal(review.mark_args(), { "mark", "--json", "--opened-head", "bbbb2222" }))
vim.cmd("tabclose")
vim.api.nvim_set_current_tabpage(tabs.two.tab)
vim.t.diffreview_head = "cccc3333"
assert(vim.deep_equal({ unpack(review.mark_args(), 5) }, { "--agent-block", "block:two", "--session", "session:two" }))
-- Comments go to the tab's file, not REVIEW_FILE; marks clear only in their own tab.
for _, name in ipairs({ "one", "two" }) do
  vim.api.nvim_set_current_tabpage(tabs[name].tab)
  vim.api.nvim_win_set_cursor(0, { 1, 0 })
  review.comment()
  assert(table.concat(vim.fn.readfile(tabs[name].file), "\n"):find(name .. " line", 1, true))
end
local function marks(b)
  return #vim.api.nvim_buf_get_extmarks(b, ns, 0, -1, {})
end
assert(marks(tabs.one.buf) == 1 and marks(tabs.two.buf) == 1)
-- A commented buffer no longer shown in its tab is still cleared with it.
vim.api.nvim_set_current_tabpage(tabs.one.tab)
vim.cmd("enew")
review.clear_marks(tabs.one.tab)
assert(marks(tabs.one.buf) == 0, "marks left in the cleared tab")
assert(marks(tabs.two.buf) == 1, "cleared another tab's marks")

-- Space r d from tab two in the shared editor runs `diffreview open` for tab two's session,
-- pane and directory (never the server's environment), asynchronously: that command takes the
-- session's docview lock and picks the tab and viewer itself, so nothing opens in-editor.
vim.env.REX_SESSION = "session:server-start"
vim.env.REX_BLOCK = nil
vim.api.nvim_set_current_tabpage(tabs.two.tab)
local twodir = dir .. "/repo-two"
vim.fn.mkdir(twodir, "p")
vim.cmd.tcd(vim.fn.fnameescape(twodir))
local asked, answer
vim.system = function(args, opts, cb)
  asked = { args = args, cwd = opts.cwd }
  answer = cb
  return {}
end
package.loaded["docview_server"] = {
  open_diff = function()
    error("opened a diff in-editor, outside the session lock")
  end,
}
local ntabs = #vim.api.nvim_list_tabpages()
review.open_since_review()
assert(answer, "diffreview was not run asynchronously")
assert(vim.deep_equal({ unpack(asked.args, 2) },
  { "open", "--json", "--session", "session:two", "--agent-block", "block:two" }), vim.inspect(asked.args))
assert(vim.uv.fs_realpath(asked.cwd) == vim.uv.fs_realpath(twodir), asked.cwd)
notices = {}
answer({ code = 0, stdout = vim.json.encode({ message = "Reviewing x" }) })
settle()
assert(#vim.api.nvim_list_tabpages() == ntabs and notices[1] == "Reviewing x", vim.inspect(notices))
-- A tab with no agent pane runs nothing.
vim.cmd("tabnew")
asked, notices = nil, {}
review.open_since_review()
assert(asked == nil and notices[1]:find("no agent pane", 1, true), vim.inspect(notices))
vim.cmd("tabclose")
package.loaded["docview_server"] = nil

-- Plain Neovim: `diffreview base` with the tab's session and pane, then DiffviewOpen from the
-- originating tab with the key `diffreview close` uses, even if another tab is current by then.
vim.g.docview_shared = false
vim.api.nvim_create_user_command("DiffviewOpen", function(c)
  vim.cmd("tabnew")
  vim.t.fake_base, vim.t.fake_cwd = c.args, vim.fn.getcwd()
end, { nargs = "*" })
vim.api.nvim_set_current_tabpage(tabs.two.tab)
review.open_since_review()
assert(vim.deep_equal({ unpack(asked.args, 2) },
  { "base", "--json", "--session", "session:two", "--agent-block", "block:two" }), vim.inspect(asked.args))
vim.api.nvim_set_current_tabpage(tabs.one.tab)
answer({ code = 0, stdout = vim.json.encode({ repo = "/r", base = "b0", head = "h1", dirty = false,
  comments = "/s/review/r.two.diff-comments.md", tab_key = "r-1234.two", message = "ok" }) })
settle()
assert(vim.t.fake_base == "b0" and vim.uv.fs_realpath(vim.t.fake_cwd) == vim.uv.fs_realpath(twodir), vim.t.fake_cwd)
assert(vim.t.diffreview_key == "r-1234.two" and vim.t.review_block == "block:two"
  and vim.t.review_session == "session:two" and vim.t.review_comments_file == "/s/review/r.two.diff-comments.md")
print("review_spec: all checks passed")
vim.cmd("qa!")
