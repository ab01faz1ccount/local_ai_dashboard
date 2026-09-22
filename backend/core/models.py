"""
backend/core/models.py

Model Manager (minimal, per MVP scope): scans a configured folder for
`.gguf` files and keeps the `models` table in sync. Deliberately skips
parsing tokenizer/chat-template/arch metadata out of the gguf header for
now -- name, path, and file size only, as scoped.
"""

from __future__ import annotations

import glob
import re
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from ..storage.db import MLModel


def scan_models_folder(db: Session, folder: str) -> list[MLModel]:
    """Scans `folder` (non-recursive) for *.gguf files. Adds new ones,
    updates file_size_bytes for existing ones whose file changed size,
    and leaves everything else untouched. Never removes a DB row just
    because a file went missing -- a runtime may still reference it, and
    "the file was temporarily on a disconnected drive" shouldn't silently
    delete history.

    Input is deliberately forgiving about two common mistakes:
      - Windows' "Copy as path" wraps the path in double quotes -- those
        get stripped rather than making the path look invalid.
      - Pasting the full path to a .gguf file (instead of the folder it's
        in) is treated as "use its parent folder", since that's clearly
        what was meant.
    """
    cleaned = folder.strip().strip('"').strip("'")
    folder_path = Path(cleaned)

    if folder_path.is_file() and folder_path.suffix == ".gguf":
        folder_path = folder_path.parent

    if not folder_path.is_dir():
        raise NotADirectoryError(f"'{cleaned}' is not a valid folder")

    found = list(folder_path.glob("*.gguf"))
    existing_by_path = {m.file_path: m for m in db.query(MLModel).all()}

    for f in found:
        file_path = str(f.resolve())
        size = f.stat().st_size
        if file_path in existing_by_path:
            model = existing_by_path[file_path]
            if model.file_size_bytes != size:
                model.file_size_bytes = size
        else:
            db.add(MLModel(name=f.stem, file_path=file_path, file_size_bytes=size))

    db.commit()
    return db.query(MLModel).all()


_SHARD_SUFFIX_RE = re.compile(r"-(\d{5})-of-(\d{5})$")


def register_model_file(
    db: Session,
    path: str,
    *,
    hf_repo_id: Optional[str] = None,
    hf_filename: Optional[str] = None,
    quantization: Optional[str] = None,
    param_count: Optional[str] = None,
) -> MLModel:
    """Adds ONE specific .gguf file to the catalog (what the offline
    browser's "pick a file" and the online downloader both need), as
    opposed to scan_models_folder() which sweeps a whole folder.

    If a row for this exact path already exists it's updated in place
    (size refreshed; Hugging Face link/metadata filled in only where the
    row doesn't already have a value, so a user's manual edits survive).

    A split model (`name-00001-of-00003.gguf`) is registered once, under
    its first shard -- the one llama.cpp is pointed at -- with the size of
    all shards found next to it.
    """
    cleaned = path.strip().strip('"').strip("'")
    f = Path(cleaned)
    if not f.is_file():
        raise FileNotFoundError(f"'{cleaned}' یه فایل معتبر نیست")
    if f.suffix.lower() != ".gguf":
        raise ValueError("فقط فایل .gguf قابل اضافه شدنه")

    resolved = f.resolve()
    stem = resolved.stem
    size = resolved.stat().st_size

    shard = _SHARD_SUFFIX_RE.search(stem)
    if shard:
        base = stem[: shard.start()]
        total = int(shard.group(2))
        if int(shard.group(1)) != 1:
            raise ValueError("این یه بخش وسط از مدل چندتکه‌ست — فایل اولش (…-00001-of-…) رو انتخاب کن")
        siblings = list(resolved.parent.glob(f"{glob.escape(base)}-*-of-{shard.group(2)}.gguf"))
        size = sum(s.stat().st_size for s in siblings) or size
        stem = base
        if len(siblings) != total:
            raise ValueError(f"مدل چندتکه‌ست ولی فقط {len(siblings)} از {total} بخش کنار فایل هست")

    file_path = str(resolved)
    model = db.query(MLModel).filter(MLModel.file_path == file_path).first()
    if model is None:
        model = MLModel(name=stem, file_path=file_path, file_size_bytes=size)
        db.add(model)
    else:
        model.file_size_bytes = size

    if hf_repo_id and not model.hf_repo_id:
        model.hf_repo_id = hf_repo_id
    if hf_filename and not model.hf_filename:
        model.hf_filename = hf_filename
    if quantization and not model.quantization:
        model.quantization = quantization
    if param_count and not model.param_count:
        model.param_count = param_count

    db.commit()
    db.refresh(model)
    return model
