---
name: markdown-plans
description: Default workflow for writing and presenting internal plans, documents, research notes, and review write-ups as readable Markdown in Neovim inside Rex. Use Mermaid when it clarifies the content. Requested artifacts and outward-facing deliverables use claude-artifacts.
---

# Markdown plans

Produce and open a Markdown file the person can scan, navigate by headings, and comment on using their normal Neovim configuration. This is the default for internal documents, not just implementation plans. Use sections appropriate to the document: a research note should foreground findings and evidence; a review should foreground findings and verification. Short conversational answers need no document; for a quick picture beside the conversation, use the show-pictures skill and `show FILE` instead. For requested artifacts or outward-facing deliverables, use claude-artifacts instead; follow explicit format and destination requests. Do not fall back to Plannotator unless explicitly requested.

## Write the plan

Lead with the recommended action and the outcome it produces. Explain the facts that determine the choice, then describe the work and how completion will be checked. Separate observed facts, assumptions, and unresolved decisions. Link supporting source files or primary documentation near the claims they support.

Write for the person deciding whether the approach makes sense. Name the actor, action, and observable result: "The worker retries failed deliveries three times" is more useful than "Ensure robust delivery." Use active voice, concrete nouns, and one topic per paragraph. Cut promotional adjectives, generic assurances, repeated conclusions, and sentences that do not change the reader's understanding or decision.

Use one `#` title, `##` sections, and `###` subsections only when needed. Give sections descriptive names that make sense in the heading sidebar. Keep paragraphs short and each step independently reviewable. Prefer a compact comparison table for genuine alternatives, numbered steps for sequence, and checkboxes only for trackable work. Use GitHub-style callouts sparingly for a decision or constraint that is easy to miss.

Scale the document to the decision. A useful starting structure is:

- Recommendation and expected outcome
- Relevant facts and constraints
- Proposed design or workflow
- Implementation steps and verification
- Open decisions, only when there are any

Include material risks and recovery steps alongside the affected work. Omit empty sections, chronological research notes, and a manually maintained table of contents: the sidebar derives it from headings. Keep the current proposal clear when revising; retain discussion in the saved review comments or task record.

Describe implementation steps as independently checkable outcomes. Include the affected component, the intended behavior, and evidence that will prove it works. Group setup with the deliverable it supports. Include exact commands, signatures, or code only when they resolve a meaningful ambiguity; reserve exhaustive execution instructions for a separate implementation plan when needed.

## Use diagrams when they explain something

Before adding a diagram, identify the reader's question it answers. Use a fenced `mermaid` block only when the relationships are easier to understand visually than in a sentence or small table. A short plan may need no diagram. Introduce each diagram with its question or takeaway; keep the implementation steps and acceptance criteria in text.

| Reader's question | Useful representation |
| --- | --- |
| What depends on what, or where does data go? | Flowchart with labeled relationships |
| Who calls whom, and in what order? | Sequence diagram |
| Which states exist, and what triggers a transition? | State diagram |
| How do stored entities relate? | Entity relationship diagram |
| Where are the system boundaries? | Context or container-level architecture view |
| How do options compare? | Usually a table |

Keep one concept and one level of abstraction per diagram. Show systems and actors for context, applications and stores for deployment-independent architecture, or internal components when that detail changes the decision. Name nodes after actual system concepts and label edges with their relationship or action. Show a failure or alternative path when it matters to the proposal. A diagram that merely turns the numbered task list into boxes adds little.

Prefer `flowchart TD` for narrow reading panes. Quote labels with punctuation. Split a crowded diagram by concern rather than shrinking its text. Use Mermaid's theme colors instead of hard-coded colors.

Validate diagrams with the installed `mmdc` command before presenting the document. For a Markdown input, render to a temporary output path outside the repository, inspect the generated images, and correct syntax or unreadable layout. Check `mmdc --help` when needed; its Markdown mode produces numbered image files.

Before opening review, check that the recommendation is clear from the opening paragraph, headings convey the plan's structure, each step has an observable completion condition, and every diagram answers its stated question. Remove duplicated explanations and diagrams whose information is clearer in text. If an image cannot be rendered or inspected, state that limitation rather than claiming it was validated.

## Design references

The writing and diagram guidance adapts ideas from these public skills; they are references, not dependencies or additional workflow requirements:

- [Writing clearly and concisely](https://github.com/softaworks/agent-toolkit/tree/main/skills/writing-clearly-and-concisely): concrete language, focused paragraphs, and editing for concision.
- [Mermaid diagrams](https://github.com/softaworks/agent-toolkit/tree/main/skills/mermaid-diagrams): diagram selection and focused views. Here, diagrams are optional and must answer a useful question.
- [C4 architecture](https://github.com/softaworks/agent-toolkit/tree/main/skills/c4-architecture): choose the level of detail for the audience and decision.
- [Superpowers writing plans](https://github.com/obra/superpowers/tree/main/skills/writing-plans): independently checkable tasks. Here, human review determines the level of implementation detail.

## Open for review in Rex

Use the installed `rex` skill for session discovery and current CLI syntax. Open the document in a floating pane on the originating conversation’s Rex tab when presenting it. Use a separate tab only when requested or floating panes are unavailable. If Rex is unavailable, retain the Markdown and report the opening limitation in chat. Keep the plan in the task's documentation location, or a task-specific temporary directory for a disposable trial. Use absolute paths for the plan and a separate comments file. Use a fresh comments file for a substantially revised plan and retain earlier feedback so quotes stay associated with their revision.

Open the document with `docview`, which uses the person's normal Neovim configuration:

```sh
docview open /absolute/plan.md --title 'Descriptive title' --comments /absolute/comments.md
```

Run it from the agent pane that should receive feedback; it reads `REX_SESSION` and `REX_BLOCK`. From anywhere else, pass `--session` and `--agent-block` with verified IDs, never IDs from an old example. It opens the document as a tab (`Doc: <title>`) in the session's editor float, shows the float on that pane's Rex tab, routes that tab's `Space r s` to that pane, and prints JSON. Check that `attached` is true in the output. `readonly: true` means the file is already open in another Neovim (the output's `note` says when that is an older docview); say so instead of claiming it is ready for edits.

The installed review controls are:

| Keys | Action |
| --- | --- |
| Space r t | Toggle the heading sidebar; j/k and Enter navigate it |
| Space r b | Open the table under the cursor with wrapped cells; q/Escape returns |
| Space r c | Comment on a visual selection or current line |
| Space r e | Open saved comments for editing |
| Space r s | Send all saved comments to the configured agent pane |
| Space r i | Preview a diagram or image |
| Space u m | Toggle Markdown rendering |

Save edits to the comments file before sending. Inline annotations last for the Neovim session; the comments file is the durable record. Source is revealed near the cursor and during selection so normal Vim editing remains available.

## Current documents and floating panes

`docview` keeps one background Neovim per Rex session. Each document, diff review and ticket draft is a tab page in it, and one floating viewer pane shows it. Tal switches tabs with gt/gT and hides or shows the float with Ctrl+Opt+Shift+Cmd+E. See [Rex floating documents](references/rex-floating-documents.md) for how it works and for the manual fallback.

- **Show a document again, or on another tab:** run `docview open` again. It switches to the document's tab and moves the float to the caller's Rex tab (or restores it if minimized); it never starts a second editor. Never attach a second viewer yourself with `nvim --remote-ui`: Neovim sizes itself to the smallest viewer, so the document stops fitting its pane.
- **Find what is open:** `docview ls` lists each open tab per session (label, kind, agent pane, unsaved edits), the float's state, and older per-document Neovims marked `legacy`. Use it after resuming instead of trusting remembered IDs. A hidden float does not mean its document is superseded.
- **Retire a document:** `docview close FILE --quit`. It exits 2 and leaves the tab open when there are unsaved edits; report that instead of forcing it. Without `--quit` it closes the tab but keeps unsaved edits loaded. Closing the last tab closes the float. It also retires legacy documents.

Closing the float never loses edits, because the background Neovim holds them. If a command reports that the editor did not answer, a prompt is waiting in the float: ask Tal to answer it.

Keep complementary documents current together. Retire a document only when the new revision actually replaces it, or when the person says it is no longer relevant; never infer supersession merely from age or a similar title. For replacements, preserve the reviewed source revision and feedback, open and verify the replacement first, then retire only the matching old document. Preserve source files, review comments, and unrelated panes.

## Handle feedback

Read each quoted passage against the reviewed revision. Address the comments and report the concrete changes or unresolved decisions. Feedback delivery, an empty comments file, or a positive remark does not authorize unrelated actions. Continue execution only within the person's existing grants.

Finish by identifying the document file and floating pane, noting any diagram validation limitations, and giving the few shortcuts needed for the next review action.
