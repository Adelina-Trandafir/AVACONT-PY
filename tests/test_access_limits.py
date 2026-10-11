"""
Limits on the Access sync routes (utils/access_limits.py): size, rows, registered unit, one call per day.
OFFLINE: the database helpers are replaced by recorders, no connection is made.
"""
import json

import pytest
from flask import Flask, jsonify

import utils.access_limits as AL


def test_count_rows_counts_items_and_nested_rectifications():
    assert AL.count_rows({}) == 0
    assert AL.count_rows({"data": [1, 2, 3]}) == 3
    body = {"clasificatii": [{"rectificari": [1, 2]}, {"rectificari": []}, {}]}
    assert AL.count_rows(body) == 3 + 2


@pytest.fixture
def world(monkeypatch):
    state = {"units": {"000_DEMO"}, "claims": set(), "released": [], "handled": 0, "status": 200}

    monkeypatch.setattr(AL, "_dc_exists", lambda dc: dc in state["units"])

    def claim(kind, dc, day, rows):
        key = (kind, dc, day)
        if key in state["claims"]:
            return False
        state["claims"].add(key)
        return True

    def release(kind, dc, day):
        state["claims"].discard((kind, dc, day))
        state["released"].append((kind, dc))

    monkeypatch.setattr(AL, "_claim", claim)
    monkeypatch.setattr(AL, "_release", release)

    app = Flask(__name__)
    app.config["TESTING"] = True

    @app.route("/x", methods=["POST"])
    @AL.access_sync_limits("test_route")
    def x():
        state["handled"] += 1
        return jsonify(ok=True), state["status"]

    return app.test_client(), state


def _post(client, body):
    return client.post("/x", data=json.dumps(body), headers={"Content-Type": "application/json"})


def test_a_good_call_goes_through_once_per_day(world):
    client, state = world
    assert _post(client, {"db_name": "000_DEMO", "data": [{}]}).status_code == 200
    again = _post(client, {"db_name": "000_DEMO", "data": [{}]})
    assert again.status_code == 429
    assert again.get_json()["reason"] == "ACCESS_SYNC_ALREADY_TODAY"
    assert state["handled"] == 1


def test_more_than_200_rows_is_refused_whole_and_uses_no_slot(world):
    client, state = world
    r = _post(client, {"db_name": "000_DEMO", "data": [{}] * 201})
    assert r.status_code == 413 and r.get_json()["reason"] == "ACCESS_SYNC_TOO_MANY_ROWS"
    assert state["handled"] == 0 and not state["claims"]
    assert _post(client, {"db_name": "000_DEMO", "data": [{}] * 200}).status_code == 200


def test_nested_rectifications_count_toward_the_cap(world):
    client, state = world
    body = {"db_name": "000_DEMO", "clasificatii": [{"rectificari": [{}] * 100}] * 2}   # 2 + 200
    assert _post(client, body).status_code == 413


def test_unknown_unit_and_bad_name_are_refused_before_anything_runs(world):
    client, state = world
    assert _post(client, {"db_name": "AVACONT_COMUN", "data": []}).status_code == 403
    assert _post(client, {"db_name": "x; DROP", "data": []}).status_code == 400
    assert _post(client, {"data": []}).status_code == 400
    assert client.post("/x", data="not json", headers={"Content-Type": "application/json"}).status_code == 400
    assert state["handled"] == 0 and not state["claims"]


def test_a_failed_call_gives_the_slot_back(world):
    client, state = world
    state["status"] = 500
    assert _post(client, {"db_name": "000_DEMO", "data": [{}]}).status_code == 500
    assert state["released"] == [("test_route", "000_DEMO")]
    state["status"] = 200
    assert _post(client, {"db_name": "000_DEMO", "data": [{}]}).status_code == 200


def test_oversized_body_is_refused(world, monkeypatch):
    client, state = world
    monkeypatch.setattr(AL, "MAX_BYTES", 50)
    big = {"db_name": "000_DEMO", "data": [{"x": "y" * 100}]}
    assert _post(client, big).status_code == 413
    assert state["handled"] == 0


def test_limits_that_cannot_be_checked_refuse(world, monkeypatch):
    import mysql.connector
    client, state = world

    def boom(dc):
        raise mysql.connector.Error("down")

    monkeypatch.setattr(AL, "_dc_exists", boom)
    r = _post(client, {"db_name": "000_DEMO", "data": []})
    assert r.status_code == 503 and state["handled"] == 0
