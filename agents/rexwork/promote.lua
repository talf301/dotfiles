-- Loaded by `rexwork promote` into the session's shared editor Neovim (docview's
-- background server), with its parameters passed as JSON data. Adds the confirm
-- step: <leader>rL or :RexworkFile takes a snapshot of the buffer, shows Tal what
-- would be filed, and on a typed "yes" sends that exact snapshot, its sha256 and
-- the one-time token to `rexwork promote --file --stdin`. The file on disk is never
-- what gets filed. Closing the float files nothing.
--
-- The Neovim is shared by every document of the session, so nothing here blocks
-- it: the question uses vim.ui.input and filing runs in the background. State is
-- per draft buffer, so two drafts can be open at once.
return function(p)
  -- The token comes in a 0600 one-shot file; read it and delete it.
  local fh = io.open(p.token_file, "r")
  if not fh then error("no token file " .. tostring(p.token_file)) end
  local token = fh:read("*a")
  fh:close()
  os.remove(p.token_file)
  if not token or token == "" then error("empty token file") end

  local function find_buf()
    local want = vim.fn.resolve(p.draft)
    for _, b in ipairs(vim.api.nvim_list_bufs()) do
      local name = vim.api.nvim_buf_get_name(b)
      if name ~= "" and vim.fn.resolve(name) == want then return b end
    end
    local b = vim.fn.bufadd(p.draft)
    vim.fn.bufload(b)
    return b
  end
  local buf = find_buf()

  local hint = " Space r L: file in Linear (type yes) | closing files nothing"
    .. (p.dry_run and " | DRY RUN" or "") .. " | ticket draft"
  local function set_winbar(text)
    for _, win in ipairs(vim.fn.win_findbuf(buf)) do
      vim.wo[win].winbar = text
    end
  end
  local group = vim.api.nvim_create_augroup("rexwork_promote_" .. buf, { clear = true })
  vim.api.nvim_create_autocmd("BufWinEnter", {
    group = group,
    buffer = buf,
    callback = function() set_winbar(vim.b[buf].rexwork_winbar or hint) end,
  })
  vim.b[buf].rexwork_winbar = hint
  vim.b[buf].rexwork_filed = nil
  set_winbar(hint)

  local function rexwork(args, request, on_exit)
    local cmd = { p.python, p.rexwork, "promote", "--session", p.session }
    vim.list_extend(cmd, args)
    local opts = { text = true, stdin = vim.json.encode(request) }
    if on_exit then
      return vim.system(cmd, opts, vim.schedule_wrap(on_exit))
    end
    return vim.system(cmd, opts):wait()
  end

  -- A float above the question that shows what would be filed.
  local function show_preview(lines)
    local pbuf = vim.api.nvim_create_buf(false, true)
    vim.api.nvim_buf_set_lines(pbuf, 0, -1, false, lines)
    vim.bo[pbuf].bufhidden = "wipe"
    local width = 20
    for _, l in ipairs(lines) do
      width = math.max(width, vim.fn.strdisplaywidth(l))
    end
    width = math.min(width + 2, math.max(20, vim.o.columns - 4))
    local height = math.min(#lines, math.max(1, vim.o.lines - 8))
    local ok, win = pcall(vim.api.nvim_open_win, pbuf, false, {
      relative = "editor", row = 1, col = math.max(0, math.floor((vim.o.columns - width) / 2)),
      width = width, height = height, style = "minimal", border = "rounded",
      title = " Ticket to file ", focusable = false, zindex = 60,
    })
    return function()
      if ok and vim.api.nvim_win_is_valid(win) then
        pcall(vim.api.nvim_win_close, win, true)
      end
    end
  end

  local used, asking, filing = false, false, false
  local function file_it()
    if vim.b[buf].rexwork_filed then
      vim.notify("rexwork: already filed: " .. vim.b[buf].rexwork_filed, vim.log.levels.WARN)
      return
    end
    if used then
      vim.notify("rexwork: this confirm key was used; run `rexwork promote` again", vim.log.levels.WARN)
      return
    end
    if asking or filing then
      vim.notify("rexwork: already asking or filing", vim.log.levels.WARN)
      return
    end
    -- The snapshot: exactly what Tal sees now. Edits or rewrites after this point are not filed.
    local content = table.concat(vim.api.nvim_buf_get_lines(buf, 0, -1, false), "\n") .. "\n"
    local digest = vim.fn.sha256(content)
    local res = rexwork({ "--preview" }, { content = content })
    local okp, pre = pcall(vim.json.decode, res.stdout or "")
    if res.code ~= 0 or not okp or type(pre) ~= "table" then
      vim.notify("rexwork: can't file this draft:\n" .. (res.stderr or "") .. (res.stdout or ""), vim.log.levels.ERROR)
      return
    end
    if pre.digest ~= digest then
      vim.notify("rexwork: digest mismatch between Neovim and rexwork; not filed", vim.log.levels.ERROR)
      return
    end
    local lines = {}
    if p.dry_run then lines[#lines + 1] = "DRY RUN: nothing is written to Linear." end
    lines[#lines + 1] = "Create a Linear issue in TAL, assigned to you, In Progress:"
    lines[#lines + 1] = "Title: " .. pre.title
    lines[#lines + 1] = ("Description: %d lines, %d characters, starting:"):format(pre.description_lines, pre.description_chars)
    for _, l in ipairs(pre.description_head or {}) do lines[#lines + 1] = "  | " .. l end
    if pre.description_lines > #(pre.description_head or {}) then lines[#lines + 1] = "  | ..." end
    lines[#lines + 1] = "Branch: " .. tostring(pre.branch)
    if type(pre.pending) == "string" and pre.pending ~= "" then
      lines[#lines + 1] = "Note: " .. pre.pending
    end
    local close_preview = show_preview(lines)
    asking = true
    vim.ui.input({ prompt = "Type yes to file exactly this: " }, function(answer)
      asking = false
      close_preview()
      if answer ~= "yes" then
        vim.notify("rexwork: not filed", vim.log.levels.INFO)
        return
      end
      if used or vim.b[buf].rexwork_filed then
        return
      end
      used, filing = true, true
      local args = { "--file", "--stdin" }
      if p.dry_run then args[#args + 1] = "--dry-run" end
      -- Exactly the snapshot taken before the question: later edits are not filed.
      local request = { token = token, content = content, digest = digest, dry_run = p.dry_run and true or false }
      rexwork(args, request, function(r)
        filing = false
        if r.code ~= 0 then
          vim.notify("rexwork promote failed:\n" .. (r.stderr or "") .. (r.stdout or ""), vim.log.levels.ERROR)
          return
        end
        local okj, out = pcall(vim.json.decode, r.stdout or "")
        if p.dry_run or not okj or type(out) ~= "table" or not out.ticket then
          vim.notify("rexwork (dry run):\n" .. (r.stdout or ""), vim.log.levels.INFO)
          return
        end
        vim.b[buf].rexwork_filed = out.ticket
        vim.b[buf].rexwork_winbar = " Filed " .. out.ticket .. ": " .. out.url .. "  (close with :q)"
        set_winbar(vim.b[buf].rexwork_winbar)
        local extra = ""
        if type(out.notes) == "table" and #out.notes > 0 then extra = "\n" .. table.concat(out.notes, "\n") end
        vim.notify("rexwork: filed " .. out.ticket .. " " .. out.url .. extra, vim.log.levels.INFO)
      end)
    end)
  end

  vim.keymap.set("n", "<leader>rL", file_it, { buffer = buf, desc = "rexwork: file this ticket in Linear" })
  vim.api.nvim_buf_create_user_command(buf, "RexworkFile", file_it, { desc = "rexwork: file this ticket in Linear" })
  return 1
end
