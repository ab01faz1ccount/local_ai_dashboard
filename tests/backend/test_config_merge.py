"""
tests/backend/test_config_merge.py

PATCH /runtimes|/agents `config_json` is merged into the stored dict. A
`null` removes the key: before this, a field cleared in Settings was simply
not sent, the merge kept the old value, and llama-server went on getting it.
"""

from backend.core.config_merge import merge_config_patch


def test_merges_and_overrides_without_touching_other_keys():
    assert merge_config_patch({"a": 1, "b": 2}, {"b": 3, "c": 4}) == {"a": 1, "b": 3, "c": 4}


def test_null_removes_the_key():
    assert merge_config_patch({"ctx_size": 240000, "jinja": True}, {"ctx_size": None}) == {"jinja": True}


def test_removing_a_missing_key_is_a_noop():
    assert merge_config_patch({"a": 1}, {"zzz": None}) == {"a": 1}


def test_false_and_zero_and_empty_list_are_values_not_removals():
    out = merge_config_patch({}, {"jinja": False, "seed": 0, "extra_args": []})
    assert out == {"jinja": False, "seed": 0, "extra_args": []}


def test_does_not_mutate_the_stored_dict():
    stored = {"a": 1}
    merge_config_patch(stored, {"a": None, "b": 2})
    assert stored == {"a": 1}


def test_existing_may_be_none():
    assert merge_config_patch(None, {"a": 1, "b": None}) == {"a": 1}
