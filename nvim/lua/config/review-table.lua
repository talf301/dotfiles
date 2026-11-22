-- A read-only, cell-wrapped view of the Markdown table under the cursor.
local M = {}

function M.cells(line)
  line = vim.trim(line)
  if line:sub(1, 1) == "|" then
    line = line:sub(2)
  end
  local cells, cell, escaped = {}, "", false
  for char in line:gmatch(".") do
    if escaped then
      cell = cell .. (char == "|" and "|" or "\\" .. char)
      escaped = false
    elseif char == "\\" then
      escaped = true
    elseif char == "|" then
      cells[#cells + 1], cell = vim.trim(cell), ""
    else
      cell = cell .. char
    end
  end
  if escaped then
    cell = cell .. "\\"
  end
  if cell ~= "" or line:sub(-1) ~= "|" or escaped then
    cells[#cells + 1] = vim.trim(cell)
  end
  return cells
end

local function wrap(text, width)
  local lines, line = {}, ""
  for word in text:gmatch("%S+") do
    if line ~= "" and vim.fn.strdisplaywidth(line .. " " .. word) <= width then
      line = line .. " " .. word
    else
      if line ~= "" then
        lines[#lines + 1], line = line, ""
      end
      for _, char in ipairs(vim.fn.split(word, "\\zs")) do
        if vim.fn.strdisplaywidth(line .. char) > width then
          lines[#lines + 1], line = line, ""
        end
        line = line .. char
      end
    end
  end
  lines[#lines + 1] = line
  return lines
end

function M.render(rows, width)
  local count, widths = #rows[1], {}
  if width < count * 5 + 1 then
    return nil, "Window too narrow for this table; widen it and try again"
  end
  for col = 1, count do
    widths[col] = 2
    for _, row in ipairs(rows) do
      widths[col] = math.max(widths[col], vim.fn.strdisplaywidth(row[col] or ""))
    end
  end
  local available = width - count * 3 - 1
  while true do
    local total, largest = 0, 1
    for col, size in ipairs(widths) do
      total = total + size
      if size > widths[largest] then
        largest = col
      end
    end
    if total <= available then
      break
    end
    widths[largest] = widths[largest] - 1
  end
  local lines, header_end = {}, 0
  local function border(left, middle, right)
    local parts = {}
    for _, size in ipairs(widths) do
      parts[#parts + 1] = string.rep("─", size + 2)
    end
    lines[#lines + 1] = left .. table.concat(parts, middle) .. right
  end
  border("╭", "┬", "╮")
  for index, row in ipairs(rows) do
    local wrapped, height = {}, 1
    for col, size in ipairs(widths) do
      wrapped[col] = wrap(row[col] or "", size)
      height = math.max(height, #wrapped[col])
    end
    for line = 1, height do
      local parts = {}
      for col, size in ipairs(widths) do
        local text = wrapped[col][line] or ""
        parts[#parts + 1] = " " .. text .. string.rep(" ", size - vim.fn.strdisplaywidth(text)) .. " "
      end
      lines[#lines + 1] = "│" .. table.concat(parts, "│") .. "│"
    end
    if index == 1 then
      header_end = #lines
    end
    if index < #rows then
      border("├", "┼", "┤")
    end
  end
  border("╰", "┴", "╯")
  return lines, header_end
end

function M.open()
  if vim.bo.filetype ~= "markdown" then
    return vim.notify("Place the cursor in a Markdown table", vim.log.levels.INFO)
  end
  local node = vim.treesitter.get_node({ ignore_injections = true })
  while node and node:type() ~= "pipe_table" do
    node = node:parent()
  end
  if not node then
    return vim.notify("Place the cursor in a Markdown table", vim.log.levels.INFO)
  end
  local first, _, last, last_col = node:range()
  local source = vim.api.nvim_buf_get_lines(0, first, last + (last_col > 0 and 1 or 0), false)
  local rows = {}
  for index, line in ipairs(source) do
    if index ~= 2 then
      rows[#rows + 1] = M.cells(line)
    end
  end
  local origin, view = vim.api.nvim_get_current_win(), vim.fn.winsaveview()
  local buf = vim.api.nvim_create_buf(false, true)
  vim.bo[buf].bufhidden = "wipe"
  local win
  local function layout()
    local width = math.max(1, math.min(vim.o.columns - 4, 140))
    local lines, header = M.render(rows, width)
    if not lines then
      lines, header = { header }, 0
    end
    local height = math.max(1, math.min(#lines, vim.o.lines - vim.o.cmdheight - 6))
    vim.bo[buf].modifiable = true
    vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
    vim.bo[buf].modifiable = false
    local ns = vim.api.nvim_create_namespace("review_table")
    vim.api.nvim_buf_clear_namespace(buf, ns, 0, -1)
    for line = 1, header do
      vim.api.nvim_buf_set_extmark(buf, ns, line - 1, 0, { end_row = line, hl_group = "Title", hl_eol = true })
    end
    local config = {
      relative = "editor",
      width = width,
      height = height,
      row = math.max(0, math.floor((vim.o.lines - height - 2) / 2)),
      col = math.floor((vim.o.columns - width - 2) / 2),
      border = "rounded",
      style = "minimal",
      title = " Table ",
      title_pos = "center",
      footer = " j/k · Ctrl-d/u · q close ",
      footer_pos = "center",
    }
    if win then
      vim.api.nvim_win_set_config(win, config)
    else
      win = vim.api.nvim_open_win(buf, true, config)
      vim.wo[win].wrap = false
      vim.wo[win].conceallevel = 0
      vim.wo[win].cursorline = true
    end
  end
  layout()
  local resize = vim.api.nvim_create_autocmd("VimResized", { callback = layout })
  vim.api.nvim_create_autocmd("BufWipeout", {
    buffer = buf,
    once = true,
    callback = function()
      vim.api.nvim_del_autocmd(resize)
    end,
  })
  local function close()
    vim.api.nvim_win_close(win, true)
    if vim.api.nvim_win_is_valid(origin) then
      vim.api.nvim_set_current_win(origin)
      vim.fn.winrestview(view)
    end
  end
  for _, key in ipairs({ "q", "<Esc>" }) do
    vim.keymap.set("n", key, close, { buffer = buf, nowait = true, desc = "Close table" })
  end
  for _, key in ipairs({ "<leader>rc", "<leader>re", "<leader>rs" }) do
    vim.keymap.set({ "n", "x" }, key, function()
      vim.notify("Close the table to review the original Markdown source", vim.log.levels.INFO)
    end, { buffer = buf })
  end
  return win, buf
end

return M
