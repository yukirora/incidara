"""Integration tests for init_stage.py — bundle path and env forwarding."""
import os
import pytest
from node_operations.init_stage import get_bundle_path, _build_env_prefix


class TestBundlePaths:
    """Verify bundled scripts are present and accessible."""

    @pytest.mark.parametrize("bundle", ["init_bundle", "software_bundle", "config_bundle", "ltp_bundle"])
    def test_bundle_exists(self, bundle):
        path = get_bundle_path(bundle)
        assert path.is_dir(), f"{bundle} not found at {path}"
        print(f"  {bundle} -> {path}")

    def test_init_bundle_has_required_scripts(self):
        path = get_bundle_path("init_bundle")
        for script in ["teardown.sh", "create_debug_user.sh", "clear_node.sh",
                        "create_user.sh", "harden_node.sh", "set_bmc_password_remote.sh"]:
            assert (path / script).is_file(), f"missing {script}"

    def test_software_bundle_has_required_scripts(self):
        path = get_bundle_path("software_bundle")
        for script in ["install_general.sh", "install_h200.sh", "install_b300.sh",
                        "install_cpu.sh", "install_storage.sh"]:
            assert (path / script).is_file(), f"missing {script}"

    def test_config_bundle_has_required_scripts(self):
        path = get_bundle_path("config_bundle")
        for script in ["config_apt.sh", "config_raid.sh", "check_raid.sh",
                        "config_h200.sh", "config_b300.sh", "config_cpu.sh", "config_storage.sh",
                        "delete_k8s_pod.sh", "scale_kubespray_node.sh", "restart_k8s_services.sh"]:
            assert (path / script).is_file(), f"missing {script}"

    def test_ltp_bundle_has_required_scripts(self):
        path = get_bundle_path("ltp_bundle")
        for script in ["collect_system_info.sh", "collect_system_info_b300.sh",
                        "collect_system_info_cpu.sh", "gen_sku.py", "sku_spec.py", "system_info.py"]:
            assert (path / script).is_file(), f"missing {script}"

    def test_nonexistent_bundle_raises(self):
        with pytest.raises(RuntimeError, match="bundle not found"):
            get_bundle_path("nonexistent_bundle")


class TestEnvForwarding:
    """Test env var forwarding to remote scripts."""

    def test_build_env_prefix_empty(self, monkeypatch):
        for key in ["APT_MIRROR", "PIP_MIRROR", "DATA_MOUNT", "KUBESPRAY_DIR", "KUBESPRAY_VENV", "CLUSTER_KEY"]:
            monkeypatch.delenv(key, raising=False)
        assert _build_env_prefix() == ""

    def test_build_env_prefix_with_values(self, monkeypatch):
        monkeypatch.setenv("APT_MIRROR", "10.0.0.1")
        monkeypatch.setenv("DATA_MOUNT", "/mnt/data")
        prefix = _build_env_prefix()
        assert 'APT_MIRROR="10.0.0.1"' in prefix
        assert 'DATA_MOUNT="/mnt/data"' in prefix

    def test_build_env_prefix_skips_unset(self, monkeypatch):
        monkeypatch.setenv("APT_MIRROR", "10.0.0.1")
        monkeypatch.delenv("PIP_MIRROR", raising=False)
        prefix = _build_env_prefix()
        assert "APT_MIRROR" in prefix
        assert "PIP_MIRROR" not in prefix
