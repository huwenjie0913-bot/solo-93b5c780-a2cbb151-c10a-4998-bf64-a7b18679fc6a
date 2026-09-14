import copy

from conftest import good_doc


def test_import_ok(client):
    r = client.post("/projects/import", json=good_doc())
    assert r.status_code == 201, r.json()
    assert r.json()["units_normalized_to"] == {"current": "A", "time": "s"}


def test_import_normalizes_units(client):
    doc = good_doc()
    doc["units"] = {"current": "kA", "time": "ms"}
    # 把数值缩放到 kA / ms 表示，物理量不变
    for load in doc["loads"]:
        load["current"] /= 1000
        if "start_current" in load and load["start_current"]:
            load["start_current"] /= 1000
            load["start_duration"] *= 1000
    for fc in doc["fault_currents"]:
        fc["min"] /= 1000
        fc["max"] /= 1000
    for dev in doc["devices"]:
        dev["rated_current"] /= 1000
        for st in dev["settings"]:
            for s in st["segments"]:
                for p in s["points"]:
                    p["i"] /= 1000
                    p["t"] *= 1000
    assert client.post("/projects/import", json=doc).status_code == 201
    stored = client.get("/projects/baseline").json()["doc"]
    assert stored["units"] == {"current": "A", "time": "s"}
    assert stored["loads"][0]["current"] == 100
    assert stored["devices"][0]["settings"][0]["segments"][0]["points"][0] == {"i": 500, "t": 100}


def test_reject_non_monotonic_curve(client):
    doc = good_doc()
    pts = doc["devices"][0]["settings"][0]["segments"][0]["points"]
    pts[1]["t"] = pts[0]["t"] + 1  # 时间随电流回升
    r = client.post("/projects/import", json=doc)
    assert r.status_code == 422
    assert any("非单调" in e for e in r.json()["errors"])


def test_reject_disconnected_topology(client):
    doc = good_doc()
    doc["topology"]["nodes"].append({"id": "ISLAND"})
    doc["topology"]["branches"].append(
        {"id": "brX", "from_node": "ISLAND", "to_node": "B2"})
    r = client.post("/projects/import", json=doc)
    assert r.status_code == 422
    assert any("拓扑断开" in e for e in r.json()["errors"])


def test_reject_missing_segment(client):
    doc = good_doc()
    doc["devices"][1]["settings"][0]["segments"] = \
        [s for s in doc["devices"][1]["settings"][0]["segments"] if s["kind"] != "short_circuit"]
    r = client.post("/projects/import", json=doc)
    assert r.status_code == 422
    assert any("缺失保护段" in e for e in r.json()["errors"])


def test_reject_unknown_unit(client):
    doc = good_doc()
    doc["units"]["current"] = "mA"
    r = client.post("/projects/import", json=doc)
    assert r.status_code == 422
    assert any("未知电流单位" in e for e in r.json()["errors"])
