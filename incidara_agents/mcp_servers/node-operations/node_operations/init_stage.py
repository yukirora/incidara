import os
import pathlib
import logging
from node_operations.ssh import scp_upload, ssh_with_password

logger = logging.getLogger(__name__)


def _find_bundles_root() -> pathlib.Path:
    env_root = os.environ.get("BUNDLES_ROOT")
    if env_root:
        return pathlib.Path(env_root)
    candidates = [
        pathlib.Path(__file__).resolve().parent.parent / "bundles",
        pathlib.Path.cwd() / "node-operations" / "bundles",
    ]
    for p in candidates:
        if p.is_dir():
            return p
    return candidates[0]


_BUNDLES_ROOT = _find_bundles_root()

_FORWARD_ENV_KEYS = [
    "APT_MIRROR",
    "PIP_MIRROR",
    "DATA_MOUNT",
    "SYS_MOUNT",
    "EXT_MOUNT",
    "KUBESPRAY_DIR",
    "KUBESPRAY_VENV",
    "CLUSTER_KEY",
]


def get_bundle_path(bundle: str) -> pathlib.Path:
    p = _BUNDLES_ROOT / bundle
    if not p.is_dir():
        raise RuntimeError(f"bundle not found: {p}")
    return p


def _build_env_prefix() -> str:
    parts = []
    for key in _FORWARD_ENV_KEYS:
        val = os.environ.get(key)
        if val:
            parts.append(f'{key}="{val}"')
    return " ".join(parts)


def run_init_stage(*, hostname: str, ip: str, ssh_user: str, ssh_password: str,
                   bmc_password: str, timeout: int,
                   bundle: str, script: str) -> None:
    bundle_path = get_bundle_path(bundle)
    bundle_name = bundle_path.name
    logger.info("running %s/%s on %s (%s)", bundle, script, hostname, ip)

    env_prefix = _build_env_prefix()
    remote_cmd = f"cd /tmp/{bundle_name} && {env_prefix} bash {script} '{ssh_password}' '{bmc_password}' '{hostname}'"
    if not env_prefix:
        remote_cmd = f"cd /tmp/{bundle_name} && bash {script} '{ssh_password}' '{bmc_password}' '{hostname}'"

    scp_upload(ip, ssh_user, ssh_password, str(bundle_path), "/tmp/", timeout)
    rc, output = ssh_with_password(ip, ssh_user, ssh_password, remote_cmd, timeout)
    ssh_with_password(ip, ssh_user, ssh_password, f"rm -r /tmp/{bundle_name}", timeout)

    if output and output.strip():
        for line in output.strip().splitlines()[-20:]:
            logger.debug("  [%s/%s] %s", bundle, script, line)
    if rc != 0:
        last_lines = (output or "").strip().splitlines()[-10:]
        tail = "\n".join(last_lines) if last_lines else "(no output)"
        logger.error("%s/%s failed on %s (exit %d), last output:\n%s",
                     bundle, script, hostname, rc, tail)
        raise RuntimeError(f"{bundle}/{script} failed on {hostname} (exit {rc}):\n{tail}")
    logger.info("%s/%s completed on %s (exit 0)", bundle, script, hostname)
