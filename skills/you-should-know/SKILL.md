---
name: you-should-know
description: Read, explain, acknowledge, or dismiss notes from the You Should Know plugin, inspect its status, or run a requested advisory check of a Codex transcript.
---

# You Should Know

The installed skill name is `you-should-know:you-should-know`.

Automatic hooks skip identified local `codex exec`, `codex e`, `codex exec resume`, and `codex review` processes. Explicit manual checks remain available. Process detection uses the current invocation, so historical transcript source metadata does not disable an interactive resume. Unrecognized clients and unavailable process information retain automatic hooks.

Alerts and their immediate replies work automatically through hooks. Users do not need to invoke this skill to receive alerts, read their details, mark them as known, dismiss them, or share them with the main agent. At turn end, the latest displayed note initially offers `1` or `tell me more`, `2` or `already knew that`, and `3` or `dismiss`. These replies are handled locally. After the user reads the details with `1`, the menu offers only `3` or `dismiss` and `4` or `chat in main session`. Chat is unavailable before reading details. The user types a number or phrase and presses Enter. Chat shares the selected note with the main agent and allows the user prompt to proceed. The user can append feedback or a request after a space, such as `4 This was intentional. Does the concern still apply?`. The forms `4: your message` and `chat in main session: your message` also work. The user's original message reaches the agent unchanged.

Prefer `ysk 1` through `ysk 4` when explaining replies. An assistant question, numbered or lettered choices, or an unavailable transcript requires the prefix so bare answers reach the assistant. The reply window expires after ten minutes by default. `ysk 4 your message` includes feedback. `ysk off` and `ysk off project` mute the session or resolved working directory; `ysk on` and `ysk on project` remove the corresponding mute. These controls do not require an active note and do not override the global `enabled` setting.

Ordinary task prompts pause the shortcuts during work. At turn end, the newest pending note returns with compact choices, at most once per turn, until the user marks it known, dismisses it, or shares it with `4`. Reading details retains the reminder with only dismiss and chat choices. Each note's menu stage survives continuing work and resuming the session. Older pending notes retain their own stages and appear on a later turn after the newest is resolved or shared. Replies pass through as ordinary messages when no note is active or the choice is unavailable at that stage. Reminders are repeated hook messages, not a pinned interface.

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

Findings remain hidden candidates until a separate confirmation checks subsequent work and disclosure. `status` reports pending, suppressed, and withdrawn candidate counts and reasons, recent outcomes, reported token usage, errors, latency, and hook sightings. An unconfirmed candidate is not a verified note. The default budget is 30 model calls per session per rolling hour, including confirmations; an explicit legacy lifetime limit still applies. Inactive state expires after 30 days by default.

`recent_checks` records the requested model ID and its source (`config`, `hook`, or `argument`) for model calls. `model_usage` groups calls, errors, tokens, and calls with unknown usage by model and source. A `cli_default` source with a null model means the reviewer CLI selected an unreported model. Null model metadata in older records is unknown. Do not infer those model IDs from the current chat or configuration. Review and confirmation can use different models if the chat's selection changes between checks. The plugin's configured model takes precedence over hook models and manual `--model` arguments.

Completed-turn reviews bypass the background cooldown while retaining call budgets and unchanged-excerpt detection. Setting `final_check = false` still disables those reviews and confirmations. Optional `review_instructions` adds up to 8,000 characters of focus to initial reviews; evidence validation and separate confirmation still apply.

Run `know` or `dismiss` when the user requests that disposition. Reading notes does not change their status. If the user asks for a manual check, use the requested transcript and session. Manual checks consume Codex model usage and can make two calls when a candidate needs confirmation. Do not guess a transcript path or scan unrelated sessions.

The hooks display notifications separately from the main agent. Choosing `chat in main session` or reading a note through this skill shares that note with the agent. Do not inject unrelated notes into the conversation or automatically act on an alert.

The default data directory is `${CODEX_HOME:-~/.codex}/you-should-know`. `YSK_DATA_DIR` overrides the default. The global `--data-dir PATH` option precedes the command when an explicit directory is needed. Read the root [README](../../README.md) for installation, configuration, and runtime limitations.
