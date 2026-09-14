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
    import argparse
    parser = argparse.ArgumentParser(description="Render compose files from config.yaml")
    parser.add_argument("--check", action="store_true", help="Validate config without rendering")
    parser.add_argument("--diff", action="store_true", help="Validate + show diff vs current rendered")
    args = parser.parse_args()

    if not os.path.exists(CONFIG_PATH):
        print(f"ERROR: {CONFIG_PATH} not found.\n  cp compose/config.yaml.example compose/config.yaml", file=sys.stderr)
        sys.exit(1)

    config = yaml.safe_load(open(CONFIG_PATH))

    # Auto-inject repo_dir from repo location only if not set in config.yaml
    if "common" not in config:
        config["common"] = {}
    if "repo_dir" not in config["common"]:
        config["common"]["repo_dir"] = REPO_DIR

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
