"""
backend/core/onboarding.py

First-run setup wizard logic (backend half). The frontend renders the
actual step-by-step UI; this module answers the two questions it needs:

  1. Is llama.cpp already installed somewhere findable?
  2. If not, where should the user get it, and what should they grab for
     their OS?

Nothing here downloads or auto-installs llama.cpp. Its release assets are
large, vendor-controlled, and their naming changes across versions --
silently auto-downloading is exactly the kind of shortcut that becomes a
maintenance trap later. This gives the wizard accurate, current
information, and the user does the actual download/click-through
themselves.

Finding and downloading *models* used to live here as a small hardcoded
"starter list". That's now the online Browse feature (core/
model_discovery.py + api/discovery.py): search Hugging Face by an
approximate name and download for real, after the user picks a file."""

from __future__ import annotations

import platform
import shutil
from pathlib import Path
from typing import Optional

LLAMA_SERVER_BINARY_NAMES = {
    "Windows": "llama-server.exe",
    "Linux": "llama-server",
    "Darwin": "llama-server",
}

# Common install locations people actually end up with llama.cpp in,
# beyond whatever's already on PATH.
COMMON_SEARCH_DIRS = [
    Path.home() / "llama.cpp",
    Path.home() / "llama.cpp" / "build" / "bin",
    Path.home() / ".local" / "bin",
    Path("/usr/local/bin"),
    Path("/opt/llama.cpp"),
]


def detect_llama_cpp() -> Optional[str]:
    """Looks for the llama-server binary on PATH first, then in a short
    list of common install locations. Returns an absolute path, or None
    if it can't be found -- the wizard falls back to install guidance."""
    binary_name = LLAMA_SERVER_BINARY_NAMES.get(platform.system(), "llama-server")

    found_on_path = shutil.which(binary_name)
    if found_on_path:
        return str(Path(found_on_path).resolve())

    for directory in COMMON_SEARCH_DIRS:
        candidate = directory / binary_name
        if candidate.is_file():
            return str(candidate.resolve())

    return None


def verify_executable_path(path: str) -> Optional[str]:
    """Checks a user-typed path (not one we auto-detected) actually points
    at a file, cleaning up the same 'Copy as path' quoting issue the model
    folder input handles. Returns the cleaned absolute path if valid,
    None otherwise -- the wizard should not let the user proceed past
    this step on an unverified path."""
    cleaned = path.strip().strip('"').strip("'")
    p = Path(cleaned)
    if p.is_file():
        return str(p.resolve())
    return None


def get_llama_cpp_install_guidance() -> dict:
    """Static, OS-aware guidance on where to get llama.cpp. Points at the
    releases page rather than guessing an exact asset filename, since
    asset naming has changed across llama.cpp versions and guessing wrong
    would silently break the wizard for some users."""
    system = platform.system()
    releases_url = "https://github.com/ggml-org/llama.cpp/releases/latest"

    if system == "Windows":
        note = (
            "Download the Windows zip that matches your hardware (e.g. "
            "a 'cuda' build for an NVIDIA GPU, an 'avx2' build for "
            "CPU-only), extract it anywhere, then point this app at the "
            "llama-server.exe inside it."
        )
    elif system == "Darwin":
        note = (
            "`brew install llama.cpp` is usually the fastest path on "
            "macOS; otherwise grab the matching macOS asset from the "
            "releases page."
        )
    else:
        note = (
            "Build from source (`cmake -B build && cmake --build build "
            "--config Release`) or download the matching Linux asset "
            "from the releases page."
        )

    return {"platform": system, "releases_url": releases_url, "note": note}
