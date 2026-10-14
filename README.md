# You Should Know for Codex

Catch consequential oversights while Codex works. You Should Know runs an independent review of recent conversation evidence, shows at most one advisory note per check, and remembers notes you mark as known or dismiss.

For example, if you ask Codex to change one setting while preserving the others, and it replaces the entire settings file, the reviewer can flag the lost settings even when Codex claims the task succeeded.

Inspired by Claude Code's `cc-plugin-you-should-know`. Alerts appear automatically in your conversation with numbered options. At the end of the turn, type a number and press Enter: **1** to learn more, **2** to mark the note as known, or **3** to dismiss it. After you read the details, choose **3** to dismiss or **4** to chat in the main session. You do not need to remember a phrase or invoke a skill. Numbered replies work in the Codex CLI without terminal-specific setup; the options are text hints, not clickable buttons.

## Install

You need:

- Linux, WSL, or macOS, with Python 3.11 or newer available as `python3`.
- Git and an authenticated Codex CLI with plugin and hook support. Tested with Codex CLI **0.162.1**.

No Python packages or separate API key are required. Each review uses your existing Codex authentication and consumes model usage. Selected conversation content, including tool results, is sent to the configured Codex model for review.

Run these commands from any directory:

```sh
codex plugin marketplace add iliaal/codex-you-should-know
codex plugin add you-should-know@you-should-know
```

Then activate the hooks:

1. Start a new Codex session.
2. Open `/hooks`.
3. Review and trust all four hooks:
   - `PostToolUse`: **You Should Know: review recent work**
   - `PostToolUse`: **You Should Know: display new notes**
   - `Stop`: **You Should Know: review completed turn**
   - `UserPromptSubmit`: **You Should Know: handle note replies**

Codex skips untrusted hooks. Their commands point to `you-should-know/.../scripts/you_should_know.py`.

The installation applies across projects using the same `CODEX_HOME`, normally `~/.codex`. Other machines or separate Codex profiles need their own installation.

To inspect review status, ask Codex:

```text
You Should Know status
```

An empty check history is normal immediately after installation. After you finish a turn, `status` shows whether a review completed or failed. A completed review can return no finding.

## Update

See [CHANGELOG.md](CHANGELOG.md) for version history and upgrade notes.

Exit Codex sessions using this plugin before updating. The updater can remove cached hook scripts that running sessions still reference.

If you installed from GitHub, refresh the marketplace and install its current plugin version:

```sh
codex plugin marketplace upgrade you-should-know
codex plugin add you-should-know@you-should-know
```

Start a new session after updating. Review and trust any new or modified definitions in `/hooks`. Your notes and configuration stay in the data directory. Updating from 0.1.1 adds the display and reply hooks; existing notes are preserved without replaying old alerts.

## Use

Continue working in Codex. After six completed tools, the plugin can start a background check. Completed findings appear in the conversation after a subsequent tool or at turn end. The end-of-turn hook waits for an outstanding review and displays its findings even when the review cooldown prevents another check. A final check also covers turns without tools.

Automatic checks are at least 120 seconds apart and limited to 30 attempts over a session's lifetime, including resumed sessions. Unchanged excerpts do not trigger another model request. A final check or an outstanding background check can delay completion while the reviewer finishes. Queued findings survive a restart and can appear when you resume the same session.

The reviewer looks for consequential mistakes, overlooked constraints, and tradeoffs supported by the transcript. It can return no note. An alert looks like this:

```text
You Should Know [8f31a7c62b40] · Your previous settings were deleted.

After the turn ends, reply with a number or phrase:
[1] Tell me more
[2] Already knew that
[3] Dismiss
```

The initial reply options appear when the turn finishes. Type the number or phrase, then press Enter:

| Reply | Result |
| --- | --- |
| `1` or `tell me more` | Display the explanation and supporting transcript quotes. |
| `2` or `already knew that` | Mark the topic as known so it stays suppressed. |
| `3` or `dismiss` | Dismiss the note and keep it out of future alerts. |

After you choose **1**, the plugin displays the explanation and evidence with only these options:

```text
[3] Dismiss
[4] Chat in main session
To include feedback, reply "4 your message".
```

The numbers keep the same meaning between menus. Chat becomes available only after you read the details. You can use `dismiss` or `chat in main session` instead of a number.

These replies act on the most recent displayed note in the current session. If you continue ordinary work, the shortcuts pause during that work and the note returns at turn end with compact choices. Before you read the details, the reminder looks like this:

```text
You Should Know [8f31a7c62b40] · Your previous settings were deleted.

[1] Tell me more · [2] Already knew that · [3] Dismiss
Reply with a number or phrase.
```

After you read the details, reminders retain only `[3] Dismiss` and `[4] Chat in main session`, including the feedback hint. The menu stage survives resuming the same session.

The reminder repeats at most once per turn until you mark the note as known, dismiss it, or share it using `4`. It appears near the prompt after the turn; it is not pinned during streaming output. Reminders survive resuming the same session and do not require another model review. If several notes are pending, the newest appears first. After you resolve or share it, the next pending note appears at the end of a later turn.

Replies are inactive during work, so they cannot stop a running task. When no note is active, or a choice is unavailable in the displayed menu, replies pass through as ordinary messages without sharing the note. The aliases `ysk more`, `ysk known`, `ysk dismiss`, and `ysk chat` follow the same menu stages.

The first three replies are handled locally. Codex displays a **Hook stopped** heading because the hook answers the reply and stops normal prompt processing. The note stays outside the main agent's context until you choose to share it. Alerts themselves never stop the task.

After reading the details, choose **4** or **chat in main session** to hand the selected note to Codex. The agent receives its summary, explanation, and supporting transcript quotes, then responds normally. Sharing does not mark the note as known or dismissed. Add feedback or a request after a space, such as `4 Please check whether this applies here.` The colon form, `4: your message`, still works. Your original message reaches the agent unchanged.

This reply does not stop the turn. Once the note is shared, continue the discussion normally; you do not need its ID or a skill command. Sharing a note alone does not ask Codex to change files, but you can include an explicit request to fix the issue.

To revisit older notes or share one with the agent, use an ordinary request such as "Show my You Should Know notes" or "Explain You Should Know note 8f31a7c62b40." The bundled skill and command-line interface remain available for these requests.

### Example: read a note, then discuss it

After an alert appears and Codex finishes its turn:

1. Read the expanded explanation privately:

   ```text
   1
   ```

   The plugin shows the explanation and supporting transcript quotes. The main agent has not received the note. You now see only `[3] Dismiss` and `[4] Chat in main session`, plus the hint for adding feedback.

2. Pass that context to the main agent, with your own feedback if needed:

   ```text
   4 This was intentional. Does the concern still apply?
   ```

   Codex receives the same full explanation and evidence you just read, along with your message. It responds normally, and you can continue the discussion without a note ID or skill command.

To share the note without adding a message, reply `4` alone. The phrases `tell me more` and `chat in main session: your message` work too. If you continue another task first, wait for the reminder at turn end before replying. Check its summary: the numbered choices always target the latest displayed note.

## Command-line access

For direct access to notes and settings without asking Codex, clone this repository:

```sh
git clone https://github.com/iliaal/codex-you-should-know.git
cd codex-you-should-know
```

The CLI uses the same data directory as the installed plugin:

```sh
python3 scripts/you_should_know.py notes
python3 scripts/you_should_know.py notes --all
python3 scripts/you_should_know.py show 8f31a7c62b40
python3 scripts/you_should_know.py know 8f31a7c62b40
python3 scripts/you_should_know.py dismiss 8f31a7c62b40
python3 scripts/you_should_know.py status
```

The identifier `8f31a7c62b40` is illustrative; replace it with an identifier from `notes`. The list contains up to 100 recent open notes, or 100 notes of any status with `--all`. Reading a note leaves its status unchanged.

To request a check immediately, supply a Codex rollout transcript and session identifier. Manual checks bypass the tool cadence, cooldown, and session attempt limit:

```sh
python3 scripts/you_should_know.py check --transcript /path/to/rollout.jsonl --session my-check
```

Identical topic labels, ignoring whitespace, are suppressed across sessions and projects sharing the data directory. Path punctuation and case remain significant. The reviewer also sees summaries of up to 40 recent notes to help avoid semantically equivalent alerts. Known and dismissed notes remain in that history. Notes do not expire, and there is no reopen action in this version.

## Configure

Create `config.toml` in `${CODEX_HOME:-~/.codex}/you-should-know`, or set `YSK_DATA_DIR` to choose a different data directory. These are the defaults:

```toml
enabled = true
every_n_tools = 6
min_interval_seconds = 120
max_checks_per_session = 30
timeout_seconds = 90
max_context_chars = 60000
```

Add `model = "your-model-id"` to select a reviewer model. Otherwise, automatic checks use the hook's session model when available, then the Codex CLI default. Manual checks accept `--model`; without a configured or explicit model, they use the CLI default. Set `enabled = false` to disable checks. Local note state survives plugin updates and removals. To inspect a separate data directory, place the option before the command:

```sh
python3 scripts/you_should_know.py --data-dir /path/to/ysk-data status
```

## Boundaries

The reviewer runs separately with a read-only sandbox. Tool use invalidates a review result. This does not promise that every Codex backend can hide every tool from the reviewer.

Checks retain the first three user messages and recent transcript activity within a character budget. A second byte limit bounds the complete model input, dropping older note summaries first and shortening the conversation with a visible marker if necessary. A note can still miss earlier context or be mistaken. Failed or timed-out checks do not interrupt the main task. Use `status` to inspect check state and errors. Native buttons and a separate alert panel would require changes to Codex itself.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `codex plugin` or `/hooks` is unavailable | Update Codex to a version with plugin and hook support. This project was tested with CLI 0.162.1. |
| The skill does not appear | Start a new Codex session after installation. Use the full name `$you-should-know:you-should-know`. |
| Hooks are installed but no checks run | Open `/hooks` and confirm all four plugin hooks are enabled and trusted. Check that the plugin's `enabled` setting is `true`. |
| A note reply reaches the main agent | Confirm the reply hook is trusted, wait for the reply options at turn end, and send an exact whole-message reply before another task prompt. |
| A hook is marked **modified** after an update | Review and trust the new definition through `/hooks`. |
| A check reports an authentication failure | Run `codex login`, then try another check. |
| No notes appear after a successful check | The reviewer may have found nothing consequential, or the topic may already be in your note history. |

## Development

Record user-visible changes under `[Unreleased]` in [CHANGELOG.md](CHANGELOG.md). For a release, move those entries into a dated version section and use that section for the GitHub release notes.

From a checkout, install the development tools with [uv](https://docs.astral.sh/uv/) and run the local checks:

```sh
uv --no-config sync --locked
uv --no-config run pytest --cov
uv --no-config run ruff check .
uv --no-config run ruff format --check .
uv --no-config run ty check .
```

To install from a working checkout, build a bundle first:

```sh
python3 scripts/package_plugin.py
codex plugin marketplace add "$(pwd)/dist/you-should-know"
codex plugin add you-should-know@you-should-know
```

The local bundle includes runtime files, documentation, and the license. It excludes development dependencies, local state, and Git history. Rebuilding replaces only output marked as belonging to this builder. Use a separate `CODEX_HOME` when testing an installation you want to keep separate from your normal profile.

`.codex-plugin/plugin.json` declares the native Codex skill and hooks. `.agents/plugins/marketplace.json` declares the marketplace. Keep the native format: Codex 0.162.1 skips plugin hooks when a portable root `plugin.json` is present.

Hook input and delivery follow the [Codex hooks documentation](https://learn.chatgpt.com/docs/hooks). Installation commands follow the installed CLI's `codex plugin --help` interface.

## License

[MIT](LICENSE). Copyright (c) 2026 Ilia Alshanetsky.
