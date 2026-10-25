---
name: setup
description: Install or remove the optional You Should Know keyboard shortcuts for Codex CLI.
---

# You Should Know shortcut setup

Use this workflow when the user requests You Should Know shortcut setup or removal. A question about shortcuts does not request a configuration change. Plugin installation alone does not enable shortcuts.

Resolve the plugin root as two directories above this skill directory. Run `python3 "<plugin-root>/scripts/you_should_know.py" shortcuts install` for setup. Run the same command with `remove` instead of `install` for removal. Replace `<plugin-root>` with the resolved path. Do not assume the current working directory is the plugin root.

The helper targets `${CODEX_HOME:-~/.codex}/config.toml`. Use `--config PATH` only when the user requests another Codex configuration file. The plugin's `--data-dir` option does not select the shortcut configuration. These commands do not access the notes database or call the reviewer.

The helper preserves existing settings and comments. Matching settings that already exist remain unowned. Conflicting settings stop installation without changing the file. Report the conflicting keys instead of replacing them manually. Removal deletes only unchanged additions marked by the helper. Report any modified additions that removal preserves.

After a successful change, tell the user to restart Codex. In Codex CLI 0.164.0, press **Alt+Y**, release the keys, then press **1**, **2**, **3**, or **4**. The digit submits the corresponding `ysk N` message without Enter. These are sequential key presses.

| Digit | Action |
| --- | --- |
| 1 | Read the displayed note's explanation and evidence. |
| 2 | Mark the displayed note as already known. |
| 3 | Dismiss the displayed note. |
| 4 | Share the displayed note with the main agent after reading its details. |

Use the choices offered by the current note menu. The shortcuts retain the existing reply window and menu stages. If no matching note action is available, the submitted text can reach the main agent as an ordinary message. Use a typed `ysk 4 your message` to include feedback.

Read [Keyboard shortcuts](../../README.md#keyboard-shortcuts) for the user-facing workflow and limitations.
