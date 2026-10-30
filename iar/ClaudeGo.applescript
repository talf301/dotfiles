-- URL handler for board.md links: claudego://s/<sessionId> opens a session (go.sh), claudego://act/<verb>/<arg> runs
-- a cleanup action (act.sh). Only Launch Services starts it, for a link; BoardRefresh.app does the refreshes.
-- Build: see install.sh.
on open location u
	do shell script "echo \"$(date '+%F %T') link \"" & quoted form of u & " >> $HOME/Library/Logs/session-board-actions.log"
	set linkPath to text 12 thru -1 of u
	if linkPath starts with "act/" then
		set AppleScript's text item delimiters to "/"
		set parts to text items of linkPath
		do shell script "$HOME/dotfiles/iar/act.sh " & quoted form of (item 2 of parts) & " " & quoted form of (item 3 of parts) & " >> $HOME/Library/Logs/session-board-actions.log 2>&1 &"
	else if linkPath starts with "s/" then
		do shell script "$HOME/dotfiles/iar/go.sh " & quoted form of (text 3 thru -1 of linkPath) & " >> $HOME/Library/Logs/session-board-actions.log 2>&1 &"
	end if
end open location
