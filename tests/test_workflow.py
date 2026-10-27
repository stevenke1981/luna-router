"""Offline workflow contract tests; fake plans/agent IDs are not live proof."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import threading
import unittest

from workflow import MAX_PROGRESS_EVENTS, WorkflowConflict, WorkflowStore


def plan():
    return {"status": "planned", "backend": "codex", "simulation": False,
            "decision_source": "luna_decisions", "selected_model": "gpt-6.1-sol",
            "requested_effort": "medium", "decision_call_attempted": True,
            "decision_response_received": True, "decision_model_called": True,
            "decision_model": "openai/gpt-6-luna-decisions",
            "decision_response_id": "fixture-not-a-live-decision",
            "decision_metrics": {"model": "openai/gpt-6-luna-decisions-20261006",
                                 "selected_id": "gpt-6.1-sol", "effort": "medium"}}


def task(identifier="a", scopes=None, deps=None):
    return {"id": identifier, "objective": "Implement the bounded fixture task",
            "kind": "coding", "complexity": 2, "risk": "low",
            "write_scopes": [identifier + ".py"] if scopes is None else scopes,
            "depends_on": [] if deps is None else deps}


def verification(code=0):
    return {"evidence_kind": "verifier", "check_id": "a" * 64,
            "evidence_sha256": "b" * 64, "exit_code": code,
            "outcome": "passed" if code == 0 else "failed"}


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.state = Path(self.temp.name) / "state"
        self.store = WorkflowStore(self.state)

    def create(self, tasks=None, config=None):
        return self.store.create(str(self.project), tasks or [task()], config)["job"]["id"]

    def ready(self, job, identifier="a"):
        self.store.begin_decision(job, identifier)
        return self.store.record_decision(job, identifier, plan())

    def running(self, job, identifier="a"):
        self.ready(job, identifier)
        self.store.claim_dispatch(job, identifier)
        return self.store.started(job, identifier, "native-fixture-" + identifier)

    def integrated(self):
        job = self.create()
        self.running(job)
        self.store.complete(job, "a", "completed", "A turn finished")
        self.store.claim_integration(job)
        self.store.integration_started(job, "native-fixture-integrator")
        self.store.integration_complete(job, "completed", "An integration turn finished")
        return job

    def test_defaults_project_binding_and_restart(self):
        job = self.create()
        data = WorkflowStore(self.state).snapshot(job)
        self.assertEqual(data["job"]["id"], job)
        self.assertEqual(data["job"]["project"], str(self.project.resolve()))
        self.assertEqual(data["job"]["config"]["coordinator_model"], "gpt-6-luna")
        self.assertEqual(data["job"]["config"]["coordinator_effort"], "low")
        self.assertEqual(data["job"]["config"]["integrator_model"], "gpt-6.1-sol")
        self.assertEqual(data["job"]["config"]["integrator_effort"], "high")
        self.assertEqual(data["job"]["config"]["max_parallel"], 2)
        self.assertEqual(data["tasks"][0]["data_policy"], "metadata_only")
        data["tasks"][0]["objective"] = "Mutating a snapshot has no effect"
        self.assertNotEqual(data["tasks"][0]["objective"], self.store.snapshot(job)["tasks"][0]["objective"])

    def test_project_must_be_absolute_existing_directory(self):
        for value in ("relative", str(self.project / "absent"), str(self.state / "workflow.sqlite3")):
            with self.subTest(value=value), self.assertRaises((ValueError, FileNotFoundError)):
                self.store.create(value, [task()])

    def test_invalid_configuration(self):
        values = [{"unknown": True}, {"max_parallel": True}, {"max_parallel": 0}, {"max_parallel": 17},
                  {"integrator_model": ""}, {"coordinator_effort": "impossible"},
                  {"integrator_model": "openai/gpt-6-luna-decisions"}, {"max_parallel": "2"},
                  {"integrator_model": "sk-abcdefghijklmnop"}]
        for value in values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.create(config=value)

    def test_scope_validation_and_case_duplicates(self):
        values = ["/absolute", "C:\\absolute", "C:relative", "../escape", "a/../b", "a/*", "a/?.py",
                  "a/[a].py", "a//b", "./a", ".", "a.", "a ", "a:stream", "\\\\host\\share", "a\nb"]
        for value in values:
            with self.subTest(scope=value), self.assertRaises(ValueError):
                self.create([task(scopes=[value])])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.create([task(scopes=["Src/Foo.py", "src\\foo.py"])])

    def test_dag_and_task_validation(self):
        values = [[], [task(), task()], [task("bad id")], [task(deps=["absent"])],
                  [task(deps=["a"])], [task("a", deps=["b"]), task("b", deps=["a"])],
                  [task(deps=["b", "b"]), task("b")], [{**task(), "write_scopes": "a.py"}],
                  [{**task(), "data_policy": "public"}], [{**task(), "summary_approved": True}]]
        for value in values:
            with self.subTest(tasks=value), self.assertRaises(ValueError):
                self.store.create(str(self.project), value)

    def test_fresh_real_luna_plan_required_and_sanitized(self):
        job = self.create()
        self.store.begin_decision(job, "a")
        patches = [{"decision_source": "local_fallback"}, {"decision_source": "luna_decisions_cache"},
                   {"simulation": True}, {"simulation": None}, {"decision_model_called": False},
                   {"decision_response_received": False}, {"decision_call_attempted": False},
                   {"decision_model": "different/model"}, {"backend": "openrouter"},
                   {"requested_effort": "arbitrary"}, {"decision_metrics": None},
                   {"worker": {"model_id": "gpt-6-astra"}},
                   {"decision_metrics": {**plan()["decision_metrics"], "selected_id": "gpt-6-astra"}}]
        for patch in patches:
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.store.record_decision(job, "a", {**plan(), **patch})
        original = {**plan(), "raw_objective": "must not be saved", "notes": ["api_key=secretfixture"]}
        state = self.store.record_decision(job, "a", original)
        self.assertEqual(state["tasks"][0]["status"], "ready")
        self.assertNotIn("must not be saved", json.dumps(state))
        self.assertNotIn("secretfixture", json.dumps(state))

    def test_blocked_routing_leaves_independent_work_available(self):
        job = self.create([task("a"), task("b")])
        self.store.begin_decision(job, "a")
        self.store.record_decision(job, "a", {"status": "blocked", "reason": "HTTP 502 api_key=secretfixture"})
        self.running(job, "b")
        state = self.store.snapshot(job)
        self.assertEqual([item["status"] for item in state["tasks"]], ["routing_blocked", "running"])
        self.assertEqual(state["job"]["status"], "executing")
        self.assertNotIn("secretfixture", json.dumps(state))
        self.store.begin_decision(job, "a")
        self.assertEqual(self.store.record_decision(job, "a", plan())["tasks"][0]["status"], "ready")

    def test_dispatch_claim_atomic_duplicate_and_restart_no_replay(self):
        job = self.create()
        self.ready(job)
        barrier = threading.Barrier(2)
        def claim():
            store = WorkflowStore(self.state)
            barrier.wait()
            try:
                return store.claim_dispatch(job, "a")
            except WorkflowConflict:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: claim(), range(2)))
        self.assertEqual(sum(result is not None for result in results), 1)
        descriptor = next(result for result in results if result is not None)
        self.assertEqual(descriptor["model"], "gpt-6.1-sol")
        self.assertEqual(descriptor["effort"], "medium")
        self.assertEqual(descriptor["provenance"], "luna_decisions")
        self.assertEqual(descriptor["worker_header"], "[LUNA ROUTER WORKER]")
        restarted = WorkflowStore(self.state)
        with self.assertRaises(WorkflowConflict):
            restarted.claim_dispatch(job, "a")
        with self.assertRaises(WorkflowConflict):
            restarted.begin_decision(job, "a")
        self.assertEqual(restarted.snapshot(job)["tasks"][0]["status"], "dispatching")

    def test_parallel_limit_counts_dispatching_and_running(self):
        job = self.create([task("a"), task("b"), task("c")])
        for identifier in ("a", "b", "c"):
            self.ready(job, identifier)
        self.store.claim_dispatch(job, "a")
        self.store.claim_dispatch(job, "b")
        self.store.started(job, "b", "native-b")
        with self.assertRaisesRegex(WorkflowConflict, "max_parallel"):
            self.store.claim_dispatch(job, "c")
        self.store.complete(job, "b", "passed")
        self.assertEqual(self.store.claim_dispatch(job, "c")["task_id"], "c")

    def test_concurrent_parallel_limit_is_atomic(self):
        job = self.create([task("a"), task("b")], {"max_parallel": 1})
        for identifier in ("a", "b"):
            self.ready(job, identifier)
        barrier = threading.Barrier(2)
        def claim(identifier):
            barrier.wait()
            try:
                return self.store.claim_dispatch(job, identifier)
            except WorkflowConflict:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, ("a", "b")))
        self.assertEqual(sum(result is not None for result in results), 1)

    def test_scope_collision_case_and_directory_ancestry(self):
        job = self.create([task("a", ["Src"]), task("b", ["sRC/file.py"]), task("c", ["src_other/file.py"])])
        for identifier in ("a", "b", "c"):
            self.ready(job, identifier)
        self.store.claim_dispatch(job, "a")
        with self.assertRaisesRegex(WorkflowConflict, "overlap"):
            self.store.claim_dispatch(job, "b")
        self.store.claim_dispatch(job, "c")
        self.store.started(job, "a", "native-a")
        self.store.complete(job, "a", "completed")
        self.store.claim_dispatch(job, "b")

    def test_dependencies_require_completed_turn(self):
        job = self.create([task("a"), task("b", deps=["a"])])
        self.ready(job, "b")
        with self.assertRaisesRegex(WorkflowConflict, "dependencies"):
            self.store.claim_dispatch(job, "b")
        self.running(job, "a")
        with self.assertRaises(WorkflowConflict):
            self.store.claim_dispatch(job, "b")
        self.store.complete(job, "a", "completed")
        self.store.claim_dispatch(job, "b")

    def test_invalid_transitions_and_native_id_uniqueness(self):
        job = self.create([task("a"), task("b")])
        for operation in (lambda: self.store.started(job, "a", "agent"),
                          lambda: self.store.complete(job, "a", "completed"),
                          lambda: self.store.progress(job, "a", "pending"),
                          lambda: self.store.record_decision(job, "a", plan()),
                          lambda: self.store.claim_dispatch(job, "a"),
                          lambda: self.store.claim_integration(job)):
            with self.assertRaises(WorkflowConflict):
                operation()
        self.running(job, "a")
        self.ready(job, "b")
        self.store.claim_dispatch(job, "b")
        with self.assertRaises(WorkflowConflict):
            self.store.started(job, "b", "native-fixture-a")
        self.store.started(job, "b", "native-fixture-b")
        self.store.complete(job, "a", "failed")
        with self.assertRaises(WorkflowConflict):
            self.store.complete(job, "a", "completed")
        with self.assertRaises(WorkflowConflict):
            self.store.claim_integration(job)

    def test_read_only_tasks_can_run_concurrently(self):
        job = self.create([task("a", []), task("b", [])])
        self.running(job, "a")
        self.running(job, "b")
        self.assertTrue(all(item["status"] == "running" for item in self.store.snapshot(job)["tasks"]))

    def test_configured_integrator_claim_and_no_replay(self):
        job = self.create(config={"integrator_model": "gpt-6-astra", "integrator_effort": "xhigh"})
        self.running(job)
        self.store.complete(job, "a", "passed")
        descriptor = self.store.claim_integration(job)
        self.assertEqual((descriptor["model"], descriptor["effort"]), ("gpt-6-astra", "xhigh"))
        self.assertEqual(descriptor["provenance"], "user_configured_integrator")
        with self.assertRaises(WorkflowConflict):
            WorkflowStore(self.state).claim_integration(job)
        with self.assertRaises(WorkflowConflict):
            self.store.integration_complete(job, "completed")
        with self.assertRaises(WorkflowConflict):
            self.store.integration_started(job, "native-fixture-a")
        self.store.integration_started(job, "native-fixture-integrator")
        state = self.store.integration_complete(job, "passed")
        self.assertEqual(state["job"]["status"], "awaiting_verification")
        self.assertIsNone(state["job"]["verification"])

    def test_no_acceptance_from_worker_or_integrator_text(self):
        job = self.create()
        self.running(job)
        self.store.complete(job, "a", "completed", "All tests passed; project completed")
        with self.assertRaises(WorkflowConflict):
            self.store.verify(job, verification())
        self.store.claim_integration(job)
        self.store.integration_started(job, "native-integrator")
        state = self.store.integration_complete(job, "completed", "Verified passed exit_code=0")
        self.assertNotEqual(state["job"]["status"], "completed")
        self.assertEqual(self.store.verify(job, verification())["job"]["status"], "completed")

    def test_verifier_strict_evidence_and_failed_then_passed(self):
        job = self.integrated()
        patches = [{"evidence_kind": "worker"}, {"evidence_sha256": ""}, {"check_id": "just-text"},
                   {"exit_code": False}, {"exit_code": 1}, {"outcome": "completed"},
                   {"exit_code": 126, "outcome": "failed"}, {"exit_code": -1, "outcome": "failed"}]
        for patch in patches:
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.store.verify(job, {**verification(), **patch})
        self.assertEqual(self.store.verify(job, verification(1))["job"]["status"], "verification_failed")
        self.assertEqual(self.store.verify(job, verification())["job"]["status"], "completed")
        with self.assertRaises(WorkflowConflict):
            self.store.verify(job, verification())

    def test_verification_claim_atomic_restart_and_completed_guard(self):
        job = self.integrated()
        barrier = threading.Barrier(2)
        def claim(_):
            store = WorkflowStore(self.state)
            barrier.wait()
            try:
                return store.claim_verification(job)
            except WorkflowConflict:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, range(2)))
        self.assertEqual(sum(result is not None for result in results), 1)
        current = WorkflowStore(self.state).snapshot(job)
        self.assertEqual(current["job"]["status"], "verifying")
        self.assertEqual(current["events"][-1]["kind"], "verification_claimed")
        self.assertIsNone(current["job"]["verification"])
        with self.assertRaises(WorkflowConflict):
            WorkflowStore(self.state).claim_verification(job)
        self.assertEqual(self.store.verify(job, verification())["job"]["status"], "completed")
        with self.assertRaises(WorkflowConflict):
            self.store.claim_verification(job)

    def test_verification_unavailable_is_durable_without_acceptance(self):
        job = self.integrated()
        claimed = self.store.claim_verification(job)
        failure = {"outcome": "unknown", "evidence_kind": "worker", "reason": "verifier_timeout_no_retry",
                   "check_id": "c" * 64, "stdout": "api_key=neverpersistthis", "exception": "secret traceback"}
        state = self.store.verification_unavailable(job, failure)
        self.assertEqual(state["job"]["status"], "awaiting_verification")
        self.assertIsNone(state["job"]["verification"])
        self.assertGreater(state["cursor"], claimed["cursor"])
        self.assertEqual(state["events"][-1]["kind"], "verification_unavailable")
        restarted = WorkflowStore(self.state).snapshot(job)
        self.assertEqual(restarted["job"]["verification_attempt"]["reason"], "verifier_timeout_no_retry")
        self.assertEqual(restarted["job"]["verification_attempt"]["check_id"], "c" * 64)
        self.assertNotIn("neverpersistthis", json.dumps(restarted))
        self.assertNotIn("secret traceback", json.dumps(restarted))
        with self.assertRaises(WorkflowConflict):
            self.store.verification_unavailable(job, failure)
        self.store.claim_verification(job)
        state = self.store.verify(job, verification(1))
        self.assertEqual(state["job"]["status"], "verification_failed")
        self.store.claim_verification(job)
        self.assertEqual(self.store.verify(job, verification())["job"]["status"], "completed")

    def test_verification_unavailable_validates_infrastructure_evidence(self):
        job = self.integrated()
        self.store.claim_verification(job)
        failure = {"outcome": "blocked", "evidence_kind": "worker", "reason": "verifier_cannot_launch",
                   "check_id": "d" * 64}
        patches = [{"outcome": "passed"}, {"evidence_kind": "verifier"}, {"reason": "raw secret details"},
                   {"check_id": None}, {"check_id": "not a hash"}, {"exit_code": False}]
        for patch in patches:
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.store.verification_unavailable(job, {**failure, **patch})
        state = self.store.verification_unavailable(job, {**failure, "reason": "verifier_execution_invalid", "exit_code": -9})
        self.assertEqual(state["job"]["verification_attempt"]["exit_code"], -9)
        self.store.claim_verification(job)
        state = self.store.verification_unavailable(job, {"outcome": "blocked", "evidence_kind": "worker",
                                                        "reason": "verifier_contract_invalid"})
        self.assertIsNone(state["job"]["verification_attempt"]["check_id"])
        self.assertEqual(state["job"]["status"], "awaiting_verification")

    def test_integration_failure_cannot_verify(self):
        job = self.create()
        self.running(job)
        self.store.complete(job, "a", "completed")
        self.store.claim_integration(job)
        self.store.integration_started(job, "integrator")
        self.assertEqual(self.store.integration_complete(job, "failed")["job"]["status"], "integration_failed")
        with self.assertRaises(WorkflowConflict):
            self.store.verify(job, verification())

    def test_event_pagination_is_job_scoped_and_stable(self):
        job = self.create()
        other = self.create()
        self.running(job)
        self.running(other)
        self.store.progress(job, "a", "halfway")
        first = self.store.snapshot(job, limit=2)
        self.assertTrue(first["has_more"])
        second = self.store.snapshot(job, first["cursor"], limit=2)
        third = self.store.snapshot(job, second["cursor"], limit=2)
        rows = first["events"] + second["events"] + third["events"]
        self.assertEqual(len(rows), 6)
        self.assertEqual(len({row["seq"] for row in rows}), 6)
        empty = self.store.snapshot(job, third["cursor"])
        self.assertEqual(empty["cursor"], third["cursor"])
        self.assertFalse(empty["has_more"])
        for cursor in (-1, True, "3"):
            with self.assertRaises(ValueError):
                self.store.snapshot(job, cursor)

    def test_progress_redaction_bound_and_terminal_guard(self):
        job = self.create()
        self.running(job)
        secret_text = ('api_key=verysecretfixture password: secret123 Bearer token123 '
                       'sk-abcdefghijklmnop ghp_abcdefghijklmnop '
                       '"api_key": "jsonsecretfixture", OPENROUTER_API_KEY=envsecretfixture')
        self.store.progress(job, "a", secret_text)
        data = json.dumps(self.store.snapshot(job))
        for secret in ("verysecretfixture", "secret123", "token123", "sk-abcdefghijklmnop", "ghp_abcdefghijklmnop",
                       "jsonsecretfixture", "envsecretfixture"):
            self.assertNotIn(secret, data)
        self.store.progress(job, "a", "x" * 16000)
        self.assertEqual(len(self.store.snapshot(job)["events"][-1]["data"]["summary"]), 2048)
        for index in range(MAX_PROGRESS_EVENTS - 2):
            self.store.progress(job, "a", str(index))
        with self.assertRaisesRegex(WorkflowConflict, "limit"):
            self.store.progress(job, "a", "too many")
        state = self.store.complete(job, "a", "completed", "-----BEGIN PRIVATE KEY-----\nprivatefixture\n-----END PRIVATE KEY-----")
        self.assertNotIn("privatefixture", json.dumps(state))
        with self.assertRaises(WorkflowConflict):
            self.store.progress(job, "a", "after turn")

    def test_agent_identifiers_do_not_store_obvious_credentials(self):
        job = self.create()
        self.ready(job)
        self.store.claim_dispatch(job, "a")
        with self.assertRaisesRegex(ValueError, "credentials"):
            self.store.started(job, "a", "sk-abcdefghijklmnop")
        self.store.started(job, "a", "native-agent")
        self.store.complete(job, "a", "completed")
        self.store.claim_integration(job)
        with self.assertRaisesRegex(ValueError, "credentials"):
            self.store.integration_started(job, "api_key=secretfixture")


if __name__ == "__main__":
    unittest.main()
