from node_operations.config import (
    get_category_stage_pairs, _CATEGORY_PIPELINES, _STAGE_TRIGGERS,
)


def test_h200():
    i, c = get_category_stage_pairs("h200")
    assert i == ("software_bundle", "install_h200.sh")
    assert c == ("config_bundle", "config_h200.sh")


def test_unknown_raises():
    try:
        get_category_stage_pairs("unknown")
        assert False
    except RuntimeError:
        pass


# ── _CATEGORY_PIPELINES tests ──────────────────────────────────────────

def test_all_categories_have_pipeline():
    """Every category in _CATEGORY_STAGES must also be in _CATEGORY_PIPELINES."""
    for cat in ("h200", "b300", "cpu", "ctrl", "storage"):
        assert cat in _CATEGORY_PIPELINES, f"{cat} missing from _CATEGORY_PIPELINES"


def test_pipelines_have_7_stages():
    """All current categories have 7 stages (same as original hardcoded list)."""
    for cat, pipe in _CATEGORY_PIPELINES.items():
        assert len(pipe["stages"]) == 7, f"{cat} has {len(pipe['stages'])} stages, expected 7"


def test_all_pipelines_start_with_create_user():
    """First stage must always be create_user.sh."""
    for cat, pipe in _CATEGORY_PIPELINES.items():
        assert pipe["stages"][0] == ("init_bundle", "create_user.sh"), f"{cat} doesn't start with create_user.sh"


def test_compute_pipelines_include_config_raid():
    """Compute/control nodes use RAID configuration; storage uses config_img."""
    for cat in ("h200", "b300", "cpu", "ctrl"):
        assert ("config_bundle", "config_raid.sh") in _CATEGORY_PIPELINES[cat]["stages"]
    assert ("config_bundle", "config_img.sh") in _CATEGORY_PIPELINES["storage"]["stages"]


def test_pipeline_last_two_match_category_stages():
    """Last 2 stages of pipeline must match _CATEGORY_STAGES-derived values."""
    from node_operations.config import _CATEGORY_STAGES
    for cat, pipe in _CATEGORY_PIPELINES.items():
        ib, is_, cb, cs = _CATEGORY_STAGES[cat]
        assert pipe["stages"][-2] == (ib, is_), f"{cat}: second-to-last stage mismatch"
        assert pipe["stages"][-1] == (cb, cs), f"{cat}: last stage mismatch"


def test_k8s_device_pods():
    """Only h200 and b300 need device pod restart."""
    assert _CATEGORY_PIPELINES["h200"]["k8s_device_pods"] is True
    assert _CATEGORY_PIPELINES["b300"]["k8s_device_pods"] is True
    assert _CATEGORY_PIPELINES["cpu"]["k8s_device_pods"] is False
    assert _CATEGORY_PIPELINES["ctrl"]["k8s_device_pods"] is False
    assert _CATEGORY_PIPELINES["storage"]["k8s_device_pods"] is False


# ── _STAGE_TRIGGERS tests ──────────────────────────────────────────────

def test_create_user_triggers_sync_time():
    triggers = _STAGE_TRIGGERS.get(("init_bundle", "create_user.sh"), [])
    names = [t[0] for t in triggers]
    assert "sync_node_time" in names


def test_config_apt_triggers_sbsysinfo_and_sku():
    triggers = _STAGE_TRIGGERS.get(("config_bundle", "config_apt.sh"), [])
    names = [t[0] for t in triggers]
    assert "collect_sbsysinfo" in names
    assert "check_sku" in names
    # collect_sbsysinfo must come before check_sku
    idx_collect = names.index("collect_sbsysinfo")
    idx_sku = names.index("check_sku")
    assert idx_collect < idx_sku, "collect_sbsysinfo must run before check_sku"


def test_config_raid_triggers_reboot_and_check():
    triggers = _STAGE_TRIGGERS.get(("config_bundle", "config_raid.sh"), [])
    names = [t[0] for t in triggers]
    assert "reboot_and_wait" in names
    assert "check_raid.sh" in names
    # reboot must come before check_raid
    idx_reboot = names.index("reboot_and_wait")
    idx_check = names.index("check_raid.sh")
    assert idx_reboot < idx_check, "reboot_and_wait must run before check_raid.sh"


def test_non_triggered_stages_have_no_triggers():
    """install_general.sh, harden_node.sh, and category-specific stages have no triggers."""
    no_trigger_stages = [
        ("software_bundle", "install_general.sh"),
        ("init_bundle", "harden_node.sh"),
        ("software_bundle", "install_h200.sh"),
        ("config_bundle", "config_h200.sh"),
    ]
    for stage in no_trigger_stages:
        assert stage not in _STAGE_TRIGGERS, f"{stage} should not have triggers"


def test_triggers_only_fire_for_stages_in_pipeline():
    """Every trigger key must be a stage that exists in at least one pipeline."""
    all_stages = set()
    for pipe in _CATEGORY_PIPELINES.values():
        all_stages.update(pipe["stages"])
    for trigger_key in _STAGE_TRIGGERS:
        assert trigger_key in all_stages, f"Trigger for {trigger_key} but stage not in any pipeline"


def test_standalone_tool_hints_are_strings():
    """Each trigger's standalone_tool hint must be a non-empty string."""
    for key, triggers in _STAGE_TRIGGERS.items():
        for auto_name, tool_hint in triggers:
            assert isinstance(tool_hint, str) and tool_hint, f"Empty tool hint for {key}/{auto_name}"
