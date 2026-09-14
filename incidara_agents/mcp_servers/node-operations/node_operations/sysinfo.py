import datetime as dt
import json
import pathlib
import subprocess
import zoneinfo
import logging
from node_operations.init_stage import run_init_stage
from node_operations.ssh import run_remote_command, scp_download

logger = logging.getLogger(__name__)
SHANGHAI_TZ = zoneinfo.ZoneInfo("Asia/Shanghai")


def read_sku(ip: str, base_dir: str = ".") -> str:
    sku = pathlib.Path(base_dir, "sbsysinfo_results", ip, "sku.txt").read_text("utf-8").strip()
    if sku.startswith("match failed"):
        raise RuntimeError(f"sku match failed for {ip}: {sku}")
    return sku


def read_sys_info(ip: str, base_dir: str = ".") -> dict:
    return json.loads(pathlib.Path(base_dir, "sbsysinfo_results", ip, "info.json").read_text("utf-8"))


def collect_sbsysinfo(*, hostname: str, ip: str, category: str,
                      ssh_user: str, ssh_password: str,
                      bmc_password: str, timeout: int, base_dir: str = ".") -> None:
    script = "collect_system_info.sh" if category == "h200" else f"collect_system_info_{category}.sh"
    run_init_stage(hostname=hostname, ip=ip, ssh_user=ssh_user, ssh_password=ssh_password,
                   bmc_password=bmc_password, timeout=timeout,
                   bundle="ltp_bundle", script=script)
    local_dir = pathlib.Path(base_dir, "sbsysinfo_results", ip)
    local_dir.mkdir(parents=True, exist_ok=True)
    scp_download(ip, ssh_user, ssh_password,
               "/tmp/sbsysinfo/*", str(local_dir) + "/", timeout)
    run_remote_command(ip, ssh_user, "sudo rm -r /tmp/sbsysinfo", timeout)


def sync_node_time(ip: str, ssh_user: str) -> None:
    now_str = dt.datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M:%S")
    cmd = (
        f"echo date-before-sync && sudo date "
        f"&& sudo timedatectl set-timezone 'Asia/Shanghai' "
        f"&& sudo date -s '{now_str}' "
        f"&& echo hwclock-before-sync && sudo hwclock -r "
        f"&& sudo hwclock --systohc --utc "
        f"&& echo hwclock-after-sync && sudo hwclock -r "
        f"&& echo date-after-sync && sudo date"
    )
    if run_remote_command(ip, ssh_user, cmd, 30) != 0:
        raise RuntimeError(f"sync node time failed on {ip}")
