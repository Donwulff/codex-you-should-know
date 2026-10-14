# Changelog

## [Unreleased]

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

[Unreleased]: https://github.com/iliaal/codex-you-should-know/compare/v0.2.2...HEAD
[0.2.2]: https://github.com/iliaal/codex-you-should-know/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/iliaal/codex-you-should-know/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/iliaal/codex-you-should-know/releases/tag/v0.2.0
[0.1.1]: https://github.com/iliaal/codex-you-should-know/tree/3dee356fdd41ec0e8542862f094a09afbf1c07ba
