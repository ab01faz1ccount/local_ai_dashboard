"""The GGUF header reader, against files built to the format's byte layout (not against mocks of it)."""

import os
import struct

import pytest

from backend.core import gguf_meta as g

U8, I8, U16, I16, U32, I32, F32, BOOL, STR, ARR, U64, I64, F64 = range(13)


def s(text: str) -> bytes:
    raw = text.encode()
    return struct.pack("<Q", len(raw)) + raw


def kv_str(k, v):
    return s(k) + struct.pack("<I", STR) + s(v)


def kv_u32(k, v):
    return s(k) + struct.pack("<II", U32, v)


def kv_arr_str(k, items):
    return s(k) + struct.pack("<IIQ", ARR, STR, len(items)) + b"".join(s(i) for i in items)


def kv_arr_i32(k, items):
    return s(k) + struct.pack("<IIQ", ARR, I32, len(items)) + struct.pack(f"<{len(items)}i", *items)


def tensor(name, dims, ttype):
    return s(name) + struct.pack("<I", len(dims)) + struct.pack(f"<{len(dims)}Q", *dims) + struct.pack("<IQ", ttype, 0)


def build(kvs: list, tensors: list, version=3, pad=0) -> bytes:
    head = g.MAGIC + struct.pack("<I", version) + struct.pack("<QQ", len(tensors), len(kvs))
    return head + b"".join(kvs) + b"".join(tensors) + b"\0" * pad


def model_bytes(ftype=15, tmix=((12, 1000), (14, 200)), extra_kv=(), arch="qwen2", name="Demo-9B", pad=0):
    kvs = [
        kv_str("general.architecture", arch),
        kv_str("general.name", name),
        kv_u32("general.file_type", ftype),
        kv_u32(f"{arch}.block_count", 4),
        kv_u32(f"{arch}.embedding_length", 64),
        kv_u32(f"{arch}.attention.head_count", 8),
        kv_u32(f"{arch}.attention.head_count_kv", 2),
        kv_u32(f"{arch}.context_length", 32768),
        kv_str("tokenizer.chat_template", "{{ messages }}"),
        kv_arr_str("tokenizer.ggml.tokens", ["a", "bb", "ccc"]),
        kv_arr_i32("tokenizer.ggml.token_type", [1, 1, 3]),
        *extra_kv,
    ]
    ts = [tensor(f"t{i}", (n, 1), ty) for i, (ty, n) in enumerate(tmix)]
    return build(kvs, ts, pad=pad)


@pytest.fixture
def write(tmp_path):
    def _w(name, data):
        p = tmp_path / name
        p.write_bytes(data)
        return str(p)
    return _w


# ---------- reading ----------

def test_reads_metadata_arrays_and_tensors_exactly():
    raw = g.parse_header(model_bytes())
    assert raw["version"] == 3 and raw["tensor_count"] == 2
    kv = raw["kv"]
    assert kv["general.architecture"] == "qwen2" and kv["qwen2.block_count"] == 4
    assert kv["tokenizer.ggml.tokens"] == {"array_len": 3}  # the vocabulary itself is never kept
    assert raw["tensors"] == {"Q4_K": {"count": 1, "params": 1000}, "Q6_K": {"count": 1, "params": 200}}
    assert raw["total_params"] == 1200


def test_version_2_is_supported():
    assert g.parse_header(build([kv_str("general.architecture", "x")], [], version=2))["kv"]["general.architecture"] == "x"


def test_all_scalar_types_round_trip():
    kvs = [
        s("a") + struct.pack("<IB", U8, 200), s("b") + struct.pack("<Ib", I8, -5),
        s("c") + struct.pack("<IH", U16, 65000), s("d") + struct.pack("<Ih", I16, -300),
        s("e") + struct.pack("<II", U32, 4_000_000_000), s("f") + struct.pack("<Ii", I32, -7),
        s("g") + struct.pack("<If", F32, 1.5), s("h") + struct.pack("<I?", BOOL, True),
        s("i") + struct.pack("<IQ", U64, 2**40), s("j") + struct.pack("<Iq", I64, -2**40),
        s("k") + struct.pack("<Id", F64, 2.25),
    ]
    kv = g.parse_header(build(kvs, []))["kv"]
    assert kv == {"a": 200, "b": -5, "c": 65000, "d": -300, "e": 4_000_000_000, "f": -7, "g": 1.5, "h": True, "i": 2**40, "j": -2**40, "k": 2.25}


def test_long_text_is_cut_for_storage_but_parsing_continues_after_it():
    long = "x" * 10_000
    kv = g.parse_header(build([kv_str("tokenizer.chat_template", long), kv_u32("after", 7)], []))["kv"]
    assert len(kv["tokenizer.chat_template"]) == g.KEEP_STRING_CHARS and kv["after"] == 7


def test_non_utf8_text_does_not_crash():
    bad = s("k") + struct.pack("<I", STR) + struct.pack("<Q", 2) + b"\xff\xfe"
    assert "k" in g.parse_header(build([bad], []))["kv"]


def test_unknown_tensor_type_is_named_not_fatal():
    raw = g.parse_header(build([], [tensor("w", (5,), 99)]))
    assert raw["tensors"] == {"type 99": {"count": 1, "params": 5}}


def test_multi_dimensional_tensor_params_are_the_product():
    assert g.parse_header(build([], [tensor("w", (4, 8, 2), 0)]))["total_params"] == 64


def test_nested_arrays_are_skipped():
    # array of 2 arrays of 2 int32: outer header, then each element is (etype u32, count u64, data)
    elems = (struct.pack("<IQ", I32, 2) + struct.pack("<2i", 1, 2)) * 2
    outer = s("n") + struct.pack("<IIQ", ARR, ARR, 2) + elems
    assert g.parse_header(build([outer, kv_u32("after", 1)], []))["kv"]["after"] == 1


# ---------- refusing bad files ----------

def test_not_a_gguf():
    with pytest.raises(g.GgufError, match="not a GGUF"):
        g.parse_header(b"NOPE" + b"\0" * 64)


@pytest.mark.parametrize("version,msg", [(1, "version 1"), (4, "version 4"), (0x03000000, "big-endian")])
def test_unsupported_versions(version, msg):
    with pytest.raises(g.GgufError, match=msg):
        g.parse_header(build([], [], version=version))


def test_absurd_counts_are_refused_before_any_allocation():
    data = g.MAGIC + struct.pack("<IQQ", 3, 10, 10**12) + b"\0" * 64
    with pytest.raises(g.GgufError, match="impossible number"):
        g.parse_header(data)


def test_huge_text_length_is_refused():
    data = build([s("k") + struct.pack("<I", STR) + struct.pack("<Q", 2**60)], [])
    with pytest.raises(g.GgufError):
        g.parse_header(data)


def test_array_larger_than_the_file_is_refused():
    data = build([s("k") + struct.pack("<IIQ", ARR, U32, 2**50)], [])
    with pytest.raises(g.GgufError):
        g.parse_header(data)
    data = build([s("k") + struct.pack("<IIQ", ARR, STR, 2**50)], [])
    with pytest.raises(g.GgufError):
        g.parse_header(data)


def test_too_many_dimensions_and_unknown_types_are_refused():
    with pytest.raises(g.GgufError):
        g.parse_header(build([], [s("w") + struct.pack("<I", 99) + b"\0" * 200]))
    with pytest.raises(g.GgufError, match="Unknown value type"):
        g.parse_header(build([s("k") + struct.pack("<I", 77)], []))


def test_every_truncation_is_a_clean_error_never_another_exception():
    data = model_bytes()
    for cut in range(0, len(data)):
        try:
            g.parse_header(data[:cut])
        except g.GgufError:
            continue
        # a cut that still parses is only possible past the last tensor entry
        assert cut >= len(data) - 1, f"cut at {cut} parsed"


def test_random_corruption_never_escapes_as_a_different_exception():
    import random
    rnd = random.Random(7)
    base = bytearray(model_bytes())
    for _ in range(400):
        data = bytearray(base)
        for _k in range(rnd.randint(1, 6)):
            data[rnd.randrange(len(data))] = rnd.randrange(256)
        try:
            g.parse_header(bytes(data))
        except g.GgufError:
            pass


# ---------- summary ----------

def test_summary_facts():
    raw = g.parse_header(model_bytes(ftype=15))
    sm = g.summarize(raw, file_size_bytes=1000)
    assert sm["architecture"] == "qwen2" and sm["name"] == "Demo-9B"
    assert sm["parameters"] == 1200 and sm["file_type"] == "Q4_K_M"
    assert sm["layers"] == 4 and sm["embedding_length"] == 64 and sm["attention_heads"] == 8 and sm["kv_heads"] == 2
    assert sm["context_length"] == 32768 and sm["vocab_size"] == 3 and sm["has_chat_template"] is True
    assert sm["bits_per_weight"] == round(1000 * 8 / 1200, 2)
    assert [m["type"] for m in sm["tensor_mix"]] == ["Q4_K", "Q6_K"]
    assert sm["tensor_mix"][0]["share"] == round(1000 / 1200, 4)


def test_q4_and_q6_files_of_the_same_model_differ_in_what_people_compare():
    q4 = g.summarize(g.parse_header(model_bytes(ftype=15, tmix=((12, 1000), (14, 200)))), 700)
    q6 = g.summarize(g.parse_header(model_bytes(ftype=18, tmix=((14, 1100), (0, 100)))), 980)
    assert q4["file_type"] == "Q4_K_M" and q6["file_type"] == "Q6_K"
    assert q4["tensor_mix"] != q6["tensor_mix"]
    assert q4["bits_per_weight"] < q6["bits_per_weight"]
    # the model family facts are the same, which is exactly why a model card can't tell them apart
    for key in ("architecture", "layers", "context_length", "vocab_size"):
        assert q4[key] == q6[key]


def test_kv_cache_per_token_is_exact_for_known_geometry():
    kv = {"qwen2.block_count": 4, "qwen2.attention.head_count": 8, "qwen2.attention.head_count_kv": 2, "qwen2.embedding_length": 64}
    c = g.kv_cache_bytes_per_token(kv, "qwen2")
    # 2 (K and V) * 4 layers * 2 kv heads * head_dim 8 * 2 bytes (f16)
    assert c["bytes_per_token"] == 2 * 4 * 2 * 8 * 2 == 256
    assert c["head_dim"] == 8 and c["note"] is None


def test_explicit_key_length_wins_over_embedding_split():
    kv = {"x.block_count": 2, "x.attention.head_count": 4, "x.attention.head_count_kv": 4, "x.embedding_length": 64, "x.attention.key_length": 32}
    assert g.kv_cache_bytes_per_token(kv, "x")["head_dim"] == 32


def test_missing_kv_heads_falls_back_to_head_count():
    kv = {"x.block_count": 2, "x.attention.head_count": 4, "x.embedding_length": 64}
    assert g.kv_cache_bytes_per_token(kv, "x")["kv_heads"] == 4


def test_hybrid_models_only_count_their_full_attention_layers():
    kv = {"x.block_count": 40, "x.attention.head_count": 8, "x.attention.head_count_kv": 2, "x.embedding_length": 64, "x.full_attention_interval": 4}
    c = g.kv_cache_bytes_per_token(kv, "x")
    assert c["layers_with_cache"] == 10 and c["bytes_per_token"] == 2 * 10 * 2 * 8 * 2 and "about 10 of 40" in c["note"]


def test_missing_geometry_gives_no_cache_figure_rather_than_a_guess():
    assert g.kv_cache_bytes_per_token({"x.block_count": 4}, "x") is None
    assert g.kv_cache_bytes_per_token({}, None) is None


def test_summary_of_a_file_with_almost_nothing_is_all_none_not_an_error():
    sm = g.summarize(g.parse_header(build([], [])), None)
    assert sm["architecture"] is None and sm["parameters"] is None and sm["bits_per_weight"] is None
    assert sm["has_chat_template"] is False and sm["vocab_size"] is None and sm["kv_cache"] is None


def test_unknown_file_type_number_is_none():
    kv = [kv_str("general.architecture", "x"), kv_u32("general.file_type", 999)]
    assert g.summarize(g.parse_header(build(kv, [])))["file_type"] is None


# ---------- files on disk ----------

def test_summarize_file_and_cache(write):
    p = write("m.gguf", model_bytes(pad=500))
    a = g.summarize_file(p)
    assert a["parameters"] == 1200
    assert g.summarize_file(p) is a  # cached
    with open(p, "ab") as f:
        f.write(b"\0" * 10)  # file changed -> re-read
    assert g.summarize_file(p) is not a


def test_bits_per_weight_uses_the_real_file_size(write):
    p = write("m.gguf", model_bytes(pad=4000))
    sm = g.summarize_file(p)
    assert sm["bits_per_weight"] == round(os.path.getsize(p) * 8 / 1200, 2)


def test_split_model_sums_all_parts(write):
    first = build([kv_str("general.architecture", "x"), kv_u32("general.file_type", 15)], [tensor("a", (100,), 12)])
    second = build([kv_u32("split.no", 1)], [tensor("b", (50,), 14)])
    p = write("big-00001-of-00002.gguf", first)
    write("big-00002-of-00002.gguf", second)
    sm = g.summarize_file(p)
    assert sm["parameters"] == 150 and sm["parts"] == 2
    assert {m["type"] for m in sm["tensor_mix"]} == {"Q4_K", "Q6_K"}
    assert sm["bits_per_weight"] == round((len(first) + len(second)) * 8 / 150, 2)


def test_split_model_with_a_missing_part_says_so(write):
    p = write("big-00001-of-00003.gguf", build([], []))
    write("big-00002-of-00003.gguf", build([], []))
    _, why = g.safe_summary(p)
    assert "1 of its 3 parts are missing" in why


def test_safe_summary_never_raises(write, tmp_path):
    assert g.safe_summary(str(tmp_path / "nope.gguf"))[0] is None
    assert "can't be read" in g.safe_summary(str(tmp_path / "nope.gguf"))[1]
    empty = write("e.gguf", b"")
    assert g.safe_summary(empty) == (None, "The file is empty.")
    assert "not a GGUF" in g.safe_summary(write("t.gguf", b"just some text, not a model file at all"))[1]
    ok, why = g.safe_summary(write("ok.gguf", model_bytes()))
    assert why is None and ok["architecture"] == "qwen2"


def test_large_vocabulary_header_parses_quickly(write):
    import time
    tokens = [f"tok{i}" for i in range(200_000)]
    p = write("v.gguf", build([kv_str("general.architecture", "x"), kv_arr_str("tokenizer.ggml.tokens", tokens)], [tensor("w", (10,), 0)]))
    t0 = time.time()
    sm = g.summarize_file(p)
    assert sm["vocab_size"] == 200_000 and sm["parameters"] == 10
    assert time.time() - t0 < 5


def test_interval_that_does_not_divide_the_layers_counts_only_complete_groups():
    # llama.cpp marks layer i as full attention when (i + 1) % interval == 0:
    # 10 layers, every 4th -> layers 3 and 7 keep a cache: 2, not 3
    kv = {"x.block_count": 10, "x.attention.head_count": 8, "x.attention.head_count_kv": 2, "x.embedding_length": 64, "x.full_attention_interval": 4}
    assert g.kv_cache_bytes_per_token(kv, "x")["layers_with_cache"] == 2
