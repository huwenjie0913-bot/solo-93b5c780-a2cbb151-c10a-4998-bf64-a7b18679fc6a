import os
import sys

sys.path.insert(0, "/workspace/.pylibs")
sys.path.insert(0, "/workspace")

os.environ["SELECTIVITY_DB"] = "/tmp/test_selectivity.db"

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client():
    if os.path.exists(os.environ["SELECTIVITY_DB"]):
        os.remove(os.environ["SELECTIVITY_DB"])
    return TestClient(app)


def seg(kind, points, cur_tol=0.0, time_tol=0.0):
    return {"kind": kind,
            "points": [{"i": i, "t": t} for i, t in points],
            "tolerance": {"current_pct": cur_tol, "time_pct": time_tol}}


def good_doc():
    """三级配电：CB1 总开关（slow/fast 两档），CB2 馈线带静态负载，CB3 带电动机。"""
    return {
        "meta": {"name": "demo"},
        "units": {"current": "A", "time": "s"},
        "topology": {
            "source": "S",
            "nodes": [{"id": "S"}, {"id": "B1"}, {"id": "B2"}, {"id": "B3"}],
            "branches": [
                {"id": "br0", "from_node": "S", "to_node": "B1", "device_id": "CB1"},
                {"id": "br1", "from_node": "B1", "to_node": "B2", "device_id": "CB2", "load_id": "L1"},
                {"id": "br2", "from_node": "B1", "to_node": "B3", "device_id": "CB3", "load_id": "M1"},
            ],
        },
        "loads": [
            {"id": "L1", "kind": "static", "current": 100},
            {"id": "M1", "kind": "motor", "current": 50, "start_current": 300, "start_duration": 5},
        ],
        "fault_currents": [
            {"node_id": "B1", "min": 3000, "max": 15000},
            {"node_id": "B2", "min": 2000, "max": 8000},
            {"node_id": "B3", "min": 2000, "max": 8000},
        ],
        "devices": [
            {
                "id": "CB1", "type": "breaker", "rated_current": 400,
                "active_setting": "slow",
                "settings": [
                    {"name": "slow", "segments": [
                        seg("overload", [(500, 100), (5000, 10), (20000, 1)]),
                        seg("short_circuit", [(5000, 0.5), (20000, 0.2)]),
                    ]},
                    {"name": "fast", "segments": [
                        seg("overload", [(500, 5), (5000, 0.5), (20000, 0.05)]),
                        seg("short_circuit", [(5000, 0.1), (20000, 0.05)]),
                    ]},
                ],
            },
            {
                "id": "CB2", "type": "breaker", "rated_current": 125,
                "active_setting": "std",
                "settings": [{"name": "std", "segments": [
                    seg("overload", [(100, 10), (1000, 1), (10000, 0.1)]),
                    seg("short_circuit", [(2000, 0.2), (10000, 0.05)]),
                ]}],
            },
            {
                "id": "CB3", "type": "breaker", "rated_current": 100,
                "active_setting": "std",
                "settings": [{"name": "std", "segments": [
                    seg("overload", [(50, 50), (500, 5), (5000, 0.5)]),
                    seg("short_circuit", [(2000, 0.3), (10000, 0.08)]),
                ]}],
            },
        ],
    }
