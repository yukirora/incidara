"""SSH collector — runs commands on targets via SSH, returns raw output.

Uses pexpect for interactive SSH sessions. Each target gets the same list
of commands. The collector returns raw output per command — all interpretation
belongs in the rule's analyze() code.

Collector config:
    sources:
      - type: ssh
        config:
          commands: ["show version", "show fan", "show power"]
          paging_cmd: "no cli session paging enable"  # optional, run after login
          user: admin
          password_env: SWITCH_SSH_PASSWORD
          concurrency: 64
          timeout: 15

For a single command, just use a one-element list:
    commands: ["systemctl is-active nv-fabricmanager"]

Target resolution:
  - from_inventory: true   → reads (hostname, ip) from switch_inventory table
  - schedulable: true      → resolves from Prometheus pai_node_count
  - hostname_pattern / sample → filters
"""

from __future__ import annotations

import logging
import os
import time
import concurrent.futures
from typing import Any

import psycopg2
import psycopg2.extras

from patrol_cron.collectors.base import BaseCollector
from patrol_cron.models import CollectionResult, TargetData

logger = logging.getLogger(__name__)

EVIDENCE_DB_URL = os.environ.get(
    "EVIDENCE_DB_URL",
    "",
)

PLATFORM_DB_URL = os.environ.get(
    "POSTGRES_CONNECTION_STR",
    "",
)


# ── SSH execution ─────────────────────────────────────────────────────────

def _ssh_run_commands(ip: str, user: str, password: str,
                     commands: list[str] | dict, paging_cmd: str,
                     timeout: int,
                     ssh_options: list[str] | None = None,
                     interactive: bool = False) -> dict:
    """Run commands on a target via SSH.

    Two modes:
      interactive=False (default): subprocess one-shot SSH per command.
          Works for Linux nodes with SSH agent/key auth.
      interactive=True: pexpect interactive session with password auth.
          Works for switches that need paging_cmd and password login.

    Returns {ssh_ok, outputs: {cmd_key: output}, ssh_error}.
    """
    if interactive:
        return _ssh_interactive(ip, user, password, commands, paging_cmd, timeout, ssh_options)
    return _ssh_oneshot(ip, user, password, commands, timeout, ssh_options)


def _ssh_oneshot(ip, user, password, commands, timeout, ssh_options=None):
    """One SSH subprocess per command. Uses SSH agent if available."""
    import subprocess

    base_args = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "GlobalKnownHostsFile=/dev/null",
        "-o", f"ConnectTimeout={timeout}",
    ]
    for opt in (ssh_options or []):
        base_args.extend(["-o", opt])

    outputs = {}
    ok = True
    err = ""

    if isinstance(commands, dict):
        cmd_items = list(commands.items())
    else:
        cmd_items = [(cmd, cmd) for cmd in commands]

    for key, cmd in cmd_items:
        args = base_args + [f"{user}@{ip}", cmd]
        try:
            proc = subprocess.run(
                args, capture_output=True, text=True, timeout=timeout,
                env=os.environ,
            )
            output = proc.stdout.strip()
            if proc.stderr and "Warning: Permanently added" not in proc.stderr:
                if output:
                    output += "\n" + proc.stderr.strip()
                else:
                    output = proc.stderr.strip()
            outputs[key] = output
            if proc.returncode != 0:
                ok = False
                err = output[:500] if output else f"exit code {proc.returncode}"
        except subprocess.TimeoutExpired:
            return {"ssh_ok": False, "outputs": {}, "ssh_error": "timeout"}
        except Exception as e:
            return {"ssh_ok": False, "outputs": {}, "ssh_error": str(e)}

    return {"ssh_ok": ok, "outputs": outputs, "ssh_error": err if not ok else ""}


def _ssh_interactive(ip, user, password, commands, paging_cmd, timeout, ssh_options=None):
    """Interactive pexpect SSH session. For switches with password auth."""
    import pexpect
    import shlex
    import re

    args = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "HostKeyAlgorithms=+ssh-rsa,ssh-dss",
        "-o", "KexAlgorithms=+diffie-hellman-group1-sha1,diffie-hellman-group14-sha1,diffie-hellman-group-exchange-sha1",
        "-o", "Ciphers=+aes128-cbc,aes256-cbc,3des-cbc",
        "-o", "MACs=+hmac-sha1,hmac-md5",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "GlobalKnownHostsFile=/dev/null",
        "-o", "NumberOfPasswordPrompts=2",
        "-o", f"ConnectTimeout={timeout}",
        "-o", "PubkeyAuthentication=no",
    ]
    for opt in (ssh_options or []):
        args.extend(["-o", opt])
    args.append(f"{user}@{ip}")
    ssh_cmd = " ".join(shlex.quote(x) for x in args)

    child = pexpect.spawn(ssh_cmd, timeout=timeout, encoding="utf-8", codec_errors="replace")
    outputs = {}
    err = ""

    def drain(idle_sec=1.0, max_wait=30):
        pieces = []
        deadline = time.time() + max_wait
        while time.time() < deadline:
            try:
                chunk = child.read_nonblocking(size=65536, timeout=idle_sec)
                if not chunk:
                    continue
                pieces.append(chunk)
                tail = "".join(pieces)[-256:].rstrip()
                if tail.endswith("#") or tail.endswith(">"):
                    try:
                        grace = child.read_nonblocking(size=4096, timeout=0.2)
                        if grace:
                            pieces.append(grace)
                    except (pexpect.TIMEOUT, pexpect.EOF):
                        pass
                    return "".join(pieces)
            except pexpect.TIMEOUT:
                return "".join(pieces)
            except pexpect.EOF:
                break
        return "".join(pieces)

    try:
        idx = child.expect(
            [r"[Pp]assword:", r"[Pp]ermission denied", pexpect.EOF, pexpect.TIMEOUT],
            timeout=timeout,
        )
        if idx == 1:
            return {"ssh_ok": False, "outputs": {}, "ssh_error": "permission denied"}
        if idx == 2:
            return {"ssh_ok": False, "outputs": {}, "ssh_error": "connection closed"}
        if idx == 3:
            return {"ssh_ok": False, "outputs": {}, "ssh_error": "timeout before login"}

        child.sendline(password)
        banner = drain(idle_sec=1.0, max_wait=timeout)
        if re.search(r"[Pp]assword:|[Pp]ermission denied", banner):
            return {"ssh_ok": False, "outputs": {}, "ssh_error": "authentication failed"}

        if paging_cmd:
            child.sendline(paging_cmd)
            drain(idle_sec=1.0, max_wait=timeout)

        if isinstance(commands, dict):
            cmd_items = list(commands.items())
        else:
            cmd_items = [(cmd, cmd) for cmd in commands]

        for key, cmd in cmd_items:
            child.sendline(cmd)
            outputs[key] = drain(idle_sec=1.0, max_wait=timeout)

    except Exception as e:
        err = str(e)
    finally:
        try:
            child.sendline("exit")
        except Exception:
            pass
        child.close(force=True)

    return {"ssh_ok": not err, "outputs": outputs, "ssh_error": err}


# ── Collector ─────────────────────────────────────────────────────────────

def _hostname_matches(name: str, pattern: str) -> bool:
    """Match hostname against a glob-style pattern.
    
    Supports: *-b300-* (contains), b300-* (prefix), *-b300 (suffix), exact.
    """
    p = pattern.replace("%", "*")
    if p == "*" or p == "":
        return True
    # Convert glob to regex: * → .*, escape dots, anchor
    import re
    regex = "^" + re.escape(p).replace(r"\*", ".*") + "$"
    return bool(re.match(regex, name))


class SshCollector(BaseCollector):
    """Runs commands on each target via SSH, returns raw output."""

    def collect(self, collector_cfg: dict) -> CollectionResult:
        start = time.time()
        name = collector_cfg["name"]
        target_type = collector_cfg.get("target_type", "node")
        target_filter = collector_cfg.get("target_filter") or {}
        sources = collector_cfg.get("sources") or []

        ssh_src = next((s for s in sources if s.get("type") == "ssh"), None)
        if not ssh_src:
            return CollectionResult(collector_name=name, errors=["No SSH source configured"])

        config = ssh_src.get("config", {})
        concurrency = config.get("concurrency", 64)
        timeout = config.get("timeout", int(os.environ.get("SSH_TIMEOUT", "15")))
        # user/password: check config value first, then env var name, then default
        user_env = config.get("user_env", "SSH_USER")
        user = config.get("user") or os.environ.get(user_env, "root")
        password_env = config.get("password_env", "SSH_PASSWORD")
        password = os.environ.get(password_env, "")

        commands = config.get("commands", [])
        paging_cmd = config.get("paging_cmd", "")
        ssh_options = config.get("ssh_options", [])
        interactive = config.get("interactive", False)

        if not commands:
            return CollectionResult(collector_name=name, errors=["No commands specified in config"])

        targets_list = self._resolve_targets(target_filter)
        if not targets_list:
            return CollectionResult(collector_name=name, errors=["No targets resolved"])

        targets: list[TargetData] = []
        errors: list[str] = []

        def _run(hostname, ip, meta):
            result = _ssh_run_commands(ip, user, password, commands, paging_cmd, timeout, ssh_options, interactive)
            return TargetData(id=hostname, type=target_type, payload=result, meta={"ip": ip, **meta})

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            futs = {pool.submit(_run, h, ip, meta): h
                    for h, ip, meta in targets_list}
            for fut in concurrent.futures.as_completed(futs):
                try:
                    targets.append(fut.result())
                except Exception as e:
                    errors.append(f"{futs[fut]}: {e}")

        duration = time.time() - start
        logger.info(f"[{name}] Collected {len(targets)} targets in {duration:.1f}s")
        return CollectionResult(collector_name=name, targets=targets,
                                errors=errors, duration=duration)

    # ── Target resolution ─────────────────────────────────────────────

    # ── Target resolution ─────────────────────────────────────────────
    # All resolvers return [(hostname, ip, meta_dict), ...]
    # meta_dict may contain "type" for type-aware command dispatch.

    @staticmethod
    def _resolve_targets(target_filter: dict) -> list[tuple[str, str, dict]]:
        if target_filter.get("from_inventory"):
            return SshCollector._load_switch_inventory(target_filter)
        if target_filter.get("schedulable") or target_filter.get("from_prometheus"):
            return SshCollector._resolve_from_prometheus(target_filter)
        return SshCollector._resolve_from_db(target_filter)

    @staticmethod
    def _load_switch_inventory(target_filter: dict = None) -> list[tuple[str, str, dict]]:
        try:
            target_filter = target_filter or {}
            with psycopg2.connect(EVIDENCE_DB_URL) as conn:
                cur = conn.cursor()
                switch_type = target_filter.get("type")
                if switch_type:
                    cur.execute("SELECT hostname, ip, type FROM switch_inventory WHERE type = %s ORDER BY hostname", (switch_type,))
                else:
                    cur.execute("SELECT hostname, ip, type FROM switch_inventory ORDER BY hostname")
                return [(r[0], r[1], {"type": r[2] or ""}) for r in cur.fetchall()]
        except Exception as e:
            logger.warning(f"Failed to load switch inventory: {e}")
            return []

    @staticmethod
    def _resolve_from_db(target_filter: dict) -> list[tuple[str, str, dict]]:
        pattern = target_filter.get("hostname_pattern", "%")
        # Convert wildcard: *b300* → %b300%
        pattern = pattern.replace("*", "%")
        category = target_filter.get("category")
        sample = target_filter.get("sample")
        try:
            with psycopg2.connect(PLATFORM_DB_URL) as conn:
                cur = conn.cursor()
                sql = """SELECT DISTINCT ON (hostname) hostname, ip->>0 AS primary_ip, category, sku
                         FROM ltp_sdk.physical_node_onboard_records
                         WHERE hostname LIKE %s"""
                params: list = [pattern]
                if category:
                    sql += " AND category = %s"
                    params.append(category)
                sql += " ORDER BY hostname, timestamp DESC"
                if sample:
                    sql += f" LIMIT {int(sample)}"
                cur.execute(sql, params)
                return [(r[0], r[1], {"category": r[2], "sku": r[3]}) for r in cur.fetchall()]
        except Exception as e:
            logger.warning(f"Failed to resolve nodes from DB: {e}")
            return []

    @staticmethod
    def _resolve_from_prometheus(target_filter: dict) -> list[tuple[str, str, dict]]:
        try:
            from patrol_cron.collectors.prometheus import PrometheusCollector
            client = PrometheusCollector._get_client()

            filters = ['node_name!~"aks-.*"']
            if target_filter.get("schedulable"):
                filters.append('unschedulable="false"')

            query = f'pai_node_count{{{",".join(filters)}}}'
            end = int(time.time())
            data = client.query_range(query, end - 60, end, step="60s")
            if not data or "result" not in data:
                return []

            nodes = []
            seen = set()
            pattern = target_filter.get("hostname_pattern", "")
            category = target_filter.get("category")
            sample = target_filter.get("sample")
            for rec in data["result"]:
                name = rec["metric"].get("node_name", "")
                ip = rec["metric"].get("host_ip", "")
                if name in seen:
                    continue
                if pattern and pattern != "%" and not _hostname_matches(name, pattern):
                    continue
                seen.add(name)
                nodes.append((name, ip, {}))
                if sample and len(nodes) >= int(sample):
                    break

            # Filter by category if specified — Prometheus doesn't know categories,
            # so look up from platform DB and filter
            if category and nodes:
                try:
                    hostnames = [n[0] for n in nodes]
                    with psycopg2.connect(PLATFORM_DB_URL) as conn:
                        cur = conn.cursor()
                        cur.execute(
                            """SELECT DISTINCT ON (hostname) hostname, category
                               FROM ltp_sdk.physical_node_onboard_records
                               WHERE hostname = ANY(%s)
                               ORDER BY hostname, timestamp DESC""",
                            (hostnames,),
                        )
                        cat_map = {r[0]: r[1] for r in cur.fetchall()}
                    nodes = [(n[0], n[1], {**n[2], "category": cat_map.get(n[0], "")}) for n in nodes
                             if cat_map.get(n[0]) == category]
                except Exception as e:
                    logger.warning(f"Failed to filter Prometheus nodes by category: {e}")
                    # If DB lookup fails, fall back to returning all nodes
                    pass
            return nodes
            return nodes
        except Exception as e:
            logger.warning(f"Failed to resolve from Prometheus: {e}")
            return []
