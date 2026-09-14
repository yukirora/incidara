from node_operations.init_stage import run_init_stage


def run_reset(*, hostname: str, ip: str, ssh_user: str, ssh_password: str,
              reset_ssh_user: str, reset_ssh_password: str,
              bmc_password: str, reset_bmc_password: str, timeout: int) -> None:
    common = dict(hostname=hostname, ip=ip, timeout=timeout)
    run_init_stage(**common, ssh_user=ssh_user, ssh_password=ssh_password,
                   bmc_password=bmc_password, bundle="init_bundle", script="teardown.sh")
    run_init_stage(**common, ssh_user=ssh_user, ssh_password=ssh_password,
                   bmc_password=bmc_password, bundle="init_bundle", script="create_debug_user.sh")
    run_init_stage(**common, ssh_user=reset_ssh_user, ssh_password=reset_ssh_password,
                   bmc_password=reset_bmc_password, bundle="init_bundle", script="clear_node.sh")
