vim.opt.rtp:prepend(vim.fn.fnamemodify(debug.getinfo(1, "S").source:sub(2), ":p:h:h"))
local table_view = require("config.review-table")
assert(vim.deep_equal(table_view.cells("| a \\| b | 猫 |"), { "a | b", "猫" }))
local rows = {
  { "Step", "Conversation A", "Conversation B" },
  { "Agent makes the change", "Saves that email on the account.", "Saves that number on the account." },
  { "Unicode", "猫猫猫 and café", "https://example.com/averylongunbrokenpath" },
}
for _, width in ipairs({ 25, 50, 76, 120 }) do
  local lines = assert(table_view.render(rows, width))
  local expected = vim.fn.strdisplaywidth(lines[1])
  assert(expected <= width)
  for _, line in ipairs(lines) do
    assert(vim.fn.strdisplaywidth(line) == expected, line)
  end
  local columns = { "", "", "" }
  for _, line in ipairs(lines) do
    if line:sub(1, #"│") == "│" then
      local parts = vim.split(line, "│", { plain = true })
      for col = 1, 3 do
        columns[col] = columns[col] .. parts[col + 1]:gsub("%s", "")
      end
    end
  end
  for _, row in ipairs(rows) do
    for col, cell in ipairs(row) do
      assert(columns[col]:find(cell:gsub("%s", ""), 1, true), cell)
    end
  end
end
assert(table_view.render(rows, 10) == nil)
print("PASS: cell wrapping, aligned borders, Unicode, long tokens, narrow window")
vim.cmd.qa()
