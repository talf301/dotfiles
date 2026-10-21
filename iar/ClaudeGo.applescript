-- URL handler for claudego://<sessionId> links on board.md. Build: see install.sh.
on open location u
	do shell script "$HOME/dotfiles/iar/go.sh " & quoted form of (text 12 thru -1 of u) & " > /dev/null 2>&1 &"
end open location
