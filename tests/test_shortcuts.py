import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_shortcuts(tmp_path, action, *arguments):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/you_should_know.py"), "shortcuts", action, *arguments],
        env={**os.environ, "CODEX_HOME": str(tmp_path), "YSK_DATA_DIR": str(tmp_path / "data")},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_install_preserves_existing_config_and_remove_restores_bytes(tmp_path):
    config = tmp_path / "config.toml"
    original = b'# personal settings\r\nmodel = "example"\r\n[tui]\r\nanimations = false'
    config.write_bytes(original)
    config.chmod(0o640)

    installed = run_shortcuts(tmp_path, "install")
    assert installed.returncode == 0, installed.stderr
    assert json.loads(installed.stdout)["status"] == "installed"
    settings = tomllib.loads(config.read_text())
    assert settings["model"] == "example"
    assert settings["tui"]["animations"] is False
    assert settings["tui"]["keymap"]["chat"]["open_presets"] == "alt-y"
    assert settings["tui"]["presets"] == {
        "1": {"input": "ysk 1", "label": "YSK: Tell me more"},
        "2": {"input": "ysk 2", "label": "YSK: Already knew that"},
        "3": {"input": "ysk 3", "label": "YSK: Dismiss"},
        "4": {"input": "ysk 4", "label": "YSK: Chat in main session"},
    }
    assert config.stat().st_mode & 0o777 == 0o640
    assert not (tmp_path / "data").exists()

    removed = run_shortcuts(tmp_path, "remove")
    assert removed.returncode == 0, removed.stderr
    assert json.loads(removed.stdout)["status"] == "removed"
    assert config.read_bytes() == original
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize(
    "original",
    [
        b"",
        b"[tui]",
        b"tui.animations = false\n",
        b'[tui.keymap.chat] # keep this comment\nopen_editor = "ctrl-e"\n',
        b"[ 'tui' . 'keymap' . 'chat' ]\n",
        b'[tui.presets."9"]\ninput = "existing preset"\n',
        b'model = "untouched"\n[tui]\nanimations = false\n[tui.keymap.chat]\n',
        b'model = "untouched"\nnote = """\n[tui]\nstring content\n"""\n[tui]\n',
    ],
)
def test_install_is_idempotent_and_removal_preserves_varied_toml_layouts(tmp_path, original):
    config = tmp_path / "config.toml"
    config.write_bytes(original)
    installed = run_shortcuts(tmp_path, "install")
    assert installed.returncode == 0, installed.stderr
    first = config.read_bytes()
    again = run_shortcuts(tmp_path, "install")
    assert again.returncode == 0, again.stderr
    assert json.loads(again.stdout)["status"] == "already_configured"
    assert config.read_bytes() == first
    removed = run_shortcuts(tmp_path, "remove")
    assert removed.returncode == 0, removed.stderr
    assert config.read_bytes() == original
    again = run_shortcuts(tmp_path, "remove")
    assert again.returncode == 0, again.stderr
    assert json.loads(again.stdout)["status"] == "unchanged"
    assert config.read_bytes() == original


@pytest.mark.parametrize(
    "setting",
    [
        'tui.keymap.chat.open_editor = "alt-y"',
        'tui.keymap.global.some_action = ["ctrl-a", "alt-y"]',
        'tui.keymap.composer.send = "alt-y x"',
        'tui.keymap.editor.move_left = "Alt-Y"',
        'tui.keymap.editor.move_left = "option-y"',
        'tui.keymap.composer.send = "alt--y"',
        'tui.keymap.vim_normal.enter_insert = "alt-y f12"',
        'tui.keymap.vim_operator.delete_line = "alt-y f12"',
        'tui.keymap.vim_search.forward = "alt-y f12"',
        'tui.keymap.vim_text_object.word = "alt-y f12"',
    ],
)
def test_install_rejects_alt_y_bound_to_another_chat_action(tmp_path, setting):
    config = tmp_path / "config.toml"
    original = (setting + "\n").encode()
    config.write_bytes(original)
    result = run_shortcuts(tmp_path, "install")
    assert result.returncode == 1
    assert "conflict" in result.stderr.lower()
    assert setting.split(" =")[0] in result.stderr
    assert config.read_bytes() == original


def test_install_preserves_matching_shortcuts_with_custom_labels_and_extra_bindings(tmp_path):
    config = tmp_path / "config.toml"
    original = (
        b'tui.keymap.chat.open_presets = ["ctrl-p", "alt-y"]\n'
        b'[tui.presets."2"]\ninput = "ysk 2"\nlabel = "My label"\n'
    )
    config.write_bytes(original)
    installed = run_shortcuts(tmp_path, "install")
    assert installed.returncode == 0, installed.stderr
    data = tomllib.loads(config.read_text())
    assert data["tui"]["keymap"]["chat"]["open_presets"] == ["ctrl-p", "alt-y"]
    assert data["tui"]["presets"]["2"]["label"] == "My label"
    removed = run_shortcuts(tmp_path, "remove")
    assert removed.returncode == 0, removed.stderr
    assert config.read_bytes() == original


@pytest.mark.parametrize("binding", ["option-y", "alt--y"])
def test_install_preserves_equivalent_existing_binding(tmp_path, binding):
    config = tmp_path / "config.toml"
    original = f'tui.keymap.chat.open_presets = "{binding}"\n'
    config.write_text(original)
    result = run_shortcuts(tmp_path, "install")
    assert result.returncode == 0, result.stderr
    assert tomllib.loads(config.read_text())["tui"]["keymap"]["chat"]["open_presets"] == binding
    removed = run_shortcuts(tmp_path, "remove")
    assert removed.returncode == 0, removed.stderr
    assert config.read_text() == original


def test_install_preserves_alt_y_in_contexts_without_presets(tmp_path):
    config = tmp_path / "config.toml"
    original = 'tui.keymap.list.accept = "alt-y"\ntui.keymap.pager.close = "alt-y"\n'
    config.write_text(original)
    result = run_shortcuts(tmp_path, "install")
    assert result.returncode == 0, result.stderr
    removed = run_shortcuts(tmp_path, "remove")
    assert removed.returncode == 0, removed.stderr
    assert config.read_text() == original


@pytest.mark.parametrize(
    ("original", "error"),
    [
        ('tui.presets."3" = { input = "my task" }\n', "tui.presets.3"),
        ('tui.keymap.chat.open_presets = "ctrl-p"\n', "tui.keymap.chat.open_presets"),
        ("tui = false\n", "tui"),
        ("tui = { animations = true }\n", "inline target tables"),
        ("model = [\n", "Invalid value"),
    ],
)
def test_install_errors_leave_config_unchanged(tmp_path, original, error):
    config = tmp_path / "config.toml"
    config.write_text(original)
    result = run_shortcuts(tmp_path, "install")
    assert result.returncode == 1
    assert error in result.stderr
    assert config.read_text() == original
    assert not (tmp_path / "data").exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["config.toml"]


def test_remove_preserves_modified_preset_and_unrelated_later_edits(tmp_path):
    config = tmp_path / "config.toml"
    original = 'model = "original"\n[tui]\nanimations = false\n'
    config.write_text(original)
    installed = run_shortcuts(tmp_path, "install")
    assert installed.returncode == 0, installed.stderr
    edited = config.read_text().replace('"ysk 2"', '"my custom task"')
    edited = edited.replace('model = "original"', 'model = "new"')
    edited += '\n[tui.presets."9"]\ninput = "another task"\n'
    config.write_text(edited)

    result = run_shortcuts(tmp_path, "remove")
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["kept_modified"] == ["tui.presets.2"]
    assert len(output["removed"]) == 4
    assert tomllib.loads(config.read_text()) == {
        "model": "new",
        "tui": {
            "animations": False,
            "presets": {
                "2": {"input": "my custom task", "label": "YSK: Already knew that"},
                "9": {"input": "another task"},
            },
        },
    }
    assert '"my custom task"' in config.read_text()


def test_remove_never_claims_preexisting_manual_shortcuts(tmp_path):
    config = tmp_path / "config.toml"
    original = (
        'tui.keymap.chat.open_presets = "alt-y"\n'
        'tui.presets."1" = { input = "ysk 1", label = "YSK: Tell me more" }\n'
        'tui.presets."2" = { input = "ysk 2", label = "YSK: Already knew that" }\n'
        'tui.presets."3" = { input = "ysk 3", label = "YSK: Dismiss" }\n'
        'tui.presets."4" = { input = "ysk 4", label = "YSK: Chat in main session" }\n'
    )
    config.write_text(original)
    installed = run_shortcuts(tmp_path, "install")
    assert installed.returncode == 0, installed.stderr
    assert json.loads(installed.stdout)["status"] == "already_configured"
    assert config.read_text() == original
    removed = run_shortcuts(tmp_path, "remove")
    assert removed.returncode == 0, removed.stderr
    assert json.loads(removed.stdout)["status"] == "unchanged"
    assert config.read_text() == original


def test_remove_ignores_marker_text_inside_a_multiline_string(tmp_path):
    config = tmp_path / "config.toml"
    installed = run_shortcuts(tmp_path, "install")
    assert installed.returncode == 0, installed.stderr
    fragments = config.read_text()
    original = 'notes = """\n' + fragments + '"""\n'
    config.write_text(original)
    removed = run_shortcuts(tmp_path, "remove")
    assert removed.returncode == 0, removed.stderr
    assert json.loads(removed.stdout)["status"] == "unchanged"
    assert config.read_text() == original


def test_explicit_config_path_does_not_touch_default_profile(tmp_path):
    default = tmp_path / "config.toml"
    default.write_text('model = "default"\n')
    alternate = tmp_path / "another profile" / "config.toml"
    installed = run_shortcuts(tmp_path, "install", "--config", str(alternate))
    assert installed.returncode == 0, installed.stderr
    assert json.loads(installed.stdout)["config_path"] == str(alternate)
    assert alternate.stat().st_mode & 0o777 == 0o600
    assert tomllib.loads(alternate.read_text())["tui"]["keymap"]["chat"]["open_presets"] == "alt-y"
    assert default.read_text() == 'model = "default"\n'
    assert not (tmp_path / "data").exists()


def test_remove_without_install_creates_nothing(tmp_path):
    result = run_shortcuts(tmp_path, "remove")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "unchanged"
    assert list(tmp_path.iterdir()) == []


def test_install_rejects_symlink_config_without_changing_target(tmp_path):
    target = tmp_path / "target.toml"
    target.write_text('model = "keep"\n')
    config = tmp_path / "config.toml"
    config.symlink_to(target)
    result = run_shortcuts(tmp_path, "install")
    assert result.returncode == 1
    assert "symlink" in result.stderr
    assert config.is_symlink()
    assert target.read_text() == 'model = "keep"\n'


def test_atomic_write_failure_preserves_config_and_cleans_temporary_file(tmp_path, monkeypatch):
    from you_should_know.shortcuts import configure_shortcuts

    config = tmp_path / "config.toml"
    original = b'model = "keep"\n'
    config.write_bytes(original)

    def fail_replace(source, destination):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr("you_should_know.shortcuts.os.replace", fail_replace)
    with pytest.raises(OSError, match="simulated replacement failure"):
        configure_shortcuts("install", config)
    assert config.read_bytes() == original
    assert list(tmp_path.iterdir()) == [config]


def test_concurrent_edit_is_detected_before_replacement(tmp_path, monkeypatch):
    from you_should_know.shortcuts import configure_shortcuts

    config = tmp_path / "config.toml"
    config.write_text('model = "old"\n')

    def concurrent_edit(fd):
        config.write_text('model = "edited elsewhere"\n')

    monkeypatch.setattr("you_should_know.shortcuts.os.fsync", concurrent_edit)
    with pytest.raises(ValueError, match="changed during shortcut setup"):
        configure_shortcuts("install", config)
    assert config.read_text() == 'model = "edited elsewhere"\n'
    assert list(tmp_path.iterdir()) == [config]
