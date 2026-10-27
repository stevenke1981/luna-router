"""Offline contract tests; no installed Codex, credentials, or inference needed."""

from __future__ import annotations

import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import codex_backend as cb


def runtime(auth="chatgpt", models=None):
    return {
        "source": "codex.model/list", "simulation": False,
        "auth_mode": auth,
        "models": models if models is not None else [
            {"id": "gpt-6.1-sol", "efforts": ["low", "medium", "high"],
             "default_effort": "medium", "input_modalities": ["text", "image"]}
        ],
    }


class RecordingInput(io.StringIO):
    def close(self):
        self.recorded = self.getvalue()
        super().close()


class FakeServer:
    def __init__(self, messages):
        self.stdin = RecordingInput()
        self.stdout = io.StringIO("".join(json.dumps(m) + "\n" for m in messages))
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


class DiscoveryTests(unittest.TestCase):
    def test_auth_status_is_classified_without_exposing_raw_output(self):
        cases = [
            ("Logged in using ChatGPT\n", 0, "chatgpt"),
            ("Logged in using an API key - secret-must-not-return\n", 0, "api_key"),
            ("Not logged in. Run codex login to use ChatGPT.\n", 1, "unknown"),
            ("Welcome! Sign in with ChatGPT or an API key.\n", 0, "unknown"),
            ("Logged in using ChatGPT\nLogged in using API key\n", 0, "unknown"),
        ]
        for output, exit_code, expected in cases:
            with self.subTest(expected=expected, output=output):
                def fake_run(argv, **kwargs):
                    self.assertEqual(argv, ["codex", "login", "status"])
                    self.assertIs(kwargs["shell"], False)
                    kwargs["stdout"].write(output.encode())
                    return subprocess.CompletedProcess(argv, exit_code)
                with patch.object(cb.subprocess, "run", side_effect=fake_run):
                    self.assertEqual(cb._auth_mode("codex", 1), expected)

    def test_auth_timeout_is_unknown(self):
        with patch.object(cb.subprocess, "run", side_effect=subprocess.TimeoutExpired("codex", 1)):
            self.assertEqual(cb._auth_mode("codex", 1), "unknown")

    def test_paginated_catalog_normalization_and_no_turn_start(self):
        server = FakeServer([
            {"id": 1, "result": {"userAgent": "test"}},
            {"method": "notification", "params": {}},
            {"id": 2, "result": {"data": [
                {"id": "picker-alias", "model": "gpt-6.1-sol", "displayName": "Sol",
                 "supportedReasoningEfforts": [{"reasoningEffort": "medium"}, {"reasoningEffort": "ultra"}],
                 "defaultReasoningEffort": "medium"},
                {"model": "hidden-model", "hidden": True},
            ], "nextCursor": "page-two"}},
            {"id": 3, "result": {"data": [
                {"model": "gpt-6-luna", "supportedReasoningEfforts": [{"reasoningEffort": "none"}],
                 "defaultReasoningEffort": "none", "inputModalities": ["text"]},
            ], "nextCursor": None}},
        ])
        with patch.object(cb, "_auth_mode", return_value="chatgpt"), patch.object(cb.subprocess, "Popen", return_value=server) as popen:
            result = cb.discover(timeout=2)
        self.assertEqual(result["source"], "codex.model/list")
        self.assertIs(result["simulation"], False)
        self.assertEqual(result["models"][0]["id"], "gpt-6.1-sol")
        self.assertEqual(result["models"][0]["efforts"], ["medium", "ultra"])
        self.assertEqual(result["models"][0]["input_modalities"], ["text", "image"])
        self.assertEqual(len(result["models"]), 2)
        messages = [json.loads(line) for line in server.stdin.recorded.splitlines()]
        self.assertEqual([m["method"] for m in messages], ["initialize", "initialized", "model/list", "model/list"])
        self.assertEqual(messages[-1]["params"]["cursor"], "page-two")
        self.assertNotIn("thread/start", server.stdin.recorded)
        self.assertTrue(server.terminated)
        self.assertIs(popen.call_args.kwargs["shell"], False)

    def test_repeated_cursor_fails_and_reaps_server(self):
        server = FakeServer([
            {"id": 1, "result": {}},
            {"id": 2, "result": {"data": [], "nextCursor": "repeat"}},
            {"id": 3, "result": {"data": [], "nextCursor": "repeat"}},
        ])
        with patch.object(cb, "_auth_mode", return_value="unknown"), patch.object(cb.subprocess, "Popen", return_value=server):
            with self.assertRaisesRegex(cb.CodexBackendError, "cursor"):
                cb.discover(timeout=2)
        self.assertTrue(server.terminated)

    def test_eof_during_handshake_is_not_empty_success(self):
        server = FakeServer([])
        with patch.object(cb, "_auth_mode", return_value="unknown"), patch.object(cb.subprocess, "Popen", return_value=server):
            with self.assertRaisesRegex(cb.CodexBackendError, "closed"):
                cb.discover(timeout=2)
        self.assertTrue(server.terminated)

    def test_rpc_timeout_cleans_up(self):
        server = FakeServer([])
        with patch.object(cb, "_auth_mode", return_value="unknown"), patch.object(cb.subprocess, "Popen", return_value=server), patch.object(cb, "_read_stdout", return_value=None):
            with self.assertRaisesRegex(cb.CodexBackendError, "timeout"):
                cb.discover(timeout=0.02)
        self.assertTrue(server.terminated)


class WindowsExecutableTests(unittest.TestCase):
    def test_npm_cmd_shim_resolves_to_bundled_native_binary(self):
        with tempfile.TemporaryDirectory() as prefix:
            shim = Path(prefix) / "codex.cmd"
            shim.write_text("@echo off\n")
            native = (Path(prefix) / "node_modules/@openai/codex/node_modules/@openai/codex-win32-x64"
                      / "vendor/x86_64-pc-windows-msvc/bin/codex.exe")
            native.parent.mkdir(parents=True)
            native.write_bytes(b"")
            with patch.dict(cb.os.environ, {"CODEX_CLI_PATH": ""}), \
                 patch.object(cb.shutil, "which", return_value=str(shim)):
                self.assertEqual(cb._windows_native_codex(), str(native))
            with patch.dict(cb.os.environ, {"CODEX_CLI_PATH": str(native)}):
                self.assertEqual(cb._windows_native_codex(), str(native))


class RouterMcpDisableTests(unittest.TestCase):
    def test_override_only_when_router_server_is_registered(self):
        with tempfile.TemporaryDirectory() as home:
            config = Path(home) / "config.toml"
            with patch.dict(cb.os.environ, {"CODEX_HOME": home}):
                self.assertEqual(cb._router_mcp_disable(), [])
                config.write_text('[mcp_servers.other]\ncommand = "x"\n', encoding="utf-8")
                self.assertEqual(cb._router_mcp_disable(), [])
                config.write_text('[mcp_servers.luna_router_v270]\ncommand = "py"\n', encoding="utf-8")
                self.assertEqual(cb._router_mcp_disable(),
                                 ["-c", "mcp_servers.luna_router_v270.enabled=false"])
                with tempfile.TemporaryDirectory() as project:
                    cmd = cb.build_command("gpt-6.1-sol", "high", Path(project))
                self.assertEqual(cmd[-3:], ["-c", "mcp_servers.luna_router_v270.enabled=false", "-"])


class CommandTests(unittest.TestCase):
    def test_explicit_configuration_and_stdin(self):
        with tempfile.TemporaryDirectory(prefix="router space ") as project:
            cmd = cb.build_command("gpt-6.1-sol", "high", Path(project))
        self.assertIsInstance(cmd, list)
        self.assertEqual(cmd[-1], "-")
        self.assertIn('model_provider="openai"', cmd)
        self.assertIn('forced_login_method="chatgpt"', cmd)
        self.assertIn('approval_policy="never"', cmd)
        self.assertIn("--skip-git-repo-check", cmd)
        self.assertIn('model_reasoning_effort="high"', cmd)
        self.assertEqual(cmd[cmd.index("--model") + 1], "gpt-6.1-sol")
        self.assertEqual(cmd[cmd.index("--cd") + 1], str(Path(project).resolve()))
        self.assertFalse(any("dangerously" in s or "ignore-rules" in s or "full-auto" in s for s in cmd))

    def test_rejects_unsafe_sandbox_and_invalid_identifiers(self):
        with tempfile.TemporaryDirectory() as project:
            for sandbox in ["danger-full-access", "externalSandbox", "--yolo"]:
                with self.subTest(sandbox=sandbox), self.assertRaises(ValueError):
                    cb.build_command("gpt-6.1-sol", "high", Path(project), sandbox=sandbox)
            for model in ["--yolo", 'gpt-6\"; rm -rf x', ""]:
                with self.subTest(model=model), self.assertRaises(ValueError):
                    cb.build_command(model, "high", Path(project))


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def run_task(self, **kwargs):
        args = dict(task_text="修復測試", model="gpt-6.1-sol", effort="medium", project=self.project, runtime=runtime())
        args.update(kwargs)
        return cb.run_task(**args)

    def test_simulated_runtime_never_launches_discovery_or_execution(self):
        with patch.object(cb, "discover") as discover, patch.object(cb.subprocess, "Popen") as launch:
            result = self.run_task(runtime={"simulation": True})
        self.assertEqual(result["status"], "failed")
        discover.assert_not_called()
        launch.assert_not_called()

    def test_fresh_auth_overrules_stale_runtime(self):
        for auth in ["unknown", "api_key"]:
            with self.subTest(auth=auth), patch.object(cb, "discover", return_value=runtime(auth)) as fresh, patch.object(cb.subprocess, "Popen") as launch:
                result = self.run_task()
                self.assertEqual(result["error"], "chatgpt_subscription_auth_required")
                fresh.assert_called_once()
                launch.assert_not_called()

    def test_model_and_effort_must_be_in_fresh_catalog(self):
        cases = [({"model": "gpt-6.1-astra"}, "model_not_available"), ({"effort": "max"}, "effort_not_supported")]
        for kwargs, error in cases:
            with self.subTest(kwargs=kwargs), patch.object(cb, "discover", return_value=runtime()), patch.object(cb.subprocess, "Popen") as launch:
                result = self.run_task(**kwargs)
                self.assertEqual(result["error"], error)
                launch.assert_not_called()

    def fake_launch(self, events, returncode=0, timeout=False):
        def launch(argv, **kwargs):
            self.assertIs(kwargs["shell"], False)
            self.assertNotIn("修復測試", argv)
            proc = Mock()
            proc.stdin = io.StringIO()
            proc.returncode = returncode
            proc.poll.return_value = returncode
            def communicate(input=None, timeout=None):
                self.assertEqual(input, "修復測試")
                for event in events:
                    kwargs["stdout"].write(json.dumps(event).encode() + b"\n")
                if timeout_flag:
                    raise subprocess.TimeoutExpired(argv, timeout)
            timeout_flag = timeout
            proc.communicate.side_effect = communicate
            return proc
        return launch

    def test_one_turn_uses_stdin_and_records_only_observed_terminal(self):
        events = [
            {"type": "thread.started", "thread_id": "test-thread"},
            {"type": "item.completed", "item": {"id": "1", "type": "agent_message", "text": "完成模型回合"}},
            {"type": "turn.completed", "usage": {"input_tokens": 12}},
        ]
        with patch.object(cb, "discover", return_value=runtime()), patch.object(cb.subprocess, "Popen", side_effect=self.fake_launch(events)) as launch:
            result = self.run_task()
        launch.assert_called_once()
        self.assertEqual(result["status"], "turn_completed")
        self.assertEqual(result["thread_id"], "test-thread")
        self.assertEqual(result["worker_message"], "完成模型回合")
        self.assertIsNone(result["actual_model"])
        self.assertIsNone(result["actual_effort"])

    def test_exit_zero_without_terminal_is_unknown(self):
        with patch.object(cb, "discover", return_value=runtime()), patch.object(cb.subprocess, "Popen", side_effect=self.fake_launch([])) as launch:
            result = self.run_task()
        self.assertEqual(result["status"], "unknown")
        launch.assert_called_once()

    def test_failed_terminal_is_failed_even_with_exit_zero(self):
        with patch.object(cb, "discover", return_value=runtime()), patch.object(cb.subprocess, "Popen", side_effect=self.fake_launch([{"type": "turn.failed"}])):
            self.assertEqual(self.run_task()["status"], "failed")

    def test_timeout_never_retries_or_claims_completion(self):
        with patch.object(cb, "discover", return_value=runtime()), patch.object(cb.subprocess, "Popen", side_effect=self.fake_launch([{"type": "turn.completed"}], timeout=True)) as launch:
            result = self.run_task(timeout=0.02)
        self.assertEqual(result["status"], "unknown")
        self.assertIn("do_not_retry", result["error"])
        launch.assert_called_once()

    def test_unsafe_sandbox_rejected_before_discovery(self):
        with patch.object(cb, "discover") as discover:
            result = self.run_task(sandbox="danger-full-access")
        self.assertEqual(result["status"], "failed")
        discover.assert_not_called()

    def test_output_tail_is_bounded_and_excludes_raw_tool_text(self):
        with tempfile.TemporaryFile(mode="w+b") as output:
            output.write(b'{"type":"thread.started","thread_id":"early"}\n')
            output.write(b"x" * (cb.MAX_OUTPUT_TAIL + 100) + b"\n")
            output.write(b'{"type":"item.completed","item":{"type":"command_execution","aggregated_output":"private-output"}}\n')
            output.write(b'{"type":"turn.completed"}\n')
            parsed = cb._parse_task_output(output)
        self.assertTrue(parsed["output_truncated"])
        self.assertEqual(parsed["thread_id"], "early")
        self.assertEqual(parsed["terminal"], "turn.completed")
        self.assertNotIn("private-output", json.dumps(parsed))


if __name__ == "__main__":
    unittest.main()
