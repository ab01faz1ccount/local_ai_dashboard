"""How a model that was just downloaded is described: by hash, by size only, or
not at all -- and that the weakest file decides for a split model."""

from backend.core import authenticity as au
from backend.core.model_discovery import RepoFile


def files(*shas, size=100):
    return [RepoFile(f"m-0000{i + 1}.gguf", size, sha) for i, sha in enumerate(shas)]


def test_all_files_hash_checked_is_verified():
    f = files("aa", "bb")
    r = au.verification_from_download(f, {f[0].path: "hash", f[1].path: "hash"}, f[0].path)
    assert r["status"] == au.STATUS_VERIFIED and r["method"] == "download"
    assert r["remote_sha256"] == "aa" and r["local_sha256"] == "aa"
    assert r["files_checked"] == 2 and "all 2 parts" in r["reason"]


def test_single_file_reason_names_the_hash():
    f = files("aa")
    r = au.verification_from_download(f, {f[0].path: "hash"}, f[0].path)
    assert r["status"] == au.STATUS_VERIFIED and "SHA-256" in r["reason"] and "all" not in r["reason"]


def test_one_unhashed_part_downgrades_the_whole_model_to_size_only():
    f = files("aa", None)
    r = au.verification_from_download(f, {f[0].path: "hash", f[1].path: "size"}, f[0].path)
    assert r["status"] == au.STATUS_METADATA_ONLY
    assert r["local_sha256"] is None  # never claim a local hash that wasn't compared
    assert "size" in r["reason"]


def test_nothing_compared_is_no_hash_available():
    f = files(None)
    r = au.verification_from_download(f, {f[0].path: "none"}, f[0].path)
    assert r["status"] == au.STATUS_NO_HASH_AVAILABLE and r["local_sha256"] is None


def test_missing_evidence_counts_as_nothing_checked_not_verified():
    f = files("aa")
    assert au.verification_from_download(f, {}, f[0].path)["status"] == au.STATUS_NO_HASH_AVAILABLE


def test_no_files_is_never_verified():
    assert au.verification_from_download([], {}, "x.gguf")["status"] == au.STATUS_NO_HASH_AVAILABLE


def test_size_only_is_stored_as_a_value_the_existing_check_constraint_accepts():
    # models.verification_status has a CHECK that doesn't list "metadata_only".
    allowed = {"pending", "verified", "hash_mismatch", "metadata_mismatch", "not_found", "no_hash_available", "error"}
    for status in (au.STATUS_VERIFIED, au.STATUS_HASH_MISMATCH, au.STATUS_METADATA_ONLY, au.STATUS_METADATA_MISMATCH,
                   au.STATUS_NOT_FOUND, au.STATUS_NO_HASH_AVAILABLE, au.STATUS_ERROR):
        assert au.column_status(status) in allowed, status
    assert au.column_status(au.STATUS_METADATA_ONLY) == au.STATUS_NO_HASH_AVAILABLE
    assert au.column_status(au.STATUS_VERIFIED) == au.STATUS_VERIFIED
