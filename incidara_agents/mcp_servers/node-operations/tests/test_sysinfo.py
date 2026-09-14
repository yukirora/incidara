from node_operations.sysinfo import read_sku

def test_read_sku(tmp_path):
    d = tmp_path / "sbsysinfo_results" / "10.0.0.1"
    d.mkdir(parents=True)
    (d / "sku.txt").write_text("h200x8-64c2\n")
    assert read_sku("10.0.0.1", base_dir=str(tmp_path)) == "h200x8-64c2"

def test_read_sku_match_failed(tmp_path):
    d = tmp_path / "sbsysinfo_results" / "10.0.0.1"
    d.mkdir(parents=True)
    (d / "sku.txt").write_text("match failed: no sku")
    try:
        read_sku("10.0.0.1", base_dir=str(tmp_path))
        assert False
    except RuntimeError:
        pass
