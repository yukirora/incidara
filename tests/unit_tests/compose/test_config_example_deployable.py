"""Guards that `cp compose/config.yaml.example compose/config.yaml` deploys.

A clean host has no database, no OSS repository, and a checkout at an arbitrary
path. These tests render the shipped example the same way compose/render.py does
and assert the result is deployable without editing anything except credentials.
"""

import os
import sys
from pathlib import Path

import jinja2
import yaml


ROOT = Path(__file__).resolve().parents[3]
COMPOSE_DIR = ROOT / "compose"
sys.path.insert(0, str(COMPOSE_DIR))

import render  # noqa: E402


def _example_config(port_offset: int = 0) -> dict:
    config = yaml.safe_load((COMPOSE_DIR / "config.yaml.example").read_text())
    render.apply_deploy_defaults(config, port_offset)
    return config


def _render(template_name: str, config: dict) -> str:
    template = (COMPOSE_DIR / template_name).read_text()
    return jinja2.Template(template, keep_trailing_newline=True).render(cfg=config, **config)


def test_example_config_validates():
    assert render.validate_config(_example_config()) == []


def test_example_needs_no_edit_for_a_clean_host():
    """Paths and endpoints come from the checkout, so a clone works anywhere."""
    example = yaml.safe_load((COMPOSE_DIR / "config.yaml.example").read_text())
    assert "repo_dir" not in example["common"]
    assert "chat_ui_dir" not in example["common"]
    assert "state_root" not in example["common"]
    assert example["common"]["bind_host"] == "127.0.0.1"

    # No hardcoded host directories: every path is repo- or state-relative.
    for service, section in example.items():
        if not isinstance(section, dict):
            continue
        for key, value in (section.get("_deploy") or {}).items():
            if isinstance(value, str) and value.startswith("/"):
                raise AssertionError(f"{service}._deploy.{key} is an absolute host path: {value}")


def test_derived_state_paths_are_inside_the_state_root():
    config = _example_config()
    state_root = config["common"]["state_root"]
    for service, section in config.items():
        for key, value in (section.get("_deploy") or {}).items():
            if isinstance(value, str) and value.startswith("/") and key != "ssh_key":
                assert value.startswith(state_root + os.sep), f"{service}._deploy.{key} escapes state_root"


def test_example_disables_backup_for_a_first_boot():
    """No OSS repository exists yet, so a clean deployment must not require one."""
    example = _example_config()
    assert example["agent-backup"]["enabled"] is False
    for db in ("agent-db", "chat-ui-db"):
        assert example[db]["BACKUP_ENABLED"] is False
        assert example[db]["BACKUP_CRON_ENABLED"] is False


def test_internal_credentials_are_usable_without_edits():
    """A demo deployment comes up with no edits; CHANGE_ME would break it."""
    example = _example_config()
    for db in ("agent-db", "chat-ui-db"):
        assert example[db]["POSTGRES_PASSWORD"] not in ("", "CHANGE_ME")
    for key in ("CHAT_UI_PASSWORD", "INTERNAL_SECRET"):
        assert example["common"][key] not in ("", "CHANGE_ME")
    # iron-session refuses anything shorter than 32 characters.
    assert len(example["incidara-console"]["SESSION_SECRET"]) >= 32


def test_internal_urls_are_derived_from_the_database_sections():
    config = _example_config()
    agent_db, console_db = config["agent-db"], config["chat-ui-db"]

    assert config["common"]["EVIDENCE_DB_URL"] == (
        f"postgresql://{agent_db['POSTGRES_USER']}:{agent_db['POSTGRES_PASSWORD']}"
        f"@127.0.0.1:{agent_db['_deploy']['port']}/{agent_db['POSTGRES_DB']}")
    assert config["detection-agent"]["CHAT_UI_DB_URL"] == (
        f"postgresql://{console_db['POSTGRES_USER']}:{console_db['POSTGRES_PASSWORD']}"
        f"@127.0.0.1:{console_db['_deploy']['port']}/{console_db['POSTGRES_DB']}")
    assert config["incidara-console"]["CHAT_UI_DATABASE_URL"] == config["common"]["CHAT_UI_DB_URL"]
    assert config["common"]["CHAT_UI_URL"] == f"http://127.0.0.1:{config['incidara-console']['_deploy']['port']}"


def test_port_offset_moves_ports_and_derived_urls_together():
    """A second stack on one host needs one flag, not a config rewrite."""
    base, shifted = _example_config(), _example_config(port_offset=3000)
    for service, section in base.items():
        if isinstance(section, dict) and isinstance((section.get("_deploy") or {}).get("port"), int):
            assert shifted[service]["_deploy"]["port"] == section["_deploy"]["port"] + 3000
    assert ":8434/" in shifted["common"]["EVIDENCE_DB_URL"]
    assert shifted["common"]["CHAT_UI_URL"] == f"http://127.0.0.1:{shifted['incidara-console']['_deploy']['port']}"
    assert shifted["incidara-console"]["_deploy"]["api_port"] == base["incidara-console"]["_deploy"]["api_port"] + 3000


def test_explicit_values_win_over_derived_ones():
    config = yaml.safe_load((COMPOSE_DIR / "config.yaml.example").read_text())
    config["common"]["EVIDENCE_DB_URL"] = "postgresql://external:secret@db.internal:5432/agent"
    config["detection-agent"]["CHAT_UI_DB_URL"] = "postgresql://external:secret@db.internal:5432/console"
    config["common"]["state_root"] = "/srv/incidara"
    render.apply_deploy_defaults(config)

    assert config["common"]["EVIDENCE_DB_URL"] == "postgresql://external:secret@db.internal:5432/agent"
    assert config["detection-agent"]["CHAT_UI_DB_URL"] == "postgresql://external:secret@db.internal:5432/console"
    assert config["detection-agent"]["_deploy"]["workspace"].startswith("/srv/incidara/")


def test_console_registry_falls_back_to_shipped_examples():
    """The Console must not be handed a missing mount: Docker creates a directory."""
    config = _example_config()
    for key, name in (("agents_config_path", "agents.yaml"), ("groups_config_path", "groups.yaml")):
        path = config["common"][key]
        assert Path(path).is_file(), f"{key} points at a missing file: {path}"
        assert path.endswith(name + ".example") or path.endswith(name)

    custom = yaml.safe_load((COMPOSE_DIR / "config.yaml.example").read_text())
    custom["common"]["chat_ui_dir"] = str(ROOT / "console")
    real = ROOT / "console" / "config" / "agents.yaml"
    created = not real.exists()
    real.write_text("[]\n")
    try:
        render.apply_deploy_defaults(custom)
        assert custom["common"]["agents_config_path"] == str(real)
    finally:
        if created:
            real.unlink()

    # A directory is what Docker leaves behind for a missing bind source.
    custom["common"].pop("agents_config_path")
    real.unlink(missing_ok=True)
    real.mkdir()
    try:
        render.apply_deploy_defaults(custom)
        assert custom["common"]["agents_config_path"].endswith("agents.yaml.example")
    finally:
        real.rmdir()


def test_container_names_follow_the_name_prefix():
    """Two stacks on one host need distinct container names, not only ports."""
    default = yaml.safe_load(_render("databases.yml.j2", _example_config()))
    assert default["services"]["agent-db"]["container_name"] == "incidara-agent-db"

    renamed = _example_config()
    renamed["common"]["name_prefix"] = "demo"
    services = yaml.safe_load(_render("databases.yml.j2", renamed))["services"]
    assert services["agent-db"]["container_name"] == "demo-agent-db"
    assert services["chat-ui-db"]["container_name"] == "demo-console-db"


def test_console_web_and_api_ports_come_from_the_config():
    """nginx listens on the configured port, so a shifted deployment still serves."""
    config = _example_config()
    console = config["incidara-console"]["_deploy"]
    rendered = yaml.safe_load(_render("chat-ui.yml.j2", config))["services"]

    web, api = rendered["incidara-console-web"], rendered["incidara-console-api"]
    assert f"PORT={console['port']}" in web["environment"]
    assert f"API_PORT={console['api_port']}" in web["environment"]
    assert f"PORT={console['api_port']}" in api["environment"]
    assert f"127.0.0.1:{console['port']}" in web["healthcheck"]["test"][-1]
    assert f"127.0.0.1:{console['api_port']}" in api["healthcheck"]["test"][-1]

    template = (ROOT / "console" / "nginx.conf.template").read_text()
    assert "listen ${PORT};" in template
    assert template.count("proxy_pass http://127.0.0.1:${API_PORT};") == 2


def test_example_ships_two_console_accounts():
    """A fresh Console needs a sign-in: one admin and one non-admin delegate."""
    example = _example_config()
    accounts = {
        "admin": (example["incidara-console"]["ADMIN_EMAIL"], example["incidara-console"]["ADMIN_PASSWORD"]),
        "delegate": (example["common"]["CHAT_UI_USER"], example["common"]["CHAT_UI_PASSWORD"]),
    }
    for role, (email, password) in accounts.items():
        assert "@" in email, f"{role} account has no email"
        assert len(password) >= 8, f"{role} password is shorter than the signup minimum"
    assert accounts["admin"][0] != accounts["delegate"][0]

    # The admin is in the admins group; the delegate is not, so it stays non-admin.
    groups = yaml.safe_load((ROOT / "console" / "config" / "groups.yaml.example").read_text())
    members = {group["id"]: group["members"] for group in groups}
    assert accounts["admin"][0] in members["admins"]
    assert accounts["delegate"][0] not in members["admins"]
    assert accounts["delegate"][0] in members["sre-team"]


def test_rendered_databases_need_no_backup_repository():
    config = _example_config()
    databases = yaml.safe_load(_render("databases.yml.j2", config))
    services = databases["services"]

    for service in ("agent-db", "chat-ui-db"):
        assert services[service]["image"] == "postgres:16-alpine"
        assert "privileged" not in services[service]
    assert "agent-db-cron" not in services


def test_console_db_initialises_from_the_checkout():
    """The init directory must follow chat_ui_dir, not a hardcoded absolute path."""
    config = _example_config()
    databases = yaml.safe_load(_render("databases.yml.j2", config))
    mounts = databases["services"]["chat-ui-db"]["volumes"]

    init_mounts = [m for m in mounts if "docker-entrypoint-initdb.d" in m]
    assert init_mounts == [f"{config['common']['chat_ui_dir']}/db/init:/docker-entrypoint-initdb.d:ro"]
    assert (ROOT / "console" / "db" / "init").is_dir()


def test_every_rendered_compose_file_is_valid_yaml():
    config = _example_config()
    names = ("docker-compose.yml", "databases.yml", "mcp-servers.yml", "agents.yml", "chat-ui.yml")
    for name in names:
        parsed = yaml.safe_load(_render(f"{name}.j2", config))
        assert isinstance(parsed, dict) and parsed, f"{name} did not render to a compose document"


def test_agent_backup_service_appears_only_when_enabled():
    config = _example_config()
    assert yaml.safe_load(_render("backup.yml.j2", config)) is None

    config["agent-backup"]["enabled"] = True
    services = yaml.safe_load(_render("backup.yml.j2", config))["services"]
    assert "agent-backup" in services
