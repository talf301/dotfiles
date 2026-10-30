## When and how to ask Tal (required)

**When:** only in the cases listed in `~/dotfiles/iar/autonomy.md`. Decide everything else yourself and log it there.

**How:**
- Ask with `iar/ask`: the asking bead id, the question, 2–3 options, the default option, `reversible: yes|no`, `gated: yes|no`, and a deadline (default 4 h; none if gated). It creates the question bead and posts the Inbox question. Say whether the answer can change what is built.
- The question contains: the evidence, 2–3 options, your recommendation, and what you will do after each answer.
- If your last message asks Tal, its first line is `QUESTION: <qid> <the one question>`, where `<qid>` is the question bead id (`precog-…`). A `QUESTION:` line without one is malformed.
- Until `iar/ask` is installed: post the Inbox `:::question` yourself (`inbox-etiquette.md`) and use your own bead id as `<qid>`.
