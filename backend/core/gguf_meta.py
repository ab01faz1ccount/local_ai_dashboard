"""
backend/core/gguf_meta.py

What a GGUF file says about itself. Read straight from the file's header, so it
is exact for THIS file and works offline -- unlike a model card on Hugging Face,
which describes the whole repository and is word-for-word the same for the Q4
and the Q6 download of one model.

A GGUF file starts with:

    "GGUF"  version(u32)  tensor_count(u64)  kv_count(u64)
    kv_count x  { key(string)  value_type(u32)  value }
    tensor_count x  { name(string)  n_dims(u32)  dims(u64 x n)  ggml_type(u32)  offset(u64) }

(all little-endian; a string is u64 length + bytes). Version 1 used 32-bit
counts and is not supported. This module reads the header only, never the
weights, through a read-only memory map, so a 20 GB file costs a few megabytes
and well under a second. Everything is bounds-checked: a truncated or hostile
file produces a `GgufError`, never a crash, a hang or a huge allocation.

`summarize()` turns the raw header into the handful of facts a person compares
(exact parameter count, which quantization types the tensors really use, bits
per weight, layers, trained context, KV cache cost per token, ...). Pure
functions, so every rule is unit-tested without a model file.
"""

from __future__ import annotations

import mmap
import re
import struct
from pathlib import Path
from typing import Any, Optional

MAGIC = b"GGUF"
SUPPORTED_VERSIONS = (2, 3)

MAX_KV = 200_000
MAX_TENSORS = 200_000
MAX_DIMS = 8
MAX_STRING_BYTES = 256 * 1024 * 1024  # a chat template is a few KB; this only stops absurd values
KEEP_STRING_CHARS = 400  # long strings (chat templates) are cut for storage
MAX_SHARDS = 512

# value types
_U8, _I8, _U16, _I16, _U32, _I32, _F32, _BOOL, _STR, _ARR, _U64, _I64, _F64 = range(13)
_SCALAR = {
    _U8: ("<B", 1), _I8: ("<b", 1), _U16: ("<H", 2), _I16: ("<h", 2), _U32: ("<I", 4), _I32: ("<i", 4),
    _F32: ("<f", 4), _BOOL: ("<?", 1), _U64: ("<Q", 8), _I64: ("<q", 8), _F64: ("<d", 8),
}

# ggml tensor types (ggml.h `enum ggml_type`)
GGML_TYPES = {
    0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 6: "Q5_0", 7: "Q5_1", 8: "Q8_0", 9: "Q8_1",
    10: "Q2_K", 11: "Q3_K", 12: "Q4_K", 13: "Q5_K", 14: "Q6_K", 15: "Q8_K",
    16: "IQ2_XXS", 17: "IQ2_XS", 18: "IQ3_XXS", 19: "IQ1_S", 20: "IQ4_NL", 21: "IQ3_S", 22: "IQ2_S",
    23: "IQ4_XS", 24: "I8", 25: "I16", 26: "I32", 27: "I64", 28: "F64", 29: "IQ1_M", 30: "BF16",
    34: "TQ1_0", 35: "TQ2_0", 39: "MXFP4",
}

# `general.file_type` (llama.cpp `enum llama_ftype`): the quantization the file was labelled with
LLAMA_FTYPES = {
    0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0", 9: "Q5_1", 10: "Q2_K",
    11: "Q3_K_S", 12: "Q3_K_M", 13: "Q3_K_L", 14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S", 17: "Q5_K_M",
    18: "Q6_K", 19: "IQ2_XXS", 20: "IQ2_XS", 21: "Q2_K_S", 22: "IQ3_XS", 23: "IQ3_XXS", 24: "IQ1_S",
    25: "IQ4_NL", 26: "IQ3_S", 27: "IQ3_M", 28: "IQ2_S", 29: "IQ2_M", 30: "IQ4_XS", 31: "IQ1_M",
    32: "BF16", 36: "TQ1_0", 37: "TQ2_0", 38: "MXFP4_MOE",
}

_SHARD_RE = re.compile(r"^(?P<base>.+)-(?P<idx>\d{5})-of-(?P<total>\d{5})\.gguf$", re.IGNORECASE)


class GgufError(ValueError):
    """The file isn't a GGUF this reader understands. The message is meant to be shown."""


class _Reader:
    """Bounds-checked sequential reader over a bytes-like object (an mmap)."""

    def __init__(self, buf: Any):
        self.buf = buf
        self.size = len(buf)
        self.pos = 0

    def _need(self, n: int) -> None:
        if n < 0 or self.pos + n > self.size:
            raise GgufError("The file ends in the middle of its header: it is truncated or not a GGUF file.")

    def unpack(self, fmt: str, n: int):
        self._need(n)
        (v,) = struct.unpack_from(fmt, self.buf, self.pos)
        self.pos += n
        return v

    def u32(self) -> int:
        return self.unpack("<I", 4)

    def u64(self) -> int:
        return self.unpack("<Q", 8)

    def skip(self, n: int) -> None:
        self._need(n)
        self.pos += n

    def string(self, keep: int = 4096) -> str:
        n = self.u64()
        if n > MAX_STRING_BYTES:
            raise GgufError("A text value in the header is impossibly large: the file is corrupt.")
        self._need(n)
        raw = bytes(self.buf[self.pos : self.pos + min(n, keep * 4)])
        self.pos += n
        return raw.decode("utf-8", errors="replace")[:keep]

    def skip_string(self) -> None:
        n = self.u64()
        if n > MAX_STRING_BYTES:
            raise GgufError("A text value in the header is impossibly large: the file is corrupt.")
        self.skip(n)


def _read_value(r: _Reader, vtype: int, depth: int = 0) -> Any:
    if vtype in _SCALAR:
        fmt, n = _SCALAR[vtype]
        return r.unpack(fmt, n)
    if vtype == _STR:
        return r.string(KEEP_STRING_CHARS)
    if vtype == _ARR:
        if depth > 1:
            raise GgufError("Arrays nested too deeply: the file is corrupt.")
        etype = r.u32()
        count = r.u64()
        if etype in _SCALAR:
            _, n = _SCALAR[etype]
            if count * n > r.size:
                raise GgufError("An array in the header is larger than the file: it is corrupt.")
            r.skip(count * n)
        elif etype == _STR:
            if count > r.size:
                raise GgufError("An array in the header is larger than the file: it is corrupt.")
            for _ in range(count):
                r.skip_string()
        elif etype == _ARR:
            for _ in range(count):
                _read_value(r, _ARR, depth + 1)
        else:
            raise GgufError(f"Unknown array element type {etype}.")
        return {"array_len": count}  # the contents (a whole vocabulary) are never kept
    raise GgufError(f"Unknown value type {vtype}.")


def parse_header(buf: Any, *, want_kv: bool = True) -> dict:
    """Parse a GGUF header from a bytes-like object.

    Returns {"version", "tensor_count", "kv": {key: value}, "tensors": {type_name: {"count", "params"}},
    "total_params"}. With `want_kv=False` the metadata is skipped (used for the 2nd..nth shard of a
    split model, which only contributes tensors)."""
    r = _Reader(buf)
    if r.size < 24 or bytes(buf[:4]) != MAGIC:
        raise GgufError("This is not a GGUF file (it doesn't start with 'GGUF').")
    r.pos = 4
    version = r.u32()
    if version not in SUPPORTED_VERSIONS:
        raise GgufError(
            f"GGUF version {version} isn't supported (versions 2 and 3 are)."
            if version < 0x10000
            else "This GGUF file is big-endian, which isn't supported."
        )
    tensor_count = r.u64()
    kv_count = r.u64()
    if kv_count > MAX_KV or tensor_count > MAX_TENSORS:
        raise GgufError("The header declares an impossible number of entries: the file is corrupt.")

    kv: dict[str, Any] = {}
    for _ in range(kv_count):
        key = r.string(512)
        vtype = r.u32()
        value = _read_value(r, vtype)
        if want_kv:
            kv[key] = value

    tensors: dict[str, dict] = {}
    total = 0
    for _ in range(tensor_count):
        r.skip_string()
        n_dims = r.u32()
        if n_dims > MAX_DIMS:
            raise GgufError("A tensor declares too many dimensions: the file is corrupt.")
        elems = 1
        for _d in range(n_dims):
            elems *= r.u64()
        ttype = r.u32()
        r.skip(8)  # data offset
        name = GGML_TYPES.get(ttype, f"type {ttype}")
        t = tensors.setdefault(name, {"count": 0, "params": 0})
        t["count"] += 1
        t["params"] += elems
        total += elems
    return {"version": version, "tensor_count": tensor_count, "kv": kv, "tensors": tensors, "total_params": total}


def _shard_paths(path: Path) -> list[Path]:
    """All parts of a split model when `path` is its first part, else just the file."""
    m = _SHARD_RE.match(path.name)
    if not m or int(m.group("idx")) != 1:
        return [path]
    total = int(m.group("total"))
    if total > MAX_SHARDS:
        raise GgufError("This split model declares an impossible number of parts.")
    parts = [path.with_name(f"{m.group('base')}-{i:05d}-of-{m.group('total')}.gguf") for i in range(1, total + 1)]
    missing = [p.name for p in parts if not p.is_file()]
    if missing:
        raise GgufError(f"This is a split model but {len(missing)} of its {total} parts are missing next to it.")
    return parts


def read_file(path: str) -> dict:
    """Header of a GGUF file on disk (all parts, for a split model). Raises GgufError / OSError."""
    p = Path(path)
    shards = _shard_paths(p)
    merged: Optional[dict] = None
    for i, sp in enumerate(shards):
        with open(sp, "rb") as f:
            if sp.stat().st_size == 0:
                raise GgufError("The file is empty.")
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                part = parse_header(mm, want_kv=(i == 0))
        if merged is None:
            merged = part
            merged["parts"] = len(shards)
        else:
            for name, t in part["tensors"].items():
                m = merged["tensors"].setdefault(name, {"count": 0, "params": 0})
                m["count"] += t["count"]
                m["params"] += t["params"]
            merged["total_params"] += part["total_params"]
            merged["tensor_count"] += part["tensor_count"]
    assert merged is not None
    return merged


# ---------------------------------------------------------------------
# From raw header to the facts people compare
# ---------------------------------------------------------------------


def _fmt_params(n: int) -> str:
    if n >= 1e9:
        return f"{n / 1e9:.2f} B"
    if n >= 1e6:
        return f"{n / 1e6:.0f} M"
    return str(n)


def _int(v: Any) -> Optional[int]:
    return int(v) if isinstance(v, int) and not isinstance(v, bool) and v > 0 else None


def tensor_mix(tensors: dict, limit: int = 4) -> list[dict]:
    """The quantization types the weights really use, biggest share first. A "Q4_K_M" file is
    mostly Q4_K with some Q6_K tensors: that mix, not the label, is what differs between files."""
    total = sum(t["params"] for t in tensors.values()) or 1
    items = sorted(tensors.items(), key=lambda kv: -kv[1]["params"])
    return [{"type": name, "share": round(t["params"] / total, 4), "tensors": t["count"]} for name, t in items[:limit]]


def kv_cache_bytes_per_token(kv: dict, arch: Optional[str]) -> Optional[dict]:
    """Bytes the attention cache needs per token of context at f16 (K and V, every layer that
    keeps one). Needs layers, KV heads and head size from the header; None if any is missing.

    Models that mix full attention with other layer kinds (a `full_attention_interval` key)
    only keep a cache on the full-attention layers; without that key every layer is assumed to."""
    if not arch:
        return None
    layers = _int(kv.get(f"{arch}.block_count"))
    heads = _int(kv.get(f"{arch}.attention.head_count"))
    kv_heads = _int(kv.get(f"{arch}.attention.head_count_kv")) or heads
    emb = _int(kv.get(f"{arch}.embedding_length"))
    head_dim = _int(kv.get(f"{arch}.attention.key_length")) or (emb // heads if emb and heads else None)
    if not (layers and kv_heads and head_dim):
        return None
    cached_layers = layers
    note = None
    interval = _int(kv.get(f"{arch}.full_attention_interval"))
    if interval:
        # llama.cpp marks layer i as full attention when (i + 1) % interval == 0
        cached_layers = max(layers // interval, 1)
        note = f"about {cached_layers} of {layers} layers keep a cache (full attention every {interval})"
    per_token = 2 * cached_layers * kv_heads * head_dim * 2
    return {"bytes_per_token": per_token, "layers_with_cache": cached_layers, "kv_heads": kv_heads, "head_dim": head_dim, "note": note}


def summarize(raw: dict, file_size_bytes: Optional[int] = None) -> dict:
    """The facts worth comparing, from `parse_header`/`read_file` output."""
    kv = raw.get("kv", {})
    arch = kv.get("general.architecture") if isinstance(kv.get("general.architecture"), str) else None
    params = raw.get("total_params") or 0
    ftype = kv.get("general.file_type")
    mix = tensor_mix(raw.get("tensors", {}))

    def a(key: str) -> Optional[int]:
        return _int(kv.get(f"{arch}.{key}")) if arch else None

    cache = kv_cache_bytes_per_token(kv, arch)
    template = kv.get("tokenizer.chat_template")
    tokens = kv.get("tokenizer.ggml.tokens")
    bpw = round(file_size_bytes * 8 / params, 2) if file_size_bytes and params else None
    return {
        "architecture": arch,
        "name": kv.get("general.name") if isinstance(kv.get("general.name"), str) else None,
        "size_label": kv.get("general.size_label") if isinstance(kv.get("general.size_label"), str) else None,
        "finetune": kv.get("general.finetune") if isinstance(kv.get("general.finetune"), str) else None,
        "parameters": params or None,
        "parameters_label": _fmt_params(params) if params else None,
        "file_type": LLAMA_FTYPES.get(ftype) if isinstance(ftype, int) else None,
        "tensor_mix": mix,
        "bits_per_weight": bpw,
        "layers": a("block_count"),
        "embedding_length": a("embedding_length"),
        "attention_heads": a("attention.head_count"),
        "kv_heads": cache["kv_heads"] if cache else a("attention.head_count_kv"),
        "context_length": a("context_length"),
        "experts": a("expert_count"),
        "vocab_size": tokens.get("array_len") if isinstance(tokens, dict) else None,
        "has_chat_template": isinstance(template, str) and bool(template.strip()),
        "kv_cache": cache,
        "parts": raw.get("parts", 1),
        "tensor_count": raw.get("tensor_count"),
    }


_CACHE: dict[tuple, dict] = {}
_CACHE_MAX = 64


def summarize_file(path: str) -> dict:
    """`summarize(read_file(path))`, remembered per (path, size, mtime) so comparing the same
    models again doesn't re-read their headers. Raises GgufError / OSError."""
    p = Path(path)
    st = p.stat()
    key = (str(p.resolve()), st.st_size, st.st_mtime_ns)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    size = st.st_size
    shards = _shard_paths(p)
    if len(shards) > 1:
        size = sum(s.stat().st_size for s in shards)
    out = summarize(read_file(path), size)
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = out
    return out


def safe_summary(path: str) -> tuple[Optional[dict], Optional[str]]:
    """(summary, None) or (None, reason). Never raises: a model whose header can't be read still
    appears in the comparison, just without the file-derived rows."""
    try:
        return summarize_file(path), None
    except GgufError as exc:
        return None, str(exc)
    except OSError as exc:
        return None, f"The file can't be read: {exc.strerror or exc}"
