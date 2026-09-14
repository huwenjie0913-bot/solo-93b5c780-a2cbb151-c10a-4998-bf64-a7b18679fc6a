import copy

from conftest import good_doc


def _import(client, doc):
    r = client.post("/projects/import", json=doc)
    assert r.status_code == 201, r.json()


def test_coordinated_system_has_no_conflicts(client):
    _import(client, good_doc())
    r = client.post("/checks", json={"label": "baseline"})
    assert r.status_code == 201, r.json()
    body = r.json()
    assert body["summary"]["conflict_count"] == 0
    assert body["rules_version"] == "1.0.0"
    assert body["snapshot_id"] >= 1


def test_fast_upstream_breaks_selectivity(client):
    doc = good_doc()
    doc["devices"][0]["active_setting"] = "fast"  # 换上偏快的总开关
    _import(client, doc)
    body = client.post("/checks", json={}).json()
    kinds = {c["kind"] for c in body["conflicts"]}
    assert {"overload", "short_circuit", "motor_start"} <= kinds
    for c in body["conflicts"]:
        assert c["min_margin_s"] < c["required_margin_s"]
        assert c["overlap_current_range"]["from"] <= c["overlap_current_range"]["to"]
        assert c["upstream"] == "CB1"
    # 受影响回路包含两条馈线
    affected = {c["downstream"] for c in body["conflicts"]}
    assert {"CB2", "CB3"} <= affected


def test_motor_start_conflict_points_at_start_current(client):
    doc = good_doc()
    doc["devices"][0]["active_setting"] = "fast"
    _import(client, doc)
    body = client.post("/checks", json={}).json()
    motor = [c for c in body["conflicts"] if c["kind"] == "motor_start"]
    assert motor
    assert all(c["min_margin_at_current"] == 300 for c in motor)


def test_check_without_baseline_returns_409(client):
    r = client.post("/checks", json={})
    assert r.status_code == 409


def test_inline_doc_check(client):
    r = client.post("/checks", json={"doc": good_doc()})
    assert r.status_code == 201
    assert r.json()["summary"]["conflict_count"] == 0
