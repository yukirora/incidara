#!/usr/bin/env python3
"""compose/render.py — config.yaml + templates → compose/rendered/

Reads compose/config.yaml, renders:
  - env.j2 per service  → compose/rendered/<service>.env
  - *.yml.j2 templates  → compose/rendered/*.yml

cfg context available in all templates:
  cfg['common']          — shared values
  cfg['<service-name>']  — any service's config

Adding a new service: add section to config.yaml, optionally add env.j2.

Usage:
  python3 compose/render.py              # render
  python3 compose/render.py --check      # validate only, no write
  python3 compose/render.py --diff       # validate + show diff vs current rendered
"""

import os
import re
import sys
import difflib

import jinja2
import yaml

from typing import Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)  # incidara/incidara/ (inner repo root)
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.yaml")
RENDERED_DIR = os.path.join(SCRIPT_DIR, "rendered")

_DEPLOY_KEY = "_deploy"  # namespace for template-only keys (excluded from env dump)

REQUIRED_SERVICES = []  # No required services — config drives what gets deployed

_TOKENS = re.compile(r"\{(repo|state|service)\}")


def apply_deploy_defaults(config: dict, port_offset: int = 0) -> dict:
    """Resolve host locations, ports and internal database URLs.

    Every agent, MCP server and console service runs with network_mode: host, so
    ports and database URLs are host addresses. Host paths use {repo}, {state}
    and {service} tokens so config.yaml stays readable without hardcoding
    directories that only exist on one machine. Internal URLs are derived from
    the two database sections, so changing a password or a port cannot leave a
    service pointing at the wrong address.

    Values set explicitly in config.yaml are never overwritten.
    """
    common = config.setdefault("common", {})
    state_root = os.path.abspath(os.path.expanduser(str(common.get("state_root") or os.path.join(REPO_DIR, "state"))))
    common["state_root"] = state_root
    common.setdefault("repo_dir", REPO_DIR)
    common.setdefault("chat_ui_dir", os.path.join(REPO_DIR, "console"))

    for name, svc in config.items():
        if not isinstance(svc, dict):
            continue
        tokens = {"repo": common["repo_dir"], "state": state_root, "service": name}
        for value in [svc, svc.get(_DEPLOY_KEY) or {}]:
            for key, item in value.items():
                if isinstance(item, str) and _TOKENS.search(item):
                    value[key] = _TOKENS.sub(lambda m: tokens[m.group(1)], item)
        deploy = svc.get(_DEPLOY_KEY)
        if isinstance(deploy, dict):
            if name == "incidara-console":
                # The Console API is reached by the web container over loopback.
                deploy.setdefault("api_port", 3456)
            for key in ("port", "api_port"):
                if port_offset and isinstance(deploy.get(key), int):
                    deploy[key] += port_offset

    def db_url(section: str) -> Optional[str]:
        db = config.get(section) or {}
        user, password = db.get("POSTGRES_USER"), db.get("POSTGRES_PASSWORD")
        port = (db.get(_DEPLOY_KEY) or {}).get("port")
        if not (user and password and port and db.get("POSTGRES_DB")):
            return None
        return f"postgresql://{user}:{password}@127.0.0.1:{port}/{db['POSTGRES_DB']}"

    console_port = ((config.get("incidara-console") or {}).get(_DEPLOY_KEY) or {}).get("port")
    derived = {
        "EVIDENCE_DB_URL": db_url("agent-db"),
        "CHAT_UI_DB_URL": db_url("chat-ui-db"),
        "CHAT_UI_DATABASE_URL": db_url("chat-ui-db"),
        "CHAT_UI_URL": f"http://{common.get('bind_host', '127.0.0.1')}:{console_port}" if console_port else None,
        "CHAT_UI_API_URL": f"http://{common.get('bind_host', '127.0.0.1')}:{console_port}" if console_port else None,
    }
    for key, value in derived.items():
        if value and key not in common:
            common[key] = value
        for name, svc in config.items():
            if isinstance(svc, dict) and key not in svc:
                svc[key] = value

    # The backup service syncs every agent directory, so its root is state_root.
    for section in ("common", "agent-backup"):
        svc = config.setdefault(section, {})
        if isinstance(svc, dict) and svc.get("agent_data_root") in (None, "", "CHANGE_ME"):
            svc["agent_data_root"] = state_root

    # The Console reads its agent and group registries from files. Fall back to
    # the shipped examples so a fresh deployment starts without a copy step, and
    # never hand Docker a missing path (it creates a directory and the Console
    # fails on EISDIR).
    for key, name in (("agents_config_path", "agents.yaml"), ("groups_config_path", "groups.yaml")):
        registry = os.path.join(common["chat_ui_dir"], "config", name)
        # isfile, not exists: Docker creates a directory when a bind source is missing.
        common.setdefault(key, registry if os.path.isfile(registry) else registry + ".example")

    return config


def prepare_state_dirs(config: dict) -> tuple[list[str], list[str]]:
    """Create host state directories as the current user.

    Docker creates missing bind-mount sources as root, which then need root to
    clean up. Creating them here keeps a deployment removable by its operator.
    Rendering does not depend on this, so an unwritable path is a warning.

    Returns (created, failed).
    """
    created, failed = [], []
    for name, svc in config.items():
        if not isinstance(svc, dict):
            continue
        for key, path in (svc.get(_DEPLOY_KEY) or {}).items():
            if key != "ssh_key" and isinstance(path, str) and path.startswith("/") and not os.path.exists(path):
                try:
                    os.makedirs(path, exist_ok=True)
                    created.append(path)
                except OSError as err:
                    failed.append(f"{path} ({err.strerror})")
    return created, failed


def find_template(svc_name: str, svc_config: dict) -> Optional[str]:
    """Find env.j2 for a service.

    Priority:
    1. _template field in service config or _deploy._template
    2. Auto-discover: walk repo for matching dir/env.j2
    3. None → fallback to flat env dump
    """
    explicit = svc_config.get("_template") or svc_config.get(_DEPLOY_KEY, {}).get("_template")
    if explicit:
        path = os.path.join(REPO_DIR, explicit)
        if os.path.exists(path):
            return path
        print(f"  WARNING: _template {explicit} not found for {svc_name}", file=sys.stderr)

    # Auto-discover: strip instance suffix for shared templates
    base = svc_name
    for suffix in ("-ops", "-diagnosis", "-feedback", "-write"):
        if svc_name.endswith(suffix):
            base = svc_name[:-len(suffix)]
            break

    for root, dirs, files in os.walk(REPO_DIR):
        dirs[:] = [d for d in dirs if not d.startswith(('.', '_')) and d != 'node_modules']
        if "env.j2" in files:
            dir_name = os.path.basename(root)
            # Match by exact name, base name, or if base is a prefix of dir_name
            if dir_name == svc_name or dir_name == base or dir_name.startswith(base.rstrip('-')):
                return os.path.join(root, "env.j2")

    return None


def render_template(template_str: str, cfg: dict) -> str:
    """Render Jinja2 template with cfg dict as context."""
    env = jinja2.Environment(
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    # Filter: render booleans as lowercase (true/false, not True/False)
    env.filters['lowerbool'] = lambda v: str(v).lower() if isinstance(v, bool) else v
    template = env.from_string(template_str)
    return template.render(cfg=cfg)


def validate_config(config: dict) -> list[str]:
    """Validate config.yaml. Returns list of errors (empty = valid)."""
    errors = []

    # Check required services
    missing = [s for s in REQUIRED_SERVICES if s not in config]
    if missing:
        errors.append(f"Missing required services: {missing}")

    if len(config) < 2:
        errors.append(f"Only {len(config)} keys — config appears empty")

    if "common" not in config:
        errors.append("Missing 'common' section")
    else:
        for key in ["bind_host", "repo_dir"]:
            if key not in config["common"] or config["common"][key] in (None, "", "CHANGE_ME"):
                errors.append(f"common.{key} is missing or has placeholder value")

    # Check each agent has _deploy section with required paths
    for svc_name, svc_config in config.items():
        if svc_name in ("common", "agent-backup") or not isinstance(svc_config, dict):
            continue
        if "_deploy" not in svc_config and svc_name not in ("agent-db", "chat-ui-db"):
            if svc_name.endswith("-agent"):
                errors.append(f"{svc_name}: missing _deploy section")

    return errors


def check_diff(config: dict) -> bool:
    """Dry-run render and show diff against current rendered files. Returns True if changes exist."""
    has_changes = False
    # We do a full render to a temp string and compare
    # For simplicity, just check env files
    for svc_name, svc_config in config.items():
        if svc_name == "common" or not isinstance(svc_config, dict):
            continue
        env_path = os.path.join(RENDERED_DIR, f"{svc_name}.env")
        if not os.path.exists(env_path):
            print(f"  {svc_name}.env: NEW (does not exist)")
            has_changes = True
            continue

    return has_changes


def main():
    global CONFIG_PATH, RENDERED_DIR
    import argparse
    parser = argparse.ArgumentParser(description="Render compose files from config.yaml")
    parser.add_argument("--check", action="store_true", help="Validate config without rendering")
    parser.add_argument("--diff", action="store_true", help="Validate + show diff vs current rendered")
    parser.add_argument("--config", default=CONFIG_PATH, help="config file to read (default: compose/config.yaml)")
    parser.add_argument("--rendered-dir", default=RENDERED_DIR, help="output directory (default: compose/rendered)")
    parser.add_argument("--port-offset", type=int, default=0,
                        help="shift every service port; use when another deployment already owns the defaults")
    args = parser.parse_args()

    CONFIG_PATH = os.path.abspath(args.config)
    RENDERED_DIR = os.path.abspath(args.rendered_dir)

    if not os.path.exists(CONFIG_PATH):
        print(f"ERROR: {CONFIG_PATH} not found.\n  cp compose/config.yaml.example compose/config.yaml", file=sys.stderr)
        sys.exit(1)

    config = yaml.safe_load(open(CONFIG_PATH))

    # Auto-inject repo_dir from repo location only if not set in config.yaml
    if "common" not in config:
        config["common"] = {}
    config["common"].setdefault("repo_dir", REPO_DIR)
    config["common"].setdefault("chat_ui_dir", os.path.join(REPO_DIR, "console"))
    apply_deploy_defaults(config, args.port_offset)

    # --- Validate ---
    errors = validate_config(config)
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        print(f"\nConfig validation failed. Fix config.yaml or restore from remote.", file=sys.stderr)
        sys.exit(1)

    n_services = len([k for k in config if k != "common" and isinstance(config[k], dict)])
    print(f"Config valid: {len(config)} keys, {n_services} services")

    if args.check:
        print("Check passed. No files written.")
        return

    state_dirs, unwritable = prepare_state_dirs(config)
    if state_dirs:
        print(f"Created {len(state_dirs)} state directories under {config['common']['state_root']}")
    if unwritable:
        print(f"WARNING: could not create state directories: {'; '.join(unwritable)}", file=sys.stderr)

    os.makedirs(RENDERED_DIR, exist_ok=True)

    # --- Render per-service env files ---
    count = 0
    for svc_name, svc_config in config.items():
        if svc_name == "common" or not isinstance(svc_config, dict):
            continue

        # Build cfg context: all sections + _self (current service) + _name
        cfg = dict(config)
        cfg['_self'] = svc_config
        cfg['_name'] = svc_name

        # Auto-inject common keys as base, then template overrides
        common = config.get("common", {})
        common_lines = {}
        for k, v in common.items():
            if k.isupper():  # skip deploy keys like bind_host, chat_ui_dir, repo_dir
                val = str(v).lower() if isinstance(v, bool) else v
                common_lines[k] = f"{k}={val}"

        template_path = find_template(svc_name, svc_config)
        if template_path:
            rendered = render_template(open(template_path).read(), cfg)
            # Strip comment lines — they cause unnecessary container recreates
            rendered_lines = []
            for line in rendered.split("\n"):
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    rendered_lines.append(stripped)
            # Template lines override common keys
            for line in rendered_lines:
                if "=" in line:
                    key = line.split("=", 1)[0]
                    common_lines[key] = line  # template wins over common
            env_content = "\n".join(common_lines[k] for k in sorted(common_lines)) + "\n"
        else:
            # Flat dump: service-specific keys override common
            merged = dict(common_lines)  # start with common
            for k, v in svc_config.items():
                if k != _DEPLOY_KEY and k.isupper():
                    val = str(v).lower() if isinstance(v, bool) else v
                    merged[k] = f"{k}={val}"
            env_content = "\n".join(merged[k] for k in sorted(merged)) + "\n"

        # Auto-inject port env var (PORT or custom key like NODE_OPS_PORT)
        deploy = svc_config.get(_DEPLOY_KEY, {})
        if "port" in deploy:
            port_key = deploy.get("_port_env_key", "PORT")
            port_line = f"{port_key}={deploy['port']}"
            # Prepend if not already in content
            if f"\n{port_key}=" not in env_content and not env_content.startswith(f"{port_key}="):
                env_content = port_line + "\n" + env_content

        out_path = os.path.join(RENDERED_DIR, f"{svc_name}.env")
        with open(out_path, "w") as f:
            f.write(env_content)
        count += 1

        tpl = os.path.relpath(template_path, REPO_DIR) if template_path else "(flat)"
        print(f"  {svc_name}: {tpl}")

    # --- Render docker-compose.yml + included sub-files ---
    compose_templates = {
        "docker-compose.yml": os.path.join(SCRIPT_DIR, "docker-compose.yml.j2"),
        "databases.yml": os.path.join(SCRIPT_DIR, "databases.yml.j2"),
        "mcp-servers.yml": os.path.join(SCRIPT_DIR, "mcp-servers.yml.j2"),
        "agents.yml": os.path.join(SCRIPT_DIR, "agents.yml.j2"),
        "chat-ui.yml": os.path.join(SCRIPT_DIR, "chat-ui.yml.j2"),
        "backup.yml": os.path.join(SCRIPT_DIR, "backup.yml.j2"),
    }
    compose_count = 0
    for out_name, tpl_path in compose_templates.items():
        if os.path.exists(tpl_path):
            content = render_template(open(tpl_path).read(), cfg)
            out_path = os.path.join(RENDERED_DIR, out_name)
            with open(out_path, "w") as f:
                f.write(content)
            print(f"  {out_name}: rendered")
            compose_count += 1
        else:
            print(f"  WARNING: {tpl_path} not found, skipping", file=sys.stderr)

    print(f"\nGenerated {count} env files + {compose_count} compose files in {RENDERED_DIR}/")


if __name__ == "__main__":
    main()
