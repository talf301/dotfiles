---
name: show-pictures
description: Draw 1-4 big pictures (Mermaid or SVG) and put them in the Show pane beside the conversation with `show`. Use when a diagram, graph, plot or sketch explains how something works, what changed and why, or how options differ, better than prose.
---

# Show pictures

Help Tal understand with big pictures and few words, without withholding key details. If the request names a focus, show that; for a code change, show what changed and why. The pictures carry the explanation; the reply adds a few sentences around them.

## Steps

1. **Pick the pictures.** One idea per picture, 1-4 pictures. Name each picture's takeaway in one line before drawing it. Two simple pictures beat one crowded one.
2. **Draw each one** as a file in the session's picture dir. Get its literal path first:
   ```sh
   d="$HOME/.local/state/rexwork/sessions/${REX_SESSION#session:}/pictures"; mkdir -p "$d"; echo "$d"
   ```
   Write files to that literal path with your file-writing tool; shell guards may block redirects to computed paths. Outside Rex, use a temp dir. Name files `NN-topic.mmd` or `NN-topic.svg`.
3. **Send each one** in reading order: `show DIR/01-topic.mmd --title "Takeaway in a few words"`. `show` prints JSON whose `image` is the PNG it drew. Pictures go to your own Show pane (each agent pane has one); under rexchat they are drawn inline in the chat.
4. **Check the PNG.** Open `image` with your image viewer (Claude: the Read tool) when you can. Done when every label is readable at pane size and nothing overlaps or is clipped; otherwise fix and re-send.
5. **Reply** with one line per picture saying what it shows, plus any detail the pictures could not carry.

If `show` fails, say so and give the explanation in text.

## Legible in a narrow pane

The Show pane is 60-80 columns, about 600 px. Pictures are scaled down to fit, never up, so a wide picture means tiny text.

- **Tall, not wide.** Natural width at most 600 px. Mermaid: `flowchart TD`; at most 3 nodes side by side.
- **Few nodes.** At most 8 boxes per picture, labels of 1-4 words. Detail goes in the reply, not in the boxes.
- **Large text, high contrast.** Dark text on a white background. SVG text 20 px or more, titles 26 px or more.
- **Colour means something.** At most 3 colours, for example new vs old, or ok vs failing. Name them in a legend or the reply.
- **True labels.** File, function and number labels come from the code or this thread.

## Mermaid or SVG

| Content | Use |
|---|---|
| Flow, dependency, pipeline | Mermaid `flowchart TD` |
| Who calls whom, in order | Mermaid `sequenceDiagram`, at most 4 participants |
| States and transitions | Mermaid `stateDiagram-v2` |
| Branch and commit history | Mermaid `gitGraph` |
| Simple counts or shares | Mermaid `xychart-beta` or `pie` |
| Before/after, layouts, memory, timelines, anything spatial | Hand-written SVG |
| Real data plots | A plotting script that writes PNG or SVG |

Mermaid: quote labels with punctuation (`A["parse()"]`); use `classDef` for meaning colours. A `gitGraph` needs this first line, or its commit labels come out tiny and rotated:
`%%{init: {'gitGraph': {'rotateCommitLabel': false}, 'themeVariables': {'commitLabelFontSize': '18px', 'fontSize': '18px'}}}%%`

SVG: `viewBox="0 0 600 H"` with matching `width` and `height`, a white `<rect>` background, `font-family="Helvetica, Arial, sans-serif"`, monospace only for code names. Size each box to its longest label (about 0.6 x font size per character). Draw nodes as `<circle>` or `<rect>` and edges as `<line>` or `<path>` with arrowheads, not box-drawing characters in `<text>`. Plain shapes, thin dark strokes; no gradients, shadows or emoji.
