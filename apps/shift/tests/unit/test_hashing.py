"""Canonical JSON and SHA-256 vectors behind the event hash chain."""

from shift.common.hashing import GENESIS_HASH, canonical_json, chain_hash, sha256_hex


def test_sha256_matches_the_standard_vector() -> None:
    assert sha256_hex("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_canonical_json_sorts_keys_and_keeps_unicode() -> None:
    left = canonical_json({"b": 2, "a": "سیب"})
    right = canonical_json({"a": "سیب", "b": 2})

    assert left == right
    assert left == '{"a":"سیب","b":2}'


def test_chain_hash_depends_on_previous_hash() -> None:
    material = {"event": "one"}

    first = chain_hash(GENESIS_HASH, material)
    second = chain_hash("f" * 64, material)

    assert first != second
    assert len(first) == 64
