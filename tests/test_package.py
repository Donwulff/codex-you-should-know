import json
import os
import runpy
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
build_package = runpy.run_path(str(ROOT / "scripts/package_plugin.py"))["build_package"]


def read_json(relative_path):
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def rpc_request(process, identifier, method, params):
    process.stdin.write(
        (json.dumps({"id": identifier, "method": method, "params": params}) + "\n").encode()
    )
    process.stdin.flush()
    pending = bytearray()
    deadline = time.monotonic() + 20
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while time.monotonic() < deadline:
            if not selector.select(max(0, deadline - time.monotonic())):
                break
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                raise AssertionError("Codex app-server exited before answering")
            pending.extend(chunk)
            while b"\n" in pending:
                line, _, rest = pending.partition(b"\n")
                pending = bytearray(rest)
                response = json.loads(line)
                if response.get("id") == identifier:
                    return response
    raise AssertionError(f"Codex app-server did not answer {method}")


class PackageTests(unittest.TestCase):
    def test_marketplace_resolves_to_native_plugin_components(self):
        native = read_json(".codex-plugin/plugin.json")
        marketplace = read_json(".agents/plugins/marketplace.json")
        self.assertLessEqual(len(native["interface"]["shortDescription"]), 30)
        self.assertTrue((ROOT / native["hooks"]).is_file())
        self.assertTrue((ROOT / native["skills"] / "you-should-know/SKILL.md").is_file())
        entry = marketplace["plugins"][0]
        self.assertEqual(entry["name"], native["name"])
        self.assertEqual(entry["source"]["source"], "local")
        self.assertEqual((ROOT / entry["source"]["path"]).resolve(), ROOT)

    def test_bundle_excludes_local_state_and_rebuilds_owned_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = build_package(ROOT, Path(temporary) / "source")
            for directory in (".scratch", ".venv", ".git", "src/you_should_know/__pycache__"):
                target = source / directory / "private-sentinel"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("Do not distribute this local file.")
            destination = build_package(source, Path(temporary) / "bundle")
            (destination / "old-generated-file").write_text("Old output")
            build_package(source, destination)
            self.assertFalse((destination / "old-generated-file").exists())
            self.assertFalse(
                any(path.name == "private-sentinel" for path in destination.rglob("*"))
            )
            self.assertTrue((destination / "src/you_should_know/cli.py").is_file())
            self.assertTrue((destination / ".codex-plugin/plugin.json").is_file())
            self.assertEqual(
                (destination / "LICENSE").read_bytes(), (ROOT / "LICENSE").read_bytes()
            )

    def test_builder_preserves_unowned_outputs_and_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary = Path(temporary)
            unowned = temporary / "unowned"
            unowned.mkdir()
            sentinel = unowned / "keep"
            sentinel.write_text("Existing user data")
            with self.assertRaisesRegex(ValueError, "unowned"):
                build_package(ROOT, unowned)
            self.assertEqual(sentinel.read_text(), "Existing user data")
            symlink = temporary / "symlink"
            symlink.symlink_to(unowned, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink"):
                build_package(ROOT, symlink)
            self.assertEqual(sentinel.read_text(), "Existing user data")

    @unittest.skipUnless(shutil.which("codex"), "Codex CLI is required for native discovery")
    def test_codex_discovers_both_installed_hooks(self):
        with tempfile.TemporaryDirectory(prefix="ysk native ") as temporary:
            temporary = Path(temporary)
            home = temporary / "home"
            home.mkdir()
            package = temporary / "package"
            built = subprocess.run(
                [sys.executable, str(ROOT / "scripts/package_plugin.py"), "--output", str(package)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(built.returncode, 0, built.stderr)
            environment = dict(os.environ, CODEX_HOME=str(home))
            for arguments in (
                ["plugin", "marketplace", "add", str(package), "--json"],
                ["plugin", "add", "you-should-know@you-should-know", "--json"],
            ):
                installed = subprocess.run(
                    ["codex", *arguments],
                    cwd=temporary,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=20,
                    check=False,
                )
                self.assertEqual(installed.returncode, 0, installed.stderr)
            with (
                tempfile.TemporaryFile() as errors,
                subprocess.Popen(
                    ["codex", "app-server", "--disable", "remote_plugin", "--disable", "apps"],
                    cwd=temporary,
                    env=environment,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=errors,
                ) as process,
            ):
                try:
                    initialized = rpc_request(
                        process,
                        1,
                        "initialize",
                        {
                            "clientInfo": {"name": "ysk-package-test", "version": "0.1.0"},
                            "capabilities": {"experimentalApi": True},
                        },
                    )
                    self.assertIn("result", initialized, initialized)
                    assert process.stdin is not None
                    process.stdin.write(b'{"method":"initialized","params":{}}\n')
                    process.stdin.flush()
                    response = rpc_request(process, 2, "hooks/list", {"cwds": [str(temporary)]})
                    self.assertIn("result", response, response)
                    discovery = response["result"]["data"][0]
                    self.assertEqual(discovery["errors"], [])
                    self.assertEqual(discovery["warnings"], [])
                    hooks = discovery["hooks"]
                    self.assertEqual({hook["eventName"] for hook in hooks}, {"postToolUse", "stop"})
                    self.assertTrue(all(hook["enabled"] for hook in hooks))
                    self.assertTrue(all(hook["source"] == "plugin" for hook in hooks))
                    self.assertEqual(
                        {hook["eventName"]: hook["statusMessage"] for hook in hooks},
                        {
                            "postToolUse": "You Should Know: review recent work",
                            "stop": "You Should Know: review completed turn",
                        },
                    )
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()

    def test_installed_hook_commands_work_with_spaces_in_plugin_path(self):
        hooks = read_json("hooks/hooks.json")["hooks"]
        with tempfile.TemporaryDirectory(prefix="ysk package ") as temporary:
            temporary = Path(temporary)
            plugin = temporary / "plugin with spaces"
            shutil.copytree(ROOT / "scripts", plugin / "scripts")
            shutil.copytree(ROOT / "src", plugin / "src")
            data_dir = temporary / "data"
            data_dir.mkdir()
            (data_dir / "config.toml").write_text("enabled = false\n", encoding="utf-8")
            transcript = temporary / "rollout.jsonl"
            transcript.write_text("", encoding="utf-8")
            environment = dict(os.environ, PLUGIN_ROOT=str(plugin), YSK_DATA_DIR=str(data_dir))
            for event, entries in hooks.items():
                with self.subTest(event=event):
                    command = entries[0]["hooks"][0]
                    result = subprocess.run(
                        command["command"],
                        shell=True,
                        cwd=temporary,
                        env=environment,
                        input=json.dumps(
                            {
                                "hook_event_name": event,
                                "session_id": "package-smoke",
                                "transcript_path": str(transcript),
                                "cwd": str(temporary),
                                "tool_name": "exec_command",
                            }
                        ),
                        text=True,
                        capture_output=True,
                        timeout=10,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    output = json.loads(result.stdout) if result.stdout.strip() else {}
                    self.assertEqual(output, {})
            self.assertTrue(hooks["PostToolUse"][0]["hooks"][0]["async"])
            self.assertFalse(hooks["Stop"][0]["hooks"][0].get("async", False))


if __name__ == "__main__":
    unittest.main()
