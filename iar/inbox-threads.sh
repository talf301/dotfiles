#!/usr/bin/env zsh
# One JSON object per unresolved Plannotator Inbox thread, for /digest and the first mate. Reads the Inbox's own
# logs (~/.plannotator/inbox/projects/*/{messages,questions}.jsonl, where the newest record per id wins) and joins
# the session that started the thread with `claude agents`. `waiting_on` is "tal" when an agent spoke last and
# "session" when Tal replied last. `session_alive` is false when that session has no process: Tal's reply then
# waits until the session is resumed (`resume`). Prints nothing when the Inbox has never run.

JQ=/usr/bin/jq
INBOX=$HOME/.plannotator/inbox
[[ -d $INBOX/projects ]] || exit 0
url=$("$JQ" -r '.url // empty' "$INBOX/inbox.json" 2>/dev/null)
agents=$("$HOME/.local/bin/claude" agents --json --all 2>/dev/null || echo '[]')

for project in $INBOX/projects/*(N/); do
  [[ -f $project/messages.jsonl ]] || continue
  questions=$project/questions.jsonl
  [[ -f $questions ]] || questions=/dev/null
  "$JQ" -cn --slurpfile m "$project/messages.jsonl" --slurpfile q "$questions" \
    --argjson agents "$agents" --arg url "$url" --arg project "${project:t}" '
    def newest: group_by(.id) | map(max_by(.seq).record);
    ($m | newest) as $msgs
    | ($q | newest) as $qs
    | $msgs | group_by(.thread_id)[]
    | (map(select(.id == .thread_id))[0]) as $root
    | select($root != null and $root.resolved_at == null)
    | sort_by(.created_at) as $thread
    | ($thread | last) as $last
    | ($thread | map(.id)) as $ids
    | ($root.author.session // null) as $sid
    | ([$agents[] | select(.sessionId == $sid)][0]) as $a
    | {thread_id: $root.id, project: $project, subject: $root.subject,
       url: (if $url != "" then "\($url)#thread=\($root.id)" else null end),
       guide: ($root.guide != null), messages: length,
       open_questions: [$qs[] | select((.message_id as $id | $ids | index($id)) and .answer == null) | .prompt],
       last_from: $last.author.kind, last_at: $last.created_at,
       last_text: ($last.body // "" | split("\n")[0] | .[0:160]),
       waiting_on: (if $last.author.kind == "person" then "session" else "tal" end),
       session_id: $sid, session: ($a.name // null), session_state: ($a.state // null),
       session_alive: ($a.pid != null),
       resume: (if $sid != null and $a.pid == null
                then "cd \($a.cwd // $root.base_path // ".") && claude --bg --resume \($sid)" else null end)}'
done
