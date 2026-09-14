from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
BACKUP_DIR = ROOT / "infra" / "postgresql" / "backup"


def test_restore_and_promote_script_is_packaged_in_db_image():
    dockerfile = (BACKUP_DIR / "Dockerfile").read_text()
    script = BACKUP_DIR / "scripts" / "restore-and-promote.sh"

    assert script.exists()
    assert "COPY scripts/restore-and-promote.sh /usr/local/bin/restore-and-promote.sh" in dockerfile
    assert "/usr/local/bin/restore-and-promote.sh" in dockerfile


def test_restore_profile_uses_full_restore_and_promote_flow():
    template = (ROOT / "compose" / "databases.yml.j2").read_text()

    assert 'entrypoint: ["/usr/local/bin/restore-and-promote.sh"]' in template
    assert 'pgbackrest", "--stanza=app", "--type=immediate", "--delta", "restore"' not in template
    assert "{{ cfg['agent-db']['_deploy']['pgbackrest_dir'] }}:/var/lib/pgbackrest" in template
    assert "{{ cfg['chat-ui-db']['_deploy']['pgbackrest_dir'] }}:/var/lib/pgbackrest" in template


def test_entrypoint_uses_real_promotion_not_replay_resume():
    entrypoint = (BACKUP_DIR / "scripts" / "entrypoint.sh").read_text()

    assert "pg_ctl -D \"$PGDATA\" promote" in entrypoint
    assert "pg_wal_replay_resume" not in entrypoint
    assert "cron" in entrypoint
    assert "crond" not in entrypoint
