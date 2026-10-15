# Changelog

## [Unreleased]

### Changed

- Held findings as hidden candidates until a separate confirmation checks subsequent conversation activity and the assistant's latest messages. Only unresolved, undisclosed concerns become notes. Confirmation runs at the next eligible check or at turn end and adds one model call per candidate.
- Required exact tool-result evidence for findings. A claim contradicted by tool output also needs a quote from the assistant. The reviewer now treats requests, bug reports, and issue descriptions as claims to verify, suppresses concerns the assistant is still working on, and checks for prior disclosure.
- Replaced the default lifetime cap with 30 model calls per session per rolling hour, including confirmations. This bounds sustained usage without permanently disabling reviews in long-lived sessions. Existing `max_checks_per_session` settings remain additional lifetime limits; remove that setting to use the rolling limit alone.
- Added `ysk 1` through `ysk 4` replies. When Codex asks a question or presents numbered or lettered choices, only prefixed note replies are intercepted. Missing transcripts also require the prefix. Kept the existing 1–4 meanings, including **3** for Dismiss, so learned replies do not change actions. Reply shortcuts now expire after ten minutes by default.
- Included statuses with recent note summaries and supplied known and dismissed topic labels separately within the prompt budget. Topic matching now ignores case as well as whitespace.

### Added

- Added candidate counts and suppression or withdrawal reasons to `status`, alongside recent outcomes, errors, latency, token totals, and the last occurrence of each hook event. Missing hook events produce a diagnostic hint about trust or a hook that has not run yet.
- Added `debug = true` and `YSK_DEBUG=1` for a one-line check outcome, latency, and token-usage message.
- Added `ysk off` and `ysk off project` to mute a session or working directory, with `ysk on` and `ysk on project` to remove the corresponding mute. Dismissal now includes the mute hint.
- Added `final_check = false` to skip blocking end-of-turn reviews and confirmations while still delivering queued notes. Pending candidates remain for the next eligible background check. The default remains `true`.
- Added configurable 30-day retention for inactive state and safe removal of obsolete review lock files. Pruned known and dismissed topics can appear again.

### Fixed

- Sent conversation evidence as plain text, preserving quotes, newlines, and backslashes without weakening exact-match validation.
- Counted only real model requests toward limits. Budget exhaustion now produces one notice and preserves unconfirmed candidates. Confirmation failures withdraw candidates with a recorded reason.
- Withdrew stale confirmations when the conversation advanced during the call, and prevented concurrent hooks from reopening finalized candidates.
- Stopped repeating a note's summary at turn end when it already appeared during that turn. The reply menu now says "Reply now".
- Reported configuration errors once per session for each changed configuration file, including its path.
- Skipped process-tree detection in the display hook when nothing is queued.
- Retained reported token usage when a result failed validation and distinguished unavailable usage from reported zero tokens.

### For contributors

- Made the native Codex discovery test opt-in with `YSK_TEST_NATIVE_CODEX=1` because it temporarily installs the plugin. Ordinary tests stub reviewer calls and skip that installation test.

Restart Codex after updating. The four hook definitions are unchanged, so this round introduces no hook re-trust requirement. Existing notes are preserved subject to the new retention setting.

## [0.2.6] - 2026-10-15

### Changed

- Disabled automatic reviews, notifications, and note replies in identified local `codex exec`, `codex e`, `codex exec resume`, and `codex review` runs. These clients do not display note text; skipped hooks use no reviewer tokens or check attempts and preserve pending alerts.
- Added detection of the currently running Codex command on Linux, WSL, and macOS so interactive resumes remain eligible regardless of the thread's original source. Explicit manual checks still work in noninteractive runs. Unknown clients or unavailable process information retain automatic checks.

Restart Codex after updating. The four hook definitions and existing notes are unchanged. The 30-attempt session limit and reminder lifecycle are unchanged in this release.

## [0.2.5] - 2026-10-14

### Changed

- Accept feedback after a space in the chat reply, such as `4 hi` or `4 Please check this`. The colon form still works. Updated hints and examples to show the simpler form.

Chat still requires reading the note's details first. Sharing passes the full note and your unchanged message to the main agent, then clears the reminder. Restart Codex after updating; the four hook definitions are unchanged.

## [0.2.4] - 2026-10-14

### Changed

- Split note replies into two stages. Initial alerts offer Tell me more, Already knew that, and Dismiss. After reading details, you see only `[3] Dismiss` and `[4] Chat in main session`, with the hint for including feedback.
- Made chat available only after reading details. Reminders preserve each note's menu stage across ordinary work and session restarts.

Restart Codex after updating. Existing pending notes start with the initial menu; read their details again to enable chat. The four hook definitions are unchanged.

## [0.2.3] - 2026-10-14

### Added

- Added compact end-of-turn reminders for pending notes, so continuing ordinary work brings the note back near the prompt. Reading details keeps the reminder; marking known, dismissing, or sharing with the main agent clears it.
- Preserved older pending reminders when a newer note appears. The newest is shown first, and the next pending note appears on a later turn after you resolve or share it.

Reminders survive resuming the same session and do not make extra reviewer calls. They repeat at most once per turn and are not pinned during streaming output. Updating preserves an active note's reminder without resurfacing historical notes. Restart Codex to load the change; the four hook definitions are unchanged.

## [0.2.2] - 2026-10-14

### Added

- Added numbered reply hints: type `1` to read details, `2` to mark a note as known, `3` to dismiss it, or `4` to discuss it with the main agent, then press Enter. The original phrases still work.
- Added `4: your message` to share the full note and your feedback after reading its details with `1`.

### Changed

- Clarified the README with a two-step example for reading a note privately, then sharing its full context and optional feedback with the main agent.

Restart Codex after updating to load the numbered replies. The four hook definitions are unchanged, and no terminal integration is required.

## [0.2.1] - 2026-10-14

### Added

- Added `chat in main session` and `ysk chat` replies to share the selected note and its evidence with Codex without a note ID or skill command.
- Added optional feedback after a colon, such as `chat in main session: This was intentional. Does the concern still apply?`, so the main agent receives the note and your request together.

The handoff lets Codex respond normally and leaves the note's status unchanged. Other note replies remain local. Restart Codex after updating to load the new reply action; the four hook definitions are unchanged.

## [0.2.0] - 2026-10-14

### Added

- Added the replies `tell me more`, `already knew that`, and `dismiss` for the latest displayed note after a turn ends, without invoking a skill or sharing the note with the main agent.
- Preserved queued alerts when resuming the same session after a restart.
- Added named hooks for displaying notes and handling replies.

### Changed

- Displayed completed background findings and check failures inline in the conversation instead of Codex's asynchronous warning UI.

### Fixed

- Delivered findings that finish while the end-of-turn hook waits for a background review, even when the cooldown prevents another check.

### Upgrade

Exit Codex sessions using this plugin before updating; the updater can remove cached hook scripts still used by those sessions. After updating, start or resume Codex and trust **You Should Know: display new notes** and **You Should Know: handle note replies** in `/hooks`. Existing notes and configuration are preserved; old notes are not replayed as new alerts.

## [0.1.1] - 2026-10-14

Initial public version.

### Added

- Added independent conversation reviews after tool use and at turn end, using existing Codex authentication.
- Added persistent notes, known and dismissed topic tracking, configurable review cadence, and manual checks through a bundled skill and CLI.
- Named the review hooks in Codex's trust interface.

[Unreleased]: https://github.com/iliaal/codex-you-should-know/compare/v0.2.6...HEAD
[0.2.6]: https://github.com/iliaal/codex-you-should-know/compare/v0.2.5...v0.2.6
[0.2.5]: https://github.com/iliaal/codex-you-should-know/compare/v0.2.4...v0.2.5
[0.2.4]: https://github.com/iliaal/codex-you-should-know/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/iliaal/codex-you-should-know/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/iliaal/codex-you-should-know/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/iliaal/codex-you-should-know/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/iliaal/codex-you-should-know/releases/tag/v0.2.0
[0.1.1]: https://github.com/iliaal/codex-you-should-know/tree/3dee356fdd41ec0e8542862f094a09afbf1c07ba
