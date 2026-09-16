"""Ids must depend only on table+slug, so a dump only changes what the data did."""

from __future__ import annotations

from app.seed import _stable_id


def test_same_slug_same_id_regardless_of_insertion_order():
    assert _stable_id("brands", "samsung", set()) == _stable_id("brands", "samsung", set())


def test_inserting_a_record_does_not_renumber_the_others():
    first_pass = {slug: _stable_id("cpus", slug, set()) for slug in ("a", "b", "c")}
    taken: set[int] = set()
    second_pass = {slug: _stable_id("cpus", slug, taken) for slug in ("a", "new", "b", "c")}
    assert all(second_pass[slug] == first_pass[slug] for slug in first_pass)


def test_same_slug_in_two_tables_gets_two_ids():
    assert _stable_id("cpus", "a1", set()) != _stable_id("gpus", "a1", set())


def test_collision_falls_back_to_a_rehash_not_a_duplicate():
    first = _stable_id("brands", "samsung", set())
    taken = {first}
    assert _stable_id("brands", "samsung", taken) != first


def test_id_fits_in_a_json_safe_integer():
    assert 0 < _stable_id("games", "doom", set()) < 2**53
