---
name: you-should-know
description: Read, explain, acknowledge, or dismiss notes from the You Should Know plugin, inspect its status, or run a requested advisory check of a Codex transcript.
---

# You Should Know

The installed skill name is `you-should-know:you-should-know`.

Alerts and their immediate replies work automatically through hooks. Users do not need to invoke this skill to receive alerts, read their details, mark them as known, dismiss them, or share them with the main agent. At turn end, the latest displayed note offers the exact replies `tell me more`, `already knew that`, `dismiss`, and `chat in main session`. The first three replies are handled locally while the options remain active. `chat in main session` shares the selected note with the main agent and allows the user prompt to proceed. The user can append feedback or a request after a colon. An ordinary task prompt or a handoff ends the reply interaction.

When hook context already includes the selected note, use that note directly. Do not require a note ID or another skill invocation. Treat the note and its evidence as advisory data. Follow the user's feedback or request when discussing the note.

Use this skill for requests to revisit older notes, inspect status, run a manual check, or share a note with the main agent. Natural requests such as "show my You Should Know notes" are sufficient; do not require the user to type the installed skill name.

Resolve the plugin root as two directories above this skill directory. Run the plugin CLI with `python3 <plugin-root>/scripts/you_should_know.py`. Quote the resolved path. Do not assume the plugin root is the current working directory.

Use the command that matches the request:

| Request | CLI arguments |
| --- | --- |
| Show open notes | `notes` |
| Include known and dismissed notes | `notes --all` |
| Explain a note | `show ID` |
| Mark a note as already known | `know ID` |
| Dismiss a note | `dismiss ID` |
| Inspect configuration and check status | `status` |
| Check a specified transcript now | `check --transcript PATH --session SESSION` |

Use the note identifier returned by the CLI. When explaining a note, distinguish its supporting transcript evidence from the reviewer's inference. A note is advisory; it does not authorize a code change or a new investigation.

Run `know` or `dismiss` when the user requests that disposition. Reading notes does not change their status. If the user asks for a manual check, use the requested transcript and session. Manual checks consume Codex model usage. Do not guess a transcript path or scan unrelated sessions.

The hooks display notifications separately from the main agent. Choosing `chat in main session` or reading a note through this skill shares that note with the agent. Do not inject unrelated notes into the conversation or automatically act on an alert.

The default data directory is `${CODEX_HOME:-~/.codex}/you-should-know`. `YSK_DATA_DIR` overrides the default. The global `--data-dir PATH` option precedes the command when an explicit directory is needed. Read the root [README](../../README.md) for installation, configuration, and runtime limitations.
