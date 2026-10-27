import argparse
import copy
import json
import os
from pathlib import Path
import tempfile
import tomllib
import subprocess
import sys
import unittest
from unittest.mock import patch

import free_agents as fa
import openrouter_backend as orb
from policy import build_candidates, normalize_task


def rec(model_id="sample/worker:free", outputs=None, tools=True, pricing=None, context=4096):
    return {
        "id": model_id,
        "pricing": {"prompt": "0", "completion": "0", "request": "0"} if pricing is None else pricing,
        "architecture": {"input_modalities": ["text"], "output_modalities": outputs or ["text"]},
        "context_length": context,
        "supported_parameters": ["max_tokens", "tools"] if tools else ["max_tokens"],
        "expiration_date": None,
    }


def catalog(*items):
    return {"data": list(items), "source": "local-test", "fetched_at": "2026-10-27T00:00:00Z", "simulation": True}


class FreeAgentTests(unittest.TestCase):
    def test_validate_zero_free_slugs_and_reject_paid_surprise(self):
        self.assertTrue(fa._is_free(rec()))
        self.assertFalse(fa._is_free(rec(pricing={"prompt": "0", "completion": "0", "image": "0.1"})))
        self.assertFalse(fa._is_free(rec("sample/worker", pricing={"prompt": "0", "completion": "0"})))
        self.assertFalse(fa._is_free(rec(pricing={"prompt": "0", "completion": "0.0002"})))
        self.assertFalse(fa._is_free(rec(pricing={"prompt": "0"})))
        self.assertFalse(fa._is_free(rec(pricing={"prompt": "NaN", "completion": "0"})))
        self.assertTrue(fa._is_free(rec("openrouter/free")))

    def test_duplicate_ids_fail(self):
        with self.assertRaises(fa.AgentError):
            fa.registry_from_catalog(catalog(rec(), rec()))

    def test_slug_stable_path_safe_and_collision_resistant(self):
        x = fa.agent_slug("sample/a-b:free")
        y = fa.agent_slug("sample/a_b:free")
        self.assertNotEqual(x, y)
        self.assertTrue(x.startswith("orfree_"))
        self.assertLess(len(x), 70)
        with self.assertRaises(fa.AgentError):
            fa.agent_slug("../../config.toml")

    def test_each_supported_special_kind_has_bridge(self):
        models = [
            rec("sample/chat:free", ["text"]),
            rec("sample/decide:free", ["decisions"], context=None),
            rec("sample/embed:free", ["embeddings"], context=None),
            rec("sample/rank:free", ["rerank"], context=None),
            rec("sample/tts:free", ["speech"], context=None),
        ]
        with tempfile.TemporaryDirectory() as d:
            reg = fa.build_generated(catalog(*models), Path(d))
            self.assertEqual(reg["total"], 5)
            self.assertEqual(set(reg["by_kind"].values()), {1})
            files = list((Path(d) / "codex-agents").glob("*.toml"))
            self.assertEqual(len(files), 6)  # 5 bridge + 1 native chat
            for f in files:
                role = tomllib.loads(f.read_text(encoding="utf-8"))
                self.assertIn("name", role)
                self.assertIn("description", role)
                self.assertIn("developer_instructions", role)
                if f.stem.endswith("native"):
                    self.assertEqual(role["model"], "sample/chat:free")
                else:
                    self.assertNotIn("model", role)

    def test_text_model_without_tools_disables_native_shell(self):
        with tempfile.TemporaryDirectory() as d:
            reg = fa.build_generated(catalog(rec(tools=False)), Path(d))
            agent = (Path(d) / "codex-agents" / (reg["agents"][0]["name"] + "_native.toml"))
            self.assertFalse(tomllib.loads(agent.read_text(encoding="utf-8"))["features"]["shell_tool"])

    def test_reject_overwriting_unmanaged_agent(self):
        with tempfile.TemporaryDirectory() as d:
            reg = fa.build_generated(catalog(rec()), Path(d))
            p = Path(d) / "codex-agents" / (reg["agents"][0]["name"] + ".toml")
            p.write_text("# user file", encoding="utf-8")
            with self.assertRaises(fa.AgentError):
                fa.build_generated(catalog(rec()), Path(d))

    def test_removes_retired_managed_agent(self):
        with tempfile.TemporaryDirectory() as d:
            fa.build_generated(catalog(rec("a/one:free"), rec("b/two:free")), Path(d))
            fa.build_generated(catalog(rec("a/one:free")), Path(d))
            self.assertEqual(len(list((Path(d) / "codex-agents").glob("*.toml"))), 2)

    def test_free_rechecked_at_every_call(self):
        with self.assertRaises(fa.AgentError):
            fa.fresh_record("a/one:free", catalog(rec("a/one:free", pricing={"prompt": "1", "completion": "0"})))
        with self.assertRaises(fa.AgentError):
            fa.fresh_record("a/one:free", catalog(rec("b/two:free")))

    def test_text_invocation_uses_selected_verified_model(self):
        record = rec()
        e = fa.registry_from_catalog(catalog(record))["agents"][0]
        with patch.object(orb, "complete", return_value={"status": "response_received", "requested_model": record["id"]}) as mocked:
            out = fa.invoke(e, "hello", catalog=catalog(record))
            self.assertEqual(out["status"], "response_received")
            self.assertEqual(mocked.call_args.args[0]["id"], record["id"])
            self.assertEqual(mocked.call_args.args[1], "hello")

    def test_decisions_uses_structured_endpoint_not_chat(self):
        record = rec("demo/decisions:free", ["decisions"])
        e = fa.registry_from_catalog(catalog(record))["agents"][0]
        with patch.object(orb, "request_json", return_value={"model": record["id"], "answers": {"yes": {}}}) as mock:
            out = fa.invoke(e, {"state": "hello", "questions": {"yes": {"type": "boolean"}}}, catalog=catalog(record))
            self.assertEqual(out["kind"], "decisions")
            self.assertEqual(mock.call_args.args[1], "/api/alpha/decisions")
            self.assertEqual(mock.call_args.args[2]["model"], record["id"])

    def test_embedding_uses_embedding_endpoint(self):
        r = rec("demo/embed:free", ["embeddings"])
        e = fa.registry_from_catalog(catalog(r))["agents"][0]
        with patch.object(orb, "request_json", return_value={"model": r["id"], "data": [{"embedding": [0.1]}]}) as mock:
            out = fa.invoke(e, {"input": "hello"}, catalog=catalog(r))
            self.assertEqual(mock.call_args.args[1], "/api/v1/embeddings")
            self.assertEqual(out["status"], "response_received")

    def test_rerank_uses_rerank_endpoint(self):
        r = rec("demo/ranker:free", ["rerank"])
        e = fa.registry_from_catalog(catalog(r))["agents"][0]
        with patch.object(orb, "request_json", return_value={"model": r["id"], "results": []}) as mock:
            fa.invoke(e, {"query": "q", "documents": ["a", "b"]}, catalog=catalog(r))
            self.assertEqual(mock.call_args.args[1], "/api/v1/rerank")

    def test_cannot_override_model_or_provider(self):
        for k in ("model", "provider", "unknown"):
            with self.subTest(k=k), self.assertRaises(fa.AgentError):
                fa._validate_payload("decisions", {"state": "a", "questions": {"x": {}}, k: "override"})

    def test_privacy_requires_explicit_public_or_approved(self):
        for policy, approved, allowed in [
            ("no_external", False, False), ("approved", False, False),
            ("approved", True, True), ("public", False, True),
        ]:
            a = argparse.Namespace(data_policy=policy, approved=approved)
            if allowed:
                self.assertIsNone(fa._privacy(a))
            else:
                with self.assertRaises(fa.AgentError):
                    fa._privacy(a)

    def test_codex_argv_is_stdin_and_no_persistent_config(self):
        e = fa.registry_from_catalog(catalog(rec()))["agents"][0]
        args = fa.codex_argv(e, Path("/tmp/demo"))
        self.assertEqual(args[-1], "-")
        self.assertIn('model_provider="openrouter_free"', args)
        self.assertIn("read-only", args)
        self.assertNotIn("--yolo", args)
        self.assertIn("--ephemeral", args)
        self.assertNotIn("OPENROUTER_ACTUAL_SECRET", " ".join(args))
        self.assertIn("workspace-write", fa.codex_argv(e, Path("/tmp/demo"), editable=True))
        with self.assertRaises(fa.AgentError):
            fa.codex_argv(fa.registry_from_catalog(catalog(rec(tools=False)))["agents"][0], Path("/tmp/demo"))

    def test_install_does_not_modify_codex_config(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as workspace:
            src = Path(d)
            (src / "generated").mkdir()
            (src / "free_agents.py").write_bytes((fa.ROOT / "free_agents.py").read_bytes())
            (src / "openrouter_backend.py").write_bytes((fa.ROOT / "openrouter_backend.py").read_bytes())
            (src / "credentials.py").write_bytes((fa.ROOT / "credentials.py").read_bytes())
            fa.build_generated(catalog(rec()), src / "generated")
            config = Path(workspace) / ".codex" / "config.toml"
            config.parent.mkdir()
            config.write_text('model = "gpt-6.1-sol"\n', encoding="utf-8")
            result = fa._install(src, "project", workspace)
            self.assertEqual(config.read_text(encoding="utf-8"), 'model = "gpt-6.1-sol"\n')
            self.assertFalse(result["modified_codex_config"])
            installed = Path(workspace)/".codex"/"free-agent-router"
            self.assertTrue((installed/"generated"/"registry.json").is_file())
            self.assertEqual(result["roles_installed"], 2)
            # Copying the executable and registry is not enough: it must work
            # from the installed location, without relying on the release dir.
            p = subprocess.run([sys.executable, str(installed/"free_agents.py"), "list", "--json"], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(json.loads(p.stdout)["count"], 1)
            bridge = next(p for p in (Path(workspace)/".codex"/"agents").glob("orfree_*.toml") if not p.stem.endswith("_native"))
            self.assertIn(".codex", tomllib.loads(bridge.read_text(encoding="utf-8"))["developer_instructions"])

    def test_live_catalog_all_modalities(self):
        with patch.object(orb, "catalog", return_value={"data": []}) as mocked:
            fa.fetch_catalog()
            mocked.assert_called_once_with("all")

    def test_dynamic_free_pin_outside_shortlist(self):
        r1, r2 = rec("some/x:free", context=8192), rec("some/z:free", context=8192)
        cfg = {"policy": {"max_effort": "max", "dynamic_free_profiles": True, "max_dynamic_free_candidates": 1}, "gpt_profiles": [], "free_profiles": []}
        t = normalize_task({"objective": "extract 3 keywords", "kind": "extraction", "complexity": 1, "risk": "low", "needs_tools": False, "data_policy": "public", "requested_model": r2["id"]})
        cs = build_candidates(t, cfg, {"models": []}, catalog(r1, r2))
        self.assertEqual(len(cs), 1)
        self.assertEqual(cs[0]["id"], r2["id"])

    def test_dynamic_free_bounded_shortlist(self):
        records = [rec("many/model" + str(i) + ":free", context=8192) for i in range(50)]
        cfg = {"policy": {"dynamic_free_profiles": True, "max_dynamic_free_candidates": 12}, "gpt_profiles": [], "free_profiles": []}
        t = normalize_task({"objective": "summarize", "kind": "summary", "complexity": 1, "risk": "low", "needs_tools": False, "data_policy": "public"})
        cs = build_candidates(t, cfg, {"models": []}, catalog(*records))
        self.assertEqual(len(cs), 12)

    def test_private_or_tool_task_never_gets_dynamic_free_text(self):
        r = rec(context=8192)
        cfg = {"policy": {"dynamic_free_profiles": True}, "gpt_profiles": [], "free_profiles": []}
        for updates in ({"data_policy": "metadata_only"}, {"needs_tools": True}, {"risk": "high"}):
            raw = {"objective": "task", "kind": "summary", "complexity": 1, "risk": "low", "needs_tools": False, "data_policy": "public", **updates}
            cs = build_candidates(normalize_task(raw), cfg, {"models": []}, catalog(r))
            self.assertEqual(cs, [])

    def test_real_snapshot_generates_one_per_model(self):
        snapshot = fa.safe_json(fa.ROOT / "evidence/openrouter-text-free.json")
        reg = fa.registry_from_catalog(snapshot)
        self.assertEqual(reg["total"], len(snapshot["data"]))
        self.assertTrue(all(x["id"].endswith(":free") or x["id"] == "openrouter/free" for x in reg["agents"]))


if __name__ == "__main__":
    unittest.main()
