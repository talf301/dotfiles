"""Exercise a table popup in a live Markdown buffer, preserving its source/view.

Usage: python3 review_table_live.py SOCKET MODULE_PATH TABLE_LINE
"""
import json
import subprocess
import sys

socket, module, line = sys.argv[1:]
code = '''(function()
  local origin, buf = vim.api.nvim_get_current_win(), vim.api.nvim_get_current_buf()
  local view, tick = vim.fn.winsaveview(), vim.api.nvim_buf_get_changedtick(buf)
  local popup
  local ok, err = pcall(function()
    vim.api.nvim_win_set_cursor(origin, {LINE, 0})
    local expected = vim.fn.winsaveview()
    local win, scratch = dofile(MODULE).open()
    assert(win and scratch, "No table popup")
    popup = win
    assert(not vim.bo[scratch].modifiable)
    local width = vim.api.nvim_win_get_width(win)
    local lines = vim.api.nvim_buf_get_lines(scratch, 0, -1, false)
    local border_width = vim.fn.strdisplaywidth(lines[1])
    for _, text in ipairs(lines) do
      assert(vim.fn.strdisplaywidth(text) == border_width, "Misaligned border")
      assert(vim.fn.strdisplaywidth(text) <= width, "Overflow")
    end
    vim.cmd("normal! 3j")
    assert(vim.fn.line(".") == 4, "Navigation failed")
    vim.api.nvim_exec_autocmds("VimResized", {})
    vim.fn.maparg("q", "n", false, true).callback()
    assert(not vim.api.nvim_buf_is_valid(scratch), "Scratch buffer leaked")
    assert(vim.api.nvim_get_current_win() == origin)
    assert(vim.deep_equal(vim.fn.winsaveview(), expected), "View not restored")
    assert(vim.api.nvim_buf_get_changedtick(buf) == tick, "Source changed")
  end)
  if popup and vim.api.nvim_win_is_valid(popup) then vim.api.nvim_win_close(popup, true) end
  vim.api.nvim_set_current_win(origin)
  vim.fn.winrestview(view)
  assert(ok, err)
  return "PASS: live wrapping, navigation, resize, close, source preservation"
end)()'''.replace('MODULE', json.dumps(module)).replace('LINE', str(int(line)))
result = subprocess.run(
    ['nvim', '--server', socket, '--remote-expr', 'luaeval(' + json.dumps(code) + ')'],
    text=True, capture_output=True, check=True,
)
print(result.stdout)
