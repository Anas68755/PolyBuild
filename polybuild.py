#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PolyBuild Pro v3.1 — أداة سطر أوامر شاملة لتحويل أي مشروع برمجي إلى ملف تنفيذي
أصلي (EXE / APK / Binary) للنظام المُستهدف.

- ملف واحد يعمل على Windows / Linux / macOS مع Python 3.9+ .
- بدون اعتمادات pip إلزامية (Standard Library فقط).
- يكتشف لغة المشروع تلقائيًا (نظام نقاط Confidence)، يثبّت الأدوات الناقصة
  إن أمكن، ويبني عبر Builder مخصص لكل لغة (25+ لغة/بيئة).
- تكامل مع أداة Toolbox كوحدة داخلية عبر subprocess.

الاستخدام السريع:
    python polybuild.py                       # معالج تفاعلي (على TTY)
    python polybuild.py -p ./myapp -n myapp   # كشف تلقائي + بناء
    python polybuild.py -p . --lang python -f # فرض اللغة + onefile
    python polybuild.py --check-tools         # حالة كل الأدوات
    python polybuild.py --update              # تحديث PolyBuild نفسه

متغيرات البيئة:
    POLYBUILD_VERSION_URL  رابط JSON يعلن أحدث إصدار + sha256 + url
    POLYBUILD_UPDATE_URL   رابط تحميل السكربت الجديد (مطلوب مع --update)
    POLYBUILD_NO_UPDATE_CHECK = 1  لتعطيل فحص التحديث عند الإقلاع
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
import select
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
# 2) ثوابت الإصدار والإعدادات
# ═══════════════════════════════════════════════════════════════════════════
TOOL_NAME = "PolyBuild Pro"
VERSION = "3.1.0"
VERSION_TAG = "v3.1"

# مهل صريحة لكل subprocess — لا شيء يعمل بلا حد زمني (متطلب أمان)
TIMEOUT_SHORT = 60      # استعلامات سريعة (--version ...)
TIMEOUT_MED = 180       # خطوات وسيطة (cmake configure ...)
TIMEOUT_LONG = 900      # تجميعات ثقيلة (pyinstaller / cargo / gradle ...)
TIMEOUT_PKG = 600       # تثبيت الحزم (apt / brew / npm / pip ...)

ENV_VERSION_URL = "POLYBUILD_VERSION_URL"
ENV_UPDATE_URL = "POLYBUILD_UPDATE_URL"
ENV_NO_UPDATE_CHECK = "POLYBUILD_NO_UPDATE_CHECK"

# مجلدات تُستثنى من الفحص والرَّدم (بناء/تخزين مؤقت/VCS)
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
MAX_SCAN_FILES = 5000     # سقف حماية أداء أثناء فحص المشاريع الضخمة
MAX_SCAN_DEPTH = 7

# أنظمة الهدف المدعومة لخيار --target-os
TARGET_OS_CHOICES = ("native", "windows", "linux", "macos", "android")

# ═══════════════════════════════════════════════════════════════════════════
# 3) أدوات الطرفية: ألوان ANSI + رموز Unicode + صناديق + طباعة موحّدة
# ═══════════════════════════════════════════════════════════════════════════

def _force_utf8_stdio() -> None:
    """[إصلاح حرج] إعادة تهيئة stdout/stderr إلى UTF-8 عند الإقلاع لتفادي
    انفجار الترميز على Windows (cp1252/cp437) مع الرموز والعربية."""
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
    """كشف دعم الطرفية للألوان — يحترم NO_COLOR ويدعم Windows Terminal/VT."""
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
        # [إصلاح حرج] تمكين VT100 على conhost عبر SetConsoleMode بدل os.system("")
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
    """هل يمكن ترميز رموزنا على التدفق الحالي؟ وإلا نرجع لبدائل ASCII."""
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
    """[*] معلومات — أزرق."""
    _emit("[*]", Ansi.BLUE, msg)


def ok(msg: str) -> None:
    """[✓] نجاح — أخضر."""
    _emit(f"[{SYM['ok']}]", Ansi.GREEN, msg)


def warn(msg: str) -> None:
    """[!] تحذير — أصفر."""
    _emit("[!]", Ansi.YELLOW, msg)


def err(msg: str) -> None:
    """[✗] خطأ — أحمر (على stderr)."""
    _emit(f"[{SYM['err']}]", Ansi.RED, msg, stream=sys.stderr)


def hint(msg: str) -> None:
    """[i] تلميح — سماوي."""
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
    """طول العرض التقريبي — يعتبر كل الحروف خلية واحدة (يكفي لمحاذاتنا)."""
    return len(s)


def _pad(s: str, width: int) -> str:
    gap = width - _visible_len(s)
    return s + (" " * gap if gap > 0 else "")


def _clip(s: str, width: int) -> str:
    if _visible_len(s) <= width:
        return s
    return s[: max(0, width - 1)] + "…"


def render_box(title: str = "", rows: Optional[List[Any]] = None, width: int = 62) -> None:
    """صندوق Unicode: rows عناصرها إمّا سطر نص حر أو tuple(label, value)."""
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
        print(paint(v, Ansi.CYAN) + paint(_pad(line, inner), Ansi.WHITE) + paint(v, Ansi.CYAN))
    print(edge(_box_char("bl"), _box_char("br")))


def summary_panel(file_path: Path, elapsed: float, lang_label: str,
                  tool_label: str, extra: Optional[List[Tuple[str, str]]] = None) -> None:
    """شريط الملخص النهائي: الملف/الحجم/الزمن/اللغة."""
    is_dir = file_path.is_dir()
    if is_dir:
        total = sum(f.stat().st_size for f in file_path.rglob("*") if f.is_file())
    else:
        total = file_path.stat().st_size if file_path.exists() else 0
    rows: List[Tuple[str, str]] = [
        ("اسم الملف", file_path.name),
        ("المسار", str(file_path)),
        ("الحجم", human_size(total) + (" (مجلد)" if is_dir else "")),
        ("الزمن", f"{elapsed:.1f}s"),
        ("اللغة", lang_label),
        ("الأداة", tool_label),
    ]
    for kv in (extra or []):
        rows.append(kv)
    render_box(f"{TOOL_NAME} {VERSION_TAG} — البناء اكتمل بنجاح", rows, width=70)


def print_banner() -> None:
    render_box(
        f"{TOOL_NAME} {VERSION_TAG}  ({VERSION})",
        ["من أي مشروع برمجي " + SYM["arrow"] + " ملف تنفيذي أصلي",
         "25+ لغة | كشف تلقائي | تثبيت التبعيات | بدون اعتمادات pip"],
        width=62,
    )

# ═══════════════════════════════════════════════════════════════════════════
# 4) أدوات Helpers عامة
# ═══════════════════════════════════════════════════════════════════════════

class BuildError(Exception):
    """فشل بناء — تُعرض رسالتها للمستخدم كما هي (بالأحمر)."""


class GuidanceBuild(Exception):
    """لغات "كشف بدون بناء" (Unity/Unreal/...) — تعرض إرشادات بدل البناء."""

    def __init__(self, title: str, steps: List[str]):
        super().__init__(title)
        self.title = title
        self.steps = steps


class UserCancel(Exception):
    """ألغى المستخدم من المعالج التفاعلي (Esc / Ctrl+C)."""


def which_cmd(tool: str, refresh: bool = False) -> Optional[str]:
    """shutil.which مع cache — refresh لإجبار إعادة الفحص بعد تثبيت الأب."""
    cache: Dict[str, Optional[str]] = getattr(which_cmd, "_cache", {})
    if refresh:
        cache.pop(tool, None)
    if tool not in cache:
        cache[tool] = shutil.which(tool)
        which_cmd._cache = cache  # type: ignore[attr-defined]
    return cache[tool]


def invalidate_tool_cache(*tools: str) -> None:
    """إبطاء الكاش بعد أي تثبيت — لا نفترض أن الابن (npm) جاء مع الأب (node)."""
    if not tools:
        getattr(which_cmd, "_cache", {}).clear()
        return
    for t in tools:
        which_cmd(t, refresh=True)


def exe_ext(target_os: str) -> str:
    """امتداد الملف التنفيذي حسب النظام الهدف."""
    return ".exe" if target_os == "windows" else ""


def current_os() -> str:
    """نظام الاستضافة الحالي بصيغة موحّدة."""
    return {"Windows": "windows", "Darwin": "macos"}.get(platform.system(), "linux")


def resolve_target_os(target_os: str) -> str:
    """native = نفس نظام الاستضافة."""
    return current_os() if target_os in ("native", "", None) else target_os


def version_tuple(v: str) -> Tuple[int, ...]:
    nums = re.findall(r"\d+", v or "")
    return tuple(int(x) for x in nums[:4]) if nums else (0,)


def is_newer_version(candidate: str, current: str) -> bool:
    """مقارنة إصدارات مرنة (3.1.0 مقابل 3.1 مقابل v3.1.1 ...)."""
    a, b = version_tuple(candidate), version_tuple(current)
    n = max(len(a), len(b))
    a += (0,) * (n - len(a))
    b += (0,) * (n - len(b))
    return a > b


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_hex_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0
    return f"{n:.1f} TB"


def sanitize_name(name: str) -> str:
    """اسم مخرج آمن: مسافات → _ وقص الرموز الخطرة."""
    name = re.sub(r"[^\w.\-]+", "_", (name or "").strip())
    return name.strip("._") or "app"


def read_small_text(path: Path, limit: int = 512 * 1024) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except Exception:
        return ""


def tail_lines(text: str, n: int = 6) -> str:
    """آخر n سطر غير فارغة — لعرض أخطاء الأدوات الخارجية باقتضاب."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    keep = lines[-n:]
    return "\n".join("    " + ln.strip()[:180] for ln in keep)


def run_cmd(cmd: Sequence[str], cwd: Optional[Path] = None,
            timeout: int = TIMEOUT_MED, env: Optional[Dict[str, str]] = None,
            capture: bool = True) -> subprocess.CompletedProcess:
    """[إصلاح حرج] نقطة تنفيذ واحدة لكل العمليات الخارجية:
    timeout صريح دائمًا + text=True + errors='replace' (لا UnicodeDecodeError
    من أدوات خارجية) + لا shell=True إطلاقًا."""
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
        raise BuildError(f"انتهت المهلة ({timeout}s) أثناء تنفيذ: {cmd[0]} …")
    except FileNotFoundError:
        raise BuildError(f"الأداة غير موجودة: {cmd[0]} — ثبّتها أولًا أو حدّد مسارها.")
    except PermissionError:
        raise BuildError(f"لا صلاحية لتنفيذ: {cmd[0]}")


def collect_files(root: Path, extra_excludes: Optional[set] = None,
                  max_files: int = MAX_SCAN_FILES,
                  max_depth: int = MAX_SCAN_DEPTH) -> List[Path]:
    """ردم المشروع مع استثناء مجلدات البناء/الكاش — بسقوف حماية للأداء."""
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

# ═══════════════════════════════════════════════════════════════════════════
# 5) الأنواع: LangType + DetectedProject
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
    UNITY = "unity"
    UNREAL = "unreal"
    GAMEMAKER = "gamemaker"
    RENPY = "renpy"
    UNKNOWN = "unknown"


# تسميات عربية للعرض في القوائم والملخصات
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
    LangType.UNITY: "Unity",
    LangType.UNREAL: "Unreal Engine",
    LangType.GAMEMAKER: "GameMaker",
    LangType.RENPY: "Ren'Py",
    LangType.UNKNOWN: "غير معروفة",
}


@dataclass
class DetectedProject:
    """نتيجة الكشف التلقائي عن المشروع."""
    lang: LangType = LangType.UNKNOWN
    confidence: float = 0.0          # 0..1
    score: int = 0                   # النقاط الخام
    evidence: List[str] = field(default_factory=list)
    entry_point: Optional[str] = None
    project_name: Optional[str] = None

    @property
    def label(self) -> str:
        return LANG_LABELS.get(self.lang, self.lang.value)

# ═══════════════════════════════════════════════════════════════════════════
# 6) نظام التحديث الذاتي — تحقق ثلاثي + rollback
# ═══════════════════════════════════════════════════════════════════════════

class SelfUpdater:
    """يحمّل نسخة أحدث من السكربت نفسه ويتحقق ثلاثيًا قبل الاستبدال:
      1) مطابقة SHA-256 مع ما يعلنه السيرفر (لا ثقة بمحتوى بلا hash صريح).
      2) ast.parse() للسلامة النحوية.
      3) py_compile.compile() لقابلية الترجمة.
    أي فشل → استرجاع تلقائي من نسخة .backup."""

    BACKUP_SUFFIX = ".backup"

    def __init__(self, assume_yes: bool = False):
        self.assume_yes = assume_yes
        self.target = Path(os.path.abspath(__file__))

    # ---------- الشبكة ----------
    def _fetch(self, url: str, timeout: int = TIMEOUT_MED) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": f"{TOOL_NAME}/{VERSION}"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()

    def latest_info(self) -> Optional[Dict[str, str]]:
        """يتوقع JSON: {"version": "...", "sha256": "...", "url": "..."} —
        أو نصًا بسيطًا يحمل رقم الإصدار فقط (بدون hash)."""
        url = os.environ.get(ENV_VERSION_URL, "").strip()
        if not url:
            return None
        try:
            raw = self._fetch(url, timeout=10)
        except Exception as e:
            warn(f"تعذّر فحص التحديث: {e.__class__.__name__}")
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

    # ---------- التحقق ----------
    def _verify(self, content: bytes, declared_sha: str) -> None:
        if not declared_sha:
            # [إصلاح أمني] لا نثق بأي محتوى بدون hash صريح من السيرفر
            raise BuildError("السيرفر لا يعلن SHA-256 للمحتوى — رفض التحديث لأسباب أمنية.")
        actual = sha256_bytes(content)
        if not hmac.compare_digest(actual, declared_sha.strip().lower()):
            raise BuildError(f"عدم تطابق SHA-256!\n  المعلن : {declared_sha}\n  الفعلي : {actual}")
        try:
            ast.parse(content.decode("utf-8", errors="strict"), filename="update.py")
        except SyntaxError as e:
            raise BuildError(f"المحتوى المُحمّل ليس كود Python سليمًا (ast): {e}")
        tmp_src = Path(tempfile.gettempdir()) / f"polybuild_check_{os.getpid()}.py"
        try:
            tmp_src.write_bytes(content)
            import py_compile
            py_compile.compile(str(tmp_src), cfile=str(tmp_src) + "c", doraise=True)
        except Exception as e:
            raise BuildError(f"فشل الترجمة التجريبية للتحديث (py_compile): {e}")
        finally:
            try:
                tmp_src.unlink(missing_ok=True)
                Path(str(tmp_src) + "c").unlink(missing_ok=True)
            except Exception:
                pass

    # ---------- الاستبدال + Rollback ----------
    def _replace(self, content: bytes) -> None:
        backup = self.target.with_name(self.target.name + self.BACKUP_SUFFIX)
        try:
            shutil.copy2(self.target, backup)
            tmp = self.target.with_name(self.target.name + ".new")
            tmp.write_bytes(content)
            os.chmod(tmp, stat.S_IMODE(os.stat(self.target).st_mode) | stat.S_IXUSR)
            os.replace(tmp, self.target)
            # تحقق نهائي: النسخة الجديدة قابلة للترجمة فعلًا على مكانها
            import py_compile
            py_compile.compile(str(self.target), cfile=str(tmp) + "c", doraise=True)
        except Exception as e:
            if backup.exists():
                shutil.copy2(backup, self.target)   # rollback تلقائي
            raise BuildError(f"فشل الاستبدال — تمت الاستعادة من .backup: {e}")
        finally:
            try:
                Path(str(tmp) + "c").unlink(missing_ok=True)
            except Exception:
                pass

    # ---------- الواجهات ----------
    def check_at_startup(self) -> None:
        """فحص صامت سريع عند الإقلاع — لا يعطل التشغيل أبدًا."""
        if os.environ.get(ENV_NO_UPDATE_CHECK) == "1":
            return
        info_ = self.latest_info()
        if info_ and info_.get("version") and is_newer_version(info_["version"], VERSION):
            hint(f"يتوفر إصدار أحدث ({info_['version']}). شغّل: python {self.target.name} --update")

    def run(self) -> None:
        if not (os.environ.get(ENV_VERSION_URL) or os.environ.get(ENV_UPDATE_URL)):
            raise BuildError(
                "حدّد " + ENV_VERSION_URL + " و" + ENV_UPDATE_URL +
                " قبل التحديث.\n  مثال: POLYBUILD_VERSION_URL=https://host/version.json"
            )
        info("جارٍ فحص أحدث إصدار …")
        info_ = self.latest_info() or {}
        latest = info_.get("version", "")
        if not latest:
            raise BuildError("تعذّر معرفة أحدث إصدار من " + ENV_VERSION_URL)
        if not is_newer_version(latest, VERSION):
            ok(f"أنت تستخدم أحدث إصدار ({VERSION}).")
            return
        url = info_.get("url") or os.environ.get(ENV_UPDATE_URL, "")
        if not url:
            raise BuildError("لا يوجد رابط تحميل (" + ENV_UPDATE_URL + " أو url من JSON الإصدار).")
        info(f"تحميل الإصدار {latest} …")
        content = self._fetch(url, timeout=TIMEOUT_LONG)
        self._verify(content, info_.get("sha256", ""))
        ok("التحقق الثلاثي ناجح (SHA-256 + ast + py_compile).")
        if not self.assume_yes and sys.stdin.isatty():
            ans = input(paint("تثبيت الإصدار الجديد الآن؟ [Y/n]: ", Ansi.BOLD)).strip().lower()
            if ans in ("n", "no"):
                info("أُلغي التحديث.")
                return
        self._replace(content)
        ok(f"تم التحديث إلى {latest}. أعد التشغيل لتطبيق التغييرات.")

# ═══════════════════════════════════════════════════════════════════════════
# 7) BaseToolInstaller — كشف مدير الحزم وتثبيت الأدوات
# ═══════════════════════════════════════════════════════════════════════════

class BaseToolInstaller:
    """يكشف مدير الحزم المتاح (apt/brew/winget/choco + pip/gem/cpan) ويثبّت الأدوات.
    لا يستخدم sudo إلا عند الحاجة الفعلية."""

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
        # مديرات لغوية إضافية تعمل في كل الأنظمة
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
        # [إصلاح] لا sudo إلا عند الحاجة: apt خارج الجذر فقط
        if self.manager != "apt":
            return False
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            return False
        return which_cmd("sudo") is not None

    def install(self, packages: Dict[str, Any]) -> bool:
        """packages: {manager: pkg أو [pkgs]} — يجرب مدير النظام ثم مديرات اللغات
        (pip/gem/cpan) كاحتياط — timeout صريح دائمًا."""
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
            base = ["brew", "install"] + pkgs
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
        info(f"تثبيت {' '.join(pkgs)} عبر {mgr} …")
        proc = run_cmd(base, timeout=TIMEOUT_PKG)
        if proc.returncode != 0:
            warn("فشل التثبيت التلقائي — استخدم أمر التثبيت اليدوي أدناه.")
        return proc.returncode == 0

    def hint(self, packages: Dict[str, Any]) -> str:
        """أمر تثبيت يدوي واضح — بالمديرات المنطقية لهذا النظام فقط."""
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
        return "ثبّت الأداة يدويًا من موقعها الرسمي."


# سجل الحزم لكل أداة: {tool: {manager: pkg([s])}}
TOOL_PACKAGES: Dict[str, Dict[str, Any]] = {
    "pyinstaller": {"pip": "pyinstaller"},
    "nuitka": {"pip": "nuitka", "brew": "nuitka"},
    "node": {"apt": ["nodejs", "npm"], "brew": "node",
             "winget": "OpenJS.NodeJS.LTS", "choco": "nodejs-lts"},
    "npm": {},                                   # مُجمّعة مع node — تُعاد فحصها
    "npx": {},                                   # مُجمّعة مع node
    "ruby": {"apt": "ruby", "brew": "ruby", "choco": "ruby"},
    "gem": {},                                   # مُجمّعة مع ruby
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
# 8) DependencyManager — فحص/تثبيت الأدوات مع cache ومعالجة الأدوات المُجمّعة
# ═══════════════════════════════════════════════════════════════════════════

# أدوات تُثبَّت ضمن أداة أب (الأب ← الابن)
BUNDLED_WITH: Dict[str, str] = {"npm": "node", "npx": "node", "gem": "ruby",
                                "cpan": "perl", "jar": "javac", "jpackage": "javac",
                                "pp": "perl"}

# أدوات تُستدعى عبر npx بدل تثبيت عالمي
NPX_TOOLS = {"pkg", "electron-builder"}


class DependencyManager:
    def __init__(self, installer: BaseToolInstaller, assume_yes: bool = False,
                 quick: bool = False, verbose: bool = False):
        self.installer = installer
        self.assume_yes = assume_yes
        self.quick = quick
        self.verbose = verbose
        self._cache: Dict[str, bool] = {}

    def _may_install(self) -> bool:
        """التثبيت التلقائي: بموافقة صريحة (-y) أو على TTY تفاعلي.
        في CI بدون TTY وبدون -y → نكتفي برسالة التثبيت اليدوي (سلوك آمن)."""
        if not self.installer.available():
            return False
        if self.assume_yes:
            return True
        try:
            return sys.stdin.isatty()
        except Exception:
            return False

    def ensure(self, tool: str, purpose: str = "") -> bool:
        """ضمان توفر أداة: فحص → تثبيت إن أمكن → [إصلاح حرج] إعادة فحص فعلي
        (لا افتراض نجاح) → رسالة تثبيت يدوي عند الفشل."""
        if tool in self._cache:
            return self._cache[tool]
        if NPX_TOOLS & {tool}:
            # يعمل عبر npx — يكفي وجود node/npm
            return self.ensure("node") and self.ensure("npx")
        parent = BUNDLED_WITH.get(tool)
        if parent and not which_cmd(tool):
            # الأداة مُجمّعة مع أب غائب → ثبّت الأب أولًا
            self.ensure(parent)
            # [إصلاح حرج] إعادة فحص الابن بعد الأب — لا نفترض نجاحه
            found = which_cmd(tool, refresh=True) is not None
            self._cache[tool] = found
            return found
        if which_cmd(tool):
            self._cache[tool] = True
            return True
        packages = TOOL_PACKAGES.get(tool, {})
        ok_install = False
        if packages and self._may_install():
            ok_install = self.installer.install(packages)
            invalidate_tool_cache()           # أي تثبيت يبطّل الكاش كاملًا
        found = which_cmd(tool, refresh=True) is not None
        self._cache[tool] = found
        if not found:
            hint(f"الأداة '{tool}' مطلوبة {'لـ' + purpose if purpose else ''} "
                 f"وليست مثبتة. التثبيت اليدوي:\n    {self.installer.hint(packages)}")
        return found

    def require(self, tools: Sequence[str], purpose: str = "") -> None:
        """مثل ensure لكن يجمّع المفقود ويُفشل البناء برسالة واحدة واضحة."""
        missing: List[str] = []
        for t in tools:
            if not self.ensure(t, purpose=purpose):
                missing.append(t)
        if missing:
            lines = [f"  - {t} → {self.installer.hint(TOOL_PACKAGES.get(t, {}))}" for t in missing]
            raise BuildError("أدوات مفقودة لا يمكن الاستمرار بدونها:\n" + "\n".join(lines))

    def reset_cache(self) -> None:
        self._cache.clear()

    def check_all(self) -> int:
        """--check-tools: جدول حالة كل الأدوات المعروفة."""
        catalog = [
            ("python", "مفسّر Python 3.9+ (مطلوب)"),
            ("pyinstaller", "بناء Python — PyInstaller"),
            ("nuitka", "بناء Python — Nuitka (اختياري)"),
            ("node", "منصة Node.js"), ("npm", "مدير حزم Node"),
            ("ruby", "مفسّر Ruby"), ("ocra", "تغليف Ruby (Windows)"),
            ("perl", "مفسّر Perl"), ("pp", "PAR::Packer لـ Perl"),
            ("love", "محرك LÖVE (Lua)"),
            ("gcc", "مترجم C"), ("g++", "مترجم C++"), ("cmake", "نظام بناء CMake"),
            ("make", "GNU Make"), ("x86_64-w64-mingw32-gcc", "MinGW للـ cross إلى Windows"),
            ("cargo", "Rust — Cargo"), ("rustup", "مدير أدوات Rust"),
            ("go", "مترجم Go"), ("nim", "مترجم Nim"), ("zig", "مترجم Zig"),
            ("crystal", "مترجم Crystal"), ("dotnet", ".NET SDK"),
            ("java", "Java Runtime"), ("javac", "مترجم Java"), ("jar", "أداة JAR"),
            ("jpackage", "تغليف Java أصلي"), ("mvn", "Maven"), ("gradle", "Gradle"),
            ("kotlinc", "مترجم Kotlin"), ("scala-cli", "Scala CLI"), ("sbt", "Scala Build Tool"),
            ("flutter", "Flutter SDK"), ("dart", "Dart SDK"),
            ("godot", "محرك Godot"),
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
            rows.append((("✓ " if found else "✗ ") + tool, label))
        render_box(f"حالة الأدوات ({len(catalog) - missing}/{len(catalog)} متوفرة)", rows, width=72)
        if missing:
            hint("ثبّت ما يلزم لمشاريعك فقط — ليست كل الأدوات ضرورية لكل لغة.")
        return 0

# ═══════════════════════════════════════════════════════════════════════════
# 9) ProjectDetector — كشف لغة المشروع بنظام نقاط (Confidence Score)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Rule:
    lang: LangType
    weight: int                    # نقاط عالية لملفات البناء، متوسطة للمصادر
    names: Tuple[str, ...]         # أسماء ملفات دقيقة أو امتدادات ".py"
    kind: str = "file"             # file | ext | dir
    label: str = ""                # وصف الدليل


# قواعد الملفات/المجلدات — نقاط عالية (ملفات البناء أقوى دليل)
_BUILD_RULES: List[Rule] = [
    Rule(LangType.PYTHON, 40, ("setup.py", "setup.cfg"), label="ملف تثبيت Python"),
    Rule(LangType.PYTHON, 35, ("pyproject.toml", "requirements.txt", "Pipfile"), label="ملف تبعيات Python"),
    Rule(LangType.PYTHON, 30, ("main.py", "app.py", "run.py", "cli.py", "__main__.py"), label="نقطة دخول Python"),
    Rule(LangType.NODE, 45, ("package.json",), label="package.json"),
    Rule(LangType.ELECTRON, 65, ("package.json",), label="package.json مع اعتماد electron"),
    Rule(LangType.RUST, 60, ("Cargo.toml",), label="Cargo.toml"),
    Rule(LangType.GO, 60, ("go.mod",), label="go.mod"),
    Rule(LangType.CPP, 50, ("CMakeLists.txt",), label="CMakeLists.txt"),
    Rule(LangType.CPP, 30, ("Makefile", "makefile", "GNUmakefile"), label="Makefile"),
    Rule(LangType.NIM, 60, ("main.nim", "app.nim"), label="نقطة دخول Nim"),
    Rule(LangType.ZIG, 60, ("build.zig",), label="build.zig"),
    Rule(LangType.CRYSTAL, 60, ("shard.yml",), label="shard.yml"),
    Rule(LangType.DOTNET, 60, (".csproj", ".vbproj", ".fsproj"), kind="ext", label="مشروع .NET"),
    Rule(LangType.DOTNET, 55, (".sln",), kind="ext", label="حل .NET"),
    Rule(LangType.JAVA, 55, ("pom.xml",), label="Maven pom.xml"),
    Rule(LangType.JAVA, 40, ("build.gradle", "build.gradle.kts"), label="Gradle build"),
    Rule(LangType.JAVA, 25, ("gradlew", "gradlew.bat"), label="Gradle wrapper"),
    Rule(LangType.ANDROID, 65, ("AndroidManifest.xml",), label="AndroidManifest.xml"),
    Rule(LangType.KOTLIN, 45, ("build.gradle.kts",), label="Gradle Kotlin DSL"),
    Rule(LangType.SCALA, 60, ("build.sbt",), label="build.sbt"),
    Rule(LangType.FLUTTER, 60, ("pubspec.yaml",), label="pubspec.yaml"),   # يُنفّص محتواه لاحقًا
    Rule(LangType.DART, 40, ("pubspec.yaml",), label="pubspec.yaml"),
    Rule(LangType.RUBY, 40, ("Gemfile",), label="Gemfile"),
    Rule(LangType.PERL, 35, ("Makefile.PL", "Build.PL"), label="بناء Perl"),
    Rule(LangType.LUA, 35, ("main.lua",), label="main.lua"),
    Rule(LangType.LOVE, 55, ("conf.lua",), label="مشروع LÖVE (conf.lua)"),
    Rule(LangType.GODOT, 70, ("project.godot",), label="project.godot"),
    Rule(LangType.UNREAL, 75, (".uproject",), kind="ext", label="ملف Unreal"),
    Rule(LangType.GAMEMAKER, 70, (".yyp", ".project.gmx"), kind="ext", label="مشروع GameMaker"),
    Rule(LangType.RENPY, 65, ("options.rpy", "script.rpy"), label="سكربت Ren'Py"),
]

# قواعد الامتدادات — نقاط متوسطة (ملفات المصدر)
_SRC_RULES: List[Tuple[LangType, int, int, Tuple[str, ...]]] = [
    # (اللغة، نقاط أول ملف، نقاط لكل ملف إضافي حتى السقف، الامتدادات)
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
    """نظام نقاط: ملفات البناء نقاط عالية، المصادر متوسطة، المجلدات الدالة عالية.
    عند التعادل يفوز الأعلى نقاطًا؛ عند فشل كامل → UNKNOWN مع نصيحة --lang."""

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

    # ---------- قواعد خاصة بالمحتوى ----------
    def _package_json_flags(self) -> Dict[str, bool]:
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

    # ---------- التنفيذ ----------
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

        # 1) ملفات البناء — نقاط عالية (لكل قاعدة مرة واحدة)
        for rule in _BUILD_RULES:
            if rule.lang is LangType.FLUTTER and not self._pubspec_is_flutter():
                continue
            if rule.lang is LangType.DART and self._pubspec_is_flutter():
                continue
            if rule.lang is LangType.ELECTRON and not self._package_json_flags().get("electron"):
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

        # 2) قاعدة Unity (مجلدات Assets + ProjectSettings) — نقاط عالية
        if self._unity_like():
            add(LangType.UNITY, 75, "مجلدا Assets + ProjectSettings")
        if self._renpy_like():
            add(LangType.RENPY, 55, "مجلد game/ مع سكربتات .rpy")

        # 3) مصادر — نقاط متوسطة مع سقف لكل لغة
        for lang, first, per, exts in _SRC_RULES:
            count = sum(1 for f in rel if f.suffix.lower() in exts)
            if count:
                pts = first + min(count - 1, 5) * per
                add(lang, pts, f"{count} ملف {exts[0]}")

        # 4) تحسينات فصل الحالات المتقاربة
        flags = self._package_json_flags()
        if flags.get("electron"):
            add(LangType.ELECTRON, 20, "اعتماد electron في package.json")
        self._refine_gradle_family(add)
        self._refine_c_family(add)

        if not scores:
            return det
        best = max(scores.items(), key=lambda kv: kv[1])
        det.lang, det.score = best[0], best[1]
        det.confidence = min(1.0, det.score / 80.0)
        det.evidence = evidence.get(det.lang, [])
        det.project_name = self._guess_name(flags.get("name") or "")
        det.entry_point = self.detect_entry(det.lang)
        if det.score < self.THRESHOLD:
            det.lang, det.confidence, det.score = LangType.UNKNOWN, 0.0, det.score
        return det

    def _refine_gradle_family(self, add: Callable[[LangType, int, str], None]) -> None:
        """Gradle بلا Manifest = جافا/كوتلن/سكالا — يفوز الأكثر ملفات مصدر."""
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
            add(best[0], 25, f"Gradle + {best[1]} ملف {best[0].value}")

    def _refine_c_family(self, add: Callable[[LangType, int, str], None]) -> None:
        """CMake/Make بلا تحديد: يقارن مصادر .c مقابل .cpp ليختار C أو C++."""
        rel = rel_files(self.files, self.project)
        c_n = sum(1 for f in rel if f.suffix in (".c",))
        cpp_n = sum(1 for f in rel if f.suffix in (".cpp", ".cc", ".cxx"))
        if c_n and cpp_n:
            add(LangType.CPP, 10, f"مزيج C/C++ (cpp: {cpp_n}, c: {c_n})")
        elif c_n:
            add(LangType.C, 15, f"{c_n} ملف .c")
        elif cpp_n:
            add(LangType.CPP, 15, f"{cpp_n} ملف .cpp")

    def _guess_name(self, fallback: str = "") -> str:
        if fallback:
            return fallback
        # Godot?  Cargo?  وإلا اسم المجلد
        godot = self.project / "project.godot"
        name = regex_first(read_small_text(godot), r'config/name\s*=\s*"([^"]+)"')
        if name:
            return name
        cargo = self.project / "Cargo.toml"
        name = regex_first(read_small_text(cargo), r'^\s*name\s*=\s*"([^"]+)"')
        if name:
            return name
        return sanitize_name(self.project.resolve().name)

    # ---------- نقطة الدخول لكل لغة ----------
    def detect_entry(self, lang: LangType) -> Optional[str]:
        if not self.files:
            # [إصلاح] يُستدعى أحيانًا قبل detect() من داخل البنّائين — امسح أولًا
            self._scan()
        rel = rel_files(self.files, self.project)
        names = {f.name: f for f in rel}
        root_files = [f for f in rel if len(f.parts) == 1]

        def first_existing(cands: Sequence[str]) -> Optional[str]:
            for c in cands:
                if c in names:
                    return str(names[c])
            return None

        if lang is LangType.PYTHON:
            entry = first_existing(("main.py", "app.py", "run.py", "cli.py", "__main__.py"))
            if entry:
                return entry
            pys = [f for f in root_files if f.suffix == ".py" and f.name != "setup.py"]
            return str(pys[0]) if len(pys) == 1 else None
        if lang in (LangType.NODE, LangType.ELECTRON):
            main = self._package_json_flags().get("main")
            if main and (self.project / main).exists():
                return main
            return first_existing(("index.js", "main.js", "app.js", "src/index.js", "src/main.js"))
        if lang is LangType.GO:
            return first_existing(("main.go", "cmd/main.go", "src/main.go"))
        if lang is LangType.CPP:
            return first_existing(("main.cpp", "src/main.cpp", "src/main.cc"))
        if lang is LangType.C:
            return first_existing(("main.c", "src/main.c"))
        if lang is LangType.NIM:
            return first_existing(("main.nim", "src/main.nim", "app.nim"))
        if lang is LangType.ZIG:
            return first_existing(("main.zig", "src/main.zig"))
        if lang is LangType.CRYSTAL:
            return first_existing(("src/main.cr", "main.cr"))
        if lang is LangType.DART:
            return first_existing(("bin/main.dart", "main.dart", "lib/main.dart"))
        if lang is LangType.LUA or lang is LangType.LOVE:
            return first_existing(("main.lua", "src/main.lua"))
        if lang is LangType.RUBY:
            return first_existing(("main.rb", "app.rb", "bin/main.rb"))
        if lang is LangType.PERL:
            return first_existing(("main.pl", "app.pl", "script.pl"))
        if lang in (LangType.JAVA, LangType.KOTLIN, LangType.SCALA):
            # بحث محدود عن ملف يحتوي main — أداء آمن بسقوف
            markers = {"java": "static void main", "kotlin": "fun main",
                       "scala": "def main"}[lang.value]
            for f in self.files[:400]:
                if f.suffix in (".java", ".kt", ".scala") and markers in read_small_text(f, 65536):
                    try:
                        return str(f.relative_to(self.project))
                    except ValueError:
                        continue
            return None
        return None

# ═══════════════════════════════════════════════════════════════════════════
# 10) BaseBuilder — الواجهة الموحّدة لكل البناؤين
# ═══════════════════════════════════════════════════════════════════════════

class BaseBuilder:
    """كل Builder يطبّق نفس الواجهة:
      build()        → مسار الملف الناتج
      _require()     → فحص/تثبيت الأدوات المطلوبة
      _run()         → تنفيذ subprocess مع timeout
      _print_result()→ طباعة موحّدة
    """

    LANG = LangType.UNKNOWN
    TITLE = "?"       # اسم الأداة المستخدمة للعرض في الملخص

    def __init__(self, opts: argparse.Namespace, deps: DependencyManager):
        self.opts = opts
        self.deps = deps
        self.t0 = time.monotonic()
        self.project: Path = Path(opts.project).expanduser().resolve()
        self.target_os: str = resolve_target_os(getattr(opts, "target_os", "native"))
        self.out: Path = self._resolve_out()
        self.name: str = sanitize_name(getattr(opts, "name", "") or self.project.name)
        self.entry: Optional[str] = getattr(opts, "script", None) or None
        self.icon: Optional[str] = getattr(opts, "icon", None) or None
        self._tmp_dirs: List[Path] = []

    # ---------- إعداد المسارات ----------
    def _resolve_out(self) -> Path:
        out = Path(getattr(self.opts, "output", "") or DEFAULT_OUTPUT_DIR)
        if not out.is_absolute():
            out = Path(self.opts.project).expanduser().resolve() / out
        return out

    def _ensure_out(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)

    def _tmp(self, label: str) -> Path:
        """مجلد عمل مؤقت داخل المشروع — يُنظّف دائمًا في finally."""
        d = self.project / ".polybuild_tmp" / label
        d.mkdir(parents=True, exist_ok=True)
        self._tmp_dirs.append(d)
        return d

    def _cleanup(self) -> None:
        # [إصلاح] تنظيف ملفات مؤقتة دائمًا — ولا كتابة خارج المشروع/dist أصلًا
        for d in self._tmp_dirs:
            shutil.rmtree(d, ignore_errors=True)

    # ---------- النقاط الأساسية ----------
    def _require(self, tools: Sequence[str], purpose: str = "") -> None:
        self.deps.require(tools, purpose=purpose or self.LANG.value)

    def _run(self, cmd: Sequence[str], cwd: Optional[Path] = None,
             timeout: int = TIMEOUT_LONG, env: Optional[Dict[str, str]] = None,
             fatal: bool = True) -> subprocess.CompletedProcess:
        if self.opts.verbose:
            dim("    $ " + " ".join(str(c) for c in cmd))
        proc = run_cmd(cmd, cwd=cwd or self.project, timeout=timeout, env=env)
        if fatal and proc.returncode != 0:
            details = tail_lines((proc.stderr or "") + "\n" + (proc.stdout or ""), 8)
            raise BuildError(
                f"أمر البناء فشل (رمز {proc.returncode}):\n    $ {' '.join(str(c) for c in cmd)[:300]}\n"
                + (f"  تفاصيل الخطأ:\n{details}" if details else "  (لا مخرجات من الأداة)"))
        return proc

    def _print_result(self, path: Path) -> None:
        ok("البناء اكتمل: " + str(path))

    def _artifact(self, *cands: Path) -> Optional[Path]:
        for c in cands:
            if c and Path(c).exists():
                return Path(c)
        return None

    def _find_executable(self, folder: Path) -> Optional[Path]:
        """أحدث ملف تنفيذي داخل مجلد (لنتائج cmake/make ...)."""
        if folder.is_file():
            return folder
        ext = exe_ext(self.target_os)
        best: Optional[Path] = None
        best_t = -1.0
        for f in folder.rglob("*" + ext) if folder.exists() else []:
            if not f.is_file():
                continue
            if ext == "" and not (os.access(f, os.X_OK) or f.suffix in ("", ".bin", ".run")):
                continue
            if f.parent.name in EXCLUDE_DIRS:
                continue
            t = f.stat().st_mtime
            if t > best_t:
                best, best_t = f, t
        return best

    def _copy_into(self, src: Path, dst_dir: Path) -> Path:
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / src.name
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        return dst

    # ---------- خطافات اختيارية للبنّائين ----------
    def _update_project_deps(self) -> None:
        """--update-deps: يُنفّذ داخل كل Builder إن كان له مدير تبعيات."""

    # ---------- القالب العام للبناء ----------
    def build(self) -> Path:
        self.t0 = time.monotonic()
        try:
            if not self.project.is_dir():
                raise BuildError(f"مجلد المشروع غير موجود: {self.project}")
            if self.opts.update_deps:
                info("تحديث تبعيات المشروع …")
                self._update_project_deps()
            self._ensure_out()
            path = self._compile()
            if not path or not Path(path).exists():
                raise BuildError("لم يُنتج البناء أي ملف — راجع مخرجات الأداة أعلاه.")
            self._print_result(Path(path))
            return Path(path)
        finally:
            self._cleanup()

    def _compile(self) -> Path:
        raise NotImplementedError

# ═══════════════════════════════════════════════════════════════════════════
# 11) البنّاؤون — المفسّرة/السكربتية
# ═══════════════════════════════════════════════════════════════════════════

class PythonBuilder(BaseBuilder):
    """Python عبر PyInstaller أو Nuitka — backend: auto / pyinstaller / nuitka."""
    LANG = LangType.PYTHON
    TITLE = "PyInstaller"

    def _pick_backend(self) -> str:
        backend = getattr(self.opts, "backend", "auto") or "auto"
        if backend != "auto":
            self.TITLE = "Nuitka" if backend == "nuitka" else "PyInstaller"
            return backend
        if which_cmd("pyinstaller") or self.deps.ensure("pyinstaller"):
            return "pyinstaller"
        if which_cmd("nuitka") or self.deps.ensure("nuitka"):
            return "nuitka"
        raise BuildError("لا يوجد PyInstaller ولا Nuitka — ثبّت أحدهما:\n"
                         f"    \"{sys.executable}\" -m pip install pyinstaller")

    def _add_data_args(self) -> List[str]:
        args: List[str] = []
        for item in (getattr(self.opts, "add_data", None) or []):
            if "=" not in item:
                warn(f"تجاهل --add-data غير صالح (الصيغة SRC=DEST): {item}")
                continue
            src, dst = item.split("=", 1)
            if not (self.project / src).exists():
                warn(f"تجاهل --add-data: الملف غير موجود {src}")
                continue
            # [إصلاح] فاصل PyInstaller يعتمد على النظام المضيف (';' على Windows)
            args += ["--add-data", f"{src}{os.pathsep}{dst}"]
        return args

    def _compile(self) -> Path:
        backend = self._pick_backend()
        entry = self.entry or ProjectDetector(self.project).detect_entry(LangType.PYTHON)
        if not entry:
            pys = [f for f in self.project.glob("*.py")]
            entry = pys[0].name if len(pys) == 1 else "main.py"
        entry_path = self.project / entry
        if not entry_path.exists():
            raise BuildError(f"نقطة الدخول غير موجودة: {entry} — حدّدها بـ --script")
        ext = exe_ext(self.target_os)
        final = self.out / (self.name + ext)
        args: List[str] = []
        if backend == "pyinstaller":
            self._require(["pyinstaller"])
            cmd = [which_cmd("pyinstaller") or "pyinstaller", "--noconfirm", "--clean",
                   "--distpath", str(self.out),
                   "--workpath", str(self._tmp("pyi_work")),
                   "--specpath", str(self._tmp("pyi_spec")),
                   "--name", self.name]
            if self.opts.onefile:
                cmd.append("--onefile")
            if not self.opts.console:
                cmd.append("--windowed")
            if self.icon:
                cmd += ["--icon", self.icon]
            for h in (getattr(self.opts, "hidden_imports", None) or []):
                cmd += ["--hidden-import", h]
            cmd += self._add_data_args()
            if self.target_os == "windows" and current_os() != "windows":
                warn("PyInstaller لا يدعم cross-compile لـ Windows من نظام آخر — سيُبنى للنظام الحالي.")
            cmd.append(entry)
            args = cmd
        else:
            self._require(["nuitka"])
            cmd = [sys.executable, "-m", "nuitka", "--standalone",
                   "--output-dir", str(self.out), "--output-filename", self.name + ext,
                   "--remove-output", "--no-prompt-file"]
            if self.opts.onefile:
                cmd.append("--onefile")
            if not self.opts.console and self.target_os == "windows":
                cmd.append("--windows-disable-console")
            if self.icon and self.target_os == "windows":
                cmd.append(f"--windows-icon-from-ico={self.icon}")
            elif self.icon and current_os() == "linux":
                cmd.append(f"--linux-onefile-icon={self.icon}")
            for h in (getattr(self.opts, "hidden_imports", None) or []):
                cmd.append(f"--include-module={h}")
            for item in (getattr(self.opts, "add_data", None) or []):
                if "=" in item:
                    cmd.append(f"--include-data-files={item}")
            cmd.append(entry)
            args = cmd
        self._run(args, timeout=TIMEOUT_LONG)
        if backend == "nuitka" and self.opts.onefile:
            return self._artifact(self.out / (self.name + ext),
                                  self.out / (entry_path.stem + ext)) or final
        if not self.opts.onefile:
            bundled = final if final.exists() else self._find_executable(self.out / self.name)
            return bundled or final
        return self._artifact(final) or self._find_executable(self.out) or final

    def _print_result(self, path: Path) -> None:
        if path.is_dir():
            ok(f"البناء اكتمل (مجلد): {path}")
            hint("وزّع المجلد كاملًا أو استخدم --onefile لملف واحد.")
        else:
            ok("البناء اكتمل: " + str(path))

    def _update_project_deps(self) -> None:
        req = self.project / "requirements.txt"
        if req.exists():
            self._run([sys.executable, "-m", "pip", "install", "-U", "-r", str(req)],
                      timeout=TIMEOUT_PKG)


class NodeBuilder(BaseBuilder):
    """Node.js عبر pkg (يُنفَّذ عبر npx — بلا تثبيت عالمي)."""
    LANG = LangType.NODE
    TITLE = "pkg"

    def _compile(self) -> Path:
        self._require(["node", "npm"])
        if not (self.project / "node_modules").exists() or self.opts.update_deps:
            info("تثبيت تبعيات npm …")
            self._run([which_cmd("npm") or "npm", "install"], timeout=TIMEOUT_PKG)
        entry = self.entry or ProjectDetector(self.project).detect_entry(LangType.NODE)
        if not entry or not (self.project / entry).exists():
            raise BuildError("لم أعثر على نقطة الدخول — حدّدها بـ --script main.js")
        self._require(["pkg"])
        target = {"windows": "node18-win-x64", "linux": "node18-linux-x64",
                  "macos": "node18-macos-x64"}.get(self.target_os)
        if not target:
            raise BuildError("pkg لا يدعم بناء Android — استهدف windows/linux/macos.")
        npx = which_cmd("npx") or "npx"
        out_name = self.out / (self.name + exe_ext(self.target_os))
        self._run([npx, "--yes", "pkg", entry, "--targets", target,
                   "--output", str(out_name)], timeout=TIMEOUT_LONG)
        return self._artifact(out_name) or out_name

    def _update_project_deps(self) -> None:
        self._require(["npm"])
        self._run([which_cmd("npm") or "npm", "update"], timeout=TIMEOUT_PKG)


class RubyBuilder(BaseBuilder):
    """Ruby عبر ocra — Windows فقط (ocra لا يعمل على Linux/macOS)."""
    LANG = LangType.RUBY
    TITLE = "ocra"

    def _compile(self) -> Path:
        if current_os() != "windows":
            # [إصلاح] رفض صريح بدل تسليم ملف مضلّل
            raise BuildError("ocra يعمل على Windows فقط. على Linux/macOS ثبّت "
                             "Ruby وواصل التشغيل كمصدر، أو استخدم Windows لبناء exe.")
        self._require(["ruby"])
        self._require(["gem"])
        self._require(["ocra"])
        entry = self.entry or ProjectDetector(self.project).detect_entry(LangType.RUBY)
        if not entry:
            raise BuildError("لم أعثر على ملف .rb — حدّده بـ --script")
        out_name = self.out / (self.name + ".exe")
        cmd = [which_cmd("ocra") or "ocra", entry, "--output", str(out_name)]
        if not self.opts.console:
            cmd.append("--windows")
        self._run(cmd, timeout=TIMEOUT_LONG)
        return out_name

    def _update_project_deps(self) -> None:
        if (self.project / "Gemfile").exists() and which_cmd("bundle"):
            self._run(["bundle", "update"], timeout=TIMEOUT_PKG)


class PerlBuilder(BaseBuilder):
    """Perl: PAR::Packer (pp) عند توفره، وإلا توزيع مباشر (مجلد جاهز)."""
    LANG = LangType.PERL
    TITLE = "PAR::Packer / توزيع مباشر"

    def _compile(self) -> Path:
        entry = self.entry or ProjectDetector(self.project).detect_entry(LangType.PERL)
        if not entry:
            raise BuildError("لم أعثر على ملف .pl — حدّده بـ --script")
        if which_cmd("pp") or (self.deps.ensure("pp") and which_cmd("pp")):
            out_name = self.out / self.name
            self._run([which_cmd("pp") or "pp", "-o", str(out_name), entry],
                      timeout=TIMEOUT_LONG)
            return out_name
        # توزيع مباشر: مصدر + مكتبات + مشغّلات
        info("pp غير متاح — إنشاء حزمة توزيع مباشر (بدون exe).")
        dest = self._copy_into(self.project, self.out / (self.name + "-perl"))
        hint("لتوليد exe واحد: cpan -T PAR::Packer ثم أعد المحاولة (pp).")
        return dest


class LoveBuilder(BaseBuilder):
    """Lua / LÖVE — حزم .love أو دمج داخل love binary (cat / copy bytes)."""
    LANG = LangType.LOVE
    TITLE = "LÖVE"

    def _compile(self) -> Path:
        entry = self.entry or "main.lua"
        if not (self.project / entry).exists():
            raise BuildError("مشروع LÖVE يحتاج main.lua — حدّد نقطة الدخول بـ --script")
        self._require(["love"])
        # 1) إنشاء game.love (zip بمصادر المشروع)
        love_pkg = self.out / (self.name + ".love")
        with zipfile.ZipFile(love_pkg, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in collect_files(self.project, extra_excludes={self.out.name}):
                arc = f.relative_to(self.project)
                if str(arc).startswith(str(self.out.relative_to(self.project))):
                    continue
                zf.write(f, str(arc))
        ok("حزمة " + love_pkg.name + " جاهزة.")
        # 2) الدمج داخل love binary (تفسير + بيانات في ملف واحد)
        love_bin = which_cmd("love")
        if love_bin and current_os() == "windows":
            fused = self.out / (self.name + ".exe")
            with open(love_bin, "rb") as a, open(love_pkg, "rb") as b, open(fused, "wb") as w:
                w.write(a.read()); w.write(b.read())   # تقنية الدمج الرسمية لـ LÖVE
            return fused
        hint(f"لتوليد تنفيذي واحد: ادمج ثنائية love مع {love_pkg.name} "
             f"(cat love {love_pkg.name} > {self.name}) — على Windows: copy /b love.exe+{love_pkg.name} {self.name}.exe")
        return love_pkg

    def _print_result(self, path: Path) -> None:
        ok("البناء اكتمل: " + str(path))


class LuaBuilder(LoveBuilder):
    """Lua: يُبنى بتضمينه داخل love binary (المسار القياسي لتوزيع Lua)."""
    LANG = LangType.LUA
    TITLE = "LÖVE fusion"

# ═══════════════════════════════════════════════════════════════════════════
# 11) البنّاؤون — المترجمة الأصلية (Native)
# ═══════════════════════════════════════════════════════════════════════════

class _CLikeBuilder(BaseBuilder):
    """أساس مشترك لـ C/C++: CMake ثم Make ثم مترجم مباشر."""
    COMPILER = "gcc"
    CROSS_COMPILER = "x86_64-w64-mingw32-gcc"

    def _cross_needed(self) -> bool:
        return self.target_os == "windows" and current_os() != "windows"

    def _cc(self) -> str:
        if self._cross_needed():
            cc = which_cmd(self.CROSS_COMPILER)
            if not cc:
                # [إصلاح] فشل واضح بدل binary مضلّل عند غياب mingw
                raise BuildError("cross-compile إلى Windows يتطلب mingw-w64 وغير مثبت:\n"
                                 "    sudo apt-get install -y mingw-w64")
            return cc
        return self.COMPILER

    def _compiler_sources(self) -> Path:
        exts = getattr(self, "SRC_EXTS", (".c",))
        cc = self._cc()
        rel = rel_files(collect_files(self.project), self.project)
        srcs = [f for f in rel if f.suffix in exts]
        if not srcs:
            raise BuildError("لم أعثر على ملفات مصدر " + "/".join(exts))
        out = self.out / (self.name + exe_ext(self.target_os))
        cmd = [cc, "-O2"] + [str(s) for s in srcs] + ["-o", str(out), "-lm"]
        self._run(cmd, timeout=TIMEOUT_LONG)
        return out

    def _compile(self) -> Path:
        if (self.project / "CMakeLists.txt").exists():
            return self._via_cmake()
        if (self.project / "Makefile").exists() or (self.project / "makefile").exists():
            return self._via_make()
        return self._compiler_sources()

    def _via_cmake(self) -> Path:
        self._require(["cmake"], purpose="بناء CMake")
        build_dir = self._tmp("cmake_build")
        cross = []
        if self._cross_needed():
            self._require(["x86_64-w64-mingw32-gcc"])
            toolchain = build_dir / "toolchain.cmake"
            cc, cxx = which_cmd(self.CROSS_COMPILER), which_cmd("x86_64-w64-mingw32-g++")
            toolchain.write_text(
                "set(CMAKE_SYSTEM_NAME Windows)\n"
                f"set(CMAKE_C_COMPILER {cc})\n"
                + (f"set(CMAKE_CXX_COMPILER {cxx})" if cxx else ""))
            cross = ["-DCMAKE_TOOLCHAIN_FILE=" + str(toolchain)]
        self._run(["cmake", "-S", ".", "-B", str(build_dir),
                   "-DCMAKE_BUILD_TYPE=Release"] + cross, timeout=TIMEOUT_MED)
        self._run(["cmake", "--build", str(build_dir), "--config", "Release"],
                  timeout=TIMEOUT_LONG)
        exe = self._find_executable(build_dir)
        if not exe:
            raise BuildError("CMake اكتمل لكن لم أجد الملف التنفيذي داخل مجلد البناء.")
        return self._copy_into(exe, self.out)

    def _via_make(self) -> Path:
        self._require(["make"], purpose="بناء Make")
        if self._cross_needed():
            warn("Makefile قد لا يحترم cross-compile — يُفضّل CMake مع toolchain.")
        build_dir = self._tmp("make_build")
        for f in self.project.iterdir():
            if f.is_file() and f.name.lower() in ("makefile", "gnumakefile"):
                shutil.copy2(f, build_dir / f.name)
        self._run(["make", "-C", str(build_dir)], timeout=TIMEOUT_LONG)
        exe = self._find_executable(build_dir)
        if not exe:
            raise BuildError("make اكتمل لكن لم أجد الملف التنفيذي.")
        return self._copy_into(exe, self.out)


class CBuilder(_CLikeBuilder):
    LANG = LangType.C
    TITLE = "gcc / CMake / Make"
    COMPILER = "gcc"
    CROSS_COMPILER = "x86_64-w64-mingw32-gcc"
    SRC_EXTS = (".c",)

    def _compile(self) -> Path:
        if (self.project / "CMakeLists.txt").exists():
            return self._via_cmake()
        if (self.project / "Makefile").exists():
            return self._via_make()
        self._require(["gcc"], purpose="بناء C")
        return self._compiler_sources()


class CppBuilder(_CLikeBuilder):
    LANG = LangType.CPP
    TITLE = "g++ / CMake / Make"
    COMPILER = "g++"
    CROSS_COMPILER = "x86_64-w64-mingw32-g++"
    SRC_EXTS = (".cpp", ".cc", ".cxx")

    def _compile(self) -> Path:
        if (self.project / "CMakeLists.txt").exists():
            return self._via_cmake()
        if (self.project / "Makefile").exists():
            return self._via_make()
        self._require(["g++"], purpose="بناء C++")
        return self._compiler_sources()


class RustBuilder(BaseBuilder):
    """Rust عبر cargo — cross إلى Windows عبر rustup target + mingw linker."""
    LANG = LangType.RUST
    TITLE = "cargo"

    def _cargo_name(self) -> str:
        name = regex_first(read_small_text(self.project / "Cargo.toml"),
                           r'^\s*name\s*=\s*"([^"]+)"')
        return sanitize_name(name or self.name)

    def _compile(self) -> Path:
        self._require(["cargo"], purpose="بناء Rust")
        cargo = which_cmd("cargo") or "cargo"
        crate = self._cargo_name()
        env = None
        target_dir_arg: List[str] = []
        if self.target_os == "windows" and current_os() != "windows":
            triple = "x86_64-pc-windows-gnu"
            if not which_cmd("x86_64-w64-mingw32-gcc"):
                raise BuildError("cross-compile لـ Windows يتطلب mingw-w64 (linker):\n"
                                 "    sudo apt-get install -y mingw-w64")
            if which_cmd("rustup"):
                info("إضافة هدف " + triple + " …")
                self._run(["rustup", "target", "add", triple], timeout=TIMEOUT_MED)
            else:
                raise BuildError("rustup غير موجود — ثبّته من rustup.rs لإدارة أهداف cross.")
            env = {"CARGO_TARGET_" + "X86_64_PC_WINDOWS_GNU" + "_LINKER":
                   "x86_64-w64-mingw32-gcc"}
            target_dir_arg = ["--target", triple]
            sub = Path("target") / triple / "release"
        else:
            sub = Path("target") / "release"
        self._run([cargo, "build", "--release"] + target_dir_arg, timeout=TIMEOUT_LONG, env=env)
        ext = exe_ext(self.target_os)
        out = self._artifact(self.project / sub / (crate + ext)) or \
              self._find_executable(self.project / Path("target"))
        if not out:
            raise BuildError("cargo اكتمل لكن لم أجد الملف التنفيذي في target/.")
        return self._copy_into(out, self.out)

    def _update_project_deps(self) -> None:
        self._require(["cargo"])
        self._run([which_cmd("cargo") or "cargo", "update"], timeout=TIMEOUT_PKG)


class GoBuilder(BaseBuilder):
    """Go عبر go build — cross مدمج في الأداة عبر GOOS/GOARCH."""
    LANG = LangType.GO
    TITLE = "go build"

    def _compile(self) -> Path:
        self._require(["go"], purpose="بناء Go")
        go = which_cmd("go") or "go"
        env = None
        ext = ""
        if self.target_os == "windows" and current_os() != "windows":
            env = {"GOOS": "windows", "GOARCH": "amd64", "CGO_ENABLED": "0"}
            ext = ".exe"
        elif self.target_os != current_os():
            m = {"linux": ("linux", "amd64"), "macos": ("darwin", "arm64")}.get(self.target_os)
            if m:
                env = {"GOOS": m[0], "GOARCH": m[1], "CGO_ENABLED": "0"}
        out = self.out / (self.name + ext)
        if not (self.project / "go.mod").exists():
            warn("go.mod غير موجود — سأجرب بناء المجلد الحالي مباشرة.")
        self._run([go, "build", "-o", str(out), "."], timeout=TIMEOUT_LONG, env=env)
        if not out.exists():
            raise BuildError("go build اكتمل بدون ملف ناتج — تأكد من وجود package main.")
        return out

    def _update_project_deps(self) -> None:
        self._require(["go"])
        self._run([which_cmd("go") or "go", "mod", "tidy"], timeout=TIMEOUT_PKG)


class NimBuilder(BaseBuilder):
    """Nim عبر nim c — cross إلى Windows عبر mingw."""
    LANG = LangType.NIM
    TITLE = "nim c"

    def _compile(self) -> Path:
        self._require(["nim"], purpose="بناء Nim")
        entry = self.entry or ProjectDetector(self.project).detect_entry(LangType.NIM)
        if not entry:
            nims = [f for f in self.project.glob("*.nim")]
            entry = nims[0].name if nims else "main.nim"
        if not (self.project / entry).exists():
            raise BuildError(f"نقطة دخول Nim غير موجودة: {entry} — حدّدها بـ --script")
        out = self.out / (self.name + exe_ext(self.target_os))
        cmd = [which_cmd("nim") or "nim", "c", "-d:release", "--opt:speed",
               "--outFile:" + str(out)]
        if self.target_os == "windows" and current_os() != "windows":
            # [إصلاح] تحقق mingw قبل cross — لا binary مضلّل
            if not which_cmd("x86_64-w64-mingw32-gcc"):
                raise BuildError("cross لـ Windows يتطلب mingw-w64:\n"
                                 "    sudo apt-get install -y mingw-w64")
            cmd += ["--os:windows", "--cpu:amd64", "-d:mingw",
                    "--gcc.exe:x86_64-w64-mingw32-gcc",
                    "--gcc.linkerexe:x86_64-w64-mingw32-gcc"]
        cmd.append(entry)
        self._run(cmd, timeout=TIMEOUT_LONG)
        return out


class ZigBuilder(BaseBuilder):
    """Zig — cross-compile مدمج في المترجم نفسه (بلا أدوات إضافية)."""
    LANG = LangType.ZIG
    TITLE = "zig"

    def _compile(self) -> Path:
        self._require(["zig"], purpose="بناء Zig")
        zig = which_cmd("zig") or "zig"
        ext = exe_ext(self.target_os)
        if (self.project / "build.zig").exists():
            prefix = self._tmp("zig_prefix")
            target = {"windows": "-Dtarget=x86_64-windows",
                      "linux": "-Dtarget=x86_64-linux",
                      "macos": "-Dtarget=aarch64-macos"}.get(self.target_os, "")
            self._run([zig, "build", "-Doptimize=ReleaseFast", target,
                       "--prefix", str(prefix)], timeout=TIMEOUT_LONG)
            exe = self._find_executable(prefix)
            if not exe:
                raise BuildError("zig build اكتمل بدون ملف تنفيذي في --prefix.")
            return self._copy_into(exe, self.out)
        entry = self.entry or ProjectDetector(self.project).detect_entry(LangType.ZIG) or "main.zig"
        if not (self.project / entry).exists():
            raise BuildError(f"ملف Zig غير موجود: {entry} — حدّده بـ --script")
        out = self.out / (self.name + ext)
        cmd = [zig, "build-exe", entry, "-O", "ReleaseFast", "-femit-bin=" + str(out)]
        if self.target_os == "windows" and current_os() != "windows":
            cmd.append("-target")
            cmd.append("x86_64-windows-gnu")
        self._run(cmd, timeout=TIMEOUT_LONG)
        return out


class CrystalBuilder(BaseBuilder):
    """Crystal — بناء أصلي فقط: يرفض cross إلى Windows صراحةً."""
    LANG = LangType.CRYSTAL
    TITLE = "crystal build"

    def _compile(self) -> Path:
        if self.target_os != current_os():
            # [إصلاح] رفض صريح — Crystal لا يدعم cross إلى Windows حاليًا
            raise BuildError("Crystal يدعم البناء الأصلي فقط (لا cross-compile إلى "
                             "Windows من Linux/macOS). ابنِ على النظام الهدف نفسه.")
        self._require(["crystal"], purpose="بناء Crystal")
        entry = self.entry or ProjectDetector(self.project).detect_entry(LangType.CRYSTAL)
        if not entry:
            shard = self.project / "shard.yml"
            entry = regex_first(read_small_text(shard), r"main:\s*(\S+)") or "main.cr"
        if not (self.project / entry).exists():
            raise BuildError(f"نقطة دخول Crystal غير موجودة: {entry}")
        out = self.out / self.name
        self._run([which_cmd("crystal") or "crystal", "build", "--release",
                   "--no-color", entry, "-o", str(out)], timeout=TIMEOUT_LONG)
        return out

# ═══════════════════════════════════════════════════════════════════════════
# 11) البنّاؤون — .NET وJVM
# ═══════════════════════════════════════════════════════════════════════════

class DotnetBuilder(BaseBuilder):
    """.NET عبر dotnet publish — self-contained + ملف واحد."""
    LANG = LangType.DOTNET
    TITLE = "dotnet publish"

    def _rid(self) -> str:
        if self.target_os == "windows":
            return "win-x64"
        if self.target_os == "macos":
            return "osx-arm64" if platform.machine() == "arm64" else "osx-x64"
        if self.target_os == "linux":
            return "linux-arm64" if platform.machine() == "arm64" else "linux-x64"
        # native حسب المضيف
        return {"windows": "win-x64", "macos": "osx-x64"}.get(current_os(), "linux-x64")

    def _find_project(self) -> Path:
        for f in sorted(self.project.glob("*.csproj")) + sorted(self.project.glob("*.sln")):
            return f
        deep = collect_files(self.project, max_depth=3)
        for f in deep:
            if f.suffix in (".csproj", ".sln"):
                return f
        raise BuildError("لم أعثر على .csproj أو .sln — هذا ليس مشروع .NET؟")

    def _compile(self) -> Path:
        if self.target_os == "android":
            raise BuildError("بناء Android عبر .NET يتطلب workload خاص — استخدم "
                             "AndroidBuilder (Gradle) أو ثبّت dotnet workload install maui-android.")
        self._require(["dotnet"], purpose="بناء .NET")
        proj = self._find_project()
        out = self.out / (self.name + "-publish")
        cmd = [which_cmd("dotnet") or "dotnet", "publish", str(proj),
               "-c", "Release", "-r", self._rid(),
               "--self-contained", "true",
               "-p:PublishSingleFile=true",
               "-p:IncludeNativeLibrariesForSelfExtract=true",
               "-o", str(out)]
        if self.opts.verbose:
            cmd.append("-v:minimal")
        self._run(cmd, timeout=TIMEOUT_LONG)
        exe = self._find_executable(out)
        if not exe:
            raise BuildError("dotnet publish اكتمل بدون ملف تنفيذي.")
        return exe

    def _update_project_deps(self) -> None:
        self._require(["dotnet"])
        proj = self._find_project()
        self._run([which_cmd("dotnet") or "dotnet", "restore", str(proj), "--force"],
                  timeout=TIMEOUT_PKG)


class JavaBuilder(BaseBuilder):
    """Java: Gradle أو Maven أو javac مباشر + JAR؛ jpackage عند --onefile."""
    LANG = LangType.JAVA
    TITLE = "javac / jar / jpackage"

    def _compile(self) -> Path:
        gradlew = self.project / ("gradlew.bat" if os.name == "nt" else "gradlew")
        if gradlew.exists():
            return self._via_gradle(gradlew)
        if (self.project / "pom.xml").exists():
            return self._via_maven()
        return self._via_javac()

    def _gradle_cmd(self, gradlew: Path) -> List[str]:
        if os.access(gradlew, os.X_OK) or os.name == "nt":
            return [str(gradlew)]
        return ["sh", str(gradlew)]    # [إصلاح] wrapper بلا صلاحية تنفيذ

    def _via_gradle(self, gradlew: Path) -> Path:
        self._require(["java"], purpose="بناء Gradle")
        task = "assembleRelease" if self.target_os != "native" else "build"
        cmd = self._gradle_cmd(gradlew) + [task, "--console=plain", "-q"]
        if self.opts.update_deps:
            cmd.append("--refresh-dependencies")
        proc = self._run(cmd, timeout=TIMEOUT_LONG)
        jar = self._newest(self.project / "build" / "libs", (".jar",))
        if not jar:
            raise BuildError("Gradle اكتمل لكن لم أجد JAR في build/libs.\n"
                             + tail_lines((proc.stderr or "") + (proc.stdout or ""), 4))
        return self._copy_into(jar, self.out)

    def _via_maven(self) -> Path:
        self._require(["mvn", "java"], purpose="بناء Maven")
        cmd = [which_cmd("mvn") or "mvn", "-q", "-DskipTests", "package"]
        if self.opts.update_deps:
            cmd.append("-U")
        self._run(cmd, timeout=TIMEOUT_LONG)
        jar = self._newest(self.project / "target", (".jar",), skip=("original-",))
        if not jar:
            raise BuildError("Maven اكتمل لكن لم أجد JAR في target/.")
        return self._copy_into(jar, self.out)

    def _via_javac(self) -> Path:
        self._require(["javac", "jar", "java"], purpose="بناء Java مباشر")
        rel = rel_files(collect_files(self.project), self.project)
        srcs = [f for f in rel if f.suffix == ".java"]
        if not srcs:
            raise BuildError("لا يوجد Gradle/Maven ولا ملفات .java — هل هذا مشروع Java؟")
        classes = self._tmp("java_classes")
        cmd = [which_cmd("javac") or "javac", "-d", str(classes)] + [str(s) for s in srcs]
        self._run(cmd, timeout=TIMEOUT_LONG)
        main_class = self._pick_main_class(classes)
        jar_path = self.out / (self.name + ".jar")
        jar = which_cmd("jar") or "jar"
        if main_class:
            self._run([jar, "cfe", str(jar_path), main_class, "-C", str(classes), "."],
                      timeout=TIMEOUT_MED)
        else:
            self._run([jar, "cf", str(jar_path), "-C", str(classes), "."], timeout=TIMEOUT_MED)
            hint("لم أحدد Main-Class — شغّل الـ JAR بـ java -cp " + jar_path.name + " <MainClass>")
        self._maybe_jpackage(jar_path)
        return jar_path

    def _pick_main_class(self, classes: Path) -> Optional[str]:
        for f in collect_files(classes, max_files=2000):
            if f.suffix != ".class" or f.name.endswith(("Test.class",)):
                continue
            text = f.read_bytes()[:65536]
            if b"main" in text and b"([Ljava/lang/String;)V" in text:
                relc = f.relative_to(classes)
                return ".".join(relc.with_suffix("").parts)
        return None

    def _maybe_jpackage(self, jar: Path) -> None:
        """--onefile + jpackage متوفر → صورة تطبيق أصلية (app-image)."""
        if not getattr(self.opts, "onefile", False):
            return
        jp = which_cmd("jpackage")
        if not jp:
            hint("jpackage غير متوفر — سيتم تسليم JAR فقط. "
                 "ثبّت JDK 14+ للحصول على صورة أصلية.")
            return
        info("تغليف أصلي عبر jpackage …")
        dest = self.out / "jpackage"
        cmd = [jp, "--input", str(self.out), "--main-jar", jar.name,
               "--name", self.name, "--type", "app-image", "--dest", str(dest)]
        if self.icon and os.name == "nt":
            cmd += ["--icon", self.icon]
        proc = run_cmd(cmd, cwd=self.project, timeout=TIMEOUT_LONG)
        if proc.returncode == 0 and dest.exists():
            ok("صورة التطبيق: " + str(dest / self.name))
        else:
            warn("jpackage فشل (يحتاج WiX على Windows لصيغ msi/exe) — تم تسليم JAR.")

    def _newest(self, folder: Path, exts: Tuple[str, ...],
                skip: Tuple[str, ...] = ()) -> Optional[Path]:
        if not folder.exists():
            return None
        cands = [f for f in folder.iterdir()
                 if f.is_file() and f.suffix in exts
                 and not any(f.name.startswith(s) for s in skip)
                 and "javadoc" not in f.name and "sources" not in f.name]
        return max(cands, key=lambda f: f.stat().st_mtime, default=None)

    def _update_project_deps(self) -> None:
        pass  # تُعالج داخل كل مسار (refresh-dependencies / -U)


class KotlinBuilder(JavaBuilder):
    """Kotlin: Gradle (kts) عند وجوده، وإلا kotlinc مباشر إلى JAR."""
    LANG = LangType.KOTLIN
    TITLE = "kotlinc / Gradle"

    def _compile(self) -> Path:
        gradlew = self.project / ("gradlew.bat" if os.name == "nt" else "gradlew")
        has_gradle = gradlew.exists() or (self.project / "build.gradle.kts").exists() \
            or (self.project / "settings.gradle.kts").exists()
        if has_gradle:
            return self._via_kotlin_gradle(gradlew)
        return self._via_kotlinc()

    def _via_kotlin_gradle(self, gradlew: Path) -> Path:
        self._require(["java"], purpose="بناء Kotlin/Gradle")
        cmd = (self._gradle_cmd(gradlew) if gradlew.exists() else ["gradle"]) \
            + ["build", "--console=plain", "-q"]
        self._run(cmd, timeout=TIMEOUT_LONG)
        for d in (self.project / "build" / "libs",):
            jar = self._newest(d, (".jar",))
            if jar:
                return self._copy_into(jar, self.out)
        raise BuildError("Gradle اكتمل بدون JAR في build/libs.")

    def _via_kotlinc(self) -> Path:
        self._require(["kotlinc"], purpose="بناء Kotlin مباشر")
        rel = rel_files(collect_files(self.project), self.project)
        srcs = [f for f in rel if f.suffix in (".kt", ".kts")]
        if not srcs:
            raise BuildError("لا توجد ملفات .kt — حدّد المشروع الصحيح.")
        jar_path = self.out / (self.name + ".jar")
        self._run([which_cmd("kotlinc") or "kotlinc"] + [str(s) for s in srcs]
                  + ["-include-runtime", "-d", str(jar_path)], timeout=TIMEOUT_LONG)
        self._maybe_jpackage(jar_path)
        return jar_path


class ScalaBuilder(BaseBuilder):
    """Scala: scala-cli عند توفره، وإلا sbt package."""
    LANG = LangType.SCALA
    TITLE = "scala-cli / sbt"

    def _compile(self) -> Path:
        entry = self.entry or ProjectDetector(self.project).detect_entry(LangType.SCALA)
        if which_cmd("scala-cli"):
            jar_path = self.out / (self.name + ".jar")
            cmd = ["scala-cli", "--power", "package", "--assembly", "--force"]
            if entry:
                cmd.append(entry)
            cmd += ["-o", str(jar_path)]
            self._run(cmd, timeout=TIMEOUT_LONG)
            return jar_path
        if which_cmd("sbt") or (self.project / "project").is_dir():
            self._require(["sbt"], purpose="بناء Scala")
            self._run(["sbt", "--batch", "package"], timeout=TIMEOUT_LONG)
            scala_dir = next((d for d in (self.project / "target").glob("scala-*")
                              if d.is_dir()), None)
            if scala_dir:
                jar = self._newest_shared(scala_dir)
                if jar:
                    return self._copy_into(jar, self.out)
            raise BuildError("sbt اكتمل بدون JAR في target/scala-*/.")
        raise BuildError("ثبّت scala-cli أو sbt لبناء Scala:\n"
                         "    curl -fLsS https://raw.githubusercontent.com/VirtusLab/coursier/main/cs-x86_64-pc-linux.gz | sh && cs install scala-cli")

    def _newest_shared(self, folder: Path) -> Optional[Path]:
        jars = [f for f in folder.glob("*.jar")
                if f.is_file() and "sources" not in f.name and "javadoc" not in f.name]
        return max(jars, key=lambda f: f.stat().st_mtime, default=None)

# ═══════════════════════════════════════════════════════════════════════════
# 11) البنّاؤون — الأطر (Flutter/Dart/Electron/Android/Godot) + كشف بدون بناء
# ═══════════════════════════════════════════════════════════════════════════

class FlutterBuilder(BaseBuilder):
    """Flutter: apk / windows / macos / linux حسب الهدف."""
    LANG = LangType.FLUTTER
    TITLE = "flutter build"

    def _compile(self) -> Path:
        self._require(["flutter"], purpose="بناء Flutter")
        flutter = which_cmd("flutter") or "flutter"
        host = current_os()
        if self.opts.update_deps:
            self._run([flutter, "pub", "upgrade"], timeout=TIMEOUT_PKG)
        elif not (self.project / ".dart_tool").exists():
            self._run([flutter, "pub", "get"], timeout=TIMEOUT_PKG)

        if self.target_os == "android":
            self._run([flutter, "build", "apk", "--release"], timeout=TIMEOUT_LONG)
            apk = self.project / "build/app/outputs/flutter-apk/app-release.apk"
            if not apk.exists():
                raise BuildError("flutter build apk اكتمل بدون APK — تحقق من توقيع التطبيق.")
            return self._copy_into(apk, self.out)

        # بناء سطح المكتب يتطلب نفس نظام الاستضافة
        if self.target_os != host:
            # [إصلاح] لا نسخّ مضلّلة: بناء سطح المكتب لا يدعم cross
            raise BuildError(f"بناء Flutter لـ {self.target_os} يتم على نظام {self.target_os} "
                             f"نفسه (المضيف الحالي {host}) — لا cross-compile لسطح المكتب.")
        self._run([flutter, "build", self.target_os, "--release"], timeout=TIMEOUT_LONG)
        folders = {"windows": self.project / "build/windows/x64/runner/Release",
                   "linux": self.project / "build/linux/x64/release/bundle",
                   "macos": self.project / "build/macos/Build/Products/Release"}
        src = folders.get(self.target_os)
        if not src or not src.exists():
            raise BuildError(f"لم أجد مخرجات بناء {self.target_os} في المسار المتوقع.")
        exe = self._find_executable(src)
        if self.target_os in ("linux", "windows"):
            # المجلد كاملًا ضروري (DLL/بيانات بجانب الملف التنفيذي)
            return self._copy_into(src, self.out / (self.name + "-" + self.target_os))
        return exe or src

    def _update_project_deps(self) -> None:
        self._require(["flutter"])
        self._run([which_cmd("flutter") or "flutter", "pub", "upgrade"], timeout=TIMEOUT_PKG)


class DartBuilder(BaseBuilder):
    """Dart: ترجمة أصلية عبر dart compile exe."""
    LANG = LangType.DART
    TITLE = "dart compile exe"

    def _compile(self) -> Path:
        self._require(["dart"], purpose="بناء Dart")
        dart = which_cmd("dart") or "dart"
        if self.opts.update_deps or not (self.project / ".dart_tool").exists():
            self._run([dart, "pub", "get"], timeout=TIMEOUT_PKG)
        entry = self.entry or ProjectDetector(self.project).detect_entry(LangType.DART)
        if not entry or not (self.project / entry).exists():
            raise BuildError("لم أعثر على bin/main.dart — حدّد نقطة الدخول بـ --script")
        out = self.out / (self.name + exe_ext(self.target_os))
        self._run([dart, "compile", "exe", entry, "-o", str(out)], timeout=TIMEOUT_LONG)
        return out

    def _update_project_deps(self) -> None:
        self._require(["dart"])
        self._run([which_cmd("dart") or "dart", "pub", "upgrade"], timeout=TIMEOUT_PKG)


class ElectronBuilder(BaseBuilder):
    """Electron عبر electron-builder (npx) — إخراج منصّة الهدف."""
    LANG = LangType.ELECTRON
    TITLE = "electron-builder"

    def _compile(self) -> Path:
        self._require(["node", "npm"])
        if not (self.project / "node_modules").exists() or self.opts.update_deps:
            info("تثبيت تبعيات npm (قد يستغرق وقتًا — electron ثقيل) …")
            self._run([which_cmd("npm") or "npm", "install"], timeout=TIMEOUT_LONG)
        host = current_os()
        if self.target_os == "android":
            raise BuildError("Electron لا يدعم Android — استخدم Flutter/Cordova لهذا الهدف.")
        if self.target_os == "macos" and host != "macos":
            raise BuildError("بناء حزم macOS (.dmg) يتطلب جهاز macOS فعليًا — "
                             "electron-builder يرفضها على Linux/Windows.")
        flag = {"windows": "--win", "linux": "--linux", "macos": "--mac"}[self.target_os]
        npx = which_cmd("npx") or "npx"
        cfg: Dict[str, Any] = {"directories": {"output": str(self.out)}}
        pj = parse_json_file(self.project / "package.json")
        if not pj.get("build"):
            # [إصلاح] مشروع بلا إعدادات build → تمرير config مصغّر يعمل مباشرة
            cfg.update({"appId": "com.polybuild." + self.name,
                        "productName": self.name,
                        "files": ["**/*"],
                        "directories": {"output": str(self.out)}})
        if self.icon:
            cfg["icon"] = self.icon
        cmd = [npx, "--yes", "electron-builder", flag, "--config", json.dumps(cfg)]
        env = {"ELECTRON_DEVTOOLS": "1"} if getattr(self.opts, "devtools", False) else None
        if getattr(self.opts, "devtools", False):
            hint("ELECTRON_DEVTOOLS=1 — فعّلها داخل main.js: "
                 "if (process.env.ELECTRON_DEVTOOLS) win.webContents.openDevTools()")
        self._run(cmd, timeout=TIMEOUT_LONG, env=env)
        exe = self._find_executable(self.out)
        if not exe:
            raise BuildError("electron-builder اكتمل بدون ملف تنفيذي داخل " + str(self.out))
        return exe

    def _update_project_deps(self) -> None:
        self._require(["npm"])
        self._run([which_cmd("npm") or "npm", "update"], timeout=TIMEOUT_PKG)


class AndroidBuilder(BaseBuilder):
    """Android عبر Gradle wrapper — يتحقق من JDK + Android SDK أولًا."""
    LANG = LangType.ANDROID
    TITLE = "gradlew assemble"

    def _sdk_dir(self) -> Optional[Path]:
        env = os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME")
        if env and Path(env).exists():
            return Path(env)
        lp = self.project / "local.properties"
        sdk = regex_first(read_small_text(lp), r"sdk\.dir\s*=\s*(.+)")
        if sdk and Path(sdk.strip()).exists():
            return Path(sdk.strip())
        return None

    def _compile(self) -> Path:
        gradlew = self.project / ("gradlew.bat" if os.name == "nt" else "gradlew")
        if not gradlew.exists():
            raise BuildError("لا يوجد gradle wrapper في المشروع — افتح المشروع في "
                             "Android Studio مرة واحدة لتوليده، أو ثبّت gradle يدويًا.")
        if not which_cmd("java"):
            raise BuildError("بناء Android يحتاج JDK 17+:\n"
                             "    sudo apt-get install -y openjdk-17-jdk")
        if not self._sdk_dir():
            raise BuildError("لم أعثر على Android SDK — ثبّت Android Studio أو cmdline-tools "
                             "ثم اضبط ANDROID_SDK_ROOT أو local.properties (sdk.dir).")
        cmd = self._invoke(gradlew) + ["assembleRelease", "--console=plain", "-q"]
        proc = run_cmd(cmd, cwd=self.project, timeout=TIMEOUT_LONG)
        apk_dir = self.project / "app/build/outputs/apk/release"
        apk = self._newest_apk(apk_dir)
        if not apk:
            # [إصلاح] فشل التوقيع شائع → نجرّب debug تلقائيًا بشفافية
            warn("لم أجد APK release (غالبًا غياب keystore التوقيع) — سأبني debug.")
            self._run(self._invoke(gradlew) + ["assembleDebug", "--console=plain", "-q"],
                      timeout=TIMEOUT_LONG)
            apk = self._newest_apk(self.project / "app/build/outputs/apk/debug")
        if not apk:
            raise BuildError("فشل بناء APK:\n" + tail_lines((proc.stderr or "") +
                                                             (proc.stdout or ""), 6))
        return self._copy_into(apk, self.out)

    def _invoke(self, gradlew: Path) -> List[str]:
        if os.name == "nt":
            return [str(gradlew)]
        return [str(gradlew)] if os.access(gradlew, os.X_OK) else ["sh", str(gradlew)]

    def _newest_apk(self, folder: Path) -> Optional[Path]:
        if not folder.exists():
            return None
        apks = [f for f in folder.rglob("*.apk") if f.is_file()]
        return max(apks, key=lambda f: f.stat().st_mtime, default=None)

    def _update_project_deps(self) -> None:
        gradlew = self.project / ("gradlew.bat" if os.name == "nt" else "gradlew")
        if gradlew.exists():
            self._run(self._invoke(gradlew) + ["--refresh-dependencies"],
                      timeout=TIMEOUT_PKG)


class GodotBuilder(BaseBuilder):
    """Godot عبر export_presets.cfg — يحتاج قوالب التصدير مثبتة."""
    LANG = LangType.GODOT
    TITLE = "godot --export-release"

    def _compile(self) -> Path:
        self._require(["godot"], purpose="تصدير Godot")
        presets_file = self.project / "export_presets.cfg"
        text = read_small_text(presets_file)
        if not text:
            raise BuildError("لا يوجد export_presets.cfg — افتح المشروع في محرر Godot "
                             "وأضف Export Preset أولًا (Project → Export).")
        presets = re.findall(r'name="([^"]+)"', text)
        if not presets:
            raise BuildError("export_presets.cfg بلا presets — أنشئ preset واحد على الأقل.")
        wanted = {"windows": "Windows", "android": "Android", "linux": "Linux",
                  "macos": "macOS"}.get(self.target_os, "")
        preset = next((p for p in presets if wanted and wanted.lower() in p.lower()), presets[0])
        ext = ".apk" if "android" in preset.lower() else exe_ext(
            "windows" if "windows" in preset.lower() else self.target_os)
        out = self.out / (self.name + ext)
        info(f"تصدير preset: {preset}")
        self._run([which_cmd("godot") or "godot", "--headless", "--export-release",
                   preset, str(out)], timeout=TIMEOUT_LONG)
        if not out.exists():
            raise BuildError("التصدير اكتمل بدون ملف — تحقق من تثبيت Export Templates "
                             "بنفس إصدار Godot.")
        return out


# ── لغات "كشف بدون بناء": إرشاد واضح بدل ادعاء ناتج زائف ──────────────────

class _GuidanceBuilder(BaseBuilder):
    """لا يبني — يعرض خطوات البناء الرسمية بوضوح (لا ملفات مضللة)."""

    def _compile(self) -> Path:
        raise GuidanceBuild(self.GUIDE_TITLE, self.GUIDE_STEPS)


class UnityBuilder(_GuidanceBuilder):
    LANG = LangType.UNITY
    TITLE = "Unity Editor (إرشاد)"
    GUIDE_TITLE = "مشاريع Unity تُبنى عبر المحرر الرسمي"
    GUIDE_STEPS = [
        "1. افتح المشروع في Unity Hub/Editor.",
        "2. File → Build Settings واختر المنصة المستهدفة.",
        "3. اضغط Build وحدد مجلد الإخراج.",
        "4. للأتمتة: unity -batchmode -quit -executeMethod BuildScript.PerformBuild",
    ]


class UnrealBuilder(_GuidanceBuilder):
    LANG = LangType.UNREAL
    TITLE = "Unreal Editor (إرشاد)"
    GUIDE_TITLE = "مشاريع Unreal تُبنى عبر المحرر/البناء الرسمي"
    GUIDE_STEPS = [
        "1. افتح المشروع في Unreal Editor أو استخدم UnrealBuildTool.",
        "2. Packaging: Platforms → <المنصة> → Package Project.",
        "3. للأتمتة: RunUAT.bat BuildCookRun -project=... -platform=Win64 -cook -allmaps -build -stage -pak -archive",
    ]


class GameMakerBuilder(_GuidanceBuilder):
    LANG = LangType.GAMEMAKER
    TITLE = "GameMaker (إرشاد)"
    GUIDE_TITLE = "مشاريع GameMaker تُبنى عبر IDE الرسمي"
    GUIDE_STEPS = [
        "1. افتح المشروع (.yyp) في GameMaker IDE.",
        "2. اختر Target (Windows/Mac/HTML5/Android).",
        "3. Create Executable / Package وحدد مجلد الإخراج.",
    ]


class RenPyBuilder(_GuidanceBuilder):
    LANG = LangType.RENPY
    TITLE = "Ren'Py SDK (إرشاد)"
    GUIDE_TITLE = "مشاريع Ren'Py توزَّع عبر Ren'Py Launcher"
    GUIDE_STEPS = [
        "1. افتح المشروع في Ren'Py Launcher.",
        "2. Build Distributions واختر المنصات (Linux/Mac/Windows).",
        "3. اضغط Build — ستحصل على حزم توزيع جاهزة.",
    ]

# ═══════════════════════════════════════════════════════════════════════════
# 12) BUILDERS factory — خريطة اللغة → البنّاء
# ═══════════════════════════════════════════════════════════════════════════

BUILDERS: Dict[LangType, type] = {
    LangType.PYTHON: PythonBuilder,
    LangType.NODE: NodeBuilder,
    LangType.RUBY: RubyBuilder,
    LangType.PERL: PerlBuilder,
    LangType.LUA: LuaBuilder,
    LangType.LOVE: LoveBuilder,
    LangType.C: CBuilder,
    LangType.CPP: CppBuilder,
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
    LangType.ELECTRON: ElectronBuilder,
    LangType.ANDROID: AndroidBuilder,
    LangType.GODOT: GodotBuilder,
    LangType.UNITY: UnityBuilder,
    LangType.UNREAL: UnrealBuilder,
    LangType.GAMEMAKER: GameMakerBuilder,
    LangType.RENPY: RenPyBuilder,
}

# ترتيب عرض القوائم التفاعلية
LANG_ORDER: List[LangType] = [k for k in BUILDERS.keys() if k is not LangType.UNKNOWN]


# ═══════════════════════════════════════════════════════════════════════════
# 13) InteractiveUI — المعالج التفاعلي
# ═══════════════════════════════════════════════════════════════════════════

class InteractiveUI:
    """معالج تفاعلي: قوائم أسهم ↑/↓ على TTY، مرقّمة على CI،
    قيمة افتراضية ذكية لكل حقل، Esc للإلغاء، وصندوق ملخص قبل التأكيد."""

    def __init__(self, opts: argparse.Namespace):
        self.opts = opts

    # ---------- إدخال منخفض المستوى ----------
    def _getch(self) -> str:
        """قراءة مفتاح واحد موحّدة: msvcrt على Windows، termios على Unix،
        input() عند غياب TTY."""
        if os.name == "nt":
            try:
                import msvcrt
                ch = msvcrt.getwch()
                if ch in ("\x00", "\xe0"):
                    nxt = msvcrt.getwch()
                    return {"H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT"}.get(nxt, "")
                return ch
            except Exception:
                return "\n"
        try:
            import termios
            import tty
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            tty.setraw(fd)
            try:
                ch = sys.stdin.read(1)
                if ch == "\x1b":
                    r, _, _ = select.select([sys.stdin], [], [], 0.05)
                    if r:
                        seq = sys.stdin.read(2)
                        if seq == "[A":
                            return "UP"
                        if seq == "[B":
                            return "DOWN"
                    return "\x1b"
                return ch
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:
            return "\n"

    @staticmethod
    def _tty() -> bool:
        try:
            return sys.stdin.isatty() and sys.stdout.isatty()
        except Exception:
            return False

    # ---------- عناصر واجهة ----------
    def menu(self, title: str, options: Sequence[str], default: int = 0) -> int:
        """قائمة أسهم على TTY، مرقّمة على CI — تُرجع فهرس الاختيار."""
        if not self._tty() or not COLOR:
            return self._numbered_menu(title, options, default)
        n = len(options)
        idx = max(0, min(default, n - 1))
        print(paint(f"◆ {title}", Ansi.CYAN, Ansi.BOLD))
        body = [f"  {options[i]}" for i in range(n)]

        def render() -> None:
            for i, line in enumerate(body):
                cur = (i == idx)
                mark = paint(SYM["cursor"] + " ", Ansi.CYAN, Ansi.BOLD) if cur else "  "
                txt = paint(line, Ansi.BOLD) if cur else paint(line, Ansi.GRAY)
                print(mark + txt)

        render()
        while True:
            ch = self._getch()
            if ch == "UP":
                idx = (idx - 1) % n
            elif ch == "DOWN":
                idx = (idx + 1) % n
            elif ch in ("\r", "\n"):
                return idx
            elif ch == "\x1b":
                raise UserCancel()
            elif ch == "\x03":          # Ctrl+C في الوضع الخام
                raise UserCancel()
            else:
                continue
            sys.stdout.write(f"\x1b[{n}A\r\x1b[J")
            sys.stdout.flush()
            render()

    def _numbered_menu(self, title: str, options: Sequence[str], default: int) -> int:
        print(paint(f"◆ {title}", Ansi.CYAN, Ansi.BOLD))
        for i, opt in enumerate(options):
            mark = paint(" ← الافتراضي", Ansi.GRAY) if i == default else ""
            print(f"  {i + 1}) {opt}{mark}")
        while True:
            try:
                raw = input(f"اختر رقمًا [{default + 1}]: ").strip()
            except EOFError:
                return default
            if not raw:
                return default
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return int(raw) - 1
            warn("رقم غير صالح — أعد المحاولة.")

    def ask(self, prompt: str, default: str = "") -> str:
        if not self._tty():
            print(paint(f"◆ {prompt}", Ansi.CYAN, Ansi.BOLD))
        try:
            raw = input(f"{prompt}" + (f" [{default}]" if default else "") + ": ").strip()
        except EOFError:
            return default
        return raw or default

    def confirm(self, prompt: str, default: bool = True) -> bool:
        if getattr(self.opts, "yes", False):   # [إصلاح] dest = yes وليس assume_yes
            return True
        suffix = "[Y/n]" if default else "[y/N]"
        if not self._tty():
            print(paint(f"◆ {prompt} {suffix}", Ansi.CYAN, Ansi.BOLD))
            return default      # CI بدون إجابة → الافتراضي الآمن
        try:
            raw = input(f"{prompt} {suffix}: ").strip().lower()
        except EOFError:
            return default
        if not raw:
            return default
        return raw in ("y", "yes", "1", "نعم")

    # ---------- المعالج ----------
    def run(self) -> None:
        opts = self.opts
        print()
        render_box("المعالج التفاعلي — PolyBuild Pro",
                   ["أدخل القيم أو اضغط Enter لقبول الافتراضي.",
                    "Esc في أي قائمة للإلغاء."], width=58)
        print()

        # 1) مجلد المشروع
        project_str = self.ask("مجلد المشروع", getattr(opts, "project", "") or ".")
        project = Path(project_str).expanduser().resolve()
        while not project.is_dir():
            warn("المجلد غير موجود — حاول مرة أخرى (Esc للإلغاء).")
            project_str = self.ask("مجلد المشروع", ".")
            project = Path(project_str).expanduser().resolve()
        opts.project = str(project)

        # 2) معاينة الكشف قبل القرار
        info("جارٍ فحص المشروع …")
        det = ProjectDetector(project).detect()
        rows = [("اللغة المرشّحة", det.label),
                ("الثقة", f"{det.confidence * 100:.0f}%"),
                ("الأدلة", "؛ ".join(det.evidence[:3]) or "لا شيء")]
        if det.entry_point:
            rows.append(("نقطة الدخول", det.entry_point))
        render_box("نتيجة الكشف التلقائي", rows, width=62)
        print()

        # 3) اللغة: كشف تلقائي أو اختيار يدوي
        langs = [LANG_LABELS[l] for l in LANG_ORDER]
        auto_label = f"كشف تلقائي — {det.label} ({det.confidence * 100:.0f}%)" \
            if det.lang is not LangType.UNKNOWN else "كشف تلقائي (لم أتأكد)"
        idx = self.menu("اختر اللغة:", [auto_label] + langs,
                        default=0 if det.lang is LangType.UNKNOWN else 0)
        if idx == 0:
            opts.lang = None                      # كشف تلقائي
            if det.lang is LangType.UNKNOWN:
                warn("الكشف لم يستقر — سيلزم --lang لاحقًا أو اختر من القائمة.")
                idx = self.menu("اختر اللغة يدويًا:", langs, default=0)
                opts.lang = LANG_ORDER[idx].value
        else:
            opts.lang = LANG_ORDER[idx - 1].value
        lang = LangType(opts.lang) if opts.lang else det.lang

        # 4) بقية الحقول بقيم ذكية
        opts.output = self.ask("مجلد المخرجات", str(opts.output or DEFAULT_OUTPUT_DIR))
        opts.name = self.ask("اسم المخرَج", det.project_name or sanitize_name(project.name))
        entry_default = det.entry_point or ""
        entry_in = self.ask("نقطة الدخول (اختياري)", entry_default)
        opts.script = entry_in or None
        os_idx = self.menu("النظام الهدف:", ["native (النظام الحالي)", "windows", "linux",
                                             "macos", "android"], default=0)
        opts.target_os = ["native", "windows", "linux", "macos", "android"][os_idx]
        opts.onefile = self.confirm("ملف تنفيذي واحد؟ (--onefile)", True)
        opts.console = self.confirm("إظهار نافذة console؟", True)
        icon_in = self.ask("أيقونة (.ico/.png/.icns) — اختياري", "")
        opts.icon = icon_in or None
        if lang is LangType.PYTHON:
            be = self.menu("محرك بناء Python:", ["auto (اختر المتوفر)", "pyinstaller", "nuitka"], 0)
            opts.backend = ["auto", "pyinstaller", "nuitka"][be]

        # 5) ملخص في صندوق قبل التأكيد
        print()
        render_box("ملخص الإعداد", [
            ("المشروع", str(project)),
            ("اللغة", LANG_LABELS.get(lang, str(opts.lang))),
            ("الاسم", str(opts.name)),
            ("المخرجات", str(opts.output)),
            ("الدخول", str(opts.script or "-")),
            ("الهدف", str(opts.target_os)),
            ("onefile", "نعم" if opts.onefile else "لا"),
            ("console", "نعم" if opts.console else "لا"),
        ], width=62)
        if not self.confirm("هل نبدأ البناء؟", True):
            raise UserCancel()

# ═══════════════════════════════════════════════════════════════════════════
# 14) build_arg_parser()
# ═══════════════════════════════════════════════════════════════════════════

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="polybuild.py",
        description=f"{TOOL_NAME} {VERSION_TAG} — من أي مشروع برمجي إلى ملف تنفيذي أصلي "
                    "(EXE / APK / Binary) مع كشف تلقائي وتثبيت تبعيات.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="أمثلة:\n"
               "  python polybuild.py -p ./myapp -n myapp -f\n"
               "  python polybuild.py -p . --lang go --target-os windows\n"
               "  python polybuild.py --check-tools\n"
               "  python polybuild.py --update\n")
    # الأساسية
    p.add_argument("-p", "--project", metavar="DIR", help="مجلد المشروع")
    p.add_argument("-s", "--script", metavar="FILE", help="نقطة الدخول (اختياري)")
    p.add_argument("-n", "--name", metavar="NAME", help="اسم المخرَج")
    p.add_argument("-i", "--icon", metavar="FILE", help="ملف .ico / .png / .icns")
    p.add_argument("-o", "--output", metavar="DIR", default=None,
                   help="مجلد المخرجات (افتراضي: dist داخل المشروع)")
    p.add_argument("--lang", metavar="LANG",
                   help="فرض اللغة (يتخطى الكشف) — مثل: python, go, cpp, flutter")
    p.add_argument("--target-os", choices=TARGET_OS_CHOICES, default="native",
                   help="نظام الهدف: native / windows / android (والأكثر)")
    # التحكم بالبناء
    p.add_argument("-f", "--onefile", action="store_true",
                   help="ملف تنفيذي واحد")
    p.add_argument("-c", "--console", action=argparse.BooleanOptionalAction, default=True,
                   help="إظهار نافذة console (استخدم --no-console لإخفائها)")
    p.add_argument("--devtools", action="store_true",
                   help="فتح DevTools في Electron (يتطلب دعمًا في main.js)")
    p.add_argument("--backend", choices=("auto", "pyinstaller", "nuitka"), default="auto",
                   help="محرك بناء Python")
    # المتقدمة
    p.add_argument("--hidden-imports", action="append", default=[], metavar="MOD",
                   help="وحدات مخفية (قابلة للتكرار)")
    p.add_argument("--add-data", action="append", default=[], metavar="SRC=DEST",
                   help="ملفات بيانات SRC=DEST (قابلة للتكرار)")
    # الإدارة
    p.add_argument("--update", action="store_true", help="تحديث PolyBuild نفسه")
    p.add_argument("--update-deps", action="store_true", help="تحديث تبعيات المشروع قبل البناء")
    p.add_argument("--check-tools", action="store_true", help="عرض حالة كل أداة")
    p.add_argument("-v", "--verbose", action="store_true", help="مخرجات تفصيلية")
    p.add_argument("-I", "--interactive", action="store_true", help="تشغيل المعالج التفاعلي")
    p.add_argument("--quick", action="store_true", help="تخطي الأسئلة، استخدام الافتراضيات")
    p.add_argument("-y", "--yes", action="store_true", help="افترض \"نعم\" على كل الأسئلة (CI)")
    p.add_argument("--version", action="version",
                   version=f"{TOOL_NAME} {VERSION_TAG} ({VERSION})")
    return p


# ═══════════════════════════════════════════════════════════════════════════
# 15) main()
# ═══════════════════════════════════════════════════════════════════════════

def _print_detection(det: DetectedProject, verbose: bool) -> None:
    rows: List[Any] = [("اللغة", det.label), ("الثقة", f"{det.confidence * 100:.0f}%")]
    if det.entry_point:
        rows.append(("نقطة الدخول", det.entry_point))
    if det.project_name:
        rows.append(("الاسم", det.project_name))
    if verbose:
        for e in det.evidence:
            rows.append(e)
    render_box("نتيجة الكشف التلقائي", rows, width=62)


def _fail_unknown() -> int:
    err("لم أتمكن من تحديد لغة المشروع.")
    langs = "، ".join(l.value for l in LANG_ORDER)
    hint(f"حدّد اللغة يدويًا بـ --lang. اللغات المدعومة:\n  {langs}")
    return 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    _force_utf8_stdio()
    parser = build_arg_parser()
    opts = parser.parse_args(argv)
    opts.update_deps = bool(getattr(opts, "update_deps", False))

    installer = BaseToolInstaller()
    deps = DependencyManager(installer, assume_yes=opts.yes, quick=opts.quick,
                             verbose=opts.verbose)

    # إجراءات إدارية لا تحتاج مشروعًا
    if opts.check_tools:
        print_banner()
        return deps.check_all()
    if opts.update:
        print_banner()
        try:
            return SelfUpdater(assume_yes=opts.yes).run()
        except BuildError as e:
            err("فشل التحديث:\n" + str(e))
            return 1

    print_banner()

    # فحص تحديث غير معيق عند الإقلاع (إن ضُبط رابط الإصدار)
    if not opts.quick:
        try:
            SelfUpdater(assume_yes=True).check_at_startup()
        except Exception:
            pass  # فشل الفحص لا يعطل الأداة أبدًا

    # المعالج التفاعلي: تلقائيًا بلا --project وعلى TTY، أو مع -I
    if opts.interactive or (not opts.project and sys.stdin.isatty() and not opts.quick):
        try:
            InteractiveUI(opts).run()
        except UserCancel:
            warn("أُلغي بواسطة المستخدم.")
            return 130
    if not opts.project:
        err("مطلوب مجلد المشروع: استخدم --project أو شغّل المعالج التفاعلي من طرفية.")
        hint("مثال: python polybuild.py -p ./myapp")
        return 2

    project = Path(opts.project).expanduser()
    if not project.is_dir():
        err(f"مجلد المشروع غير موجود: {project}")
        return 2
    opts.project = str(project.resolve())

    # تحديد اللغة
    if opts.lang:
        try:
            lang = LangType(opts.lang.strip().lower())
        except ValueError:
            err(f"لغة غير معروفة: {opts.lang}")
            hint("اللغات: " + "، ".join(l.value for l in LANG_ORDER))
            return 2
        det = ProjectDetector(project).detect()
        if opts.verbose:
            _print_detection(det, True)
        info(f"اللغة مفروضة يدويًا: {LANG_LABELS.get(lang, lang.value)}")
    else:
        info("فحص المشروع …")
        det = ProjectDetector(project).detect()
        _print_detection(det, opts.verbose)
        lang = det.lang
        if lang is LangType.UNKNOWN:
            return _fail_unknown()
    if opts.verbose and det.entry_point and not opts.script:
        info(f"نقطة دخول مقترحة: {det.entry_point}")

    # البناء
    builder_cls = BUILDERS.get(lang)
    if builder_cls is None:
        err(f"لا يوجد بنّاء للغة: {lang.value}")
        return 1
    builder = builder_cls(opts, deps)
    start = time.monotonic()
    try:
        artifact = builder.build()
    except GuidanceBuild as g:
        render_box(g.title, g.steps, width=66)
        hint("أداة PolyBuild لا تنتج ملفًا هنا — الإرشاد أعلاه هو المسار الرسمي.")
        return 0
    except BuildError as e:
        err("فشل البناء:\n" + str(e))
        return 1
    except UserCancel:
        warn("أُلغي بواسطة المستخدم.")
        return 130
    except KeyboardInterrupt:
        warn("أُلغي بواسطة المستخدم.")
        return 130
    except NotImplementedError:
        err("هذا البنّاء غير مكتمل لهذه اللغة.")
        return 1

    summary_panel(artifact, time.monotonic() - start,
                  LANG_LABELS.get(lang, lang.value), builder.TITLE)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        _force_utf8_stdio()
        warn("أُلغي بواسطة المستخدم.")
        sys.exit(130)
