import ctypes
import io
import os
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from you_should_know import host


@pytest.mark.parametrize("command", ["exec", "e", "review"])
def test_root_noninteractive_commands_are_detected(command):
    assert host.headless_command(["codex", command, "Explain this change."])


@pytest.mark.parametrize(
    "options",
    [
        ["--config", "model='exec'"],
        ["--enable", "exec"],
        ["--disable", "review"],
        ["--model", "exec"],
        ["--profile", "review"],
        ["--sandbox", "read-only"],
        ["--ask-for-approval", "never"],
        ["--cd", "exec"],
        ["--add-dir", "review"],
        ["--local-provider", "ollama"],
        ["--remote", "unix://exec"],
        ["--remote-auth-token-env", "review"],
        ["--config=model='exec'"],
        ["--model=review"],
        ["--cd=exec"],
        ["-c", "model='exec'"],
        ["-m", "exec"],
        ["-p", "review"],
        ["-s", "read-only"],
        ["-a", "never"],
        ["-C", "exec"],
        ["-cmodel='exec'"],
        ["-mexec"],
        ["-p=review"],
        ["-Cexec"],
    ],
)
def test_option_values_do_not_become_subcommands(options):
    assert not host.headless_command(["codex", *options])
    assert host.headless_command(["codex", *options, "exec", "Do the task."])


def test_root_flags_and_repeated_config_allow_a_later_subcommand():
    assert host.headless_command(
        [
            "codex",
            "--no-daemon",
            "--strict-config",
            "-c",
            "model='exec'",
            "--config=features.search=false",
            "--search",
            "--no-alt-screen",
            "review",
        ]
    )


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--model"],
        ["-C"],
        ["Please explain exec and review."],
        ["--model", "review", "Please explain exec."],
        ["resume", "--last", "exec"],
        ["fork", "session-id", "review"],
        ["app-server", "exec"],
        ["exec-server"],
        ["help", "exec"],
        ["--", "exec"],
        ["-c", "model='review'", "--", "review"],
    ],
)
def test_interactive_prompts_and_other_commands_are_not_headless(arguments):
    assert not host.headless_command(["codex", *arguments])


@pytest.mark.parametrize(
    "options",
    [
        ["--future-option", "value"],
        ["--future-option=value"],
        ["-Z"],
        ["--image", "picture.png"],
        ["--image=picture.png"],
        ["-i", "picture.png"],
        ["-ipicture.png"],
    ],
)
def test_unknown_or_variadic_options_cannot_establish_a_command_boundary(options):
    assert not host.headless_command(["codex", *options, "exec"])


@pytest.mark.parametrize(
    "executable",
    [
        "/opt/tools/codex",
        "/opt/tools/codex-x86_64-unknown-linux-gnu",
        "/opt/tools/codex-aarch64-unknown-linux-musl",
        "/opt/tools/codex-aarch64-apple-darwin",
    ],
)
def test_native_codex_is_found_through_shell_ancestors(monkeypatch, executable):
    processes = {
        900: (800, "/bin/sh", ["sh", "-c", "run hook"]),
        800: (700, executable, ["launcher-alias", "--model", "exec", "review"]),
    }
    monkeypatch.setattr(host.os, "getppid", lambda: 900)
    monkeypatch.setattr(host, "process_info", processes.__getitem__)
    assert host.noninteractive_host()


@pytest.mark.parametrize(
    "executable", ["codex-wrapper", "notcodex", "codex.sh", "codex-x86_64-other-platform"]
)
def test_non_native_executable_names_do_not_identify_codex(monkeypatch, executable):
    processes = {900: (1, f"/opt/tools/{executable}", ["codex", "exec"])}
    monkeypatch.setattr(host.os, "getppid", lambda: 900)
    monkeypatch.setattr(host, "process_info", processes.__getitem__)
    assert not host.noninteractive_host()


@pytest.mark.parametrize(
    "nearest_arguments",
    [
        [],
        ["Please explain exec."],
        ["resume", "--last"],
        ["fork", "--last"],
        ["app-server", "--listen", "stdio://"],
        ["--future-option", "exec"],
    ],
)
def test_nearest_codex_host_prevents_outer_exec_misclassification(monkeypatch, nearest_arguments):
    processes = {
        900: (800, "/bin/sh", ["sh", "-c", "run hook"]),
        800: (700, "/opt/bin/codex", ["codex", *nearest_arguments]),
        700: (1, "/opt/bin/codex", ["codex", "exec", "Start another Codex."]),
    }
    monkeypatch.setattr(host.os, "getppid", lambda: 900)
    monkeypatch.setattr(host, "process_info", processes.__getitem__)
    assert not host.noninteractive_host()


@pytest.mark.parametrize(
    "failure", [PermissionError("hidden"), ValueError("invalid"), IndexError()]
)
def test_process_snapshot_errors_leave_the_hook_enabled(monkeypatch, failure):
    def process_info(pid):
        if pid == 900:
            return 800, "/bin/sh", ["sh", "-c", "run hook"]
        raise failure

    monkeypatch.setattr(host.os, "getppid", lambda: 900)
    monkeypatch.setattr(host, "process_info", process_info)
    assert not host.noninteractive_host()


def test_ancestry_cycles_terminate_without_classifying_a_host(monkeypatch):
    inspected = set()

    def process_info(pid):
        assert pid not in inspected, "The ancestry cycle was traversed again"
        inspected.add(pid)
        return (800 if pid == 900 else 900), "/bin/sh", ["sh"]

    monkeypatch.setattr(host.os, "getppid", lambda: 900)
    monkeypatch.setattr(host, "process_info", process_info)
    assert not host.noninteractive_host()
    assert inspected == {900, 800}


@pytest.mark.parametrize("codex_depth, expected", [(32, True), (33, False)])
def test_ancestry_search_is_bounded(monkeypatch, codex_depth, expected):
    inspected = []

    def process_info(pid):
        inspected.append(pid)
        assert len(inspected) <= 32, "The ancestry search exceeded its bound"
        if pid == 1001 - codex_depth:
            return pid - 1, "/opt/bin/codex", ["codex", "exec"]
        return pid - 1, "/bin/sh", ["sh"]

    monkeypatch.setattr(host.os, "getppid", lambda: 1000)
    monkeypatch.setattr(host, "process_info", process_info)
    assert host.noninteractive_host() is expected
    assert len(inspected) == 32


@pytest.mark.skipif(sys.platform != "linux", reason="Reads Linux procfs")
def test_linux_snapshot_reads_the_running_python_process():
    parent, executable, arguments = host.process_info(os.getpid())
    assert parent == os.getppid()
    assert Path(executable).samefile(sys.executable)
    assert arguments == sys.orig_argv


@pytest.mark.skipif(sys.platform != "linux", reason="Exercises the Linux procfs adapter")
def test_linux_deleted_executable_is_detected_from_process_snapshot(monkeypatch):
    def open_snapshot(path, mode):
        assert mode == "rb"
        assert path.parent.name == "900"
        snapshots = {
            "stat": b"900 (codex (old) name) S 1 0 0 0\n",
            "cmdline": b"codex\0--model\0exec\0review\0",
        }
        return io.BytesIO(snapshots[path.name])

    def readlink(path):
        assert path.name == "exe"
        return "/opt/bin/codex (deleted)"

    monkeypatch.setattr(host.os, "getppid", lambda: 900)
    monkeypatch.setattr(Path, "open", open_snapshot)
    monkeypatch.setattr(host.os, "readlink", readlink)
    assert host.noninteractive_host()


@pytest.mark.parametrize(
    "arguments",
    [
        [b"codex", b"exec", b"a prompt with spaces"],
        [b"codex", b"--model", b"", b"resume", b"review"],
        [b"codex", b"--", b"exec"],
    ],
)
def test_darwin_argument_boundaries_exclude_environment(arguments):
    raw = (
        struct.pack("i", len(arguments))
        + b"/opt/bin/codex\0\0\0"
        + b"\0".join(arguments)
        + b"\0PRIVATE=not-an-argument\0exec\0"
    )
    assert host.darwin_arguments(raw) == [os.fsdecode(value) for value in arguments]


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        struct.pack("i", -1),
        struct.pack("i", 1_000_000),
        struct.pack("i", 2) + b"/bin/codex\0codex\0exec",
        struct.pack("i", 2) + b"/bin/codex\0codex\0",
        struct.pack("i", 1) + b"no-terminator",
    ],
)
def test_darwin_malformed_argument_buffers_are_rejected(raw):
    with pytest.raises(ValueError):
        host.darwin_arguments(raw)


@pytest.mark.parametrize(
    "failure", [None, "parent", "identity", "path", "limit", "oversize", "args"]
)
def test_darwin_syscall_adapter_preserves_raw_arguments_and_handles_errors(monkeypatch, failure):
    def pidinfo(pid, flavor, argument, output, size):
        assert (pid, flavor, argument, size) == (900, 13, 0, 64)
        raw = struct.pack("II", 901 if failure == "identity" else pid, 1) + bytes(56)
        ctypes.memmove(output, raw, len(raw))
        return -1 if failure == "parent" else len(raw)

    def pidpath(pid, output, size):
        assert pid == 900 and size == 4096
        path = b"/opt/bin/codex\0"
        ctypes.memmove(output, path, len(path))
        return 0 if failure == "path" else len(path) - 1

    def sysctl(mib, length, output, size_pointer, new_value, new_length):
        assert new_value is None and new_length == 0
        size = ctypes.cast(size_pointer, ctypes.POINTER(ctypes.c_size_t))
        if list(mib) == [1, 8]:
            assert length == 2 and size[0] == ctypes.sizeof(ctypes.c_int)
            raw = struct.pack("i", 2_000_000 if failure == "oversize" else 512)
            status = -1 if failure == "limit" else 0
        else:
            assert list(mib) == [1, 49, 900] and length == 3
            assert size[0] == 512 + ctypes.sizeof(ctypes.c_int)
            raw = (
                struct.pack("i", 4) + b"/opt/bin/codex\0\0codex\0-m\0review\0exec\0TOKEN=private\0"
            )
            status = -1 if failure == "args" else 0
        ctypes.memmove(output, raw, len(raw))
        size[0] = len(raw)
        return status

    library = SimpleNamespace(proc_pidinfo=pidinfo, proc_pidpath=pidpath, sysctl=sysctl)
    monkeypatch.setattr(ctypes, "CDLL", lambda *args, **kwargs: library)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(os, "getppid", lambda: 900)
    assert host.noninteractive_host() is (failure is None)


def test_unsupported_platform_keeps_hooks_enabled(monkeypatch):
    monkeypatch.setattr(sys, "platform", "unknown-os")
    assert not host.noninteractive_host()
