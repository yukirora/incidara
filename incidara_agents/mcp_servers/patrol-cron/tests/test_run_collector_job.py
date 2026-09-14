"""Tests for patrol_cron.run_collector_job — standalone collector execution."""

import unittest.mock as um
import pytest

from patrol_cron.models import CollectionResult, TargetData, Finding, Observation, RuleResult


class TestRunCollectorJob:
    """run_collector_job loads a collector from DB and runs it with its rules."""

    def test_raw_evidence_hash_ignores_duration(self):
        from patrol_cron.run_collector_job import snapshot_hash_and_input

        base = CollectionResult(
            collector_name="test_coll",
            targets=[
                TargetData(
                    id="n1",
                    type="node",
                    payload={"ssh_ok": False, "ssh_error": "failed"},
                    meta={"rack": "r1"},
                )
            ],
            errors=["collector warning"],
            duration=1.0,
        )
        same_evidence = CollectionResult(
            collector_name="test_coll",
            targets=[
                TargetData(
                    id="n1",
                    type="node",
                    payload={"ssh_ok": False, "ssh_error": "failed"},
                    meta={"rack": "r1"},
                )
            ],
            errors=["collector warning"],
            duration=99.0,
        )

        hash1, _ = snapshot_hash_and_input(base)
        hash2, _ = snapshot_hash_and_input(same_evidence)
        assert hash1 == hash2

    def test_snapshot_input_uses_frozen_collection_result_schema(self):
        from patrol_cron.run_collector_job import snapshot_hash_and_input

        result = CollectionResult(
            collector_name="gpu_events",
            targets=[
                TargetData(
                    id="node-1",
                    type="node",
                    payload={"transient_xid": True},
                    meta={"rack": "r1"},
                )
            ],
            errors=[],
            duration=0.25,
        )

        _, frozen_input = snapshot_hash_and_input(result)

        assert frozen_input == {
            "collector_name": "gpu_events",
            "collector_status": "success",
            "errors": [],
            "duration": 0.25,
            "run_time": 0.0,
            "targets": [
                {
                    "id": "node-1",
                    "type": "node",
                    "payload": {"transient_xid": True},
                    "meta": {"rack": "r1"},
                }
            ],
        }

    def test_runs_collector_and_rules(self):
        from patrol_cron.run_collector_job import run_job

        collector = {
            "name": "test_coll",
            "schedule_sec": 60,
            "target_type": "node",
            "sources": [{"type": "ssh", "name": "c", "config": {}}],
        }
        rule = {
            "rule_id": "test_rule",
            "analyze_code": '''
def analyze(collected, state):
    findings = []
    for t in collected.targets:
        if not t.payload.get("ssh_ok"):
            findings.append(Finding(
                target_id=t.id, severity="warning", action="create_task",
            ))
    return findings, state
''',
            "stage": "create_task",
        }
        result = CollectionResult(
            collector_name="test_coll",
            targets=[TargetData(id="n1", type="node", payload={"ssh_ok": False})],
        )

        with um.patch("patrol_cron.run_collector_job.db") as mock_db, \
             um.patch("patrol_cron.run_collector_job.run_collector", return_value=result), \
             um.patch("patrol_cron.run_collector_job.execute_finding", return_value=True):

            mock_db.get_collector_by_name.return_value = collector
            mock_db.get_rules_for_collector.return_value = [rule]
            mock_db.load_rule_state.return_value = {}
            mock_db.finding_exists.return_value = False

            run_job("test_coll")

        mock_db.save_rule_state.assert_called_once()
        mock_db.insert_finding.assert_called_once()
        insert_kwargs = mock_db.insert_finding.call_args.kwargs
        assert insert_kwargs["collector_snapshot_id"]
        assert insert_kwargs["raw_evidence_hash"]
        mock_db.delete_expired_lifecycle_state.assert_called_once()
        mock_db.update_collector_last_run.assert_called_once_with("test_coll")

    def test_explicit_rule_result_uses_lifecycle_engine(self):
        from patrol_cron.run_collector_job import run_job

        collector = {
            "name": "test_coll",
            "schedule_sec": 60,
            "target_type": "node",
            "sources": [{"type": "ssh", "name": "c", "config": {}}],
            "target_filter": {},
            "enabled": True,
        }
        rule = {
            "rule_id": "test_rule",
            "analyze_code": "def analyze(collected, state): return RuleResult()",
            "stage": "create_task",
        }
        result = CollectionResult(
            collector_name="test_coll",
            targets=[TargetData(id="n1", type="node", payload={"ssh_ok": False})],
        )
        rule_result = RuleResult(observations=[
            Observation(
                signal_key="fm_bad",
                target_id="n1",
                status="bad",
                action="alert",
            )
        ])

        with um.patch("patrol_cron.run_collector_job.db") as mock_db, \
             um.patch("patrol_cron.run_collector_job.run_collector", return_value=result), \
             um.patch("patrol_cron.run_collector_job.run_sandboxed", return_value=rule_result), \
             um.patch("patrol_cron.run_collector_job.apply_rule_result") as apply_rule_result:

            mock_db.get_collector_by_name.return_value = collector
            mock_db.get_rules_for_collector.return_value = [rule]
            mock_db.load_rule_state.return_value = {}

            run_job("test_coll")

        apply_rule_result.assert_called_once()
        mock_db.deactivate_recovered_findings.assert_not_called()
        mock_db.insert_finding.assert_not_called()
        mock_db.delete_expired_lifecycle_state.assert_called_once()

    def test_collector_not_found(self):
        from patrol_cron.run_collector_job import run_job

        with um.patch("patrol_cron.run_collector_job.db") as mock_db:
            mock_db.get_collector_by_name.return_value = None
            # Should not raise, just log and return
            run_job("nonexistent")

        mock_db.update_collector_last_run.assert_not_called()

    def test_collector_failure_still_updates_last_run(self):
        from patrol_cron.run_collector_job import run_job

        collector = {
            "name": "bad_coll", "schedule_sec": 60, "target_type": "node",
            "sources": [{"type": "ssh", "name": "c", "config": {}}],
        }

        with um.patch("patrol_cron.run_collector_job.db") as mock_db, \
             um.patch("patrol_cron.run_collector_job.run_collector",
                      side_effect=Exception("SSH failed")):
            mock_db.get_collector_by_name.return_value = collector

            run_job("bad_coll")

        mock_db.update_collector_last_run.assert_called_once_with("bad_coll")
        mock_db.delete_expired_lifecycle_state.assert_not_called()
