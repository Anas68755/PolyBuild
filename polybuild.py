#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PolyBuild Pro v3.3.1 — a single-file CLI tool that turns any software project
into a native executable (EXE / APK / binary) for the target system.

- Runs on Windows / Linux / macOS with Python 3.9+.
- No mandatory pip dependencies (Standard Library only).
- Auto-detects the project language (confidence-score system), can install
  missing tools (--auto-install), and builds via a dedicated builder for each
  language (26+ languages/environments).
- Fully non-interactive: every decision comes from command-line flags.

[toolbox compat] This build is designed to be driven by Ultimate Toolbox Pro
(tool "🏗️ PolyBuild"), which calls:
    python polybuild.py <PROJECT> [--lang NAME] [--backend NAME]
        [--target OS] [--output DIR] [--onefile] [--console] [--clean]
        [--hidden-import MOD]... [--add-data SRC:DEST]... [extra flags...]
All of those flags are accepted here: PROJECT may be a folder or a single
source file; --lang accepts toolbox names (javascript, typescript, csharp,
php ...) as aliases of the canonical ones; --backend accepts the toolbox
backend names and is honored wherever the toolchain supports it (otherwise
a clear warning is shown and the default backend is used).

Quick usage:
    python polybuild.py                              # build the current directory
    python polybuild.py ./myapp -n myapp             # positional project (toolbox style)
    python polybuild.py -p ./myapp -n myapp          # auto-detect + build
    python polybuild.py -p . --lang python -f        # force language + onefile
    python polybuild.py -p . --entry src/app.py      # explicit entry point
    python polybuild.py -s tools/app.py              # build ONE specific file
    python polybuild.py tools/app.py                 # same, via positional single file
    python polybuild.py -p . -f --icon assets/app.ico
    python polybuild.py -p . --add-data assets:assets --add-data README.md:.
    python polybuild.py -p . --backend nuitka        # toolbox backend hint
    python polybuild.py --check-tools | --list-langs | --update

Environment variables:
    POLYBUILD_VERSION_URL       JSON with latest version + sha256 + url
    POLYBUILD_UPDATE_URL        download URL of the new script (needed by --update)
    POLYBUILD_NO_UPDATE_CHECK=1 skip the silent startup update check
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import hmac
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

# ═══════════════════════════════════════════════════════════════════════════
# 2) Version constants and settings
# ═══════════════════════════════════════════════════════════════════════════
TOOL_NAME = "PolyBuild Pro"
VERSION = "3.3.1"
VERSION_TAG = "v3.3.1"

# Explicit timeouts for every subprocess — nothing runs unbounded (safety).
TIMEOUT_SHORT = 60      # quick queries (--version ...)
TIMEOUT_MED = 180       # intermediate steps (cmake configure ...)
TIMEOUT_LONG = 900      # heavy builds (pyinstaller / cargo / gradle ...)
TIMEOUT_XLONG = 2400    # very heavy builds (flutter / full gradle / maven ...)
TIMEOUT_PKG = 600       # package installation (apt / brew / npm / pip ...)

ENV_VERSION_URL = "POLYBUILD_VERSION_URL"
ENV_UPDATE_URL = "POLYBUILD_UPDATE_URL"
ENV_NO_UPDATE_CHECK = "POLYBUILD_NO_UPDATE_CHECK"

# Folders excluded from scanning/packing (build / cache / VCS)
EXCLUDE_DIRS = {
    ".git", ".hg", ".svn", ".github", ".idea", ".vs", ".vscode",
    "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
    "venv", ".venv", "env", ".env", "site-packages",
    "target", "build", "dist", "out", "bin", "obj", "release",
    ".gradle", ".dart_tool", ".next", ".nuxt", ".sbt", ".stack-work",
    "vendor", "Pods", "DerivedData", "deps", "_build", "coverage",
    ".polybuild_tmp", ".cache", "cmake-build-debug", "cmake-build-release",
}

DEFAULT_OUTPUT_DIR = "dist"
MAX_SCAN_FILES = 5000     # performance guard while scanning huge projects
MAX_SCAN_DEPTH = 7

# Supported values for --target-os
TARGET_OS_CHOICES = ("native", "windows", "linux", "macos", "android")

# ═══════════════════════════════════════════════════════════════════════════
# 3) Terminal utilities: ANSI colors + Unicode symbols + boxes + printing
# ═══════════════════════════════════════════════════════════════════════════

def _force_utf8_stdio() -> None:
    """[critical fix] Reconfigure stdout/stderr to UTF-8 at startup to avoid
    encoding crashes on Windows (cp1252/cp437) with symbols and non-ASCII."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


class Ansi:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    UNDER = "\033[4m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    GRAY = "\033[90m"
    WHITE = "\033[97m"


def _detect_color() -> bool:
    """Detect terminal color support — honors NO_COLOR and Windows Terminal/VT."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR") or os.environ.get("CLICOLOR_FORCE") == "1":
        return True
    try:
        if not sys.stdout.isatty():
            return False
    except Exception:
        return False
    if os.name == "nt":
        if os.environ.get("WT_SESSION") or os.environ.get("ANSICON"):
            return True
        # [critical fix] Enable VT100 on conhost via SetConsoleMode
        try:
            import ctypes
            k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = k32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if k32.GetConsoleMode(handle, ctypes.byref(mode)):
                return bool(k32.SetConsoleMode(handle, mode.value | 0x0004))
            return False
        except Exception:
            return False
    return os.environ.get("TERM", "") not in ("", "dumb")


def _detect_unicode() -> bool:
    """Can the current stream encode our symbols? Otherwise fall back to ASCII."""
    probe = "✓✗⚠→❯╔═║"
    try:
        enc = (sys.stdout.encoding or "ascii").lower()
        probe.encode(enc)
        return True
    except Exception:
        return False


_force_utf8_stdio()
COLOR = _detect_color()
UNICODE = _detect_unicode()

SYM = {
    "ok": "✓" if UNICODE else "+",
    "err": "✗" if UNICODE else "x",
    "warn": "⚠" if UNICODE else "!",
    "arrow": "→" if UNICODE else "->",
    "cursor": "❯" if UNICODE else ">",
    "info": "i",
    "star": "*",
}


def paint(text: str, *codes: str) -> str:
    if not COLOR or not codes:
        return text
    return "".join(codes) + text + Ansi.RESET


def _emit(prefix: str, color: str, msg: str, stream: Any = None) -> None:
    out = stream or sys.stdout
    try:
        out.write(f"{paint(prefix, color, Ansi.BOLD)} {msg}\n")
        out.flush()
    except Exception:
        try:
            out.write(("[*] " if stream is None else "") + msg + "\n")
        except Exception:
            pass


def info(msg: str) -> None:
    """[*] info — blue."""
    _emit("[*]", Ansi.BLUE, msg)


def ok(msg: str) -> None:
    """[ok] success — green."""
    _emit(f"[{SYM['ok']}]", Ansi.GREEN, msg)


def warn(msg: str) -> None:
    """[!] warning — yellow."""
    _emit("[!]", Ansi.YELLOW, msg)


def err(msg: str) -> None:
    """[x] error — red (to stderr)."""
    _emit(f"[{SYM['err']}]", Ansi.RED, msg, stream=sys.stderr)


def hint(msg: str) -> None:
    """[i] hint — cyan."""
    _emit("[i]", Ansi.CYAN, msg)


def dim(msg: str) -> None:
    print(paint(msg, Ansi.GRAY))


def _box_char(name: str) -> str:
    table_u = {"tl": "╔", "tr": "╗", "bl": "╚", "br": "╝", "h": "═",
               "v": "║", "ml": "╠", "mr": "╣"}
    table_a = {"tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-",
               "v": "|", "ml": "+", "mr": "+"}
    return (table_u if UNICODE else table_a)[name]


def _visible_len(s: str) -> int:
    """Approximate display width — every char counts as one cell."""
    return len(s)


def _pad(s: str, width: int) -> str:
    gap = width - _visible_len(s)
    return s + (" " * gap if gap > 0 else "")


def _clip(s: str, width: int) -> str:
    if _visible_len(s) <= width:
        return s
    return s[: max(0, width - 3)] + ("…" if UNICODE else "...")


def render_box(title: str = "", rows: Optional[List[Any]] = None, width: int = 62) -> None:
    """Unicode box: rows are either free text lines or (label, value) tuples.
    [fix v3.3.1] the composed (label : value) line is clipped+padded to the
    inner width at the end — long paths can no longer push the right border
    out of alignment."""
    rows = list(rows or [])
    inner = width - 2
    h, v = _box_char("h"), _box_char("v")

    def edge(l: str, r: str) -> str:
        return paint(l + h * inner + r, Ansi.CYAN)

    print(edge(_box_char("tl"), _box_char("tr")))
    if title:
        print(paint(v, Ansi.CYAN)
              + paint(_pad(_clip(title, inner), inner), Ansi.CYAN, Ansi.BOLD)
              + paint(v, Ansi.CYAN))
        print(edge(_box_char("ml"), _box_char("mr")))
    for row in rows:
        if isinstance(row, tuple):
            label, value = row
            line = f" {_pad(_clip(str(label), inner // 3), inner // 3)} : {_clip(str(value), inner - inner // 3 - 5)} "
        else:
            line = f" {_clip(str(row), inner - 2)} "
        line = _pad(_clip(line, inner), inner)   # [fix] never overflow the box
        print(paint(v, Ansi.CYAN) + paint(line, Ansi.WHITE) + paint(v, Ansi.CYAN))
    print(edge(_box_char("bl"), _box_char("br")))


def summary_panel(file_path: Path, elapsed: float, lang_label: str,
                  tool_label: str, extra: Optional[List[Tuple[str, str]]] = None) -> None:
    """Final summary panel: file / size / time / language."""
    is_dir = file_path.is_dir()
    try:
        if is_dir:
            total = sum(f.stat().st_size for f in file_path.rglob("*") if f.is_file())
        else:
            total = file_path.stat().st_size if file_path.exists() else 0
    except OSError:
        total = 0
    rows: List[Tuple[str, str]] = [
        ("File", file_path.name),
        ("Path", str(file_path)),
        ("Size", human_size(total) + (" (folder)" if is_dir else "")),
        ("Time", f"{elapsed:.1f}s"),
        ("Language", lang_label),
        ("Tool", tool_label),
    ]
    for kv in (extra or []):
        rows.append(kv)
    render_box(f"{TOOL_NAME} {VERSION_TAG} — build completed successfully", rows, width=70)


def print_banner() -> None:
    render_box(
        f"{TOOL_NAME} {VERSION_TAG}  ({VERSION})",
        ["Any software project " + SYM["arrow"] + " one native executable",
         "25+ languages | auto-detection | icon & data bundling | zero pip deps"],
        width=62,
    )

# ═══════════════════════════════════════════════════════════════════════════
# 4) General-purpose helpers
# ═══════════════════════════════════════════════════════════════════════════

class BuildError(Exception):
    """Build failure — its message is shown to the user as-is (in red)."""


class GuidanceBuild(Exception):
    """Detect-only languages (Unity/Unreal/...) — show manual steps instead."""

    def __init__(self, title: str, steps: List[str]):
        super().__init__(title)
        self.title = title
        self.steps = steps


def which_cmd(tool: str, refresh: bool = False) -> Optional[str]:
    """shutil.which with a cache — refresh forces a re-check after installs."""
    cache: Dict[str, Optional[str]] = getattr(which_cmd, "_cache", {})
    if refresh:
        cache.pop(tool, None)
    if tool not in cache:
        cache[tool] = shutil.which(tool)
        which_cmd._cache = cache  # type: ignore[attr-defined]
    return cache[tool]


def invalidate_tool_cache(*tools: str) -> None:
    """Invalidate the cache after any install — never assume a child (npm)
    arrived together with its parent (node).
    [fix v3.3.1] safe when the cache does not exist yet (used to silently
    clear a throw-away dict)."""
    cache = getattr(which_cmd, "_cache", None)
    if cache is None:
        return
    if not tools:
        cache.clear()
        return
    for t in tools:
        which_cmd(t, refresh=True)


def exe_ext(target_os: str) -> str:
    """Executable extension for the target OS."""
    return ".exe" if target_os == "windows" else ""


def current_os() -> str:
    """Current host OS in normalized form."""
    return {"Windows": "windows", "Darwin": "macos"}.get(platform.system(), "linux")


def resolve_target_os(target_os: str) -> str:
    """native/host = same as the host OS (host accepted for toolbox compat)."""
    return current_os() if target_os in ("native", "host", "", None) else target_os


def version_tuple(v: str) -> Tuple[int, ...]:
    nums = re.findall(r"\d+", v or "")
    return tuple(int(x) for x in nums[:4]) if nums else (0,)


def is_newer_version(candidate: str, current: str) -> bool:
    """Flexible version comparison (3.1.0 vs 3.1 vs v3.1.1 ...)."""
    a, b = version_tuple(candidate), version_tuple(current)
    n = max(len(a), len(b))
    a += (0,) * (n - len(a))
    b += (0,) * (n - len(b))
    return a > b


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0
    return f"{n:.1f} TB"


def sanitize_name(name: str) -> str:
    """Safe output name: spaces -> _ and strip dangerous characters."""
    name = re.sub(r"[^\w.\-]+", "_", (name or "").strip())
    return name.strip("._") or "app"


def read_small_text(path: Path, limit: int = 512 * 1024) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except Exception:
        return ""


def tail_lines(text: str, n: int = 6) -> str:
    """Last n non-empty lines — to show external tool errors concisely."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    keep = lines[-n:]
    return "\n".join("    " + ln.strip()[:180] for ln in keep)


def run_cmd(cmd: Sequence[str], cwd: Optional[Path] = None,
            timeout: int = TIMEOUT_MED, env: Optional[Dict[str, str]] = None,
            capture: bool = True) -> subprocess.CompletedProcess:
    """[critical fix] Single execution point for every external process:
    always an explicit timeout + text=True + errors='replace' (never a
    UnicodeDecodeError from external tools) + never shell=True."""
    merged = None
    if env:
        merged = dict(os.environ)
        merged.update(env)
    try:
        return subprocess.run(
            list(cmd),
            cwd=str(cwd) if cwd else None,
            timeout=timeout,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            text=True,
            errors="replace",
            env=merged,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        raise BuildError(f"Timed out ({timeout}s) while running: {cmd[0]}")
    except FileNotFoundError:
        raise BuildError(f"Tool not found: {cmd[0]} — install it first or add it to PATH.")
    except PermissionError:
        raise BuildError(f"Permission denied while executing: {cmd[0]}")


def collect_files(root: Path, extra_excludes: Optional[set] = None,
                  max_files: int = MAX_SCAN_FILES,
                  max_depth: int = MAX_SCAN_DEPTH) -> List[Path]:
    """Walk the project excluding build/cache folders — with performance caps."""
    found: List[Path] = []
    excludes = set(EXCLUDE_DIRS) | set(extra_excludes or ())
    base_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        cur_depth = len(Path(dirpath).parts) - base_depth
        if cur_depth >= max_depth:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if d not in excludes and not d.startswith(".git")]
        for fn in filenames:
            found.append(Path(dirpath) / fn)
            if len(found) >= max_files:
                return found
    return found


def rel_files(files: Iterable[Path], root: Path) -> List[Path]:
    out = []
    for f in files:
        try:
            out.append(f.relative_to(root))
        except ValueError:
            continue
    return out


def parse_json_file(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(read_small_text(path) or "{}")
    except Exception:
        return {}


def regex_first(text: str, pattern: str, group: int = 1) -> Optional[str]:
    m = re.search(pattern, text, re.MULTILINE)
    return m.group(group).strip() if m else None


def find_files_by_ext(root: Path, ext: str, limit: int = 3000,
                      excludes: Optional[set] = None) -> List[Path]:
    """Like collect_files but does NOT skip build output folders — used to
    locate produced artifacts (build/, target/, app/build/...)."""
    skip = excludes if excludes is not None else {
        ".git", ".gradle", ".dart_tool", ".idea", ".vscode",
        "node_modules", ".polybuild_tmp",
    }
    out: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for fn in filenames:
            if fn.lower().endswith(ext):
                out.append(Path(dirpath) / fn)
                if len(out) >= limit:
                    return out
    return out


def find_native_binaries(root: Path, target_os: str, max_items: int = 3000) -> List[Path]:
    """Locate built executables under *root*, skipping VCS/cache junk.
    [fix] Does not exclude target/build — the old scan could never find
    build outputs because they live inside excluded folders."""
    skip_dirs = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".polybuild_tmp"}
    bad_ext = {".o", ".a", ".so", ".dylib", ".lib", ".exp", ".pdb", ".d", ".txt",
               ".cmake", ".json", ".h", ".hpp", ".c", ".cc", ".cpp", ".cxx",
               ".ninja", ".log", ".md", ".py", ".jar", ".xml", ".yml", ".yaml",
               ".toml", ".lock", ".mod", ".sum", ".rs", ".go", ".nim", ".cr",
               ".kt", ".java", ".cs", ".dll", ".png", ".jpg", ".jpeg", ".ico",
               ".zip", ".tar", ".gz", ".plist", ".icns", ".def", ".stamp"}
    out: List[Path] = []
    if not root.exists():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fn in filenames:
            if len(out) >= max_items:
                return out
            p = Path(dirpath) / fn
            try:
                suf = p.suffix.lower()
                if suf in bad_ext:
                    continue
                if target_os == "windows":
                    if suf != ".exe":
                        continue
                else:
                    if suf == ".exe":
                        continue
                    with open(p, "rb") as fh:   # skip shell scripts
                        if fh.read(2) == b"#!":
                            continue
                if p.stat().st_size < 1024:
                    continue
                out.append(p)
            except OSError:
                continue
    return out


# ---------- [new in v3.3] --icon / --add-data helpers ----------

def split_data_spec(raw: str) -> Tuple[str, Optional[str]]:
    """Split 'SRC:DEST' / 'SRC;DEST' / 'SRC' into (src, dest).
    [fix v3.3.1] Windows drive letters (C:\\...) are preserved: the drive
    prefix is split off first and re-attached to the source, so 'C:\\data'
    no longer collapses into '\\data' (the old bug lost the drive letter
    and made every absolute Windows --add-data source fail with
    'source not found')."""
    s = raw.strip()
    m = re.match(r"^([A-Za-z]:)(.*)$", s)
    drive = m.group(1) if m else ""
    rest = m.group(2) if m else s
    for sep in (";", ":"):
        i = rest.find(sep)
        if i >= 0:
            src = (drive + rest[:i]).strip()
            return (src or drive), (rest[i + 1:].strip() or None)
    return (drive + rest).strip(), None


def resolve_data_specs(specs: Sequence[str], project: Path) -> List[Tuple[Path, str]]:
    """Resolve repeated --add-data specs into (source_path, dest_inside_bundle).
    Each source is looked up under the project root first, then the cwd.
    Destination is normalized to a POSIX relative path; '.' means the source
    keeps its own name at the bundle root."""
    out: List[Tuple[Path, str]] = []
    for raw in specs:
        src_s, dest = split_data_spec(raw)
        if not src_s:
            raise BuildError(f"--add-data: empty source in {raw!r}")
        cand = Path(src_s).expanduser()
        found: Optional[Path]
        if cand.is_absolute():
            found = cand if cand.exists() else None
        else:
            found = None
            for base in (project, Path.cwd()):
                if (base / cand).exists():
                    found = base / cand
                    break
        if found is None:
            raise BuildError(f"--add-data: source not found: {raw!r} "
                             f"(looked under {project} and the current directory)")
        if found.resolve() == project.resolve():
            raise BuildError("--add-data: refusing to bundle the whole project "
                             "root into itself.")
        dest = (dest or found.name).replace("\\", "/").strip("/")
        while dest.startswith("./"):
            dest = dest[2:]
        if dest == ".":
            dest = found.name
        if not dest:
            raise BuildError(f"--add-data: empty destination in {raw!r}")
        out.append((found.resolve(), dest))
    return out

# ═══════════════════════════════════════════════════════════════════════════
# 5) Types: LangType + DetectedProject
# ═══════════════════════════════════════════════════════════════════════════

class LangType(Enum):
    PYTHON = "python"
    NODE = "node"
    RUBY = "ruby"
    PERL = "perl"
    LUA = "lua"
    LOVE = "love"
    C = "c"
    CPP = "cpp"
    RUST = "rust"
    GO = "go"
    NIM = "nim"
    ZIG = "zig"
    CRYSTAL = "crystal"
    DOTNET = "dotnet"       # C# / F# / VB
    JAVA = "java"
    KOTLIN = "kotlin"
    SCALA = "scala"
    FLUTTER = "flutter"
    DART = "dart"
    ELECTRON = "electron"
    ANDROID = "android"
    GODOT = "godot"
    PHP = "php"
    UNITY = "unity"
    UNREAL = "unreal"
    GAMEMAKER = "gamemaker"
    RENPY = "renpy"
    UNKNOWN = "unknown"


LANG_LABELS: Dict[LangType, str] = {
    LangType.PYTHON: "Python",
    LangType.NODE: "Node.js",
    LangType.RUBY: "Ruby",
    LangType.PERL: "Perl",
    LangType.LUA: "Lua",
    LangType.LOVE: "Lua / LÖVE",
    LangType.C: "C",
    LangType.CPP: "C / C++",
    LangType.RUST: "Rust",
    LangType.GO: "Go",
    LangType.NIM: "Nim",
    LangType.ZIG: "Zig",
    LangType.CRYSTAL: "Crystal",
    LangType.DOTNET: ".NET (C#/F#/VB)",
    LangType.JAVA: "Java",
    LangType.KOTLIN: "Kotlin",
    LangType.SCALA: "Scala",
    LangType.FLUTTER: "Flutter",
    LangType.DART: "Dart",
    LangType.ELECTRON: "Electron",
    LangType.ANDROID: "Android (Gradle)",
    LangType.GODOT: "Godot",
    LangType.PHP: "PHP",
    LangType.UNITY: "Unity",
    LangType.UNREAL: "Unreal Engine",
    LangType.GAMEMAKER: "GameMaker",
    LangType.RENPY: "Ren'Py",
    LangType.UNKNOWN: "Unknown",
}


@dataclass
class DetectedProject:
    """Result of automatic project detection."""
    lang: LangType = LangType.UNKNOWN
    confidence: float = 0.0          # 0..1
    score: int = 0                   # raw score
    evidence: List[str] = field(default_factory=list)
    entry_point: Optional[str] = None
    project_name: Optional[str] = None

    @property
    def label(self) -> str:
        return LANG_LABELS.get(self.lang, self.lang.value)

# ═══════════════════════════════════════════════════════════════════════════
# 6) Self-update system — triple verification + rollback (no prompts)
# ═══════════════════════════════════════════════════════════════════════════

class SelfUpdater:
    """Downloads a newer version of this script and verifies it three ways
    before replacing anything:
      1) SHA-256 must match what the server declares (no content without a hash).
      2) ast.parse() for syntactic sanity.
      3) py_compile.compile() for actual compilability.
    Any failure -> automatic rollback from the .backup copy.

    [fix] Fully non-interactive: --update is an explicit command, so it never
    asks for confirmation. Also fixed the NameError in the finally block.
    """

    BACKUP_SUFFIX = ".backup"

    def __init__(self):
        self.target = Path(os.path.abspath(__file__))

    # ---------- network ----------
    def _fetch(self, url: str, timeout: int = TIMEOUT_MED) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": f"{TOOL_NAME}/{VERSION}"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()

    def latest_info(self) -> Optional[Dict[str, str]]:
        """Expects JSON: {"version": "...", "sha256": "...", "url": "..."} —
        or a plain text body carrying the version number only (no hash)."""
        url = os.environ.get(ENV_VERSION_URL, "").strip()
        if not url:
            return None
        try:
            raw = self._fetch(url, timeout=10)
        except Exception as e:
            warn(f"Update check failed: {e.__class__.__name__}")
            return None
        text = raw.decode("utf-8", errors="replace").strip()
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return {k: str(data.get(k, "") or "") for k in ("version", "sha256", "url")}
        except Exception:
            pass
        if text:
            return {"version": text.splitlines()[0].strip(), "sha256": "", "url": ""}
        return None

    # ---------- verification ----------
    def _verify(self, content: bytes, declared_sha: str) -> None:
        if not declared_sha:
            # [security fix] never trust content without an explicit hash
            raise BuildError("The server does not declare a SHA-256 — refusing the update for security reasons.")
        actual = sha256_bytes(content)
        if not hmac.compare_digest(actual, declared_sha.strip().lower()):
            raise BuildError(f"SHA-256 mismatch!\n  declared: {declared_sha}\n  actual  : {actual}")
        try:
            ast.parse(content.decode("utf-8", errors="strict"), filename="update.py")
        except SyntaxError as e:
            raise BuildError(f"The downloaded content is not valid Python (ast): {e}")
        tmp_src = Path(tempfile.gettempdir()) / f"polybuild_check_{os.getpid()}.py"
        try:
            tmp_src.write_bytes(content)
            import py_compile
            py_compile.compile(str(tmp_src), cfile=str(tmp_src) + "c", doraise=True)
        except Exception as e:
            raise BuildError(f"Trial compilation of the update failed (py_compile): {e}")
        finally:
            try:
                tmp_src.unlink(missing_ok=True)
                Path(str(tmp_src) + "c").unlink(missing_ok=True)
            except Exception:
                pass

    # ---------- replacement + rollback ----------
    def _replace(self, content: bytes) -> None:
        backup = self.target.with_name(self.target.name + self.BACKUP_SUFFIX)
        # [fix] define tmp up-front: the old code could raise NameError in finally
        tmp = self.target.with_name(self.target.name + ".new")
        try:
            shutil.copy2(self.target, backup)
            tmp.write_bytes(content)
            os.chmod(tmp, stat.S_IMODE(os.stat(self.target).st_mode) | stat.S_IXUSR)
            os.replace(tmp, self.target)
            # final check: the new version actually compiles in place
            import py_compile
            py_compile.compile(str(self.target), cfile=str(tmp) + "c", doraise=True)
        except Exception as e:
            try:
                if backup.exists():
                    shutil.copy2(backup, self.target)   # automatic rollback
            except Exception:
                pass
            raise BuildError(f"Self-update failed — the original was restored from .backup: {e}")
        finally:
            try:
                Path(str(tmp) + "c").unlink(missing_ok=True)
            except Exception:
                pass

    # ---------- public API ----------
    def check_at_startup(self) -> None:
        """Silent, quick check at startup — never blocks normal operation."""
        if os.environ.get(ENV_NO_UPDATE_CHECK) == "1":
            return
        info_ = self.latest_info()
        if info_ and info_.get("version") and is_newer_version(info_["version"], VERSION):
            hint(f"A newer version is available ({info_['version']}). Run: python {self.target.name} --update")

    def run(self) -> None:
        if not (os.environ.get(ENV_VERSION_URL) or os.environ.get(ENV_UPDATE_URL)):
            raise BuildError(
                "Set " + ENV_VERSION_URL + " and " + ENV_UPDATE_URL +
                " before updating.\n  Example: POLYBUILD_VERSION_URL=https://host/version.json")
        info("Checking for the latest version ...")
        info_ = self.latest_info() or {}
        latest = info_.get("version", "")
        if not latest:
            raise BuildError("Could not determine the latest version from " + ENV_VERSION_URL)
        if not is_newer_version(latest, VERSION):
            ok(f"You are already using the latest version ({VERSION}).")
            return
        url = info_.get("url") or os.environ.get(ENV_UPDATE_URL, "")
        if not url:
            raise BuildError("No download URL found (" + ENV_UPDATE_URL + " or 'url' from the version JSON).")
        info(f"Downloading version {latest} ...")
        content = self._fetch(url, timeout=TIMEOUT_LONG)
        self._verify(content, info_.get("sha256", ""))
        ok("Triple verification passed (SHA-256 + ast + py_compile).")
        # [fix] no confirmation prompt: --update is explicit consent
        self._replace(content)
        ok(f"Updated to {latest}. Restart to apply the changes.")

# ═══════════════════════════════════════════════════════════════════════════
# 7) BaseToolInstaller — detect the package manager and install tools
# ═══════════════════════════════════════════════════════════════════════════

class BaseToolInstaller:
    """Detects the available package manager (apt/brew/winget/choco plus
    pip/gem/cpan) and installs tools. Uses sudo only when truly needed."""

    def __init__(self) -> None:
        self.manager = self._detect_manager()

    def _detect_manager(self) -> Optional[str]:
        system = platform.system()
        if system == "Linux" and which_cmd("apt-get"):
            return "apt"
        if system == "Darwin" and which_cmd("brew"):
            return "brew"
        if system == "Windows":
            if which_cmd("winget"):
                return "winget"
            if which_cmd("choco"):
                return "choco"
        # extra language managers that work on every OS
        if which_cmd(sys.executable) and self._pip_ok():
            return "pip"
        return None

    def _pip_ok(self) -> bool:
        try:
            proc = subprocess.run([sys.executable, "-m", "pip", "--version"],
                                  capture_output=True, text=True, errors="replace",
                                  timeout=TIMEOUT_SHORT)
            return proc.returncode == 0
        except Exception:
            return False

    def available(self) -> bool:
        return self.manager is not None

    def _needs_sudo(self) -> bool:
        # [fix] sudo only when needed: apt outside root only
        if self.manager != "apt":
            return False
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            return False
        return which_cmd("sudo") is not None

    def install(self, packages: Dict[str, Any]) -> bool:
        """packages: {manager: pkg or [pkgs]} — tries the system manager then
        the language managers (pip/gem/cpan) as a fallback."""
        order: List[str] = [self.manager] if self.manager else []
        for mgr in ("pip", "gem", "cpan"):
            if mgr not in order and self._mgr_ready(mgr):
                order.append(mgr)
        for mgr in order:
            pkgs = packages.get(mgr)
            if not pkgs:
                continue
            if isinstance(pkgs, str):
                pkgs = [pkgs]
            if self._install_one(mgr, pkgs):
                return True
        return False

    def _mgr_ready(self, mgr: str) -> bool:
        if mgr == "pip":
            return self._pip_ok()
        if mgr == "gem":
            return which_cmd("gem") is not None
        if mgr == "cpan":
            return which_cmd("cpan") is not None
        return False

    def _install_one(self, mgr: str, pkgs: List[str]) -> bool:
        if mgr == "apt":
            base = (["sudo"] if self._needs_sudo() else []) + ["apt-get", "install", "-y"] + pkgs
        elif mgr == "brew":
            # [fix v3.3.1] brew specs may embed a subcommand, e.g. "--cask love":
            # passing that as ONE argv element made brew fail with an unknown
            # option; split every spec on whitespace instead.
            base = ["brew", "install"]
            for spec in pkgs:
                base += spec.split()
        elif mgr == "winget":
            base = ["winget", "install", "--accept-source-agreements",
                    "--accept-package-agreements", "--silent", "--exact"] + pkgs
        elif mgr == "choco":
            base = ["choco", "install", "-y"] + pkgs
        elif mgr == "pip":
            base = [sys.executable, "-m", "pip", "install", "--quiet"] + pkgs
        elif mgr == "gem":
            base = ["gem", "install"] + pkgs
        elif mgr == "cpan":
            base = ["cpan", "-T"] + pkgs
        else:
            return False
        info(f"Installing {' '.join(pkgs)} via {mgr} ...")
        proc = run_cmd(base, timeout=TIMEOUT_PKG)
        if proc.returncode != 0:
            warn("Automatic installation failed — use the manual command below.")
        return proc.returncode == 0

    def hint(self, packages: Dict[str, Any]) -> str:
        """A clear manual install command — only for the sensible managers."""
        system = platform.system()
        order: List[Optional[str]] = [self.manager]
        if system == "Linux":
            order.append("apt")
        elif system == "Darwin":
            order.append("brew")
        else:
            order += ["winget", "choco"]
        order += ["pip", "gem", "cpan"]
        seen: set = set()
        for mgr in order:
            if not mgr or mgr in seen:
                continue
            seen.add(mgr)
            pkgs = packages.get(mgr)
            if not pkgs:
                continue
            if isinstance(pkgs, str):
                pkgs = [pkgs]
            if mgr == "apt":
                return "sudo apt-get install -y " + " ".join(pkgs)
            if mgr == "brew":
                return "brew install " + " ".join(pkgs)
            if mgr == "winget":
                return "winget install --exact " + " ".join(pkgs)
            if mgr == "choco":
                return "choco install -y " + " ".join(pkgs)
            if mgr == "pip":
                return f'"{sys.executable}" -m pip install ' + " ".join(pkgs)
            if mgr == "gem":
                return "gem install " + " ".join(pkgs)
            if mgr == "cpan":
                return "cpan -T " + " ".join(pkgs)
        return "Install the tool manually from its official website."


# Package catalog per tool: {tool: {manager: pkg([s])}}
TOOL_PACKAGES: Dict[str, Dict[str, Any]] = {
    "pyinstaller": {"pip": "pyinstaller"},
    "nuitka": {"pip": "nuitka", "brew": "nuitka"},
    "node": {"apt": ["nodejs", "npm"], "brew": "node",
             "winget": "OpenJS.NodeJS.LTS", "choco": "nodejs-lts"},
    "npm": {},                                   # bundled with node
    "npx": {},                                   # bundled with node
    "ruby": {"apt": "ruby", "brew": "ruby", "choco": "ruby"},
    "gem": {},                                   # bundled with ruby
    "ocra": {"gem": "ocra"},
    "perl": {"apt": "perl", "brew": "perl"},
    "cpan": {},
    "pp": {"cpan": "PAR::Packer"},
    "love": {"apt": "love", "brew": "--cask love", "choco": "love"},
    "gcc": {"apt": "build-essential", "brew": "gcc", "choco": "mingw",
            "winget": "BrechtSanders.WinLibs.POSIX.UCRT"},
    "g++": {"apt": "build-essential", "brew": "gcc"},
    "cmake": {"apt": "cmake", "brew": "cmake", "winget": "Kitware.CMake", "choco": "cmake"},
    "make": {"apt": "make", "brew": "make", "choco": "make"},
    "x86_64-w64-mingw32-gcc": {"apt": "mingw-w64", "brew": "mingw-w64", "choco": "mingw"},
    "x86_64-w64-mingw32-g++": {"apt": "mingw-w64", "brew": "mingw-w64"},
    "cargo": {"apt": "cargo", "brew": "rust", "winget": "Rustlang.Rustup", "choco": "rustup"},
    "rustup": {"brew": "rustup", "winget": "Rustlang.Rustup", "choco": "rustup"},
    "go": {"apt": "golang-go", "brew": "go", "winget": "GoLang.Go", "choco": "golang"},
    "nim": {"apt": "nim", "brew": "nim", "choco": "nim"},
    "zig": {"brew": "zig", "choco": "zig", "winget": "zig.zig"},
    "crystal": {"apt": "crystal", "brew": "crystal", "choco": "crystal"},
    "dotnet": {"apt": "dotnet-sdk-8.0", "brew": "--cask dotnet-sdk",
               "winget": "Microsoft.DotNet.SDK.8", "choco": "dotnet-sdk"},
    "java": {"apt": "default-jdk", "brew": "--cask temurin",
             "winget": "EclipseAdoptium.Temurin.17.JDK", "choco": "temurin17"},
    "javac": {"apt": "default-jdk", "brew": "--cask temurin",
              "winget": "EclipseAdoptium.Temurin.17.JDK", "choco": "temurin17"},
    "jar": {"apt": "default-jdk", "brew": "--cask temurin",
            "winget": "EclipseAdoptium.Temurin.17.JDK", "choco": "temurin17"},
    "jpackage": {"apt": "openjdk-21-jdk", "brew": "--cask temurin",
                 "winget": "EclipseAdoptium.Temurin.21.JDK", "choco": "temurin21"},
    "mvn": {"apt": "maven", "brew": "maven", "choco": "maven"},
    "gradle": {"brew": "gradle", "choco": "gradle"},
    "kotlinc": {"brew": "kotlin", "choco": "kotlin", "apt": "kotlin"},
    "scala-cli": {"brew": "coursier/formulas/coursier", "choco": "coursier"},
    "sbt": {"brew": "sbt", "choco": "sbt"},
    "flutter": {"brew": "--cask flutter", "choco": "flutter"},
    "dart": {"brew": "dart", "choco": "dart-sdk"},
    "godot": {"brew": "--cask godot", "choco": "godot"},
}


# ═══════════════════════════════════════════════════════════════════════════
# 8) DependencyManager — check/install tools with cache (no prompts)
# ═══════════════════════════════════════════════════════════════════════════

# tools installed together with a parent tool (parent -> child)
BUNDLED_WITH: Dict[str, str] = {"npm": "node", "npx": "node", "gem": "ruby",
                                "cpan": "perl", "jar": "javac", "jpackage": "javac",
                                "pp": "perl"}

# tools invoked via npx instead of a global install
NPX_TOOLS = {"pkg", "electron-builder"}


class DependencyManager:
    """Checks tools and, only when --auto-install was passed, installs them.
    [fix] Completely non-interactive: the old TTY prompt is gone — a missing
    tool either gets installed (flag) or reported with a manual command."""

    def __init__(self, installer: BaseToolInstaller, auto_install: bool = False,
                 verbose: bool = False):
        self.installer = installer
        self.auto_install = auto_install
        self.verbose = verbose
        self._cache: Dict[str, bool] = {}

    def ensure(self, tool: str, purpose: str = "") -> bool:
        """Ensure *tool* is available: check -> install if asked -> re-check
        for real (never assume success) -> manual hint on failure."""
        if tool in self._cache:
            return self._cache[tool]
        if tool in NPX_TOOLS:
            # runs via npx — node/npm being present is enough
            # [fix v3.3.1] cache the verdict: the old code re-ran two
            # subprocess checks on every call (pkg/electron builds slow).
            found = self.ensure("node") and self.ensure("npx")
            self._cache[tool] = found
            return found
        parent = BUNDLED_WITH.get(tool)
        if parent and not which_cmd(tool):
            # the tool is bundled with an absent parent -> install the parent
            self.ensure(parent)
            # [critical fix] re-check the child after the parent — no assumptions
            found = which_cmd(tool, refresh=True) is not None
            self._cache[tool] = found
            return found
        if which_cmd(tool):
            self._cache[tool] = True
            return True
        packages = TOOL_PACKAGES.get(tool, {})
        if packages and self.auto_install and self.installer.available():
            self.installer.install(packages)
            invalidate_tool_cache()           # any install invalidates the cache
        found = which_cmd(tool, refresh=True) is not None
        self._cache[tool] = found
        if not found:
            hint(f"Tool '{tool}' is required"
                 f"{(' for ' + purpose) if purpose else ''} but is not installed.\n"
                 f"    Manual install: {self.installer.hint(packages)}\n"
                 f"    Or re-run with --auto-install to install it automatically.")
        return found

    def ensure_python_module(self, module: str, pip_pkg: str, purpose: str = "") -> bool:
        """[fix] Ensure a Python module (importable) — much more reliable than
        checking a binary on PATH (the Scripts folder is often missing there)."""
        key = f"py:{module}"
        if key in self._cache:
            return self._cache[key]
        if self._module_importable(module):
            self._cache[key] = True
            return True
        if self.auto_install and self.installer.available():
            run_cmd([sys.executable, "-m", "pip", "install", "--quiet", pip_pkg],
                    timeout=TIMEOUT_PKG)
            invalidate_tool_cache()
        found = self._module_importable(module)
        self._cache[key] = found
        if not found:
            hint(f"Python module '{module}' is required"
                 f"{(' for ' + purpose) if purpose else ''}.\n"
                 f'    Manual install: "{sys.executable}" -m pip install {pip_pkg}')
        return found

    def _module_importable(self, module: str) -> bool:
        try:
            proc = subprocess.run([sys.executable, "-c", f"import {module}"],
                                  capture_output=True, timeout=TIMEOUT_SHORT)
            return proc.returncode == 0
        except Exception:
            return False

    def require(self, tools: Sequence[str], purpose: str = "") -> None:
        """Like ensure() but collects everything missing and fails once."""
        missing: List[str] = []
        for t in tools:
            if not self.ensure(t, purpose=purpose):
                missing.append(t)
        if missing:
            lines = [f"  - {t} -> {self.installer.hint(TOOL_PACKAGES.get(t, {}))}"
                     for t in missing]
            raise BuildError("Required tools are missing:\n" + "\n".join(lines))

    def reset_cache(self) -> None:
        self._cache.clear()

    def check_all(self) -> int:
        """--check-tools: status table of every known tool."""
        catalog = [
            ("python", "Python 3.9+ interpreter (required)"),
            ("pyinstaller", "Python builds — PyInstaller"),
            ("nuitka", "Python builds — Nuitka (optional)"),
            ("node", "Node.js runtime"), ("npm", "Node package manager"),
            ("npx", "Node package runner"),
            ("ruby", "Ruby interpreter"), ("ocra", "Ruby packaging (Windows)"),
            ("perl", "Perl interpreter"), ("pp", "PAR::Packer for Perl"),
            ("love", "LÖVE engine (Lua)"),
            ("gcc", "C compiler"), ("g++", "C++ compiler"),
            ("cmake", "CMake build system"), ("make", "GNU Make"),
            ("x86_64-w64-mingw32-gcc", "MinGW cross-compiler (-> Windows)"),
            ("cargo", "Rust — Cargo"), ("rustup", "Rust toolchain manager"),
            ("go", "Go compiler"), ("nim", "Nim compiler"), ("zig", "Zig compiler"),
            ("crystal", "Crystal compiler"), ("dotnet", ".NET SDK"),
            ("java", "Java runtime"), ("javac", "Java compiler"),
            ("jar", "JAR tool"), ("jpackage", "Native Java packaging"),
            ("mvn", "Maven"), ("gradle", "Gradle"),
            ("kotlinc", "Kotlin compiler"), ("scala-cli", "Scala CLI"),
            ("flutter", "Flutter SDK"), ("dart", "Dart SDK"),
            ("godot", "Godot engine"),
        ]
        rows = []
        missing = 0
        for tool, label in catalog:
            special = {"python": sys.executable}
            found = which_cmd(special.get(tool, tool), refresh=True) is not None
            if tool == "python" and sys.version_info >= (3, 9):
                found = True
            if not found:
                missing += 1
            rows.append(((SYM["ok"] + " " if found else SYM["err"] + " ") + tool, label))
        render_box(f"Tool status ({len(catalog) - missing}/{len(catalog)} available)",
                   rows, width=72)
        if missing:
            hint("Install only what your projects need — not every tool is required for every language.")
        return 0

# ═══════════════════════════════════════════════════════════════════════════
# 9) ProjectDetector — language detection via a confidence-score system
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Rule:
    lang: LangType
    weight: int                    # high for build files, medium for sources
    names: Tuple[str, ...]         # exact file names or extensions ".py"
    kind: str = "file"             # file | ext | dir
    label: str = ""                # evidence description


_BUILD_RULES: List[Rule] = [
    Rule(LangType.PYTHON, 40, ("setup.py", "setup.cfg"), label="Python packaging file"),
    Rule(LangType.PYTHON, 35, ("pyproject.toml", "requirements.txt", "Pipfile"), label="Python dependency file"),
    Rule(LangType.PYTHON, 30, ("main.py", "app.py", "run.py", "cli.py", "__main__.py"), label="Python entry point"),
    Rule(LangType.NODE, 45, ("package.json",), label="package.json"),
    Rule(LangType.ELECTRON, 65, ("package.json",), label="package.json with an electron dependency"),
    Rule(LangType.RUST, 60, ("Cargo.toml",), label="Cargo.toml"),
    Rule(LangType.GO, 60, ("go.mod",), label="go.mod"),
    Rule(LangType.CPP, 50, ("CMakeLists.txt",), label="CMakeLists.txt"),
    Rule(LangType.CPP, 30, ("Makefile", "makefile", "GNUmakefile"), label="Makefile"),
    Rule(LangType.NIM, 60, ("main.nim", "app.nim"), label="Nim entry point"),
    Rule(LangType.ZIG, 60, ("build.zig",), label="build.zig"),
    Rule(LangType.CRYSTAL, 60, ("shard.yml",), label="shard.yml"),
    Rule(LangType.DOTNET, 60, (".csproj", ".vbproj", ".fsproj"), kind="ext", label=".NET project"),
    Rule(LangType.DOTNET, 55, (".sln",), kind="ext", label=".NET solution"),
    Rule(LangType.JAVA, 55, ("pom.xml",), label="Maven pom.xml"),
    Rule(LangType.JAVA, 40, ("build.gradle", "build.gradle.kts"), label="Gradle build script"),
    Rule(LangType.JAVA, 25, ("gradlew", "gradlew.bat"), label="Gradle wrapper"),
    Rule(LangType.ANDROID, 65, ("AndroidManifest.xml",), label="AndroidManifest.xml"),
    Rule(LangType.KOTLIN, 45, ("build.gradle.kts",), label="Gradle Kotlin DSL"),
    Rule(LangType.SCALA, 60, ("build.sbt",), label="build.sbt"),
    Rule(LangType.FLUTTER, 60, ("pubspec.yaml",), label="pubspec.yaml"),   # content-checked later
    Rule(LangType.DART, 40, ("pubspec.yaml",), label="pubspec.yaml"),
    Rule(LangType.RUBY, 40, ("Gemfile",), label="Gemfile"),
    Rule(LangType.PERL, 35, ("Makefile.PL", "Build.PL"), label="Perl build script"),
    Rule(LangType.LUA, 35, ("main.lua",), label="main.lua"),
    Rule(LangType.LOVE, 55, ("conf.lua",), label="LÖVE project (conf.lua)"),
    Rule(LangType.GODOT, 70, ("project.godot",), label="project.godot"),
    Rule(LangType.UNREAL, 75, (".uproject",), kind="ext", label="Unreal project file"),
    Rule(LangType.GAMEMAKER, 70, (".yyp", ".project.gmx"), kind="ext", label="GameMaker project"),
    Rule(LangType.RENPY, 65, ("options.rpy", "script.rpy"), label="Ren'Py script"),
]

# extension rules — medium scores (source files)
_SRC_RULES: List[Tuple[LangType, int, int, Tuple[str, ...]]] = [
    # (language, first-file points, per-extra-file points (capped), extensions)
    (LangType.PYTHON, 20, 3, (".py",)),
    (LangType.NODE, 12, 3, (".js", ".mjs", ".cjs", ".ts")),
    (LangType.RUST, 25, 5, (".rs",)),
    (LangType.GO, 25, 5, (".go",)),
    (LangType.CPP, 20, 4, (".cpp", ".cc", ".cxx", ".hpp", ".hh")),
    (LangType.C, 20, 4, (".c", ".h")),
    (LangType.NIM, 25, 5, (".nim",)),
    (LangType.ZIG, 25, 5, (".zig",)),
    (LangType.CRYSTAL, 25, 5, (".cr",)),
    (LangType.DOTNET, 20, 4, (".cs", ".vb", ".fs")),
    (LangType.JAVA, 20, 3, (".java",)),
    (LangType.KOTLIN, 22, 4, (".kt", ".kts")),
    (LangType.SCALA, 22, 4, (".scala", ".sc")),
    (LangType.DART, 18, 3, (".dart",)),
    (LangType.RUBY, 12, 2, (".rb",)),
    (LangType.PERL, 12, 2, (".pl", ".pm")),
    (LangType.LUA, 12, 2, (".lua",)),
]


class ProjectDetector:
    """Score system: build files score high, sources medium, marker dirs high.
    Ties go to the highest score; a total failure -> UNKNOWN with a --lang hint.

    [fix] Entry-point detection was rewritten:
      * candidates ranked by depth (shallowest wins), not scan order;
      * marker-based fallback ('if __name__ == "__main__"', 'int main(',
        'fun main(', 'package main', ...) finds entries name lists miss;
      * Rust / .NET / Flutter / Godot / Kotlin / Scala entries are handled;
      * 'main' from package.json is normalized and verified on disk.
    """

    THRESHOLD = 12

    def __init__(self, project: Path):
        self.project = project
        self.files: List[Path] = []
        self.top_dirs: set = set()

    def _scan(self) -> None:
        self.files = collect_files(self.project)
        try:
            self.top_dirs = {p.name for p in self.project.iterdir() if p.is_dir()}
        except OSError:
            self.top_dirs = set()

    # ---------- content-specific rules ----------
    def _package_json_flags(self) -> Dict[str, Any]:
        pj = next((f for f in self.files if f.name == "package.json"), None)
        if not pj:
            return {}
        data = parse_json_file(pj)
        deps: Dict[str, str] = {}
        deps.update(data.get("dependencies", {}) or {})
        deps.update(data.get("devDependencies", {}) or {})
        bin_val = data.get("bin")
        if isinstance(bin_val, dict) and bin_val:
            bin_val = next(iter(bin_val.values()))
        main = data.get("main") or (bin_val if isinstance(bin_val, str) else "")
        return {
            "electron": "electron" in deps or "electron-prebuilt" in deps,
            "main": main if main and isinstance(main, str) else "",
            "name": data.get("name") or "",
        }

    def _pubspec_is_flutter(self) -> bool:
        ps = next((f for f in self.files if f.name == "pubspec.yaml"), None)
        return bool(ps) and "flutter:" in read_small_text(ps)

    def _unity_like(self) -> bool:
        return {"Assets", "ProjectSettings"}.issubset(self.top_dirs)

    def _renpy_like(self) -> bool:
        game = self.project / "game"
        return game.is_dir() and any(game.glob("*.rpy"))

    # ---------- main detection ----------
    def detect(self) -> DetectedProject:
        det = DetectedProject()
        if not self.project.is_dir():
            return det
        self._scan()
        scores: Dict[LangType, int] = {}
        evidence: Dict[LangType, List[str]] = {}

        def add(lang: LangType, pts: int, why: str) -> None:
            scores[lang] = scores.get(lang, 0) + pts
            evidence.setdefault(lang, []).append(f"{why} (+{pts})")

        rel = rel_files(self.files, self.project)
        flags = self._package_json_flags()

        # 1) build files — high scores (each rule counted once)
        for rule in _BUILD_RULES:
            if rule.lang is LangType.FLUTTER and not self._pubspec_is_flutter():
                continue
            if rule.lang is LangType.DART and self._pubspec_is_flutter():
                continue
            if rule.lang is LangType.ELECTRON and not flags.get("electron"):
                continue
            hits = 0
            for f in rel:
                for name in rule.names:
                    if (rule.kind == "file" and f.name.lower() == name.lower()) or \
                       (rule.kind == "ext" and f.suffix.lower() == name.lower()) or \
                       (rule.kind == "dir" and f.name in rule.names):
                        hits += 1
                        break
            if hits:
                add(rule.lang, rule.weight, rule.label or rule.names[0])

        # 2) directory-based rules — high scores
        if self._unity_like():
            add(LangType.UNITY, 75, "Assets + ProjectSettings directories")
        if self._renpy_like():
            add(LangType.RENPY, 55, "game/ folder containing .rpy scripts")

        # 3) sources — medium scores, capped per language
        for lang, first, per, exts in _SRC_RULES:
            count = sum(1 for f in rel if f.suffix.lower() in exts)
            if count:
                pts = first + min(count - 1, 5) * per
                add(lang, pts, f"{count} file(s) {exts[0]}")

        # 4) tie-breakers for close cases
        if flags.get("electron"):
            add(LangType.ELECTRON, 20, "electron dependency in package.json")
        self._refine_gradle_family(add)
        self._refine_c_family(add)

        if not scores:
            return det
        best = max(scores.items(), key=lambda kv: kv[1])
        det.lang, det.score = best[0], best[1]
        det.confidence = min(1.0, det.score / 80.0)
        det.evidence = evidence.get(det.lang, [])
        det.project_name = self.guess_name(flags.get("name") or "")
        det.entry_point = self.detect_entry(det.lang)
        if det.score < self.THRESHOLD:
            # [fix] clear the entry point too — it belonged to a rejected guess
            det.lang, det.confidence, det.entry_point = LangType.UNKNOWN, 0.0, None
        return det

    def _refine_gradle_family(self, add: Callable[[LangType, int, str], None]) -> None:
        """Gradle without a manifest = Java/Kotlin/Scala — most sources win."""
        has_gradle = any(f.name.startswith("build.gradle") for f in self.files)
        has_manifest = any(f.name == "AndroidManifest.xml" for f in self.files)
        if not has_gradle or has_manifest:
            return
        rel = rel_files(self.files, self.project)
        counts = {l: sum(1 for f in rel if f.suffix in ext)
                  for l, ext in ((LangType.KOTLIN, (".kt",)), (LangType.SCALA, (".scala",)),
                                 (LangType.JAVA, (".java",)))}
        best = max(counts.items(), key=lambda kv: kv[1])
        if best[1] > 0:
            add(best[0], 25, f"Gradle + {best[1]} {best[0].value} file(s)")

    def _refine_c_family(self, add: Callable[[LangType, int, str], None]) -> None:
        """CMake/Make without a hint: compare .c vs .cpp sources to pick C or C++."""
        rel = rel_files(self.files, self.project)
        c_n = sum(1 for f in rel if f.suffix in (".c",))
        cpp_n = sum(1 for f in rel if f.suffix in (".cpp", ".cc", ".cxx"))
        if c_n and cpp_n:
            add(LangType.CPP, 10, f"mixed C/C++ (cpp: {cpp_n}, c: {c_n})")
        elif c_n:
            add(LangType.C, 15, f"{c_n} .c file(s)")
        elif cpp_n:
            add(LangType.CPP, 15, f"{cpp_n} .cpp file(s)")

    def guess_name(self, fallback: str = "") -> str:
        """Best-effort project name (package.json / godot / pubspec / cargo / folder).
        [fix] The Cargo name is now read from the [package] section only."""
        if fallback:
            return fallback
        pj = next((f for f in self.files if f.name == "package.json"), None)
        if pj:
            n = parse_json_file(pj).get("name")
            if isinstance(n, str) and n:
                return n
        name = regex_first(read_small_text(self.project / "project.godot"),
                           r'config/name\s*=\s*"([^"]+)"')
        if name:
            return name
        ps = self.project / "pubspec.yaml"
        if ps.exists():
            name = regex_first(read_small_text(ps), r"^name:\s*(\S+)")
            if name:
                return name
        cargo = self.project / "Cargo.toml"
        if cargo.exists():
            m = re.search(r'\[package\][^\[]*?^\s*name\s*=\s*"([^"]+)"',
                          read_small_text(cargo), re.S | re.M)
            if m:
                return m.group(1)
        return sanitize_name(self.project.name)

    # ---------------- entry-point detection (rewritten) ----------------
    def detect_entry(self, lang: LangType) -> Optional[str]:
        """Best-effort entry-point detection.
        Returns a path relative to the project root (POSIX separators) or None."""
        if not self.files:
            # may be called before detect() from builders — scan first
            self._scan()
        rel = rel_files(self.files, self.project)
        rel_posix = {f.as_posix(): f for f in rel}

        def by_path(cands: Sequence[str]) -> Optional[str]:
            for c in cands:
                if c in rel_posix:
                    return c
            return None

        def by_name(names: Sequence[str]) -> Optional[str]:
            wanted = {n.lower() for n in names}
            best: Optional[Path] = None
            for f in rel:
                if f.name.lower() in wanted:
                    if best is None or len(f.parts) < len(best.parts):
                        best = f
            return best.as_posix() if best else None

        def by_marker(suffixes: Sequence[str], markers: Sequence[str],
                      limit: int = 400) -> Optional[str]:
            ordered = sorted(rel, key=lambda p: (len(p.parts), p.as_posix()))
            for f in ordered[:limit]:
                if f.suffix.lower() in suffixes:
                    text = read_small_text(self.project / f, 262144)
                    if any(m in text for m in markers):
                        return f.as_posix()
            return None

        if lang is LangType.PYTHON:
            entry = by_path(("main.py", "app.py", "run.py", "cli.py",
                             "__main__.py", "src/main.py", "src/app.py"))
            if entry:
                return entry
            # [fix] disambiguate several .py files via the __main__ guard
            entry = by_marker((".py",), ('if __name__ == "__main__"'))
            if entry:
                return entry
            roots = [f for f in rel if len(f.parts) == 1
                     and f.suffix == ".py" and f.name != "setup.py"]
            return roots[0].as_posix() if len(roots) == 1 else None

        if lang in (LangType.NODE, LangType.ELECTRON):
            # [fix] normalize 'main' (backslashes, ./) and verify on disk
            main = (self._package_json_flags().get("main") or "").replace("\\", "/")
            while main.startswith("./"):
                main = main[2:]
            if main and (self.project / main).exists():
                return main
            entry = by_path(("index.js", "main.js", "app.js", "server.js",
                             "src/index.js", "src/main.js", "src/app.js",
                             "index.ts", "src/index.ts", "electron/main.js"))
            if entry:
                return entry
            return by_marker((".js", ".mjs", ".cjs"), ("require(", "import "))

        if lang is LangType.RUST:                      # [fix] was missing entirely
            entry = by_path(("src/main.rs", "main.rs", "src/bin/main.rs"))
            if entry:
                return entry
            m = re.search(r'\[\[bin\]\].*?path\s*=\s*"([^"]+)"',
                          read_small_text(self.project / "Cargo.toml"), re.S)
            if m and (self.project / m.group(1)).exists():
                return m.group(1)
            return None

        if lang is LangType.GO:
            entry = by_path(("main.go", "cmd/main.go", "src/main.go"))
            return entry or by_marker((".go",), ("package main",))

        if lang is LangType.C:
            entry = by_path(("main.c", "src/main.c"))
            return entry or by_marker((".c",), ("int main(", "int main (", "void main("))

        if lang is LangType.CPP:
            entry = by_path(("main.cpp", "src/main.cpp", "main.cc", "src/main.cc"))
            return entry or by_marker((".cpp", ".cc", ".cxx"),
                                      ("int main(", "int main ("))

        if lang is LangType.NIM:
            entry = by_path(("main.nim", "src/main.nim", "app.nim"))
            if entry:
                return entry
            roots = [f for f in rel if len(f.parts) == 1 and f.suffix == ".nim"]
            return roots[0].as_posix() if len(roots) == 1 else None

        if lang is LangType.ZIG:
            return by_path(("main.zig", "src/main.zig"))

        if lang is LangType.CRYSTAL:
            entry = by_path(("src/main.cr", "main.cr"))
            return entry or by_marker((".cr",), ("def main",))

        if lang is LangType.DOTNET:                    # [fix] was missing entirely
            proj = sorted((f for f in rel
                           if f.suffix in (".csproj", ".vbproj", ".fsproj")),
                          key=lambda p: (len(p.parts), p.as_posix()))
            return proj[0].as_posix() if proj else None

        # [fix] the Java/Kotlin/Scala block was truncated in the old version
        if lang is LangType.JAVA:
            return by_marker((".java",), ("static void main",))

        if lang is LangType.KOTLIN:
            return by_marker((".kt",), ("fun main(",))

        if lang is LangType.SCALA:
            return by_marker((".scala", ".sc"), ("def main(", "extends App"))

        if lang is LangType.FLUTTER:
            return by_path(("lib/main.dart", "main.dart"))

        if lang is LangType.DART:
            bins = sorted((f for f in rel if len(f.parts) == 2
                           and f.parts[0] == "bin" and f.suffix == ".dart"),
                          key=lambda p: p.as_posix())
            if bins:
                return bins[0].as_posix()
            entry = by_path(("main.dart", "bin/main.dart"))
            if entry:
                return entry
            return by_marker((".dart",), ("void main(", "Future<void> main("))

        if lang is LangType.LOVE:
            return by_path(("main.lua", "src/main.lua", "conf.lua"))

        if lang is LangType.LUA:
            return by_path(("main.lua", "src/main.lua"))

        if lang is LangType.RUBY:
            entry = by_path(("main.rb", "app.rb", "bin/main.rb", "exe/app.rb"))
            if entry:
                return entry
            roots = [f for f in rel if len(f.parts) == 1 and f.suffix == ".rb"]
            return roots[0].as_posix() if len(roots) == 1 else None

        if lang is LangType.PERL:
            entry = by_path(("main.pl", "app.pl", "script.pl", "bin/app.pl"))
            if entry:
                return entry
            roots = [f for f in rel if len(f.parts) == 1 and f.suffix == ".pl"]
            return roots[0].as_posix() if len(roots) == 1 else None

        if lang is LangType.GODOT:
            return by_path(("project.godot",))

        return None

# ═══════════════════════════════════════════════════════════════════════════
# 10) Build configuration + builder framework
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class BuildConfig:
    project: Path
    name: str
    onefile: bool
    noconsole: bool
    target_os: str            # resolved: windows | linux | macos | android
    entry: Optional[str]      # relative (POSIX) path or None
    output_dir: Path
    verbose: bool = False
    engine: str = "pyinstaller"
    icon: Optional[Path] = None                              # [new] --icon
    add_data: List[Tuple[Path, str]] = field(default_factory=list)  # [new] --add-data
    backend: str = "auto"                                    # [toolbox compat] --backend
    hidden_imports: List[str] = field(default_factory=list)  # [toolbox compat] --hidden-import
    backend: str = "auto"                                    # [toolbox compat] --backend
    hidden_imports: List[str] = field(default_factory=list)  # [toolbox compat] --hidden-import

    @property
    def host_os(self) -> str:
        return current_os()

    @property
    def crossing(self) -> bool:
        return self.target_os != self.host_os

    def entry_path(self) -> Optional[Path]:
        if not self.entry:
            return None
        return self.project / self.entry

    def need_entry(self) -> Path:
        p = self.entry_path()
        if p is None or not p.exists():
            raise BuildError(
                "No entry point available for this build.\n"
                "Specify one explicitly:  --entry <relative/path/to/file>")
        return p


def pick_binary(arts: List[Path], cfg: BuildConfig) -> Path:
    """Pick the most plausible binary from a list of candidates."""
    stems = {cfg.name.lower(), "main", "app"}
    if cfg.entry:
        stems.add(Path(cfg.entry).stem.lower())
    for p in arts:
        if p.stem.lower() in stems:
            return p
    return max(arts, key=lambda p: p.stat().st_size)


class BaseBuilder:
    LANG = LangType.UNKNOWN
    TOOL_LABEL = "?"
    # [toolbox compat] backend names this builder can actually honor
    SUPPORTED_BACKENDS: Tuple[str, ...] = ()

    def __init__(self, cfg: BuildConfig, deps: DependencyManager):
        self.cfg = cfg
        self.deps = deps
        self.project = cfg.project
        self.out = cfg.output_dir
        self.out.mkdir(parents=True, exist_ok=True)

    def build(self) -> List[Path]:
        raise NotImplementedError

    # ---- shared helpers ----
    def check_features(self, icon: bool = False, data: bool = False,
                       hidden: bool = False) -> None:
        """[new] Warn clearly when --icon/--add-data/--hidden-import cannot be
        consumed by the chosen toolchain — the build itself continues unaffected."""
        if self.cfg.icon and not icon:
            warn(f"--icon is not applicable to {self.TOOL_LABEL} — ignored.")
        if self.cfg.add_data and not data:
            warn(f"--add-data is not applicable to {self.TOOL_LABEL} — ignored.")
        if self.cfg.hidden_imports and not hidden:
            warn(f"--hidden-import is not applicable to {self.TOOL_LABEL} — ignored.")

    def check_backend(self) -> None:
        """[toolbox compat] One clear warning when the requested --backend is
        not something this builder can honor — the default backend is used."""
        b = (getattr(self.cfg, "backend", "") or "").strip().lower()
        if b in ("", "auto", "-"):
            return
        if b not in self.SUPPORTED_BACKENDS:
            want = ", ".join(self.SUPPORTED_BACKENDS) or "(none)"
            warn(f"--backend {b} is not supported by {self.TOOL_LABEL} "
                 f"(supported: {want}) — using the default backend.")

    def run(self, cmd: Sequence[str], cwd: Optional[Path] = None,
            timeout: int = TIMEOUT_LONG,
            env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
        proc = run_cmd(cmd, cwd=cwd or self.project, timeout=timeout, env=env)
        if proc.returncode != 0:
            tail = tail_lines((proc.stdout or "") + "\n" + (proc.stderr or ""), 8)
            raise BuildError(f"Command failed: {cmd[0]} (exit={proc.returncode})\n{tail}")
        if self.cfg.verbose and (proc.stdout or proc.stderr):
            dim(((proc.stdout or "") + (proc.stderr or "")).strip())
        return proc

    def emit(self, paths: Iterable[Path]) -> List[Path]:
        """Verify that artifacts exist and are non-empty; return them."""
        arts: List[Path] = []
        for p in paths:
            p = Path(p)
            try:
                if p.is_dir():
                    if any(p.iterdir()):
                        arts.append(p)
                elif p.exists() and p.stat().st_size > 0:
                    arts.append(p)
            except OSError:
                continue
        if not arts:
            raise BuildError("The build reported success but produced no artifacts.")
        return arts

    def copy_to_out(self, src: Path, name: Optional[str] = None) -> Path:
        dst = self.out / (name or src.name)
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        return dst

# ═══════════════════════════════════════════════════════════════════════════
# 11) Builders — one per language/environment
# ═══════════════════════════════════════════════════════════════════════════

class PythonBuilder(BaseBuilder):
    LANG = LangType.PYTHON
    TOOL_LABEL = "PyInstaller / Nuitka"
    SUPPORTED_BACKENDS = ("pyinstaller", "nuitka", "cx_freeze")

    def build(self) -> List[Path]:
        self.check_features(icon=True, data=True, hidden=True)
        self.check_backend()
        entry = self.cfg.need_entry()
        # [toolbox compat] --backend overrides/engine-selects:
        #   nuitka -> Nuitka, pyinstaller -> PyInstaller,
        #   cx_freeze -> not shipped here: a clear warning + PyInstaller.
        backend = (getattr(self.cfg, "backend", "") or "").strip().lower()
        if backend == "nuitka":
            self.cfg.engine = "nuitka"
        elif backend == "cx_freeze":
            warn("cx_freeze backend is not wired into PolyBuild — using PyInstaller "
                 "instead (same single-folder/onefile output).")
        if self.cfg.engine == "nuitka":
            return self._nuitka(entry)
        return self._pyinstaller(entry)

    def _pyinstaller(self, entry: Path) -> List[Path]:
        # [fix] run via 'python -m PyInstaller' + module check — the plain
        # 'pyinstaller' binary is often missing from PATH on Windows.
        if not self.deps.ensure_python_module("PyInstaller", "pyinstaller",
                                              purpose="Python packaging"):
            raise BuildError("PyInstaller is required for Python builds.")
        if self.cfg.crossing:
            warn("PyInstaller cannot cross-compile — building for the host OS.")
        work = self.project / ".polybuild_tmp" / "pyi"
        work.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, "-m", "PyInstaller",
               "--noconfirm", "--clean",
               "--distpath", str(self.out), "--workpath", str(work),
               "--specpath", str(work), "--name", self.cfg.name]
        if self.cfg.onefile:
            cmd.append("--onefile")
        if self.cfg.noconsole:
            cmd.append("--windowed")
        # [new] application icon (.ico on Windows / .icns on macOS)
        if self.cfg.icon:
            cmd += ["--icon", str(self.cfg.icon)]
        # [new] extra data -> --add-data with the OS-specific separator
        for src, dest in self.cfg.add_data:
            cmd += ["--add-data", f"{src}{os.pathsep}{dest}"]
        # [toolbox compat] --hidden-import -> PyInstaller hidden imports
        for mod in self.cfg.hidden_imports:
            cmd += ["--hidden-import", mod]
        cmd.append(str(entry))
        self.run(cmd, timeout=TIMEOUT_LONG)
        stem = self.cfg.name
        return self.emit([self.out / (stem + ".exe"), self.out / stem,
                          self.out / (stem + ".app")])

    def _nuitka(self, entry: Path) -> List[Path]:
        if not self.deps.ensure_python_module("nuitka", "nuitka",
                                              purpose="Python compilation"):
            raise BuildError("Nuitka is required for --engine nuitka.")
        if self.cfg.crossing:
            warn("Nuitka cannot cross-compile — building for the host OS.")
        ext = exe_ext(current_os())
        stem, entry_stem = self.cfg.name, entry.stem
        cmd = [sys.executable, "-m", "nuitka",
               "--output-dir", str(self.out),
               "--assume-yes-for-downloads", "--remove-output"]
        cmd.append("--onefile" if self.cfg.onefile else "--standalone")
        if self.cfg.noconsole:
            cmd.append("--windows-disable-console" if os.name == "nt"
                       else "--disable-console")
        # [new] icon: Windows (.ico) / macOS (.icns); ELF has no icon resource
        if self.cfg.icon:
            if self.cfg.target_os == "windows":
                cmd.append(f"--windows-icon-from-ico={self.cfg.icon}")
            elif self.cfg.target_os == "macos":
                cmd.append(f"--macos-app-icon={self.cfg.icon}")
            else:
                warn("--icon ignored: Nuitka cannot embed icons in Linux ELF binaries.")
        # [new] extra data -> include-data-files / include-data-dir
        for src, dest in self.cfg.add_data:
            if src.is_dir():
                cmd.append(f"--include-data-dir={src}={dest}")
            else:
                cmd.append(f"--include-data-files={src}={dest}")
        # [toolbox compat] --hidden-import -> Nuitka include-module
        for mod in self.cfg.hidden_imports:
            cmd.append(f"--include-module={mod}")
        cmd.append(str(entry))
        self.run(cmd, timeout=TIMEOUT_XLONG)
        return self.emit([self.out / (stem + ext), self.out / stem,
                          self.out / (stem + ".dist"), self.out / (stem + ".bin"),
                          self.out / (entry_stem + ext), self.out / entry_stem,
                          self.out / (entry_stem + ".dist")])


class NodeBuilder(BaseBuilder):
    LANG = LangType.NODE
    TOOL_LABEL = "pkg (via npx)"
    PLATFORM = {"windows": "win", "linux": "linux", "macos": "macos"}
    SUPPORTED_BACKENDS = ("pkg", "nexe")

    def build(self) -> List[Path]:
        self.check_features()      # pkg cannot embed icons or bundle assets
        self.check_backend()       # nexe -> warning + pkg fallback
        entry = self.cfg.need_entry()
        if entry.suffix.lower() == ".ts":
            hint("pkg builds TypeScript entries when package.json bin/scripts "
                 "are configured; otherwise compile with tsc first.")
        self.deps.require(["node", "npx"], purpose="Node.js packaging")
        plat = self.PLATFORM.get(self.cfg.target_os)
        if not plat:
            raise BuildError(f"pkg does not support target OS '{self.cfg.target_os}'.")
        arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
        out = self.out / (self.cfg.name + exe_ext(self.cfg.target_os))
        # [fix] resolve npx to its full path (npx.cmd on Windows)
        npx = which_cmd("npx") or "npx"
        self.run([npx, "--yes", "pkg", str(entry),
                  "--target", f"node18-{plat}-{arch}",
                  "--output", str(out)], timeout=TIMEOUT_XLONG)
        return self.emit([out])


class ElectronBuilder(BaseBuilder):
    LANG = LangType.ELECTRON
    TOOL_LABEL = "electron-builder (via npx)"
    FLAG = {"windows": "--win", "linux": "--linux", "macos": "--mac"}
    SUPPORTED_BACKENDS = ("electron-builder",)

    def build(self) -> List[Path]:
        self.check_features(icon=True, data=True)
        self.check_backend()
        self.deps.require(["node", "npx"], purpose="Electron packaging")
        flag = self.FLAG.get(self.cfg.target_os)
        if not flag:
            raise BuildError(f"electron-builder does not support target '{self.cfg.target_os}'.")
        npx = which_cmd("npx") or "npx"
        ed_out = self.out / "electron"
        env = {"CSC_IDENTITY_AUTO_DISCOVERY": "false"}
        cmd = [npx, "--yes", "electron-builder", flag, "--x64",
               f"--config.directories.output={ed_out}"]
        # [new] icon via the config override (project-relative when possible)
        if self.cfg.icon:
            try:
                icon_arg = self.cfg.icon.resolve() \
                    .relative_to(self.project.resolve()).as_posix()
            except ValueError:
                icon_arg = str(self.cfg.icon.resolve())
            cmd.append(f"--config.icon={icon_arg}")
        # [new] --add-data -> extraResources entries (JSON config override)
        if self.cfg.add_data:
            extras = json.dumps([{"from": str(s), "to": d}
                                 for s, d in self.cfg.add_data])
            cmd.append(f"--config.extraResources={extras}")
        self.run(cmd, timeout=TIMEOUT_XLONG, env=env)
        wanted = {".exe", ".appimage", ".dmg", ".snap", ".deb", ".appx", ".msi"}
        arts: List[Path] = []
        if ed_out.exists():
            for p in ed_out.rglob("*"):
                try:
                    if p.is_dir() and p.suffix == ".app":
                        arts.append(p)
                    elif p.is_file() and p.suffix.lower() in wanted \
                            and p.stat().st_size > 0:
                        arts.append(p)
                except OSError:
                    continue
        if not arts:
            raise BuildError("electron-builder finished but no installers were found.")
        arts.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return self.emit(arts[:3])


class RubyBuilder(BaseBuilder):
    LANG = LangType.RUBY
    TOOL_LABEL = "Ocra"
    SUPPORTED_BACKENDS = ("ocra",)

    def build(self) -> List[Path]:
        self.check_features()      # Ocra has no icon/data options
        self.check_backend()
        entry = self.cfg.need_entry()
        if current_os() != "windows" or self.cfg.target_os != "windows":
            raise BuildError(
                "Ruby has no standalone native compiler — Ocra packaging is Windows-only.\n"
                "Run this build on Windows, or ship the app together with the Ruby interpreter.")
        self.deps.require(["ruby", "ocra"], purpose="Ruby packaging")
        ocra = which_cmd("ocra") or "ocra"
        out = self.out / (self.cfg.name + ".exe")
        cmd = [ocra, str(entry), "--output", str(out), "--no-warnings"]
        cmd.append("--window" if self.cfg.noconsole else "--console")
        self.run(cmd, timeout=TIMEOUT_XLONG)
        return self.emit([out])


class PerlBuilder(BaseBuilder):
    LANG = LangType.PERL
    TOOL_LABEL = "PAR::Packer (pp)"

    def build(self) -> List[Path]:
        self.check_backend()
        entry = self.cfg.need_entry()
        self.deps.require(["perl", "pp"], purpose="Perl packaging")
        if self.cfg.crossing:
            warn("pp builds for the host OS only — cross-compilation is not supported.")
        if self.cfg.add_data:
            warn("--add-data is not applicable to PAR::Packer — ignored.")
        pp = which_cmd("pp") or "pp"
        out = self.out / (self.cfg.name + exe_ext(current_os()))
        cmd = [pp, "-o", str(out)]
        # [new] pp can embed a Win32 icon into the executable
        if self.cfg.icon:
            if current_os() == "windows" and self.cfg.icon.suffix.lower() == ".ico":
                cmd += ["--icon", str(self.cfg.icon)]
            else:
                warn("--icon ignored: pp embeds icons only into Windows .ico builds.")
        cmd.append(str(entry))
        self.run(cmd, timeout=TIMEOUT_XLONG)
        return self.emit([out])


class LoveBuilder(BaseBuilder):
    LANG = LangType.LOVE
    TOOL_LABEL = "LÖVE package"

    def build(self) -> List[Path]:
        self.check_features(data=True)   # icons are set inside conf.lua
        self.check_backend()
        entry = self.cfg.need_entry()   # usually main.lua
        love_path = self.out / f"{self.cfg.name}.love"
        with zipfile.ZipFile(love_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in collect_files(self.project, extra_excludes={self.out.name}):
                try:
                    zf.write(f, f.relative_to(self.project).as_posix())
                except (OSError, ValueError):
                    continue
            # [new] --add-data entries are packed into the .love archive
            for src, dest in self.cfg.add_data:
                if src.is_dir():
                    for f in src.rglob("*"):
                        if f.is_file():
                            zf.write(f, f"{dest}/{f.relative_to(src).as_posix()}")
                else:
                    zf.write(src, dest)
        arts = [love_path]
        if self.cfg.target_os == "windows":
            love_exe = which_cmd("love")
            if love_exe and Path(love_exe).suffix.lower() == ".exe":
                fused = self.out / f"{self.cfg.name}.exe"
                shutil.copy2(love_exe, fused)
                with open(love_path, "rb") as src, open(fused, "ab") as dst:
                    shutil.copyfileobj(src, dst)
                arts.append(fused)
            else:
                warn("love.exe not found — produced the .love package only.")
        if entry.name != "main.lua":
            warn(f"LÖVE expects main.lua at the archive root; detected entry is "
                 f"'{self.cfg.entry}'.")
        return self.emit(arts)


class NativeCBuilder(BaseBuilder):
    """C build: CMake -> Make -> direct compile (entry + sources without main)."""
    LANG = LangType.C
    COMPILER = "gcc"
    COMPILER_WIN_CROSS = "x86_64-w64-mingw32-gcc"
    SRC_EXTS = (".c",)
    STD_FLAG = "-std=c11"
    TOOL_LABEL = "gcc / CMake / Make"
    SUPPORTED_BACKENDS = ("gcc", "clang", "g++", "clang++", "cmake")

    def build(self) -> List[Path]:
        self.check_features()   # compilers have no icon/data mechanism
        self.check_backend()
        backend = (getattr(self.cfg, "backend", "") or "").strip().lower()
        # [toolbox compat] backend selects the compiler or forces CMake
        self._backend_compiler = None
        if backend in ("clang", "clang++"):
            self._backend_compiler = "clang" if backend == "clang" else "clang++"
        elif backend in ("gcc", "g++"):
            self._backend_compiler = self.COMPILER
        if (self.project / "CMakeLists.txt").exists():
            return self._cmake_build()
        if any((self.project / n).exists() for n in ("Makefile", "makefile", "GNUmakefile")):
            return self._make_build()
        return self._direct_build()

    def _compiler(self) -> str:
        backend_cc = getattr(self, "_backend_compiler", None)
        if backend_cc and self.cfg.target_os == current_os():
            return backend_cc          # backend compiler only for native builds
        if self.cfg.target_os == "windows" and current_os() != "windows":
            return self.COMPILER_WIN_CROSS
        return self.COMPILER

    def _direct_build(self) -> List[Path]:
        entry = self.cfg.need_entry()
        cc = self._compiler()
        self.deps.require([cc], purpose=f"{self.LANG.value} compilation")
        out = self.out / (self.cfg.name + exe_ext(self.cfg.target_os))
        srcs = self._link_sources(entry)
        cmd = [which_cmd(cc) or cc, "-O2", self.STD_FLAG,
               str(entry)] + [str(s) for s in srcs] + ["-o", str(out)]
        self.run(cmd, timeout=TIMEOUT_LONG)
        return self.emit([out])

    def _link_sources(self, entry: Path) -> List[Path]:
        """[fix] link every source EXCEPT files that define their own main()
        — prevents duplicate-symbol link errors on multi-file projects."""
        rel = rel_files(collect_files(self.project, extra_excludes={self.out.name}),
                        self.project)
        out: List[Path] = []
        for f in rel:
            if f.suffix.lower() not in self.SRC_EXTS:
                continue
            p = self.project / f
            if p == entry:
                continue
            if re.search(r"\b(?:int|void)\s+main\s*\(", read_small_text(p, 65536)):
                continue
            out.append(p)
            if len(out) >= 200:
                break
        return out

    def _cmake_build(self) -> List[Path]:
        self.deps.require(["cmake"], purpose="CMake build")
        bdir = self.project / ".polybuild_tmp" / "cmake"
        self.run(["cmake", "-S", str(self.project), "-B", str(bdir),
                  "-DCMAKE_BUILD_TYPE=Release"], timeout=TIMEOUT_MED)
        self.run(["cmake", "--build", str(bdir), "--config", "Release", "--parallel"],
                 timeout=TIMEOUT_XLONG)
        arts = find_native_binaries(bdir, self.cfg.target_os)
        if not arts:
            raise BuildError("CMake build finished but no binaries were found.")
        return self.emit([self.copy_to_out(pick_binary(arts, self.cfg))])

    def _make_build(self) -> List[Path]:
        tool = "make" if current_os() != "windows" else "mingw32-make"
        self.deps.require([tool], purpose="Make build")
        self.run([which_cmd(tool) or tool, "-j"], timeout=TIMEOUT_XLONG)
        arts = [a for a in find_native_binaries(self.project, self.cfg.target_os)
                if self.out.resolve() not in a.parents]
        if not arts:
            raise BuildError("make finished but no binaries were found.")
        return self.emit([self.copy_to_out(pick_binary(arts, self.cfg))])


class NativeCppBuilder(NativeCBuilder):
    LANG = LangType.CPP
    COMPILER = "g++"
    COMPILER_WIN_CROSS = "x86_64-w64-mingw32-g++"
    SRC_EXTS = (".cpp", ".cc", ".cxx")
    STD_FLAG = "-std=c++17"
    TOOL_LABEL = "g++ / CMake / Make"


class RustBuilder(BaseBuilder):
    LANG = LangType.RUST
    TOOL_LABEL = "cargo build --release"
    SUPPORTED_BACKENDS = ("cargo",)
    TRIPLE = {"windows": "x86_64-pc-windows-gnu",
              "linux": "x86_64-unknown-linux-gnu",
              "macos": "x86_64-apple-darwin"}

    def build(self) -> List[Path]:
        self.check_features()   # icons/data are managed by the OS packaging layer
        self.check_backend()
        self.deps.require(["cargo"], purpose="Rust build")
        cmd = ["cargo", "build", "--release"]
        triple = None
        if self.cfg.crossing:
            triple = self.TRIPLE[self.cfg.target_os]
            cmd += ["--target", triple]
        self.run(cmd, timeout=TIMEOUT_XLONG)
        search = self.project / "target" / (triple or "") / "release"
        cand = search / (self.cfg.name + exe_ext(self.cfg.target_os))
        if cand.exists():
            return self.emit([self.copy_to_out(cand)])
        # [fix] the crate name may differ from --name: search the folder for real
        arts = find_native_binaries(search, self.cfg.target_os)
        if not arts:
            raise BuildError("cargo finished but no binary was found in target/release.")
        return self.emit([self.copy_to_out(pick_binary(arts, self.cfg))])


class GoBuilder(BaseBuilder):
    LANG = LangType.GO
    TOOL_LABEL = "go build"
    SUPPORTED_BACKENDS = ("go",)
    GOOS = {"windows": "windows", "linux": "linux", "macos": "darwin"}

    def build(self) -> List[Path]:
        self.check_features()
        self.check_backend()
        self.deps.require(["go"], purpose="Go build")
        if self.cfg.target_os == "android":
            raise BuildError("Plain 'go build' cannot target Android (use gomobile).")
        goarch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "amd64"
        env = {"GOOS": self.GOOS[self.cfg.target_os], "GOARCH": goarch,
               "CGO_ENABLED": "0"}
        out = self.out / (self.cfg.name + exe_ext(self.cfg.target_os))
        # [fix] build the *package directory* of the detected entry, not ./...
        entry = self.cfg.entry_path()
        if entry is not None:
            # [fix v3.3.1] relative_to can raise ValueError (symlinks / Windows
            # case-insensitivity) — fall back to the whole-module build instead
            # of crashing with a traceback.
            try:
                rel_dir = entry.parent.resolve().relative_to(self.project.resolve())
                pkg = "." if str(rel_dir) == "." else "./" + rel_dir.as_posix()
            except ValueError:
                pkg = "."
        else:
            pkg = "." if (self.project / "main.go").exists() else "./..."
        self.run(["go", "build", "-trimpath", "-ldflags", "-s -w",
                  "-o", str(out), pkg], timeout=TIMEOUT_LONG, env=env)
        return self.emit([out])


class NimBuilder(BaseBuilder):
    LANG = LangType.NIM
    TOOL_LABEL = "nim compile"

    def build(self) -> List[Path]:
        self.check_features()
        self.check_backend()
        entry = self.cfg.need_entry()
        self.deps.require(["nim"], purpose="Nim compilation")
        out = self.out / (self.cfg.name + exe_ext(self.cfg.target_os))
        cmd = ["nim", "compile", "--opt:speed", "-d:release", f"--out:{out}"]
        if self.cfg.target_os == "windows" and current_os() != "windows":
            self.deps.require(["x86_64-w64-mingw32-gcc"],
                              purpose="Nim cross-compilation to Windows")
            cmd += ["--cpu:amd64", "--os:windows"]
        cmd.append(str(entry))
        self.run(cmd, timeout=TIMEOUT_LONG)
        return self.emit([out])


class ZigBuilder(BaseBuilder):
    LANG = LangType.ZIG
    TOOL_LABEL = "zig build / build-exe"
    TARGET = {"windows": "x86_64-windows", "linux": "x86_64-linux",
              "macos": "x86_64-macos"}

    def build(self) -> List[Path]:
        self.check_features()
        self.check_backend()
        self.deps.require(["zig"], purpose="Zig build")
        zig = which_cmd("zig") or "zig"
        if (self.project / "build.zig").exists():
            cmd = [zig, "build", "-Doptimize=ReleaseFast"]
            if self.cfg.crossing:
                cmd.append(f"-Dtarget={self.TARGET[self.cfg.target_os]}")
            self.run(cmd, timeout=TIMEOUT_XLONG)
            arts = find_native_binaries(self.project / "zig-out" / "bin",
                                        self.cfg.target_os)
            if not arts:
                raise BuildError("zig build finished but zig-out/bin is empty.")
            return self.emit([self.copy_to_out(pick_binary(arts, self.cfg))])
        entry = self.cfg.need_entry()
        out = self.out / (self.cfg.name + exe_ext(self.cfg.target_os))
        cmd = [zig, "build-exe", str(entry), "-O", "ReleaseFast",
               f"-femit-bin={out}"]
        if self.cfg.crossing:
            cmd += ["-target", self.TARGET[self.cfg.target_os]]
        self.run(cmd, timeout=TIMEOUT_LONG)
        return self.emit([out])


class CrystalBuilder(BaseBuilder):
    LANG = LangType.CRYSTAL
    TOOL_LABEL = "crystal build --release"

    def build(self) -> List[Path]:
        self.check_features()
        self.check_backend()
        entry = self.cfg.need_entry()
        self.deps.require(["crystal"], purpose="Crystal build")
        if self.cfg.crossing:
            raise BuildError("Crystal does not support cross-compiling executables "
                             "from this host — build on the target OS instead.")
        out = self.out / (self.cfg.name + exe_ext(self.cfg.target_os))
        self.run(["crystal", "build", "--release", str(entry), "-o", str(out)],
                 timeout=TIMEOUT_XLONG)
        return self.emit([out])


class DotnetBuilder(BaseBuilder):
    LANG = LangType.DOTNET
    TOOL_LABEL = "dotnet publish"
    SUPPORTED_BACKENDS = ("dotnet",)
    RID = {"windows": "win-x64", "linux": "linux-x64", "macos": "osx-x64"}

    def build(self) -> List[Path]:
        self.check_features(icon=True, data=True)
        self.check_backend()
        self.deps.require(["dotnet"], purpose=".NET build")
        # [fix] .NET entry point = the project file (now detected)
        proj = self.cfg.entry_path()
        if proj is None or proj.suffix not in (".csproj", ".vbproj", ".fsproj") \
                or not proj.exists():
            cands = sorted((f for f in collect_files(self.project)
                            if f.suffix in (".csproj", ".vbproj", ".fsproj")),
                           key=lambda p: (len(p.relative_to(self.project).parts), p.name))
            if not cands:
                raise BuildError("No .csproj / .vbproj / .fsproj found in the project.")
            proj = cands[0]
        rid = self.RID.get(self.cfg.target_os)
        publish_dir = self.out / "publish"
        cmd = ["dotnet", "publish", str(proj), "-c", "Release",
               "-o", str(publish_dir), "--nologo"]
        if self.cfg.onefile:
            cmd += ["--self-contained", "true", f"-r:{rid}",
                    "-p:PublishSingleFile=true"]
        elif self.cfg.crossing and rid:
            cmd += [f"-r:{rid}"]
        # [new] embed a Win32 icon into the produced .exe
        if self.cfg.icon:
            if self.cfg.target_os == "windows" and self.cfg.icon.suffix.lower() == ".ico":
                cmd += [f"-p:ApplicationIcon={self.cfg.icon}"]
            else:
                warn("--icon ignored: .NET embeds icons only for Windows .exe (.ico).")
        self.run(cmd, timeout=TIMEOUT_XLONG)
        # [new] --add-data: copy the extra files into the publish folder
        for src, dest in self.cfg.add_data:
            target = publish_dir / dest
            target.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True)
                shutil.copytree(src, target)
            else:
                shutil.copy2(src, target)
        # [fix] the output is named after the project file, not after --name
        exe_name = proj.stem + (".exe" if self.cfg.target_os == "windows" else "")
        cand = publish_dir / exe_name
        if not cand.exists():
            cand = publish_dir / (proj.stem + ".dll")
        return self.emit([cand])


class JavaBuilder(BaseBuilder):
    LANG = LangType.JAVA
    TOOL_LABEL = "Maven / Gradle / javac"
    SUPPORTED_BACKENDS = ("jpackage", "gradle", "maven")

    def build(self) -> List[Path]:
        # icon embedding needs the jpackage path, which is taken with -f
        self.check_features(icon=self.cfg.onefile, data=False)
        self.check_backend()
        backend = (getattr(self.cfg, "backend", "") or "").strip().lower()
        # [toolbox compat] backend steers the route when it exists in the
        # project; otherwise the auto path below is used with a warning.
        if backend == "maven" and (self.project / "pom.xml").exists():
            return self._maven()
        if backend == "gradle" and any(
                f.name.startswith("build.gradle")
                for f in collect_files(self.project, max_files=400)):
            return self._gradle_jar()
        if backend in ("maven", "gradle"):
            warn(f"--backend {backend}: matching build files not found — "
                 "auto-routing (pom.xml / build.gradle / javac).")
        if backend == "jpackage":
            # jpackage needs -f (onefile) to be taken
            if not self.cfg.onefile:
                info("--backend jpackage: enabling onefile (app-image packaging).")
                self.cfg.onefile = True
        if (self.project / "pom.xml").exists():
            return self._maven()
        if any(f.name.startswith("build.gradle")
               for f in collect_files(self.project, max_files=400)):
            return self._gradle_jar()
        return self._javac()

    def _maven(self) -> List[Path]:
        self.deps.require(["java", "mvn"], purpose="Maven build")
        mvn = which_cmd("mvn") or "mvn"
        self.run([mvn, "-q", "-DskipTests", "package"], timeout=TIMEOUT_XLONG)
        jars = [p for p in (self.project / "target").glob("*.jar")
                if not p.name.endswith(("sources.jar", "javadoc.jar"))
                and not p.name.startswith("original-")]
        if not jars:
            raise BuildError("Maven finished but no JAR was found in target/.")
        jar = max(jars, key=lambda p: p.stat().st_mtime)
        return self._maybe_jpackage(jar)

    def _gradle_jar(self) -> List[Path]:
        wrapper = self.project / ("gradlew.bat" if os.name == "nt" else "gradlew")
        if wrapper.exists():
            cmd = [str(wrapper)] if os.name == "nt" else ["sh", str(wrapper)]
        else:
            self.deps.require(["gradle"], purpose="Gradle build")
            cmd = [which_cmd("gradle") or "gradle"]
        self.run(cmd + ["--no-daemon", "jar"], timeout=TIMEOUT_XLONG)
        # [fix] search build/ folders too (collect_files would skip them)
        jars = [f for f in find_files_by_ext(self.project, ".jar")
                if self.out.resolve() not in f.parents]
        if not jars:
            raise BuildError("Gradle finished but no JAR was produced.")
        jar = max(jars, key=lambda p: p.stat().st_mtime)
        return self._maybe_jpackage(jar)

    def _javac(self) -> List[Path]:
        self.deps.require(["java", "javac", "jar"], purpose="Java compilation")
        entry = self.cfg.need_entry()
        srcs = [f for f in collect_files(self.project)
                if f.suffix == ".java" and self.out.resolve() not in f.parents][:150]
        if entry not in srcs:
            srcs.insert(0, entry)
        classes = self.project / ".polybuild_tmp" / "classes"
        classes.mkdir(parents=True, exist_ok=True)
        javac = which_cmd("javac") or "javac"
        self.run([javac, "-encoding", "utf-8", "-d", str(classes)]
                 + [str(s) for s in srcs], timeout=TIMEOUT_LONG)
        jar_out = self.out / f"{self.cfg.name}.jar"
        jar_tool = which_cmd("jar") or "jar"
        main_class = self._main_class(entry)
        if main_class:
            self.run([jar_tool, "--create", "--file", str(jar_out),
                      "--main-class", main_class, "-C", str(classes), "."])
        else:
            self.run([jar_tool, "--create", "--file", str(jar_out),
                      "-C", str(classes), "."])
        return self._maybe_jpackage(jar_out)

    def _main_class(self, entry: Optional[Path]) -> Optional[str]:
        """Derive the fully-qualified main class from the entry file."""
        if entry is None or not entry.exists():
            return None
        text = read_small_text(entry)
        pkg = regex_first(text, r"^\s*package\s+([\w.]+)\s*;")
        return f"{pkg}.{entry.stem}" if pkg else entry.stem

    def _maybe_jpackage(self, jar: Path) -> List[Path]:
        main_class = self._main_class(self.cfg.entry_path())
        if self.cfg.onefile and main_class \
                and self.deps.ensure("jpackage", purpose="native Java packaging"):
            jp = which_cmd("jpackage") or "jpackage"
            jp_cmd = [jp, "--name", self.cfg.name, "--type", "app-image",
                      "--input", str(jar.parent), "--main-jar", jar.name,
                      "--main-class", main_class, "--dest", str(self.out)]
            # [new] package icon (.ico on Windows / .icns on macOS)
            if self.cfg.icon:
                jp_cmd += ["--icon", str(self.cfg.icon)]
            self.run(jp_cmd, timeout=TIMEOUT_LONG)
            img = self.out / self.cfg.name
            if img.exists():
                return self.emit([img])
            exes = [p for p in self.out.rglob("*.exe") if p.is_file()]
            if exes:
                return self.emit(exes[:1])
        if self.cfg.icon:
            warn("--icon dropped: plain JARs cannot embed icons (needs jpackage + -f).")
        return self.emit([jar])


class KotlinBuilder(BaseBuilder):
    LANG = LangType.KOTLIN
    TOOL_LABEL = "kotlinc"

    def build(self) -> List[Path]:
        self.check_features()
        self.check_backend()
        self.deps.require(["java", "kotlinc"], purpose="Kotlin compilation")
        entry = self.cfg.need_entry()
        srcs = [f for f in collect_files(self.project)
                if f.suffix == ".kt" and self.out.resolve() not in f.parents][:150]
        if entry not in srcs:
            srcs.insert(0, entry)
        jar_out = self.out / f"{self.cfg.name}.jar"
        kotlinc = which_cmd("kotlinc") or "kotlinc"
        self.run([kotlinc] + [str(s) for s in srcs]
                 + ["-include-runtime", "-d", str(jar_out)], timeout=TIMEOUT_XLONG)
        # [fix] kotlinc does not set Main-Class — make the JAR actually runnable
        if not self._set_main_class(jar_out, f"{entry.stem}Kt"):
            self._set_main_class(jar_out, entry.stem)
        return self.emit([jar_out])

    def _set_main_class(self, jar_path: Path, main_class: str) -> bool:
        jar_tool = which_cmd("jar") or "jar"
        proc = run_cmd([jar_tool, "--update", "--file", str(jar_path),
                        "--main-class", main_class],
                       cwd=self.project, timeout=TIMEOUT_SHORT)
        return proc.returncode == 0


class ScalaBuilder(BaseBuilder):
    LANG = LangType.SCALA
    TOOL_LABEL = "scala-cli package"

    def build(self) -> List[Path]:
        self.check_features()
        self.check_backend()
        self.deps.require(["java", "scala-cli"], purpose="Scala build")
        entry = self.cfg.need_entry()
        out = self.out / (self.cfg.name + exe_ext(self.cfg.target_os))
        sc = which_cmd("scala-cli") or "scala-cli"
        self.run([sc, "--power", "package", "--assembly", str(entry),
                  "-o", str(out), "--force"], timeout=TIMEOUT_XLONG)
        return self.emit([out])


class FlutterBuilder(BaseBuilder):
    LANG = LangType.FLUTTER
    TOOL_LABEL = "flutter build"
    SUPPORTED_BACKENDS = ("flutter",)

    def _out_base(self, t: str) -> Path:
        candidates = {
            "windows": ("build/windows/x64/runner/Release",
                        "build/windows/arm64/runner/Release"),
            "linux": ("build/linux/x64/release/bundle",
                      "build/linux/arm64/release/bundle"),
            "macos": ("build/macos/Build/Products/Release",),
        }[t]
        for sub in candidates:
            p = self.project / sub
            if p.exists():
                return p
        return self.project / candidates[0]

    def build(self) -> List[Path]:
        self.check_features()
        self.check_backend()
        hint("App icons for Flutter/Android are set in the native resource "
             "folders (e.g. via the flutter_launcher_icons package).")
        self.deps.require(["flutter"], purpose="Flutter build")
        flutter = which_cmd("flutter") or "flutter"
        t = self.cfg.target_os
        if t == "android":
            self.run([flutter, "build", "apk", "--release"], timeout=TIMEOUT_XLONG)
            apks = [f for f in find_files_by_ext(self.project, ".apk")
                    if self.out.resolve() not in f.parents]
            if not apks:
                raise BuildError("flutter build apk finished but no APK was found.")
            apk = next((a for a in apks if a.name == "app-release.apk"),
                       max(apks, key=lambda p: p.stat().st_mtime))
            return self.emit([self.copy_to_out(apk)])
        self.run([flutter, "build", t, "--release"], timeout=TIMEOUT_XLONG)
        base = self._out_base(t)
        if not base.exists():
            raise BuildError(f"flutter build finished but the output folder is missing: {base}")
        if t == "windows":
            arts = list(base.glob("*.exe"))
        elif t == "linux":
            arts = [p for p in base.iterdir() if p.is_file() and p.suffix == ""]
        else:
            arts = [p for p in base.iterdir() if p.is_dir() and p.suffix == ".app"]
        if not arts:
            raise BuildError("flutter build produced no artifacts.")
        return self.emit([self.copy_to_out(arts[0])])


class DartBuilder(BaseBuilder):
    LANG = LangType.DART
    TOOL_LABEL = "dart compile exe"
    SUPPORTED_BACKENDS = ("dart",)

    def build(self) -> List[Path]:
        self.check_features()
        self.check_backend()
        entry = self.cfg.need_entry()
        self.deps.require(["dart"], purpose="Dart build")
        if self.cfg.crossing:
            raise BuildError("dart compile exe builds for the host OS only.")
        dart = which_cmd("dart") or "dart"
        out = self.out / (self.cfg.name + exe_ext(current_os()))
        self.run([dart, "compile", "exe", str(entry), "-o", str(out)],
                 timeout=TIMEOUT_XLONG)
        return self.emit([out])


class AndroidBuilder(BaseBuilder):
    LANG = LangType.ANDROID
    TOOL_LABEL = "Gradle (assembleRelease)"

    def build(self) -> List[Path]:
        self.check_features()
        self.check_backend()
        wrapper = self.project / ("gradlew.bat" if os.name == "nt" else "gradlew")
        if wrapper.exists():
            cmd = [str(wrapper)] if os.name == "nt" else ["sh", str(wrapper)]
        else:
            self.deps.require(["gradle"], purpose="Android build")
            cmd = [which_cmd("gradle") or "gradle"]
        self.run(cmd + ["assembleRelease"], timeout=TIMEOUT_XLONG)
        # [fix] build/ is a normal excluded folder — search it explicitly
        apks = [f for f in find_files_by_ext(self.project, ".apk")
                if self.out.resolve() not in f.parents]
        if not apks:
            raise BuildError("Gradle finished but no APK was found under build/outputs.")
        apk = next((a for a in apks if a.name == "app-release.apk"),
                   max(apks, key=lambda p: p.stat().st_mtime))
        return self.emit([self.copy_to_out(apk)])


class GodotBuilder(BaseBuilder):
    LANG = LangType.GODOT
    TOOL_LABEL = "godot --export-release"
    PRESET_BY_OS = {"windows": "Windows Desktop", "linux": "Linux/X11",
                    "macos": "macOS", "android": "Android"}
    EXT_BY_OS = {"windows": ".exe", "linux": ".x86_64", "macos": ".zip",
                 "android": ".apk"}

    def build(self) -> List[Path]:
        self.check_features()   # the icon is a project setting inside Godot
        self.check_backend()
        presets = self.project / "export_presets.cfg"
        if not presets.exists():
            raise GuidanceBuild(
                "Godot exports require export presets",
                ["Open the project in the Godot editor.",
                 "Project -> Export... -> Add and configure a preset for your target platform.",
                 "Save export_presets.cfg, then re-run PolyBuild."])
        self.deps.require(["godot"], purpose="Godot export")
        wanted = self.PRESET_BY_OS.get(self.cfg.target_os)
        if not wanted:
            raise BuildError(f"Unsupported Godot target: {self.cfg.target_os}")
        names = re.findall(r'^name\s*=\s*"([^"]+)"', read_small_text(presets), re.M)
        preset = next((n for n in names if n.lower() == wanted.lower()), None)
        if preset is None:
            if not names:
                raise BuildError("export_presets.cfg contains no export presets.")
            raise BuildError(f"No preset for '{wanted}'. Available: {', '.join(names)}")
        out = self.out / (self.cfg.name + self.EXT_BY_OS.get(self.cfg.target_os, ""))
        godot = which_cmd("godot") or "godot"
        # Godot 4 uses --export-release; fall back to Godot 3's --export
        proc = run_cmd([godot, "--headless", "--export-release", preset,
                        str(out), "project.godot"],
                       cwd=self.project, timeout=TIMEOUT_XLONG)
        if proc.returncode != 0:
            proc = run_cmd([godot, "--headless", "--export", preset,
                            str(out), "project.godot"],
                           cwd=self.project, timeout=TIMEOUT_XLONG)
            if proc.returncode != 0:
                raise BuildError("Godot export failed:\n"
                                 + tail_lines((proc.stdout or "") + (proc.stderr or ""), 8))
        return self.emit([out])


class GuidanceBuilder(BaseBuilder):
    """For project types that have no CLI build path — emits manual steps."""
    TOOL_LABEL = "Guidance"

    def __init__(self, cfg: BuildConfig, deps: DependencyManager,
                 title: str, steps: List[str]):
        super().__init__(cfg, deps)
        self._title = title
        self._steps = steps

    def build(self) -> List[Path]:
        raise GuidanceBuild(self._title, self._steps)


def _guidance_factory(title: str, steps: List[str]) -> Callable:
    def factory(cfg: BuildConfig, deps: DependencyManager) -> GuidanceBuilder:
        return GuidanceBuilder(cfg, deps, title, steps)
    return factory


GUIDANCE: Dict[LangType, Tuple[str, List[str]]] = {
    LangType.PHP: (
        "PHP is packaged into a self-contained .phar with Box",
        ["Install Box: grab the .phar from"
         " https://github.com/box-project/box/releases"
         " (or: composer require --dev humbug/box).",
         "Create box.json.dist next to your entry file, e.g.:"
         ' {"main": "index.php", "output": "app.phar"}',
         "Run: box compile  — this produces the self-contained app.phar.",
         "Optional native-style launcher: prepend a shebang stub"
         " (#!/usr/bin/env php) to the .phar and mark it executable.",
         "PHP cannot be compiled to a machine-code .exe — the .phar plus a"
         " PHP runtime is the standard distribution model."]),
    LangType.UNITY: (
        "Unity projects are built from the Unity Editor",
        ["Open the project in Unity Hub (Add -> select the project folder).",
         "File -> Build Settings -> add your scenes -> choose the target platform.",
         "Click Build and pick an output folder.",
         "For CI automation use: Unity -batchmode -quit -projectPath <path> "
         "-executeMethod YourBuildScript.Build"]),
    LangType.UNREAL: (
        "Unreal Engine projects are built via UnrealBuildTool",
        ["Right-click the .uproject -> Generate project files (Windows), or run "
         "GenerateProjectFiles.sh (Linux).",
         "Open the generated solution/workspace and build the Shipping config.",
         "Or from the command line: RunUAT BuildCookRun -project=<path>.uproject "
         "-platform=Win64 -build -cook -stage"]),
    LangType.GAMEMAKER: (
        "GameMaker projects are built from the IDE",
        ["Open the .yyp project in GameMaker Studio 2.",
         "Use the Target Manager to pick a platform (Windows VM or YYC).",
         "Create -> Create Executable to produce the .exe."]),
    LangType.RENPY: (
        "Ren'Py games are packaged from the Ren'Py Launcher",
        ["Open the game folder with the Ren'Py Launcher.",
         "Click 'Build Distributions'.",
         "Choose the target (Windows/Linux/macOS/Android/web) and build."]),
    LangType.LUA: (
        "Plain Lua cannot compile to a native executable on its own",
        ["Wrap the game with LÖVE: put a conf.lua next to main.lua and re-run "
         "with --lang love.",
         "Or use srlua / lua2c to glue the interpreter to your script.",
         "Or ship the script together with a Lua interpreter."]),
}

BUILDER_REGISTRY: Dict[LangType, Callable[[BuildConfig, DependencyManager], BaseBuilder]] = {
    LangType.PYTHON: PythonBuilder,
    LangType.NODE: NodeBuilder,
    LangType.ELECTRON: ElectronBuilder,
    LangType.RUBY: RubyBuilder,
    LangType.PERL: PerlBuilder,
    LangType.LOVE: LoveBuilder,
    LangType.C: NativeCBuilder,
    LangType.CPP: NativeCppBuilder,
    LangType.RUST: RustBuilder,
    LangType.GO: GoBuilder,
    LangType.NIM: NimBuilder,
    LangType.ZIG: ZigBuilder,
    LangType.CRYSTAL: CrystalBuilder,
    LangType.DOTNET: DotnetBuilder,
    LangType.JAVA: JavaBuilder,
    LangType.KOTLIN: KotlinBuilder,
    LangType.SCALA: ScalaBuilder,
    LangType.FLUTTER: FlutterBuilder,
    LangType.DART: DartBuilder,
    LangType.ANDROID: AndroidBuilder,
    LangType.GODOT: GodotBuilder,
    LangType.PHP: _guidance_factory(*GUIDANCE[LangType.PHP]),
    LangType.UNITY: _guidance_factory(*GUIDANCE[LangType.UNITY]),
    LangType.UNREAL: _guidance_factory(*GUIDANCE[LangType.UNREAL]),
    LangType.GAMEMAKER: _guidance_factory(*GUIDANCE[LangType.GAMEMAKER]),
    LangType.RENPY: _guidance_factory(*GUIDANCE[LangType.RENPY]),
    LangType.LUA: _guidance_factory(*GUIDANCE[LangType.LUA]),
}

# Languages whose build cannot proceed at all without an explicit entry point
ENTRY_REQUIRED_LANGS = {
    LangType.PYTHON, LangType.NODE, LangType.ELECTRON, LangType.RUBY,
    LangType.PERL, LangType.LOVE, LangType.NIM, LangType.CRYSTAL,
    LangType.DART, LangType.KOTLIN, LangType.SCALA,
}

# [new] Language inference for -s/--src (single-file mode): extension -> language
SRC_EXT_LANG: Dict[str, LangType] = {
    ".py": LangType.PYTHON,
    ".js": LangType.NODE, ".mjs": LangType.NODE, ".cjs": LangType.NODE, ".ts": LangType.NODE,
    ".rb": LangType.RUBY, ".pl": LangType.PERL, ".lua": LangType.LUA,
    ".c": LangType.C, ".cpp": LangType.CPP, ".cc": LangType.CPP, ".cxx": LangType.CPP,
    ".rs": LangType.RUST, ".go": LangType.GO, ".nim": LangType.NIM, ".zig": LangType.ZIG,
    ".cr": LangType.CRYSTAL, ".cs": LangType.DOTNET, ".java": LangType.JAVA,
    ".kt": LangType.KOTLIN, ".scala": LangType.SCALA, ".dart": LangType.DART,
    ".php": LangType.PHP,
}

# ═══════════════════════════════════════════════════════════════════════
# [toolbox compat] language aliases + backend validation
# ═══════════════════════════════════════════════════════════════════════

# toolbox --lang name -> canonical handling. "javascript"/"typescript" are
# resolved smartly (Electron when package.json declares electron, Node otherwise).
LANG_ALIASES: Dict[str, str] = {
    "js": "javascript", "ts": "typescript", "cs": "csharp",
    "csharp": "dotnet", "javascript": "javascript", "typescript": "typescript",
    "node": "node", "dotnet": "dotnet", "php": "php",
}
LANG_CHOICES = ([l.value for l in LangType if l is not LangType.UNKNOWN]
                + ["javascript", "typescript", "csharp"])

# every --backend value Ultimate Toolbox Pro can send (+ the canonical ones)
KNOWN_BACKENDS = {
    "auto", "-", "pyinstaller", "nuitka", "cx_freeze", "jpackage",
    "gradle", "maven", "pkg", "nexe", "electron-builder",
    "gcc", "clang", "g++", "clang++", "cmake", "dotnet", "go",
    "cargo", "flutter", "dart", "ocra", "box",
}


def normalize_lang_choice(value: str, detector: ProjectDetector) -> LangType:
    """Map a --lang value (canonical or toolbox alias) onto a LangType."""
    v = (value or "").strip().lower()
    v = LANG_ALIASES.get(v, v)
    if v in ("javascript", "typescript"):
        # javascript/typescript: honor Electron projects — the toolbox
        # detector reports both under these two names.
        flags = detector._package_json_flags()
        return LangType.ELECTRON if flags.get("electron") else LangType.NODE
    return LangType(v)

# ═══════════════════════════════════════════════════════════════════════════
# 12) CLI — everything driven by flags, zero prompts
# ═══════════════════════════════════════════════════════════════════════════

def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="polybuild",
        description=f"{TOOL_NAME} {VERSION} — turn any project into a native executable.",
        epilog=(
            "examples:\n"
            "  polybuild.py ./myapp -n myapp                     # positional (toolbox style)\n"
            "  polybuild.py -p ./myapp -n myapp\n"
            "  polybuild.py -p . --lang python --onefile --entry src/app.py\n"
            "  polybuild.py -s tools/app.py --icon assets/app.ico\n"
            "  polybuild.py -p . -f --add-data assets:assets --add-data README.md:.\n"
            "  polybuild.py -p . --target windows                # cross-compile (where supported)\n"
            "  polybuild.py -p . --backend nuitka --hidden-import requests\n"
            "  polybuild.py -p . --lang rust --auto-install\n"
            "  polybuild.py --check-tools | --list-langs | --update\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("project_pos", nargs="?", default=None, metavar="PROJECT",
                   help="project directory or single source file (same as -p; "
                        "given first for toolbox compatibility)")
    p.add_argument("-p", "--project", default=".",
                   help="project directory (default: current directory)")
    p.add_argument("-n", "--name", help="output artifact name (default: auto-detected)")
    p.add_argument("-l", "--lang",
                   choices=LANG_CHOICES,
                   help="force the project language instead of auto-detection "
                        "(toolbox aliases accepted: javascript, typescript, csharp)")
    p.add_argument("-e", "--entry", metavar="PATH",
                   help="explicit entry point, relative to the project root")
    p.add_argument("-s", "--src", metavar="FILE",
                   help="build this single source file directly: it becomes the "
                        "entry point, its folder becomes the project root (unless "
                        "-p is given), and the language is inferred from its "
                        "extension (exclusive with -e)")
    p.add_argument("--icon", metavar="PATH",
                   help="application icon: .ico (Windows), .icns (macOS), "
                        ".png (Electron); passed to the toolchain when supported")
    p.add_argument("--add-data", metavar="SRC[:DEST]", action="append",
                   help="bundle extra files/folders into the build; repeatable. "
                        "DEST is inside the bundle (default: the source name). "
                        "Also accepts SRC;DEST — Windows-drive-safe")
    p.add_argument("--hidden-import", metavar="MODULE", dest="hidden_imports",
                   action="append",
                   help="Python only: extra hidden import for PyInstaller "
                        "(--hidden-import) / Nuitka (--include-module); repeatable")
    p.add_argument("-f", "--onefile", action="store_true",
                   help="produce a single file where the toolchain supports it")
    p.add_argument("--noconsole", action="store_true",
                   help="build a windowed app with no console (GUI apps)")
    p.add_argument("--console", action="store_true",
                   help="build a console app (default; cancels --noconsole) — "
                        "toolbox compatibility")
    p.add_argument("-o", "--output-dir", default=DEFAULT_OUTPUT_DIR,
                   help=f"output directory inside the project (default: {DEFAULT_OUTPUT_DIR})")
    p.add_argument("--output", dest="output_dir_alt", default=None, metavar="DIR",
                   help="alias of --output-dir — toolbox compatibility")
    p.add_argument("--target-os", choices=TARGET_OS_CHOICES, default="native",
                   help="target operating system (default: native)")
    p.add_argument("--target", dest="target_os_alt",
                   choices=TARGET_OS_CHOICES + ("host",), default=None,
                   help="alias of --target-os (host = native) — toolbox compatibility")
    p.add_argument("--engine", choices=("pyinstaller", "nuitka"),
                   default="pyinstaller", help="Python build engine")
    p.add_argument("--backend", default=None, metavar="NAME",
                   help="backend hint (toolbox compatibility): auto, pyinstaller, "
                        "nuitka, cx_freeze, jpackage, gradle, maven, pkg, nexe, "
                        "electron-builder, gcc, clang, g++, clang++, cmake, "
                        "dotnet, go, cargo, flutter, dart, ocra, box",)
    p.add_argument("--auto-install", action="store_true",
                   help="install missing build tools automatically (never asks)")
    p.add_argument("--clean", action="store_true",
                   help="delete the output directory before building")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="show full output of build commands")
    p.add_argument("--check-tools", action="store_true",
                   help="show the status of all known tools and exit")
    p.add_argument("--list-langs", action="store_true",
                   help="list supported languages and exit")
    p.add_argument("--update", action="store_true",
                   help="self-update (requires POLYBUILD_* env vars; never asks)")
    p.add_argument("--version", action="version", version=f"{TOOL_NAME} {VERSION}")
    return p.parse_args(argv)


def _run_build_command(args: argparse.Namespace) -> int:
    # [toolbox compat] positional PROJECT (what the toolbox passes) wins over
    # -p/--project; it may be a directory OR a single source file.
    project_raw = getattr(args, "project_pos", None) or args.project
    project = Path(project_raw).expanduser().resolve()
    if not project.exists():
        raise BuildError(f"Project path not found: {project}")

    print_banner()
    SelfUpdater().check_at_startup()

    installer = BaseToolInstaller()
    deps = DependencyManager(installer, auto_install=args.auto_install,
                             verbose=args.verbose)

    # ---- [new] -s/--src: build one specific file ----
    src_file: Optional[Path] = None
    if args.src and getattr(args, "project_pos", None):
        raise BuildError("Use either the positional PROJECT or -s/--src, not both.")
    if args.src:
        if args.entry:
            raise BuildError("Use either -s/--src or -e/--entry, not both.")
        cand = Path(args.src).expanduser()
        cand = cand if cand.is_absolute() else (Path.cwd() / cand)
        cand = cand.resolve()
        if not cand.is_file():
            raise BuildError(f"--src file not found: {args.src}")
        if project_raw == ".":
            # no explicit -p: the file's folder becomes the project root
            project = cand.parent
        try:
            cand.relative_to(project)
        except ValueError:
            raise BuildError(f"--src file must live inside the project directory "
                             f"({project}) — or drop -p and let -s pick its folder.")
        src_file = cand
    elif project.is_file():
        # [toolbox compat] the toolbox may pass a single source file as the
        # positional PROJECT — treat it exactly like -s/--src.
        if args.entry:
            raise BuildError("Pass either a single source file or --entry, not both.")
        src_file = project
        project = project.parent

    # ---- language detection (or forced via --lang, or inferred from --src) ----
    detector = ProjectDetector(project)
    det = detector.detect()
    if args.lang:
        # [toolbox compat] aliases (javascript/typescript/csharp/php) map to
        # canonical LangTypes; javascript/typescript honor Electron projects.
        forced = normalize_lang_choice(args.lang, detector)
        det.lang = forced
        det.confidence = 1.0
        det.evidence = [f"forced via --lang {args.lang}"]
        det.entry_point = detector.detect_entry(forced)
    elif src_file is not None:
        inferred = SRC_EXT_LANG.get(src_file.suffix.lower(), LangType.UNKNOWN)
        if inferred is not LangType.UNKNOWN:
            det.lang, det.confidence = inferred, 1.0
            det.evidence = [f"inferred from --src extension ({src_file.suffix})"]
        # otherwise keep the project detection result as a fallback
    if det.lang is LangType.UNKNOWN:
        top = "; ".join(det.evidence[:3]) or "no recognizable project files"
        raise BuildError(
            f"Could not determine the project language ({top}).\n"
            "Force it explicitly, e.g.:  --lang python    (see --list-langs)")

    # ---- entry point: -s > --entry > detection ----
    entry: Optional[str] = None
    if src_file is not None:
        entry = src_file.relative_to(project).as_posix()
    elif args.entry:
        ep = Path(args.entry)
        if not (project / ep).is_file():
            raise BuildError(f"--entry file not found: {args.entry}")
        entry = ep.as_posix()
    else:
        entry = det.entry_point
        if entry is None and det.lang in ENTRY_REQUIRED_LANGS:
            raise BuildError(
                f"Could not auto-detect an entry point for {det.label}.\n"
                "Pass one explicitly:  --entry <relative/path/to/file>")

    # ---- output name / directory ----
    name = sanitize_name(args.name or det.project_name or project.name)
    # [toolbox compat] --output is an alias of --output-dir
    out_name = getattr(args, "output_dir_alt", None) or args.output_dir
    out_dir = (project / out_name).resolve()
    if out_dir == project:
        # [fix] safety guard: never use the project root as the output folder
        raise BuildError("Refusing to use the project root as the output directory.")
    if args.clean and out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)

    # ---- [new] --icon: verify the file exists (project dir first, then cwd) ----
    icon: Optional[Path] = None
    if args.icon:
        c = Path(args.icon).expanduser()
        for base in (project, Path.cwd()):
            cand = c if c.is_absolute() else (base / c)
            if cand.is_file():
                icon = cand.resolve()
                break
        if icon is None:
            raise BuildError(f"--icon file not found: {args.icon}")

    # ---- [new] --add-data: resolve every spec ----
    add_data: List[Tuple[Path, str]] = []
    if args.add_data:
        add_data = resolve_data_specs(args.add_data, project)

    # ---- [toolbox compat] --backend normalization + validation ----
    backend = (getattr(args, "backend", None) or "auto").strip().lower()
    if backend not in KNOWN_BACKENDS:
        raise BuildError(f"Unknown --backend '{args.backend}'. Known backends:\n"
                         "  " + ", ".join(sorted(KNOWN_BACKENDS - {"-"})))

    # [toolbox compat] --console cancels --noconsole; --target is an alias of
    # --target-os ("host" = native)
    noconsole = args.noconsole and not args.console
    target_arg = (args.target_os if args.target_os != "native"
                  else (getattr(args, "target_os_alt", None) or "native"))

    cfg = BuildConfig(project=project, name=name, onefile=args.onefile,
                      noconsole=noconsole,
                      target_os=resolve_target_os(target_arg),
                      entry=entry, output_dir=out_dir,
                      verbose=args.verbose, engine=args.engine,
                      icon=icon, add_data=add_data,
                      backend=backend,
                      hidden_imports=[h for h in (args.hidden_imports or []) if h.strip()])

    opts = " ".join(filter(None, [
        "onefile" if cfg.onefile else "",
        "noconsole" if cfg.noconsole else "",
        "auto-install" if args.auto_install else "",
        f"backend={cfg.backend}" if cfg.backend not in ("", "auto") else "",
        f"hidden×{len(cfg.hidden_imports)}" if cfg.hidden_imports else "",
    ])) or "default"

    render_box(
        f"Project: {name}",
        [("Language", f"{det.label}  (confidence {det.confidence:.0%})"),
         ("Evidence", "; ".join(det.evidence[:2]) or "-"),
         ("Entry point", entry or "(decided by the build system)"),
         ("Target OS", cfg.target_os + ("  [cross-compile]" if cfg.crossing else "")),
         ("Output dir", str(out_dir)),
         ("Options", opts),
         ("Icon", str(cfg.icon) if cfg.icon else "-"),
         ("Bundle data", f"{len(cfg.add_data)} item(s)" if cfg.add_data else "-")],
        width=68,
    )

    factory = BUILDER_REGISTRY.get(det.lang)
    if factory is None:
        raise BuildError(f"No builder is registered for {det.label}.")
    builder = factory(cfg, deps)

    t0 = time.time()
    info(f"Building with {builder.TOOL_LABEL} ...")
    artifacts = builder.build()
    elapsed = time.time() - t0

    summary_panel(artifacts[0], elapsed, det.label, builder.TOOL_LABEL,
                  extra=[("Artifacts", str(len(artifacts)))]
                        + [("Also", str(a)) for a in artifacts[1:4]])
    ok(f"Done in {elapsed:.1f}s — {len(artifacts)} artifact(s) in {out_dir}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        if args.check_tools:
            installer = BaseToolInstaller()
            if not installer.available():
                warn("No system package manager detected; "
                     "language managers (pip/gem/cpan) may still work.")
            return DependencyManager(installer).check_all()
        if args.list_langs:
            rows = [(l.value, LANG_LABELS[l]) for l in LangType
                    if l is not LangType.UNKNOWN]
            render_box("Supported languages", rows, width=58)
            return 0
        if args.update:
            SelfUpdater().run()
            return 0
        return _run_build_command(args)
    except GuidanceBuild as g:
        err(g.title)
        print()
        for i, step in enumerate(g.steps, 1):
            print(f"  {i}. {step}")
        print()
        hint("This project type has no CLI build path — follow the steps above.")
        return 2
    except BuildError as e:
        err(str(e))
        return 1
    except KeyboardInterrupt:
        err("Interrupted by user.")
        return 130


if __name__ == "__main__":
    sys.exit(main())