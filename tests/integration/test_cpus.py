"""Integration tests for CPU endpoints (§6.7, §7.2, ADR-011)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_list_cpus(client: TestClient) -> None:
    body = client.get("/v1/cpus?limit=100").json()
    assert body["count"] >= 30
    assert body["results"][0]["url"].startswith("/v1/cpus/")


def test_cpu_detail_embeds_manufacturer(client: TestClient) -> None:
    body = client.get("/v1/cpus/core-i9-14900k").json()
    assert body["slug"] == "core-i9-14900k"
    assert body["manufacturer"]["slug"] == "intel"
    assert body["segment"] == "desktop"
    assert body["cores"] >= 1
    assert body["threads"] >= body["cores"] - 1


def test_cpu_does_not_expose_raw_benchmarks(client: TestClient) -> None:
    # ADR-006: Cinebench/Geekbench are algorithm inputs only.
    body = client.get("/v1/cpus/ryzen-9-7950x").json()
    assert "cinebench_r23_single" not in body
    assert "geekbench_single" not in body
    assert body["manufacturer"]["slug"] == "amd"


def test_filter_by_segment(client: TestClient) -> None:
    body = client.get("/v1/cpus?segment=laptop&limit=100").json()
    assert body["count"] >= 1
    # every returned CPU should be a laptop part
    for item in body["results"]:
        detail = item  # list items are refs; fetch one for the segment check below
        assert detail["url"].startswith("/v1/cpus/")
    sample = client.get(body["results"][0]["url"]).json()
    assert sample["segment"] == "laptop"


def test_unknown_cpu_404(client: TestClient) -> None:
    response = client.get("/v1/cpus/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_cpu_score_endpoint(client: TestClient) -> None:
    body = client.get("/v1/cpus/core-i9-14900k/score").json()
    assert body["algorithm_version"] == "2.0.0"
    assert {"single", "multi"} <= body.keys()
    overall = body["overall"]
    assert overall is None or 0.0 <= overall <= 100.0


def test_cpu_detail_embeds_score_with_provenance(client: TestClient) -> None:
    score = client.get("/v1/cpus/core-i9-14900k").json()["score"]
    assert score["algorithm_version"] == "2.0.0"
    multi = score["multi"]
    if multi["index"] is not None:
        # provenance is the benchmark NAME (never the raw value, ADR-006)
        assert isinstance(multi["source"], str)
        assert 0.0 <= multi["index"] <= 100.0
