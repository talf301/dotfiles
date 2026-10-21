-- URL handler for board.md links: claudego://<sessionId> opens a session (go.sh), claudego://act/<verb>/<arg> runs
-- a cleanup action (act.sh). Run with no URL (launchd, every minute) it refreshes board.md: macOS lets this app,
-- not a bare launchd job, write into iCloud Drive. Build: see install.sh.
on run
	do shell script "$HOME/dotfiles/iar/board.py"
end run

on open location u
	set rest to text 12 thru -1 of u
	if rest starts with "act/" then
		set AppleScript's text item delimiters to "/"
		set parts to text items of rest
		do shell script "$HOME/dotfiles/iar/act.sh " & quoted form of (item 2 of parts) & " " & quoted form of (item 3 of parts) & " > /dev/null 2>&1 &"
	else
		do shell script "$HOME/dotfiles/iar/go.sh " & quoted form of rest & " > /dev/null 2>&1 &"
	end if
end open location
