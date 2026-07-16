from artel.server.feed_poller import _order_key, conflict_sibling_id
from artel.store import vclock


def test_parse_accepts_json_dict_and_none():
    assert vclock.parse(None) == {}
    assert vclock.parse("") == {}
    assert vclock.parse('{"a": 2}') == {"a": 2}
    assert vclock.parse({"a": 2}) == {"a": 2}
    assert vclock.parse("not json") == {}
    assert vclock.parse('["a"]') == {}
    assert vclock.parse({"a": "junk", "b": 3, "c": True}) == {"b": 3}


def test_dump_is_canonical_and_none_for_empty():
    assert vclock.dump({}) is None
    assert vclock.dump({"b": 2, "a": 1}) == '{"a": 1, "b": 2}'


def test_bump_increments_own_counter_only():
    assert vclock.bump({}, "a") == {"a": 1}
    assert vclock.bump({"a": 1, "b": 5}, "a") == {"a": 2, "b": 5}


def test_merge_is_pointwise_max():
    assert vclock.merge({"a": 3, "b": 1}, {"a": 1, "c": 2}) == {"a": 3, "b": 1, "c": 2}


def test_compare_orders():
    assert vclock.compare({"a": 1}, {"a": 1}) == vclock.EQUAL
    assert vclock.compare({"a": 2}, {"a": 1}) == vclock.DOMINATES
    assert vclock.compare({"a": 1}, {"a": 2}) == vclock.DOMINATED
    assert vclock.compare({"a": 2, "b": 1}, {"a": 1}) == vclock.DOMINATES
    assert vclock.compare({"a": 1}, {"b": 1}) == vclock.CONCURRENT
    assert vclock.compare({"a": 2, "b": 1}, {"a": 1, "b": 2}) == vclock.CONCURRENT


def test_compare_treats_missing_as_zero():
    assert vclock.compare({"a": 1, "b": 0}, {"a": 1}) == vclock.EQUAL


def test_order_key_is_deterministic_and_total():
    a = _order_key(1, "2026-05-16T00:00:00.000Z", "x")
    b = _order_key(1, "2026-05-16T00:00:00.000Z", "y")
    assert a != b
    assert (a > b) != (b > a)
    assert _order_key(2, "", "x") > _order_key(1, "zzz", "zzz")


def test_conflict_sibling_id_deterministic_per_gid_and_content():
    a = conflict_sibling_id("gid", "loser text")
    assert a == conflict_sibling_id("gid", "loser text")
    assert a != conflict_sibling_id("gid", "other text")
    assert a != conflict_sibling_id("gid2", "loser text")
