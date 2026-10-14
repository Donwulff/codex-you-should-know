# You Should Know for Codex

Catch consequential oversights while Codex works. You Should Know runs an independent review of recent conversation evidence, shows at most one advisory note per check, and remembers notes you mark as known or dismiss.

For example, if you ask Codex to change one setting while preserving the others, and it replaces the entire settings file, the reviewer can flag the lost settings even when Codex claims the task succeeded.

Inspired by Claude Code's `cc-plugin-you-should-know`. Alerts appear automatically in your conversation. At the end of the turn, you can reply **tell me more**, **already knew that**, or **dismiss**. You do not need to invoke a skill. Codex hooks support text replies instead of Claude's native alert buttons.

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

After the turn ends, reply "tell me more", "already knew that", or "dismiss" for this note.
```

The reply options appear when the turn finishes. Send one of those replies as your whole message:

| Reply | Result |
| --- | --- |
| `tell me more` | Display the explanation and supporting transcript quotes. |
| `already knew that` | Mark the topic as known so it stays suppressed. |
| `dismiss` | Dismiss the note and keep it out of future alerts. |

These replies act on the most recent displayed note in the current session. Reading details leaves the reply options active. An ordinary task prompt ends that interaction; the plugin does not intercept later replies about an old note. Replies are inactive during work, so they cannot stop a running task. You can also use `ysk more`, `ysk known`, and `ysk dismiss` while the options are active.

Codex displays a **Hook stopped** heading for a handled reply because the hook answers it locally and stops normal prompt processing. The note stays outside the main agent's context, and the agent does not act on it. Alerts themselves never stop the task.

To revisit older notes or share one with the agent, use an ordinary request such as "Show my You Should Know notes" or "Explain You Should Know note 8f31a7c62b40." The bundled skill and command-line interface remain available for these requests.

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
