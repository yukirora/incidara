"""Tests for patrol_cron.cron_sync — DB → crontab synchronization."""

import unittest.mock as um
import pytest


class TestBuildCrontab:
    """Build crontab content from collector configs."""

    def test_60s_becomes_every_minute(self):
        from patrol_cron.cron_sync import _collector_to_cron_line
        line = _collector_to_cron_line({
            "name": "ecc_check", "schedule_sec": 60, "enabled": True,
        })
        assert line.startswith("* * * * *")
        assert "ecc_check" in line

    def test_120s_becomes_every_2_min(self):
        from patrol_cron.cron_sync import _collector_to_cron_line
        line = _collector_to_cron_line({
            "name": "switch_health", "schedule_sec": 120, "enabled": True,
        })
        assert line.startswith("*/2 * * * *")

    def test_1800s_becomes_every_30_min(self):
        from patrol_cron.cron_sync import _collector_to_cron_line
        line = _collector_to_cron_line({
            "name": "fm_check", "schedule_sec": 1800, "enabled": True,
        })
        assert line.startswith("*/30 * * * *")

    def test_3600s_becomes_every_hour(self):
        from patrol_cron.cron_sync import _collector_to_cron_line
        line = _collector_to_cron_line({
            "name": "hourly", "schedule_sec": 3600, "enabled": True,
        })
        assert line.startswith("0 * * * *")

    def test_30s_rounds_up_to_every_minute(self):
        """Sub-minute intervals round up to 1 minute (cron minimum)."""
        from patrol_cron.cron_sync import _collector_to_cron_line
        line = _collector_to_cron_line({
            "name": "fast", "schedule_sec": 30, "enabled": True,
        })
        assert line.startswith("* * * * *")

    def test_line_includes_flock(self):
        """Each cron entry should use flock to prevent overlap."""
        from patrol_cron.cron_sync import _collector_to_cron_line
        line = _collector_to_cron_line({
            "name": "ecc_check", "schedule_sec": 60, "enabled": True,
        })
        assert "flock" in line

    def test_disabled_collector_returns_none(self):
        from patrol_cron.cron_sync import _collector_to_cron_line
        line = _collector_to_cron_line({
            "name": "disabled", "schedule_sec": 60, "enabled": False,
        })
        assert line is None


class TestBuildFullCrontab:
    """Build the complete crontab content from a list of collectors."""

    def test_multiple_collectors(self):
        from patrol_cron.cron_sync import build_crontab
        collectors = [
            {"name": "ecc", "schedule_sec": 60, "enabled": True},
            {"name": "switch", "schedule_sec": 120, "enabled": True},
            {"name": "disabled", "schedule_sec": 60, "enabled": False},
        ]
        content = build_crontab(collectors)
        # Filter to only cron job lines (contain "flock")
        job_lines = [l for l in content.strip().split("\n") if "flock" in l]
        assert len(job_lines) == 2  # disabled excluded
        assert any("ecc" in l for l in job_lines)
        assert any("switch" in l for l in job_lines)

    def test_empty_collectors(self):
        from patrol_cron.cron_sync import build_crontab
        content = build_crontab([])
        job_lines = [l for l in content.strip().split("\n") if "flock" in l]
        assert len(job_lines) == 0


class TestSyncCrontab:
    """sync_crontab reads DB and writes crontab."""

    def test_sync_writes_crontab(self):
        from patrol_cron.cron_sync import sync_crontab

        collectors = [
            {"name": "ecc", "schedule_sec": 60, "enabled": True},
        ]

        with um.patch("patrol_cron.cron_sync.db") as mock_db, \
             um.patch("patrol_cron.cron_sync._read_crontab", return_value=""), \
             um.patch("patrol_cron.cron_sync._write_crontab") as mock_write:
            mock_db.get_enabled_collectors.return_value = collectors
            changed = sync_crontab()

        assert changed is True
        mock_write.assert_called_once()
        written = mock_write.call_args[0][0]
        assert "ecc" in written

    def test_sync_skips_when_unchanged(self):
        """If crontab content matches, don't rewrite."""
        from patrol_cron.cron_sync import sync_crontab, build_crontab

        collectors = [
            {"name": "ecc", "schedule_sec": 60, "enabled": True},
        ]
        existing = build_crontab(collectors)

        with um.patch("patrol_cron.cron_sync.db") as mock_db, \
             um.patch("patrol_cron.cron_sync._read_crontab", return_value=existing), \
             um.patch("patrol_cron.cron_sync._write_crontab") as mock_write:
            mock_db.get_enabled_collectors.return_value = collectors
            changed = sync_crontab()

        assert changed is False
        mock_write.assert_not_called()
