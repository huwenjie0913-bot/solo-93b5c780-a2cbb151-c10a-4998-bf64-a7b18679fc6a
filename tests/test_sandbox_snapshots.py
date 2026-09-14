from conftest import good_doc


def _import(client, doc):
    r = client.post("/projects/import", json=doc)
    assert r.status_code == 201, r.json()


def test_sandbox_finds_feasible_setting(client):
    doc = good_doc()
    doc["devices"][0]["active_setting"] = "fast"
    _import(client, doc)
    r = client.post("/sandbox", json={"search": {"device_ids": ["CB1"]}})
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["evaluated"] == 2
    assert body["baseline_modified"] is False
    feasible_settings = [f["settings"] for f in body["feasible"]]
    assert {"CB1": "slow"} in feasible_settings
    assert all(f["settings"] != {"CB1": "fast"} for f in body["feasible"])


def test_sandbox_reports_remaining_conflicts_when_none_feasible(client):
    doc = good_doc()
    doc["devices"][0]["active_setting"] = "fast"
    _import(client, doc)
    # 只允许 fast 档，必然无解
    r = client.post("/sandbox", json={
        "search": {"device_ids": ["CB1"], "allowed_settings": {"CB1": ["fast"]}}})
    body = r.json()
    assert body["feasible_count"] == 0
    assert body["remaining_conflicts"]
    assert body["remaining_conflicts"][0]["conflicts"]


def test_sandbox_does_not_touch_baseline(client):
    doc = good_doc()
    doc["devices"][0]["active_setting"] = "fast"
    _import(client, doc)
    client.post("/sandbox", json={"search": {"device_ids": ["CB1"]}})
    stored = client.get("/projects/baseline").json()["doc"]
    assert stored["devices"][0]["active_setting"] == "fast"


def test_sandbox_rejects_unknown_device(client):
    _import(client, good_doc())
    r = client.post("/sandbox", json={"search": {"device_ids": ["NOPE"]}})
    assert r.status_code == 422


def test_snapshot_compare_shows_risk_change(client):
    bad = good_doc()
    bad["devices"][0]["active_setting"] = "fast"
    _import(client, bad)
    snap_bad = client.post("/checks", json={"label": "换闸后"}).json()["snapshot_id"]

    _import(client, good_doc())
    snap_good = client.post("/checks", json={"label": "回调slow"}).json()["snapshot_id"]

    r = client.get(f"/snapshots/compare?a={snap_bad}&b={snap_good}")
    assert r.status_code == 200
    body = r.json()
    assert body["risk_delta"]["conflict_count_before"] > 0
    assert body["risk_delta"]["conflict_count_after"] == 0
    assert body["risk_delta"]["net_change"] < 0
    snap_a = client.get(f"/snapshots/{snap_bad}").json()
    distinct_pairs = {(c["downstream"], c["upstream"], c["kind"])
                      for c in snap_a["result"]["conflicts"]}
    assert len(body["conflicts_resolved"]) == len(distinct_pairs)
    assert body["rules_version_mismatch"] is False


def test_snapshot_detail_keeps_input_and_rules(client):
    _import(client, good_doc())
    snap_id = client.post("/checks", json={}).json()["snapshot_id"]
    snap = client.get(f"/snapshots/{snap_id}").json()
    assert snap["rules_version"] == "1.0.0"
    assert snap["input"]["devices"][0]["id"] == "CB1"
    assert "conflicts" in snap["result"] and "summary" in snap["result"]
    listed = client.get("/snapshots").json()
    assert any(s["id"] == snap_id for s in listed)
