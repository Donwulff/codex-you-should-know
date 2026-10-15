import ctypes
import os
import re
import struct
import sys
from pathlib import Path

CODEX_EXECUTABLE = re.compile(
    r"codex(?:-(?:x86_64|aarch64)-(?:unknown-linux-(?:gnu|musl)|apple-darwin))?"
)
VALUE_OPTIONS = {
    "--config",
    "--enable",
    "--disable",
    "--model",
    "--profile",
    "--sandbox",
    "--ask-for-approval",
    "--cd",
    "--add-dir",
    "--local-provider",
    "--remote",
    "--remote-auth-token-env",
}
FLAG_OPTIONS = {
    "--strict-config",
    "--oss",
    "--approve-for-me",
    "--not-so-yolo",
    "--dangerously-bypass-approvals-and-sandbox",
    "--yolo",
    "--dangerously-bypass-hook-trust",
    "--worktree",
    "--search",
    "--no-alt-screen",
    "--no-daemon",
}
SHORT_VALUE_OPTIONS = {"-c", "-m", "-p", "-s", "-a", "-C"}


def headless_command(argv: list[str]) -> bool:
    arguments = iter(argv[1:])
    for argument in arguments:
        option, separator, _ = argument.partition("=")
        if option in VALUE_OPTIONS or argument in SHORT_VALUE_OPTIONS:
            if not separator and next(arguments, None) is None:
                return False
        elif argument in FLAG_OPTIONS:
            continue
        elif argument[:2] in SHORT_VALUE_OPTIONS and len(argument) > 2:
            continue
        else:
            # Unknown options and variadic --image cannot establish a subcommand boundary.
            return argument in {"exec", "e", "review"}
    return False


def process_info(pid: int) -> tuple[int, str, list[str]]:
    if sys.platform == "darwin":
        return darwin_process_info(pid)
    if sys.platform != "linux":
        raise OSError("Process inspection is unavailable")
    directory = Path("/proc") / str(pid)
    with (directory / "stat").open("rb") as stream:
        parent = int(stream.read(4096).rsplit(b")", 1)[1].split()[1])
    executable = os.readlink(directory / "exe").removesuffix(" (deleted)")
    with (directory / "cmdline").open("rb") as stream:
        raw = stream.read(1_048_577)
    if len(raw) > 1_048_576 or not raw.endswith(b"\0"):
        raise ValueError("Process arguments are unavailable or too large")
    return parent, executable, [os.fsdecode(value) for value in raw[:-1].split(b"\0")]


def darwin_arguments(raw: bytes) -> list[str]:
    if len(raw) < 4:
        raise ValueError("Missing process argument count")
    count = struct.unpack_from("i", raw)[0]
    if not 0 < count <= len(raw):
        raise ValueError("Invalid process argument count")
    offset = raw.index(b"\0", 4) + 1
    while offset < len(raw) and raw[offset] == 0:
        offset += 1
    arguments = []
    for _ in range(count):
        end = raw.index(b"\0", offset)
        arguments.append(os.fsdecode(raw[offset:end]))
        offset = end + 1
    return arguments


def darwin_process_info(pid: int) -> tuple[int, str, list[str]]:
    library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    library.proc_pidinfo.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint64,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    library.proc_pidinfo.restype = ctypes.c_int
    # Apple's PROC_PIDT_SHORTBSDINFO returns a 64-byte struct beginning with pid and ppid.
    info = ctypes.create_string_buffer(64)
    if library.proc_pidinfo(pid, 13, 0, info, len(info)) != len(info):
        raise OSError("Process ancestry is unavailable")
    observed_pid, parent = struct.unpack_from("II", info.raw)
    if observed_pid != pid:
        raise ValueError("Process identity changed")
    library.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    library.proc_pidpath.restype = ctypes.c_int
    path = ctypes.create_string_buffer(4096)
    if library.proc_pidpath(pid, path, len(path)) <= 0:
        raise OSError("Process executable is unavailable")
    executable = os.fsdecode(path.value)
    if not CODEX_EXECUTABLE.fullmatch(Path(executable).name):
        return parent, executable, []

    library.sysctl.argtypes = [
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.c_void_p,
        ctypes.c_size_t,
    ]
    library.sysctl.restype = ctypes.c_int
    argmax = ctypes.c_int()
    size = ctypes.c_size_t(ctypes.sizeof(argmax))
    if library.sysctl(
        (ctypes.c_int * 2)(1, 8), 2, ctypes.byref(argmax), ctypes.byref(size), None, 0
    ):
        raise OSError("Process argument limit is unavailable")
    if size.value != ctypes.sizeof(argmax) or not 0 < argmax.value <= 1_048_576:
        raise ValueError("Invalid process argument limit")
    # KERN_PROCARGS2 reserves argc separately from the ARG_MAX bytes of strings.
    raw = ctypes.create_string_buffer(argmax.value + ctypes.sizeof(argmax))
    size = ctypes.c_size_t(len(raw))
    if library.sysctl((ctypes.c_int * 3)(1, 49, pid), 3, raw, ctypes.byref(size), None, 0):
        raise OSError("Process arguments are unavailable")
    if size.value > len(raw):
        raise ValueError("Process arguments exceeded the buffer")
    return parent, executable, darwin_arguments(raw.raw[: size.value])


def noninteractive_host() -> bool:
    pid = os.getppid()
    seen = set()
    for _ in range(32):
        if pid <= 1 or pid in seen:
            return False
        seen.add(pid)
        try:
            parent, executable, argv = process_info(pid)
        except (OSError, ValueError, IndexError):
            return False
        if CODEX_EXECUTABLE.fullmatch(Path(executable).name):
            return headless_command(argv)
        pid = parent
    return False
