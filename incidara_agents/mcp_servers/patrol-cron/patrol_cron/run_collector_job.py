#!/usr/bin/env python3
"""Run a single collector + its bound rules. Invoked by cron per collector.

Each collector run is an independent OS process. Overlap is prevented by
flock in the crontab entry (see cron_sync.py).

Usage:
    python run_collector_job.py --name switch_health
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from uuid import uuid4

# Add parent to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from patrol_cron import db
from patrol_cron.engine import run_collector
from patrol_cron.analyzer import run_sandboxed
from patrol_cron.actions import execute_finding
from patrol_cron.lifecycle import apply_rule_result

logger = logging.getLogger("patrol_cron.job")


def _jsonable(value):
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    return value


def _serialize_result(result) -> dict:
    """Serialize a CollectionResult to a JSON-safe dict."""
    targets = [
        {
            "id": target.id,
            "type": target.type,
            "payload": _jsonable(target.payload),
            "meta": _jsonable(target.meta),
        }
        for target in result.targets
    ]
    return {
        "collector_name": result.collector_name,
        "collector_status": "error" if result.errors else "success",
        "errors": _jsonable(result.errors),
        "duration": result.duration,
        "run_time": result.run_time,
        "targets": sorted(targets, key=lambda t: (t["id"], t["type"])),
    }


def snapshot_hash_and_input(result) -> tuple[str, dict]:
    """Return (sha256 hash, frozen_input dict) for a CollectionResult.

    Hash excludes duration (timing noise). frozen_input keeps full data for replay.
    """
    frozen_input = _serialize_result(result)
    # Hash only content-relevant fields (exclude duration)
    hash_input = {k: v for k, v in frozen_input.items() if k != "duration"}
    frozen_json = json.dumps(hash_input, sort_keys=True, default=str)
    return hashlib.sha256(frozen_json.encode("utf-8")).hexdigest(), frozen_input


def _collector_config_hash(coll: dict) -> str:
    """Compute collector config hash matching update_collector's provenance hash."""
    coll_name = coll.get("name", "")
    coll_sources = json.dumps(coll.get("sources", []), sort_keys=True)
    coll_filter = json.dumps(coll.get("target_filter", {}), sort_keys=True)
    coll_schedule = str(coll.get("schedule_sec", ""))
    coll_enabled = str(coll.get("enabled", ""))
    return hashlib.md5(
        (coll_name + coll_sources + coll_filter + coll_schedule + coll_enabled).encode()
    ).hexdigest()


def _ssh_payload_ok(payload: dict) -> bool | None:
    if not isinstance(payload, dict):
        return None
    if "ssh_ok" in payload:
        return bool(payload["ssh_ok"])
    if "ok" in payload:
        return bool(payload["ok"])
    return None


def _ssh_payload_error(payload: dict) -> str:
    if not isinstance(payload, dict):
        return ""
    return payload.get("ssh_error") or payload.get("error") or ""


def run_job(collector_name: str):
    """Load collector config from DB, run collection + rules."""
    coll = db.get_collector_by_name(collector_name)
    if not coll:
        logger.error(f"Collector '{collector_name}' not found in DB")
        return

    name = coll["name"]
    start = time.time()
    logger.info(f"Starting collector: {name}")

    # Collect
    try:
        result = run_collector(coll)
    except Exception as e:
        logger.error(f"Collector {name} failed: {e}")
        db.update_collector_last_run(name)
        return

    # Set run_time for time-relative filtering in analyze()
    result.run_time = time.time()

    # Fetch bound rules for this collector
    rules = db.get_rules_for_collector(name)

    # Save collector snapshot (one row per unique collector output, keyed by content hash)
    snapshot_hash, frozen_input = snapshot_hash_and_input(result)
    db.save_collector_snapshot(snapshot_hash, name, frozen_input, len(result.targets))

    if result.errors:
        logger.warning(f"Collector {name} had {len(result.errors)} errors: {result.errors[:3]}")

    # Log sample of raw payloads for pipeline validation
    ssh_statuses = [_ssh_payload_ok(t.payload) for t in result.targets]
    ssh_known = [status for status in ssh_statuses if status is not None]
    ok_count = sum(1 for status in ssh_known if status)
    fail_count = sum(1 for status in ssh_known if not status)
    logger.info(f"Collector {name}: {ok_count} ssh_ok, {fail_count} ssh_failed out of {len(result.targets)}")
    # Log all failing targets with full per-command output for diagnosis
    for t in result.targets:
        if _ssh_payload_ok(t.payload) is False:
            err = _ssh_payload_error(t.payload)
            outputs = t.payload.get("outputs", {})
            logger.warning(f"FAIL {t.id}: error={err}")
            for cmd, out in outputs.items():
                if out:
                    logger.warning(f"  [{cmd}]: {out}")
    # Log 1 passing target with full output for pipeline validation
    for t in result.targets:
        if _ssh_payload_ok(t.payload) is True:
            outputs = t.payload.get("outputs", {})
            logger.info(f"OK {t.id}: outputs_keys={list(outputs.keys())}")
            for cmd, out in outputs.items():
                logger.info(f"  [{cmd}]: {out}")
            break

    # Run bound rules
    for rule in rules:
        rule_id = rule["rule_id"]
        try:
            state = db.load_rule_state(rule_id)
            rule_result = run_sandboxed(
                rule["analyze_code"], result, state
            )
            db.save_rule_state(rule_id, rule_result.state)

            rule_code_hash = hashlib.md5(rule["analyze_code"].encode()).hexdigest()
            provenance = {
                "collector_snapshot_id": snapshot_hash,
                "raw_evidence_hash": snapshot_hash,
                "rule_version_at": str(rule.get("updated_at", "")),
                "rule_code_hash": rule_code_hash,
                "collector_config_hash": _collector_config_hash(coll),
            }

            if rule_result.lifecycle_enabled:
                apply_rule_result(rule_result, rule, coll, provenance)
                continue

            findings = rule_result.legacy_findings()

            # Deactivate findings for targets that have recovered
            # (targets the rule returned findings for are still failing;
            #  targets NOT in the findings have recovered)
            still_failing_ids = {f.target_id for f in findings}
            deactivated = db.deactivate_recovered_findings(rule_id, still_failing_ids)
            if deactivated:
                logger.info(f"Deactivated {deactivated} recovered findings for {rule_id}")

            for f in findings:
                if db.finding_exists(rule_id, f.target_id, f.action):
                    continue
                execute_finding(f, rule["stage"], rule_id=rule_id)
                db.insert_finding(
                    rule_id=rule_id,
                    target_id=f.target_id,
                    target_type=coll.get("target_type", "unknown"),
                    severity=f.severity,
                    action=f.action,
                    action_params=f.action_params,
                    evidence=f.evidence,
                    confidence=f.confidence,
                    **provenance,
                )
                logger.info(
                    f"Finding: rule={rule_id} target={f.target_id} "
                    f"action={f.action} severity={f.severity}"
                )

        except Exception as e:
            logger.error(f"Rule {rule_id} failed: {e}")
            continue

    try:
        expired_count = db.delete_expired_lifecycle_state()
        if expired_count:
            logger.info(f"Deleted {expired_count} expired lifecycle state rows")
    except Exception as e:
        logger.error(f"Lifecycle state cleanup failed: {e}")

    db.update_collector_last_run(name)
    elapsed = time.time() - start
    logger.info(f"Collector {name} complete in {elapsed:.1f}s")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(description="Run a patrol collector job")
    parser.add_argument("--name", required=True, help="Collector name")
    args = parser.parse_args()

    run_job(args.name)


if __name__ == "__main__":
    main()
