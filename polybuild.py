#!/usr/bin/env python3
"""
PolyBuild Pro v2.3.3 - Universal App & Game Builder (EXE / APK / Native)
Auto-detects 25+ languages, self-updates, auto-manages & installs dependencies.
"""

import os
import sys
import json
import shutil
import subprocess
import argparse
import glob
import re
import urllib.request
import hashlib
import tempfile
import zipfile
import platform
import ast
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Set
from enum import Enum, auto
from datetime import datetime

# FIX: on Windows, the console's default codepage depends on the system
# locale (e.g. cp1256 on Arabic Windows, cp936 on Chinese Windows) and is
# very often NOT UTF-8. This script prints Unicode box-drawing characters
# (─) and emoji (✓, ✗, etc.) unconditionally, which crashes with
# UnicodeEncodeError the moment stdout can't represent them - confirmed
# live: 'charmap' codec can't encode character '\u2500' on cp1256. Force
# UTF-8 on stdout/stderr up front so this always works regardless of the
# user's system locale, with errors="replace" as a last-resort safety net
# for any truly unencodable byte.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ==================== VERSION & UPDATE ====================
VERSION = "2.3.3"
UPDATE_URL = os.environ.get("POLYBUILD_UPDATE_URL", "")
VERSION_CHECK_URL = os.environ.get("POLYBUILD_VERSION_URL", "")


class Colors:
    GREEN = '\033[92m'; YELLOW = '\033[93m'; RED = '\033[91m'
    BLUE = '\033[94m'; CYAN = '\033[96m'; MAGENTA = '\033[95m'
    BOLD = '\033[1m'; DIM = '\033[2m'; END = '\033[0m'

def log(msg, color=Colors.BLUE): print(f"{color}[*] {msg}{Colors.END}")
def success(msg): print(f"{Colors.GREEN}[✓] {msg}{Colors.END}")
def warn(msg): print(f"{Colors.YELLOW}[!] {msg}{Colors.END}")
def error(msg): print(f"{Colors.RED}[✗] {msg}{Colors.END}"); sys.exit(1)
def info(msg): print(f"{Colors.CYAN}[i] {msg}{Colors.END}")
def dim(msg): print(f"{Colors.DIM}{msg}{Colors.END}")


# ==================== HELPERS ====================

# Used for source-tree scanning during language detection — safe to
# exclude 'build'/'dist' here since we don't want stale generated output
# skewing detection scores.
EXCLUDED_DIRS: Set[str] = {
    '.git', 'node_modules', '__pycache__', '.venv', 'venv', 'env',
    '.idea', '.vscode', 'target', 'zig-cache', 'zig-out',
    '.gradle', '.android', 'DerivedData', '.cargo', 'cache',
    '.next', '.nuxt', '.svelte-kit', '.angular',
    'Pods', '.symlinks', 'dist', 'build',
}

# FIX (v2.3.3): a separate, much smaller exclusion set for walking BUILD
# OUTPUT looking for artifacts (APKs, etc). Artifacts always live under a
# "build" directory, so EXCLUDED_DIRS (which excludes "build") must never
# be reused here or the search can never find anything.
ARTIFACT_SEARCH_EXCLUDED_DIRS: Set[str] = {'.git', 'node_modules'}

def exe_ext(target_os: str = "native") -> str:
    if target_os == "windows": return ".exe"
    if target_os == "native": return ".exe" if sys.platform == "win32" else ""
    return ""


# ==================== SELF-UPDATE SYSTEM ====================

class SelfUpdater:
    @staticmethod
    def check_update(force: bool = False) -> bool:
        if not VERSION_CHECK_URL or not UPDATE_URL: return False
        try:
            req = urllib.request.Request(VERSION_CHECK_URL, headers={'User-Agent': 'PolyBuild-Updater'})
            with urllib.request.urlopen(req, timeout=5) as resp:
                remote_data = json.loads(resp.read().decode('utf-8'))
            remote_version = remote_data.get('version', '0.0.0')
            if SelfUpdater._version_compare(remote_version, VERSION) > 0:
                if force or input("Update now? [Y/n]: ").lower() in ('', 'y', 'yes'):
                    return SelfUpdater._perform_update(remote_data)
            else:
                success(f"Already up to date (v{VERSION})")
            return False
        except Exception as e:
            warn(f"Update check failed: {e}")
            return False

    @staticmethod
    def _version_compare(v1: str, v2: str) -> int:
        try:
            n1 = [int(x) for x in re.sub(r'[^0-9.]', '', v1).split('.') if x]
            n2 = [int(x) for x in re.sub(r'[^0-9.]', '', v2).split('.') if x]
            return (n1 > n2) - (n1 < n2)
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def _perform_update(update_data: dict) -> bool:
        try:
            req = urllib.request.Request(update_data.get('download_url', UPDATE_URL), headers={'User-Agent': 'PolyBuild-Updater'})
            with urllib.request.urlopen(req, timeout=30) as resp:
                new_code = resp.read().decode('utf-8')
            if 'sha256' not in update_data:
                error("Update server did not provide a SHA-256 hash; refusing to apply update")
            if hashlib.sha256(new_code.encode()).hexdigest() != update_data['sha256']:
                error("Update verification failed (hash mismatch)")

            # Validate it's at least parseable Python before overwriting anything.
            try:
                ast.parse(new_code)
            except SyntaxError as e:
                error(f"Update validation failed (invalid Python): {e}")

            script_path = os.path.abspath(sys.argv[0])
            backup_path = script_path + ".backup"
            shutil.copy2(script_path, backup_path)
            with open(script_path, 'w', encoding='utf-8') as f:
                f.write(new_code)

            success(f"Updated to v{update_data['version']}! Restart to use new version.")

            # FIX (v2.3.3, restored): ast.parse() only proves the code is
            # syntactically valid — it doesn't catch things like an
            # indentation edge case that trips up bytecode compilation,
            # or a truncated download. Byte-compile and roll back on failure.
            try:
                import py_compile
                py_compile.compile(script_path, doraise=True)
            except Exception as rollback_err:
                warn(f"New script failed compile check, rolling back: {rollback_err}")
                shutil.copy2(backup_path, script_path)
                error("Update rolled back — downloaded code would not compile")
            else:
                # Backup no longer needed after successful compile check
                try:
                    os.remove(backup_path)
                except OSError:
                    pass
            return True
        except Exception as e:
            error(f"Update failed: {e}")


# ==================== BASE TOOL INSTALLER ====================

class BaseToolInstaller:
    def __init__(self):
        self._apt_available = sys.platform.startswith("linux") and shutil.which("apt-get") is not None
        self._brew_available = sys.platform == "darwin" and shutil.which("brew") is not None
        self._winget_available = sys.platform == "win32" and shutil.which("winget") is not None
        self._choco_available = sys.platform == "win32" and shutil.which("choco") is not None

    def install_via_apt(self, package: str) -> bool:
        if not self._apt_available: return False
        sudo = [] if os.geteuid() == 0 else ["sudo"]
        subprocess.run(sudo + ["apt-get", "update", "-qq"], capture_output=True, timeout=180)
        result = subprocess.run(sudo + ["apt-get", "install", "-y"] + package.split(), capture_output=True, text=True, errors="replace", timeout=300)
        return result.returncode == 0

    def install_via_brew(self, package: str) -> bool:
        if not self._brew_available: return False
        result = subprocess.run(["brew", "install"] + package.split(), capture_output=True, text=True, errors="replace", timeout=300)
        return result.returncode == 0

    def install_via_winget(self, package_id: str) -> bool:
        if not self._winget_available: return False
        result = subprocess.run(["winget", "install", "--id", package_id, "-e", "--accept-source-agreements", "--accept-package-agreements"], capture_output=True, text=True, errors="replace", timeout=300)
        return result.returncode == 0

    def install_via_choco(self, package: str) -> bool:
        if not self._choco_available: return False
        result = subprocess.run(["choco", "install", package, "-y", "--no-progress"], capture_output=True, text=True, errors="replace", timeout=300)
        return result.returncode == 0

    def install_via_pkgmgr(self, apt_pkg=None, brew_pkg=None, choco_pkg=None, winget_id=None) -> bool:
        if sys.platform == "win32": return bool((winget_id and self.install_via_winget(winget_id)) or (choco_pkg and self.install_via_choco(choco_pkg)))
        elif sys.platform == "darwin": return bool(brew_pkg and self.install_via_brew(brew_pkg))
        elif sys.platform.startswith("linux"): return bool(apt_pkg and self.install_via_apt(apt_pkg))
        return False

    def install_nodejs(self) -> bool: return self.install_via_pkgmgr(apt_pkg="nodejs npm", brew_pkg="node", choco_pkg="nodejs", winget_id="OpenJS.NodeJS")
    def install_python(self) -> bool: return False
    def install_git(self) -> bool: return self.install_via_pkgmgr(apt_pkg="git", brew_pkg="git", choco_pkg="git", winget_id="Git.Git")
    def install_go(self) -> bool: return self.install_via_pkgmgr(apt_pkg="golang-go", brew_pkg="go", choco_pkg="golang", winget_id="GoLang.Go")
    def install_rust(self) -> bool: return self.install_via_pkgmgr(apt_pkg="rustc", brew_pkg="rust", choco_pkg="rust", winget_id="Rustlang.Rustup")
    def install_dotnet(self) -> bool: return self.install_via_pkgmgr(apt_pkg="dotnet-sdk-8.0", brew_pkg="dotnet-sdk", choco_pkg="dotnet-sdk", winget_id="Microsoft.DotNet.SDK.8")
    def install_java(self) -> bool: return self.install_via_pkgmgr(apt_pkg="openjdk-21-jdk", brew_pkg="openjdk", choco_pkg="openjdk", winget_id="EclipseAdoptium.Temurin.21.JDK")
    def install_cmake(self) -> bool: return self.install_via_pkgmgr(apt_pkg="cmake", brew_pkg="cmake", choco_pkg="cmake", winget_id="Kitware.CMake")
    def install_mingw(self) -> bool: return self.install_via_pkgmgr(apt_pkg="build-essential", brew_pkg="gcc", choco_pkg="mingw")
    def install_flutter(self) -> bool: return self.install_via_pkgmgr(apt_pkg="flutter", brew_pkg="flutter", choco_pkg="flutter", winget_id="Google.Flutter")
    def install_godot(self) -> bool: return self.install_via_pkgmgr(apt_pkg="godot3", brew_pkg="godot", choco_pkg="godot", winget_id="GodotEngine.GodotEngine")
    def install_love(self) -> bool: return self.install_via_pkgmgr(apt_pkg="love", brew_pkg="love", choco_pkg="love", winget_id="Love2D.Love2D")
    def install_nim(self) -> bool: return self.install_via_pkgmgr(apt_pkg="nim", brew_pkg="nim", choco_pkg="nim")
    def install_zig(self) -> bool: return self.install_via_pkgmgr(brew_pkg="zig", choco_pkg="zig", winget_id="zig.zig")
    def install_crystal(self) -> bool: return self.install_via_pkgmgr(apt_pkg="crystal", brew_pkg="crystal", choco_pkg="crystal")
    def install_ruby(self) -> bool: return self.install_via_pkgmgr(apt_pkg="ruby-full", brew_pkg="ruby", choco_pkg="ruby", winget_id="RubyInstallerTeam.Ruby.3.2")
    def install_android_sdk(self) -> bool: return self.install_via_pkgmgr(apt_pkg="android-sdk", brew_pkg="--cask android-sdk")


# ==================== DEPENDENCY MANAGER ====================

class DependencyManager:
    TOOLS = {
        # FIX: checking the literal 'python'/'pip' binaries is wrong on many
        # Linux distros (Debian/Ubuntu ship only python3/pip3 by default) —
        # this could report Python as "not installed" while the script is
        # actively running under it. Check the actual interpreter in use.
        'python': {'check': [sys.executable, '--version'], 'install_fn': 'python'},
        'pip': {'check': [sys.executable, '-m', 'pip', '--version']},
        'pyinstaller': {'check': ['pyinstaller', '--version'], 'install': 'pip', 'pkg': 'pyinstaller'},
        'nuitka': {'check': ['python', '-m', 'nuitka', '--version'], 'install': 'pip', 'pkg': 'nuitka'},
        'node': {'check': ['node', '--version'], 'install_fn': 'nodejs'},
        'npm': {'check': ['npm', '--version']},
        'pkg': {'check': ['pkg', '--version'], 'install': 'npm', 'pkg': 'pkg', 'global': True},
        'electron-builder': {'check': ['npx', 'electron-builder', '--version'], 'install': 'npm', 'pkg': 'electron-builder', 'global': False},
        'electron': {'check': ['npx', 'electron', '--version'], 'install': 'npm', 'pkg': 'electron', 'global': False},
        'gcc': {'check': ['gcc', '--version'], 'install_fn': 'mingw'},
        'g++': {'check': ['g++', '--version'], 'install_fn': 'mingw'},
        'clang': {'check': ['clang', '--version']},
        'cmake': {'check': ['cmake', '--version'], 'install_fn': 'cmake'},
        'make': {'check': ['make', '--version']},
        'dotnet': {'check': ['dotnet', '--version'], 'install_fn': 'dotnet'},
        'go': {'check': ['go', 'version'], 'install_fn': 'go'},
        'cargo': {'check': ['cargo', '--version'], 'install_fn': 'rust'},
        'java': {'check': ['java', '--version'], 'install_fn': 'java'},
        'javac': {'check': ['javac', '--version']},
        'jpackage': {'check': ['jpackage', '--version']},
        'mvn': {'check': ['mvn', '--version'], 'install': 'pkgmgr', 'apt_pkg': 'maven', 'brew_pkg': 'maven', 'choco_pkg': 'maven'},
        'gradle': {'check': ['gradle', '--version'], 'install': 'pkgmgr', 'apt_pkg': 'gradle', 'brew_pkg': 'gradle', 'choco_pkg': 'gradle'},
        'flutter': {'check': ['flutter', '--version'], 'install_fn': 'flutter'},
        'nim': {'check': ['nim', '--version'], 'install_fn': 'nim'},
        'zig': {'check': ['zig', 'version'], 'install_fn': 'zig'},
        'lua': {'check': ['lua', '-v']},
        'love': {'check': ['love', '--version'], 'install_fn': 'love'},
        'crystal': {'check': ['crystal', '--version'], 'install_fn': 'crystal'},
        'ruby': {'check': ['ruby', '--version'], 'install_fn': 'ruby'},
        'godot': {'check': ['godot', '--version'], 'install_fn': 'godot'},
        'git': {'check': ['git', '--version'], 'install_fn': 'git'},
        'aapt': {'check': ['aapt', 'version'], 'install': 'pkgmgr', 'apt_pkg': 'aapt'},
        'adb': {'check': ['adb', 'version'], 'install_fn': 'android_sdk'},
    }
    BUNDLED_TOOLS = {'npm': 'node', 'npx': 'node', 'javac': 'java', 'jpackage': 'java', 'rustc': 'cargo'}

    def __init__(self):
        self.cache = {}
        self.base_installer = BaseToolInstaller()

    def is_installed(self, tool: str) -> bool:
        if tool in self.cache: return self.cache[tool]
        tool_info = self.TOOLS.get(tool)
        if not tool_info: self.cache[tool] = False; return False
        try:
            result = subprocess.run(tool_info['check'], capture_output=True, text=True, errors="replace", timeout=10, shell=(sys.platform == 'win32'))
            installed = result.returncode == 0
            self.cache[tool] = installed
            return installed
        except Exception:
            self.cache[tool] = False; return False

    def ensure(self, *tools: str, auto_install: bool = True, cwd: str = None) -> Dict[str, bool]:
        results = {}
        missing = []
        for tool in tools:
            if self.is_installed(tool): results[tool] = True
            else: results[tool] = False; missing.append(tool)
        if missing and auto_install:
            for tool in missing:
                results[tool] = self._install(tool, cwd=cwd)
                # FIX: previously this blindly set cache[bundled] = True for
                # any tool "bundled" with the one just installed (e.g.
                # javac/jpackage whenever java installs, npm/npx whenever
                # node installs) without ever actually checking. That's a
                # real, confirmed-live false positive: a JRE-only Java
                # install has no javac at all. Invalidate the cache instead
                # so the next is_installed() call does a real check.
                for bundled, parent in self.BUNDLED_TOOLS.items():
                    if parent == tool and results[tool]:
                        self.cache.pop(bundled, None)
                        if bundled in missing: results[bundled] = self.is_installed(bundled)
        return results

    def _install(self, tool: str, cwd: str = None) -> bool:
        tool_info = self.TOOLS.get(tool, {})
        if tool in self.BUNDLED_TOOLS:
            parent = self.BUNDLED_TOOLS[tool]
            # FIX: this used to short-circuit to True the moment the parent
            # was present, without ever verifying the bundled tool itself
            # actually exists (e.g. a JRE-only "java" with no "javac").
            # There's no separate automated install path for "just javac"
            # in general, so the honest thing to do is: (re)install/ensure
            # the parent, then report the tool's REAL status afterward.
            if not self.is_installed(parent):
                self._install(parent, cwd=cwd)
            self.cache.pop(tool, None)
            return self.is_installed(tool)
        install_fn = tool_info.get('install_fn')
        if install_fn:
            installer_method = getattr(self.base_installer, f"install_{install_fn}", None)
            if installer_method:
                result = installer_method()
                if result:
                    self.cache[tool] = True
                    for bundled, parent in self.BUNDLED_TOOLS.items():
                        if parent == tool: self.cache.pop(bundled, None)
                return result
            return False
        method = tool_info.get('install', 'manual')
        try:
            if method == 'pip':
                cmd = [sys.executable, "-m", "pip", "install", "--upgrade", tool_info.get('pkg', tool)]
                result = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=120)
                if result.returncode == 0: self.cache[tool] = True; return True
                return False
            elif method == 'npm':
                pkg = tool_info.get('pkg', tool)
                if not self.is_installed('node') and not self._install('node'): return False
                cmd = ["npm", "install"]
                cmd.append("-g" if tool_info.get('global', False) else "--no-save")
                cmd.append(pkg)
                install_cwd = cwd if (cwd and not tool_info.get('global', False)) else None
                if install_cwd: os.makedirs(install_cwd, exist_ok=True)
                result = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=180, cwd=install_cwd, shell=(sys.platform == 'win32'))
                if result.returncode == 0: self.cache[tool] = True; return True
                return False
            elif method == 'pkgmgr':
                if self.base_installer.install_via_pkgmgr(apt_pkg=tool_info.get('apt_pkg'), brew_pkg=tool_info.get('brew_pkg'), choco_pkg=tool_info.get('choco_pkg'), winget_id=tool_info.get('winget_id')):
                    self.cache[tool] = True; return True
                return False
            return False
        except Exception: return False

    # FIX (v2.3.3): --update-deps was declared as a CLI flag but nothing ever
    # called this — restored so the flag isn't a silent no-op.
    def update_project_deps(self, project_dir: str, lang: 'LangType'):
        log("Updating project dependencies...")
        # FIX: every subprocess.run() below previously had no timeout (could
        # hang indefinitely on a network stall) and every success() was
        # printed unconditionally without checking the actual returncode —
        # the same "claims success regardless of outcome" bug already fixed
        # in BaseToolInstaller's winget/choco methods. Centralize both fixes.
        def _run_update(cmd, cwd=None):
            try:
                r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, errors="replace", timeout=300)
                return r.returncode == 0
            except Exception:
                return False
        try:
            if lang == LangType.PYTHON:
                req = os.path.join(project_dir, "requirements.txt")
                if os.path.exists(req):
                    if _run_update([sys.executable, "-m", "pip", "install", "-U", "-r", req]):
                        success("Updated Python requirements")
                    else:
                        warn("pip install -U failed; requirements.txt left unchanged")
                if os.path.exists(os.path.join(project_dir, "Pipfile")) and self.is_installed('pipenv'):
                    if _run_update(["pipenv", "update"], cwd=project_dir):
                        success("Updated Pipfile dependencies")
                    else:
                        warn("pipenv update failed")
            elif lang in (LangType.NODE, LangType.ELECTRON):
                if os.path.exists(os.path.join(project_dir, "package.json")):
                    if os.path.exists(os.path.join(project_dir, "yarn.lock")):
                        if _run_update(["yarn", "upgrade"], cwd=project_dir): success("Updated Yarn dependencies")
                        else: warn("yarn upgrade failed")
                    else:
                        if _run_update(["npm", "update"], cwd=project_dir): success("Updated npm dependencies")
                        else: warn("npm update failed")
            elif lang == LangType.RUST:
                if os.path.exists(os.path.join(project_dir, "Cargo.toml")):
                    if _run_update(["cargo", "update"], cwd=project_dir): success("Updated Cargo dependencies")
                    else: warn("cargo update failed")
            elif lang == LangType.GO:
                if os.path.exists(os.path.join(project_dir, "go.mod")):
                    ok1 = _run_update(["go", "get", "-u", "./..."], cwd=project_dir)
                    ok2 = _run_update(["go", "mod", "tidy"], cwd=project_dir)
                    if ok1 and ok2: success("Updated Go modules")
                    else: warn("go get/mod tidy failed")
            elif lang in (LangType.JAVA, LangType.KOTLIN, LangType.SCALA):
                if os.path.exists(os.path.join(project_dir, "pom.xml")) and self.is_installed('mvn'):
                    if _run_update(["mvn", "versions:use-latest-versions"], cwd=project_dir):
                        success("Updated Maven dependencies")
                    else:
                        warn("mvn versions:use-latest-versions failed")
                elif os.path.exists(os.path.join(project_dir, "build.gradle")) and self.is_installed('gradle'):
                    if _run_update(["gradle", "dependencies", "--refresh-dependencies"], cwd=project_dir):
                        info("Refreshed Gradle dependency resolution cache (note: this does not upgrade versions)")
                    else:
                        warn("gradle --refresh-dependencies failed")
            elif lang == LangType.CSHARP:
                if self.is_installed('dotnet'):
                    if _run_update(["dotnet", "restore", "--force-evaluate"], cwd=project_dir):
                        success("Restored .NET dependencies")
                    else:
                        warn("dotnet restore failed")
            elif lang in (LangType.FLUTTER, LangType.DART):
                if os.path.exists(os.path.join(project_dir, "pubspec.yaml")):
                    if _run_update(["flutter", "pub", "upgrade"], cwd=project_dir):
                        success("Updated Flutter dependencies")
                    else:
                        warn("flutter pub upgrade failed")
            else:
                dim(f"No dependency-update rule for {lang.name}; skipping.")
        except Exception as e:
            warn(f"Dependency update failed: {e}")


# ==================== PROJECT DETECTION ====================

class LangType(Enum):
    PYTHON = auto(); NODE = auto(); ELECTRON = auto(); CPP = auto(); C = auto(); CSHARP = auto()
    GO = auto(); RUST = auto(); JAVA = auto(); KOTLIN = auto(); SCALA = auto(); FLUTTER = auto()
    DART = auto(); LUA = auto(); LOVE2D = auto(); RUBY = auto(); PERL = auto(); NIM = auto()
    ZIG = auto(); CRYSTAL = auto(); GODOT = auto(); UNITY = auto(); UNREAL = auto()
    GAMEMAKER = auto(); RENPY = auto(); ANDROID = auto(); UNKNOWN = auto()

@dataclass
class DetectedProject:
    lang: LangType
    confidence: int
    entry_point: Optional[str]
    build_files: List[str]
    framework: Optional[str] = None
    game_engine: Optional[str] = None
    notes: List[str] = field(default_factory=list)

class ProjectDetector:
    def __init__(self, project_dir: str):
        self.dir = os.path.abspath(project_dir)
        self.files: Set[str] = set()
        self._scan()

    def _scan(self):
        for root, dirs, filenames in os.walk(self.dir):
            dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
            for f in filenames:
                rel = os.path.relpath(os.path.join(root, f), self.dir)
                self.files.add(rel.replace("\\", "/"))

    def _has(self, pattern: str) -> bool:
        p = pattern.lower()
        if p.startswith(".") and p.count(".") == 1:
            return any(os.path.splitext(f.lower())[1] == p for f in self.files)
        return any(f.lower() == p or f.lower().endswith("/" + p) for f in self.files)

    def _has_dir(self, dirname: str) -> bool:
        d = dirname.lower()
        return any(part == d for f in self.files for part in f.lower().split("/")[:-1])

    def _count(self, pattern: str) -> int:
        p = pattern.lower()
        if p.startswith(".") and p.count(".") == 1:
            return sum(1 for f in self.files if os.path.splitext(f.lower())[1] == p)
        return sum(1 for f in self.files if f.lower().endswith(p) or f.lower().endswith("/" + p))

    def _find(self, *patterns: str) -> Optional[str]:
        for p in patterns:
            pl = p.lower()
            for f in self.files:
                fl = f.lower()
                if fl == pl or fl.endswith("/" + pl): return f
        return None

    def _find_all(self, pattern: str) -> List[str]:
        pl = pattern.lower()
        return [f for f in self.files if f.lower() == pl or f.lower().endswith("/" + pl)]

    def detect(self) -> DetectedProject:
        candidates = []

        if self._has("project.godot"): candidates.append(DetectedProject(LangType.GODOT, 100, "project.godot", ["project.godot"], game_engine="Godot"))
        if self._has_dir("assets") and self._has_dir("projectsettings"): candidates.append(DetectedProject(LangType.UNITY, 60, None, [], game_engine="Unity"))
        if self._has(".uproject"): candidates.append(DetectedProject(LangType.UNREAL, 100, None, [], game_engine="Unreal Engine"))
        if self._has("main.lua"): candidates.append(DetectedProject(LangType.LOVE2D, 80, "main.lua", [], game_engine="LÖVE"))

        android_score = 0; android_builds = []
        if self._has("AndroidManifest.xml"):
            android_score += 80; android_builds.append("AndroidManifest.xml")
        for gf in self._find_all("build.gradle") + self._find_all("build.gradle.kts"):
            try:
                with open(os.path.join(self.dir, gf), 'r', encoding='utf-8') as fh:
                    content = fh.read()
                    if 'com.android.application' in content or 'com.android.tools.build' in content:
                        android_score += 50; android_builds.append(gf); break
            except Exception: pass
        if android_score > 0: candidates.append(DetectedProject(LangType.ANDROID, android_score, None, android_builds))

        # FIX (v2.3.3): don't nest the candidate-append inside `if py_count > 0`
        # — a project can legitimately score (e.g. requirements.txt present)
        # even if this particular scan pass counted zero .py files, and the
        # previous structure silently dropped that candidate entirely.
        py_score = 30 if self._has("requirements.txt") else 0
        py_count = self._count(".py")
        py_entry = None
        if py_count > 0:
            py_score += min(py_count * 3, 25)
            py_entry = self._find("main.py", "app.py", "run.py", "gui.py", "__main__.py", "start.py", "game.py")
            if py_entry: py_score += 10
        if py_score > 0:
            candidates.append(DetectedProject(LangType.PYTHON, py_score, py_entry, ["requirements.txt"] if self._has("requirements.txt") else []))

        node_score = 0; node_entry = None; is_electron = False
        pkg_jsons = self._find_all("package.json")
        if pkg_jsons:
            node_score += 40
            for pj in pkg_jsons:
                try:
                    with open(os.path.join(self.dir, pj), 'r', encoding='utf-8') as f:
                        pkg_data = json.load(f)
                        deps = {**pkg_data.get("dependencies", {}), **pkg_data.get("devDependencies", {})}
                        if "electron" in deps:
                            is_electron = True; node_score += 35; break
                except Exception: pass
        js_count = self._count(".js") + self._count(".ts") + self._count(".jsx") + self._count(".tsx")
        if js_count > 0:
            node_score += min(js_count, 20)
            node_entry = self._find("main.js", "index.js", "app.js", "main.ts", "index.ts", "electron.js")
        if node_score > 0:
            candidates.append(DetectedProject(LangType.ELECTRON if is_electron else LangType.NODE, node_score, node_entry, ["package.json"], framework="Electron" if is_electron else None))

        # FIX (v2.3.3): same un-nesting issue as Python — a CMakeLists.txt-only
        # header library should still register as a CPP candidate.
        cpp_score = 40 if self._has("CMakeLists.txt") else 0
        if self._has("Makefile"): cpp_score += 30
        c_count = self._count(".c"); cpp_count = self._count(".cpp") + self._count(".cc") + self._count(".cxx")
        h_count = self._count(".h") + self._count(".hpp")
        if cpp_count > 0: cpp_score += min(cpp_count * 3, 25)
        if c_count > 0: cpp_score += min(c_count * 2, 15)
        if h_count > 0: cpp_score += min(h_count, 10)
        if cpp_score > 0:
            cpp_entry = self._find("main.cpp", "main.c", "winmain.cpp")
            build_files = [f for f in ("CMakeLists.txt", "Makefile") if self._has(f)]
            candidates.append(DetectedProject(LangType.CPP if cpp_count >= c_count else LangType.C, cpp_score, cpp_entry, build_files))

        cs_score = 50 if self._has(".csproj") else 0
        if cs_score: candidates.append(DetectedProject(LangType.CSHARP, cs_score, self._find("Program.cs", "Main.cs"), [f for f in self.files if f.lower().endswith(".csproj")]))

        go_score = 50 if self._has("go.mod") else 0
        if go_score: candidates.append(DetectedProject(LangType.GO, go_score, self._find("main.go"), ["go.mod"]))

        rust_score = 50 if self._has("Cargo.toml") else 0
        if rust_score: candidates.append(DetectedProject(LangType.RUST, rust_score, self._find("main.rs", "lib.rs"), ["Cargo.toml"]))

        # FIX: raw .java files were never counted, only pom.xml/build.gradle
        # presence — a plain javac-only project (no build tool) always
        # scored 0 and fell through to UNKNOWN, even though JavaBuilder
        # handles exactly that case via _compile_jar().
        java_score = 40 if self._has("pom.xml") or self._has("build.gradle") else 0
        java_count = self._count(".java")
        if java_count > 0: java_score += min(java_count * 3, 25)
        if java_score: candidates.append(DetectedProject(LangType.JAVA, java_score, self._find("Main.java"), ["pom.xml"] if self._has("pom.xml") else (["build.gradle"] if self._has("build.gradle") else [])))

        # FIX (v2.3.3, restored): Kotlin/Scala/Ruby/Crystal/Perl detection was
        # missing entirely from this revision, even though JavaBuilder (used
        # for Kotlin/Scala), RubyBuilder, and CrystalBuilder are all present
        # and wired up in BUILDERS below. Without this, those projects always
        # detected as UNKNOWN and required --lang to build at all.
        kt_count = self._count(".kt") + self._count(".kts")
        if kt_count > 0:
            kt_score = min(kt_count * 5, 40) + (30 if self._has("build.gradle.kts") else 0)
            candidates.append(DetectedProject(LangType.KOTLIN, kt_score, None, []))

        scala_count = self._count(".scala")
        if scala_count > 0 or self._has("build.sbt"):
            scala_score = min(scala_count * 5, 40) + (40 if self._has("build.sbt") else 0)
            candidates.append(DetectedProject(LangType.SCALA, scala_score, None, []))

        rb_count = self._count(".rb")
        if rb_count > 0 or self._has("Gemfile"):
            ruby_score = min(rb_count * 5, 30) + (30 if self._has("Gemfile") else 0)
            candidates.append(DetectedProject(LangType.RUBY, ruby_score, self._find("main.rb", "game.rb"), []))

        cr_count = self._count(".cr")
        if cr_count > 0 or self._has("shard.yml"):
            crystal_score = min(cr_count * 10, 50) + (40 if self._has("shard.yml") else 0)
            candidates.append(DetectedProject(LangType.CRYSTAL, crystal_score, self._find("main.cr", "game.cr"), []))

        pl_count = self._count(".pl") + self._count(".pm")
        if pl_count > 0:
            candidates.append(DetectedProject(LangType.PERL, min(pl_count * 5, 30), self._find("main.pl"), []))

        flutter_score = 50 if self._has("pubspec.yaml") else 0
        if flutter_score: candidates.append(DetectedProject(LangType.FLUTTER, flutter_score, "lib/main.dart" if self._has("lib/main.dart") else None, ["pubspec.yaml"]))

        nim_score = 30 if self._has(".nimble") else 0
        if nim_score: candidates.append(DetectedProject(LangType.NIM, nim_score, self._find("main.nim"), []))

        gm_score = 90 if self._has(".yyp") else 0
        if gm_score: candidates.append(DetectedProject(LangType.GAMEMAKER, gm_score, None, []))

        renpy_count = self._count(".rpy")
        if renpy_count > 0:
            candidates.append(DetectedProject(LangType.RENPY, min(renpy_count * 5, 70), None, []))

        zig_score = 40 if self._has("build.zig") else 0
        if zig_score: candidates.append(DetectedProject(LangType.ZIG, zig_score, self._find("main.zig"), ["build.zig"]))

        if not candidates: return DetectedProject(LangType.UNKNOWN, 0, None, [], notes=["No recognizable project files found."])
        return max(candidates, key=lambda x: x.confidence)


# ==================== BUILDERS ====================

class Builder:
    def __init__(self, project: DetectedProject, args, deps: DependencyManager):
        self.project = project
        self.args = args
        self.deps = deps
        self.project_dir = os.path.abspath(args.project or ".")
        self.dist_dir = os.path.abspath(args.output or "dist")
        os.makedirs(self.dist_dir, exist_ok=True)
        self.name = args.name or Path(self.project_dir).name
        self.target_os = getattr(args, 'target_os', 'native')

    def build(self) -> str: raise NotImplementedError

    def _require(self, *tools: str) -> None:
        """
        FIX: every builder called self.deps.ensure(...) and discarded the
        result, trusting that if a tool was truly missing the subsequent
        command would fail with ITS OWN error. In practice that surfaces as
        a raw, confusing exception (confirmed live: a JRE-only Java install
        with no javac reported ensure('javac','java') as fine, and the
        build only failed later with "FileNotFoundError: javac"). Call this
        right after ensure() to fail clearly, up front, instead.
        """
        results = self.deps.ensure(*tools)
        missing = [t for t, ok in results.items() if not ok]
        if missing:
            error(f"Required tool(s) not available and could not be auto-installed: "
                  f"{', '.join(missing)}. Install manually and re-run.")

    def _run(self, cmd: List[str], cwd: str = None, env=None, shell: bool = None) -> subprocess.CompletedProcess:
        if self.args.verbose: log(f"Executing: {' '.join(cmd)}")
        if shell is None: shell = (sys.platform == 'win32')
        # FIX: 600s (10 min) was too tight for a first-run Gradle/Android
        # build (Android SDK + dependency downloads), a first Flutter build,
        # or Electron packaging producing an NSIS installer — all routinely
        # exceed 10 minutes on a fresh machine or slow connection, and would
        # abort with a raw TimeoutExpired that just looked like "Build failed".
        # FIX: errors="replace" - external build tools (npm, gradle, cargo,
        # pip, etc.) can emit non-ASCII output (localized messages, package
        # names) that the system locale's default codec can't decode (the
        # same UnicodeDecodeError class as the earlier console-encoding fix,
        # but here on the *reading* side for arbitrary subprocess output we
        # don't control). Never let that crash an otherwise-successful build.
        return subprocess.run(cmd, cwd=cwd or self.project_dir, capture_output=not self.args.verbose, text=True, errors="replace", env=env or os.environ.copy(), shell=shell, timeout=1800)

    def _find_file(self, pattern: str) -> Optional[str]:
        matches = glob.glob(os.path.join(self.project_dir, pattern), recursive=True)
        return matches[0] if matches else None

    def _print_result(self, artifact_path: str):
        if os.path.exists(artifact_path):
            size = os.path.getsize(artifact_path) / (1024*1024)
            success(f"Build artifact built successfully!")
            info(f"Location: {artifact_path}")
            info(f"Size: {size:.2f} MB")
            return artifact_path
        return None

    def _clean_dist_artifacts(self, ext: str):
        if not os.path.exists(self.dist_dir): return
        for f in os.listdir(self.dist_dir):
            full = os.path.join(self.dist_dir, f)
            if os.path.isfile(full) and f.lower().endswith(ext):
                try: os.remove(full)
                except Exception: pass

    def _find_dist_artifact(self, ext: str) -> Optional[str]:
        """Prioritize top-level artifacts to avoid picking unpacked exes."""
        candidates = []
        for f in os.listdir(self.dist_dir):
            full = os.path.join(self.dist_dir, f)
            if os.path.isfile(full) and f.lower().endswith(ext):
                candidates.append(full)
        if candidates: return max(candidates, key=lambda x: os.path.getmtime(x))
        for root, dirs, files in os.walk(self.dist_dir):
            dirs[:] = [d for d in dirs if not d.startswith('_') and d.lower() not in ('win-unpacked', 'mac', 'linux')]
            for f in files:
                if f.lower().endswith(ext):
                    candidates.append(os.path.join(root, f))
        if candidates: return max(candidates, key=lambda x: os.path.getmtime(x))
        return None


class PythonBuilder(Builder):
    def build(self) -> str:
        self._require('python', 'pip')
        backend = self.args.backend or "auto"
        if backend == "auto":
            backend = "nuitka" if self.deps.is_installed('nuitka') and not self.args.onefile else "pyinstaller"
        self._require(backend)
        return self._build_nuitka() if backend == "nuitka" else self._build_pyinstaller()

    def _build_pyinstaller(self) -> str:
        script = self._resolve_entry()
        cmd = [sys.executable, "-m", "PyInstaller", script, "--noconfirm", "--clean"]
        if not self.args.console: cmd.append("--windowed")
        cmd.append("--onefile" if self.args.onefile else "--onedir")
        cmd.extend(["--name", self.name, "--distpath", self.dist_dir])
        if self.args.icon and os.path.exists(self.args.icon): cmd.extend(["--icon", os.path.abspath(self.args.icon)])
        # FIX: --hidden-imports and --add-data were declared as CLI flags
        # but never read anywhere — silently accepted, silently ignored,
        # which for a hidden-import in particular can produce a build that
        # "succeeds" but crashes at runtime with a missing-module error.
        for hi in (self.args.hidden_imports or []): cmd.extend(["--hidden-import", hi])
        for ad in (self.args.add_data or []): cmd.extend(["--add-data", ad])
        result = self._run(cmd)
        if result.returncode != 0: error("PyInstaller build failed")
        ext = exe_ext(self.target_os)
        exe = os.path.join(self.dist_dir, self.name, f"{self.name}{ext}") if not self.args.onefile else os.path.join(self.dist_dir, f"{self.name}{ext}")
        return self._print_result(exe) or error("Build output not found")

    def _build_nuitka(self) -> str:
        script = self._resolve_entry()
        ext = exe_ext(self.target_os)
        out = os.path.join(self.dist_dir, f"{self.name}{ext}")
        cmd = [sys.executable, "-m", "nuitka", "--standalone", "--lto=yes", "--jobs=4"]
        if not self.args.console: cmd.append("--windows-disable-console")
        if self.args.icon and os.path.exists(self.args.icon):
            icon_path = os.path.abspath(self.args.icon)
            if sys.platform == "darwin": cmd.append(f"--macos-app-icon={icon_path}")
            elif sys.platform == "win32" or self.target_os == "windows": cmd.append(f"--windows-icon-from-ico={icon_path}")
            else: cmd.append(f"--linux-icon={icon_path}")
        # FIX: same dead-flag issue as PyInstaller above. Nuitka's closest
        # equivalents are --include-module for hidden imports and
        # --include-data-files=SRC=DEST for data files.
        for hi in (self.args.hidden_imports or []): cmd.append(f"--include-module={hi}")
        for ad in (self.args.add_data or []):
            sep = ";" if ";" in ad else (":" if ad.count(":") == 1 and sys.platform != "win32" else None)
            cmd.append(f"--include-data-files={ad}" if not sep else f"--include-data-files={ad.replace(sep, '=', 1)}")
        cmd.extend([f"--output-dir={self.dist_dir}", f"--output-filename={self.name}{ext}", script])
        result = self._run(cmd)
        if result.returncode != 0: error("Nuitka build failed")
        # FIX: --standalone places the exe inside a .dist/ subdirectory,
        # not directly in the output dir. Search for it there first.
        if os.path.exists(out):
            return self._print_result(out) or out
        script_base = os.path.splitext(os.path.basename(script))[0]
        dist_subdir = os.path.join(self.dist_dir, f"{script_base}.dist")
        if os.path.isdir(dist_subdir):
            candidate = os.path.join(dist_subdir, f"{self.name}{ext}")
            if os.path.exists(candidate):
                return self._print_result(candidate) or candidate
        # Fallback: search for any exe in dist subdirectories
        for entry in os.listdir(self.dist_dir):
            full = os.path.join(self.dist_dir, entry)
            if os.path.isdir(full):
                for f in os.listdir(full):
                    fp = os.path.join(full, f)
                    if os.path.isfile(fp) and f.endswith(ext or '.exe'):
                        return self._print_result(fp) or fp
        return self._print_result(out) or error("Nuitka build output not found")

    def _resolve_entry(self) -> str:
        if self.args.script: return os.path.abspath(self.args.script)
        if self.project.entry_point: return os.path.join(self.project_dir, self.project.entry_point)
        for name in ("main.py", "app.py", "run.py", "start.py", "game.py", "__main__.py"):
            p = os.path.join(self.project_dir, name)
            if os.path.exists(p): return p
        py_files = glob.glob(os.path.join(self.project_dir, "*.py"))
        if py_files: return py_files[0]
        error("No Python entry point found")


class NodeBuilder(Builder):
    def build(self) -> str:
        self._require('node', 'npm')
        if self.project.lang == LangType.ELECTRON: return self._build_electron()
        entry = self.args.script or self.project.entry_point
        if entry and entry.lower().endswith((".html", ".htm")): return self._build_web_app()
        return self._build_node()

    def _build_node(self) -> str:
        self._require('pkg')
        entry = self.args.script or self.project.entry_point or "index.js"
        ext = exe_ext(self.target_os)
        out = os.path.join(self.dist_dir, f"{self.name}{ext}")
        # FIX (v2.3.3): sys.platform is "darwin" on macOS, not "macos" — pkg's
        # target strings are node18-{win|macos|linux}-x64. Using raw
        # sys.platform produced an invalid "node18-darwin-x64" target.
        if self.target_os == "windows" or sys.platform == "win32":
            pkg_target = "node18-win-x64"
        elif sys.platform == "darwin":
            pkg_target = "node18-macos-x64"
        else:
            pkg_target = "node18-linux-x64"
        cmd = ["pkg", entry, "--output", out, "--target", pkg_target]
        result = self._run(cmd)
        if result.returncode != 0: error("pkg build failed")
        return self._print_result(out) or error("pkg build failed")

    def _build_web_app(self) -> str:
        pkg_path = os.path.join(self.project_dir, "package.json")
        web_root = self.project_dir
        if os.path.exists(pkg_path):
            if not os.path.exists(os.path.join(self.project_dir, "node_modules")): self._run(["npm", "install"])
            with open(pkg_path, 'r', encoding='utf-8') as f: pkg_data = json.load(f)
            if "build" in pkg_data.get("scripts", {}):
                result = self._run(["npm", "run", "build"])
                if result.returncode != 0: error("npm run build failed")
                found = False
                for candidate in ("dist", "build", "out", "public"):
                    candidate_path = os.path.join(self.project_dir, candidate)
                    if os.path.exists(os.path.join(candidate_path, "index.html")):
                        web_root = candidate_path; found = True; break
                # FIX: previously this fell through silently, leaving
                # web_root == self.project_dir (the pre-build SOURCE tree) —
                # shipping unminified source, and potentially .env files or
                # other project internals, with zero warning that the
                # actual build output was never located.
                if not found:
                    error("npm run build succeeded but its output wasn't found in any of "
                          "dist/, build/, out/, or public/ (no index.html there). If your "
                          "project uses a custom output directory, point --script at its "
                          "index.html directly instead.")

        stage_dir = os.path.join(self.dist_dir, "_electron_stage")
        if os.path.exists(stage_dir): shutil.rmtree(stage_dir)
        app_dir = os.path.join(stage_dir, "app")
        shutil.copytree(web_root, app_dir, ignore=shutil.ignore_patterns("node_modules", ".git", "dist", "build", "out"))

        devtools_js = "win.webContents.openDevTools();" if getattr(self.args, "devtools", False) else ""
        with open(os.path.join(app_dir, "main.js"), 'w', encoding='utf-8') as f:
            f.write(f"const {{ app, BrowserWindow }} = require('electron'); "
                    f"app.whenReady().then(() => {{ const win = new BrowserWindow({{width:1280,height:800}}); "
                    f"win.loadFile('index.html'); {devtools_js} }});")

        build_config = {"appId": f"com.polybuild.{self.name}", "productName": self.name, "directories": {"output": self.dist_dir}, "win": {"target": "portable" if self.args.onefile else "nsis"}}
        if self.args.icon and os.path.exists(self.args.icon):
            icon_abs = os.path.abspath(self.args.icon)
            build_config["win"]["icon"] = icon_abs
            build_config["mac"] = {"icon": icon_abs}
            build_config["linux"] = {"icon": icon_abs}
        with open(os.path.join(app_dir, "package.json"), 'w', encoding='utf-8') as f:
            json.dump({"name": self.name.lower(), "version": "1.0.0", "main": "main.js", "build": build_config}, f)

        # FIX: derive platform flag from target_os / sys.platform
        if self.target_os == "windows" or (self.target_os == "native" and sys.platform == "win32"):
            platform_flag = "--win --x64"
            artifact_ext = ".exe"
        elif self.target_os == "native" and sys.platform == "darwin":
            platform_flag = "--mac"
            artifact_ext = ".dmg"
        else:
            platform_flag = "--linux"
            artifact_ext = ".AppImage"
        self._clean_dist_artifacts(artifact_ext)
        self._run(["npm", "install", "--no-save", "electron", "electron-builder"], cwd=app_dir)
        try:
            result = self._run(["npx", "electron-builder"] + platform_flag.split() + ["--publish", "never"], cwd=app_dir)
            if result.returncode != 0:
                error("electron-builder failed")
            exe = self._find_dist_artifact(artifact_ext)
            return self._print_result(exe) or error(f"Electron build produced no {artifact_ext} artifact")
        finally:
            shutil.rmtree(stage_dir, ignore_errors=True)

    def _build_electron(self) -> str:
        pkg_path = os.path.join(self.project_dir, "package.json")
        # FIX: check package.json exists before reading
        if not os.path.exists(pkg_path):
            error("package.json not found. Electron projects require a package.json.")
        with open(pkg_path, 'r', encoding='utf-8') as f: pkg = json.load(f)

        config = pkg.get("build", {})
        config["directories"] = {**(config.get("directories", {})), "output": self.dist_dir}
        if self.args.icon and os.path.exists(self.args.icon):
            icon_abs = os.path.abspath(self.args.icon)
            for platform_key in ("win", "mac", "linux"):
                config[platform_key] = {**(config.get(platform_key, {})), "icon": config.get(platform_key, {}).get("icon", icon_abs)}
        config_path = os.path.join(self.dist_dir, "electron-builder-config.json")
        with open(config_path, 'w', encoding='utf-8') as f: json.dump(config, f, indent=2)

        if not os.path.exists(os.path.join(self.project_dir, "node_modules")): self._run(["npm", "install"])

        # FIX: derive platform flag from target_os / sys.platform
        if self.target_os == "windows" or (self.target_os == "native" and sys.platform == "win32"):
            platform_flag = "--win --x64"
            artifact_ext = ".exe"
        elif self.target_os == "native" and sys.platform == "darwin":
            platform_flag = "--mac"
            artifact_ext = ".dmg"
        else:
            platform_flag = "--linux"
            artifact_ext = ".AppImage"
        self._clean_dist_artifacts(artifact_ext)
        cmd = ["npx", "--yes", "electron-builder"] + platform_flag.split() + ["--publish", "never", "--config", config_path]
        try:
            result = self._run(cmd)
        finally:
            # FIX: clean up temp config file
            if os.path.exists(config_path):
                try: os.remove(config_path)
                except OSError: pass
        if result.returncode != 0: error("electron-builder failed")
        exe = self._find_dist_artifact(artifact_ext)
        return self._print_result(exe) or error(f"Electron build produced no {artifact_ext} artifact")


class CppBuilder(Builder):
    """
    FIX: every build path here used to trust `self.target_os` for naming the
    output file (appending ".exe") while always invoking the *host's* native
    compiler/generator. Requesting --target-os windows from Linux/macOS
    therefore silently produced a native ELF/Mach-O binary mislabeled as a
    Windows .exe — it would never run on Windows, with no error at all.
    Now: if a Windows target is requested from a non-Windows host, we look
    for a real mingw-w64 cross-compiler and use it; if it isn't installed,
    we fail loudly instead of shipping a mislabeled binary.
    """

    def _cross_windows(self) -> bool:
        return self.target_os == "windows" and sys.platform != "win32"

    def _mingw_prefix(self) -> Optional[str]:
        for prefix in ("x86_64-w64-mingw32-", "i686-w64-mingw32-"):
            if shutil.which(f"{prefix}gcc"): return prefix
        return None

    def build(self) -> str:
        if self._cross_windows() and not self._mingw_prefix():
            error("--target-os windows was requested but no mingw-w64 cross-compiler "
                  "(x86_64-w64-mingw32-gcc) was found. Install mingw-w64 "
                  "(e.g. 'sudo apt install mingw-w64' / 'brew install mingw-w64') "
                  "or drop --target-os to build natively for this machine.")
        if os.path.exists(os.path.join(self.project_dir, "CMakeLists.txt")):
            self._require('cmake')
            return self._build_cmake()
        elif os.path.exists(os.path.join(self.project_dir, "Makefile")):
            # FIX (v2.3.3, restored): a Makefile-only C/C++ project was
            # previously falling through to a naive single-shot gcc/g++
            # invocation, which silently mis-builds anything relying on the
            # Makefile's own flags, link order, or multiple targets.
            self._require('make', 'gcc')
            return self._build_make()
        else:
            self._require('gcc', 'g++')
            return self._build_direct()

    def _build_cmake(self) -> str:
        build_dir = os.path.join(self.project_dir, "build")
        os.makedirs(build_dir, exist_ok=True)
        cmake_cmd = ["cmake", "..", "-DCMAKE_BUILD_TYPE=Release"]
        if self._cross_windows():
            prefix = self._mingw_prefix()
            cmake_cmd += ["-DCMAKE_SYSTEM_NAME=Windows",
                          f"-DCMAKE_C_COMPILER={prefix}gcc",
                          f"-DCMAKE_CXX_COMPILER={prefix}g++",
                          f"-DCMAKE_RC_COMPILER={prefix}windres"]
        else:
            gen = "Visual Studio 17 2022" if sys.platform == "win32" else "Unix Makefiles"
            cmake_cmd.append(f"-G{gen}")
        result = self._run(cmake_cmd, cwd=build_dir)
        if result.returncode != 0: error("CMake configuration failed")
        result = self._run(["cmake", "--build", ".", "--config", "Release"], cwd=build_dir)
        if result.returncode != 0: error("CMake build failed")
        ext = exe_ext(self.target_os)
        exe = self._find_exe_in(build_dir)
        if exe:
            dest = os.path.join(self.dist_dir, f"{self.name}{ext}")
            shutil.copy2(exe, dest)
            if sys.platform != "win32" and not self._cross_windows(): os.chmod(dest, 0o755)
            return self._print_result(dest) or dest
        error("No executable found in build output")

    def _build_make(self) -> str:
        env = os.environ.copy()
        if self._cross_windows():
            prefix = self._mingw_prefix()
            env["CC"] = f"{prefix}gcc"
            env["CXX"] = f"{prefix}g++"
        else:
            env["CC"] = env.get("CC", "gcc")
            env["CXX"] = env.get("CXX", "g++")
        env["CFLAGS"] = (env.get("CFLAGS", "") + " -O2").strip()
        env["CXXFLAGS"] = (env.get("CXXFLAGS", "") + " -O2").strip()
        result = self._run(["make", "-j4"], env=env)
        if result.returncode != 0: error("Make build failed")
        ext = exe_ext(self.target_os)
        exe = self._find_exe_in(self.project_dir)
        if exe:
            dest = os.path.join(self.dist_dir, f"{self.name}{ext}")
            shutil.copy2(exe, dest)
            if sys.platform != "win32" and not self._cross_windows(): os.chmod(dest, 0o755)
            return self._print_result(dest) or dest
        error("No executable found")

    def _build_direct(self) -> str:
        entry = self.args.script or self.project.entry_point or "main.cpp"
        is_cpp = entry.endswith((".cpp", ".cc", ".cxx"))
        if self._cross_windows():
            prefix = self._mingw_prefix()
            compiler = f"{prefix}g++" if is_cpp else f"{prefix}gcc"
        else:
            compiler = "g++" if is_cpp else "gcc"
        ext = exe_ext(self.target_os)
        out = os.path.join(self.dist_dir, f"{self.name}{ext}")
        cmd = [compiler, "-O2", "-o", out, os.path.join(self.project_dir, entry)]
        for pattern in ["*.c", "*.cpp", "*.cc", "*.cxx"]:
            for f in glob.glob(os.path.join(self.project_dir, pattern)):
                if os.path.basename(f) != os.path.basename(entry): cmd.append(f)
        result = self._run(cmd)
        if result.returncode != 0: error("Compilation failed")
        if sys.platform != "win32" and not self._cross_windows(): os.chmod(out, 0o755)
        return self._print_result(out) or error("Compilation failed")

    def _find_exe_in(self, directory: str) -> Optional[str]:
        ext = exe_ext(self.target_os)
        for name_variant in [self.name, "main", "Main", "a.out"]:
            expected = os.path.join(directory, f"{name_variant}{ext}")
            if os.path.exists(expected): return expected
        for root, _, files in os.walk(directory):
            for f in files:
                full = os.path.join(root, f)
                if ext and f.lower().endswith(ext): return full
                if not ext and os.access(full, os.X_OK) and not f.endswith(('.o', '.obj', '.a', '.lib', '.so', '.dll', '.dylib', '.pdb', '.txt')): return full
        return None


class CSharpBuilder(Builder):
    def build(self) -> str:
        self._require('dotnet')
        csproj = next((f for f in self.project.build_files if f.endswith(".csproj")), self._find_file("*.csproj"))
        if not csproj: error("No .csproj file found")
        rid = self._get_rid()
        cmd = ["dotnet", "publish", csproj, "-c", "Release", "-r", rid, "--self-contained", "true", "-o", self.dist_dir]
        if self.args.onefile: cmd.extend(["-p:PublishSingleFile=true", "-p:EnableCompressionInSingleFile=true"])
        # .ico embedding is a Windows-PE-resource feature; only meaningful when actually producing a win-* RID.
        if self.args.icon and os.path.exists(self.args.icon) and rid.startswith("win"):
            cmd.append(f"-p:ApplicationIcon={os.path.abspath(self.args.icon)}")
        result = self._run(cmd)
        if result.returncode != 0: error("dotnet publish failed")
        ext = exe_ext(self.target_os)
        exe_path = next((os.path.join(self.dist_dir, f) for f in os.listdir(self.dist_dir) if os.path.isfile(os.path.join(self.dist_dir, f)) and f.lower().endswith(ext)), None)
        return self._print_result(exe_path) or error("dotnet publish output not found")

    def _get_rid(self) -> str:
        # FIX (v2.3.3): the previous version only special-cased
        # target_os == "windows" (never true for the default "native"), then
        # fell straight through linux/else to "osx-x64" — meaning a plain
        # native build run *on Windows itself* published for macOS. Handle
        # native win32/darwin/linux explicitly, same as cross-compile windows.
        if self.target_os == "windows" or (self.target_os == "native" and sys.platform == "win32"):
            return "win-x64"
        if self.target_os == "native" and sys.platform == "darwin":
            return "osx-arm64" if platform.machine() == "arm64" else "osx-x64"
        if sys.platform.startswith("linux"):
            return "linux-x64"
        return "osx-x64"


class GoBuilder(Builder):
    def build(self) -> str:
        self._require('go')
        entry = self.args.script or self.project.entry_point or "."
        ext = exe_ext(self.target_os)
        out = os.path.join(self.dist_dir, f"{self.name}{ext}")
        env = os.environ.copy()
        if self.target_os == "windows": env["GOOS"] = "windows"; env["GOARCH"] = "amd64"
        ldflags = "-s -w"
        if not self.args.console and (self.target_os == "windows" or (self.target_os == "native" and sys.platform == "win32")): ldflags += " -H=windowsgui"
        cmd = ["go", "build", f"-ldflags={ldflags}", "-o", out, entry if os.path.isdir(os.path.join(self.project_dir, entry)) else os.path.join(self.project_dir, entry)]
        result = self._run(cmd, env=env)
        if result.returncode != 0: error("Go build failed")
        if sys.platform != "win32": os.chmod(out, 0o755)
        return self._print_result(out) or error("Go build failed")


class RustBuilder(Builder):
    def build(self) -> str:
        self._require('cargo')
        target = "x86_64-pc-windows-gnu" if self.target_os == "windows" and sys.platform != "win32" else None
        cmd = ["cargo", "build", "--release"]
        if target: cmd.extend(["--target", target])
        result = self._run(cmd)
        if result.returncode != 0: error("Cargo build failed")

        ext = exe_ext(self.target_os)
        crate_name = self._get_crate_name()
        exe_name = f"{crate_name}{ext}"
        release_dir = os.path.join(self.project_dir, "target", target, "release") if target else os.path.join(self.project_dir, "target", "release")
        target_dir = os.path.join(release_dir, exe_name)

        if not os.path.exists(target_dir):
            # FIX (v2.3.3): look only at the top level of the release dir
            # (not a recursive os.walk into deps/ or incremental/), and stop
            # at the first real match instead of letting the loop's `break`
            # merely exit the inner iteration while os.walk kept going and
            # could overwrite target_dir with a wrong file from deps/.
            if os.path.isdir(release_dir):
                for f in sorted(os.listdir(release_dir)):
                    full = os.path.join(release_dir, f)
                    if os.path.isfile(full) and f.lower().endswith(ext) and not f.startswith("lib"):
                        target_dir = full
                        break

        dest = os.path.join(self.dist_dir, f"{self.name}{ext}")
        if os.path.exists(target_dir):
            shutil.copy2(target_dir, dest)
            if sys.platform != "win32": os.chmod(dest, 0o755)
        return self._print_result(dest) or error("Rust build output not found")

    def _get_crate_name(self) -> str:
        # FIX (v2.3.3): do NOT replace '-' with '_' here. Cargo only maps a
        # hyphenated package name to an underscored *library* identifier —
        # the compiled *binary* on disk keeps the hyphens exactly as written
        # in Cargo.toml (e.g. package "my-cool-app" -> target/release/my-cool-app).
        try:
            with open(os.path.join(self.project_dir, "Cargo.toml"), 'r', encoding='utf-8') as f:
                in_package = False
                for line in f.read().split('\n'):
                    s = line.strip()
                    if s.startswith('['): in_package = (s == '[package]')
                    if in_package:
                        m = re.match(r'name\s*=\s*["\'](.+?)["\']', s)
                        if m: return m.group(1)
        except Exception: pass
        return self.name


class JavaBuilder(Builder):
    def build(self) -> str:
        self._require('javac', 'java')
        if self.args.onefile:
            if self.deps.is_installed('jpackage'):
                return self._build_jpackage()
            else:
                warn("--onefile was requested but jpackage is not installed. Falling back to JAR build.")
        if os.path.exists(os.path.join(self.project_dir, "build.gradle")): return self._build_gradle()
        elif os.path.exists(os.path.join(self.project_dir, "pom.xml")): return self._build_maven()
        else:
            jar = self._compile_jar()
            warn("Created basic JAR. Use --onefile for a native EXE via jpackage.")
            return jar

    def _build_jpackage(self) -> str:
        jar = self._compile_jar()
        ext = exe_ext(self.target_os)

        # FIX: --input previously pointed straight at self.dist_dir, which
        # by this point also contains the classes/ folder and MANIFEST.MF
        # left over from _compile_jar(). jpackage bundles EVERYTHING under
        # --input into the shipped app, so those got pulled in too. Stage
        # just the jar in its own directory instead.
        jpkg_input = os.path.join(self.dist_dir, "_jpackage_input")
        if os.path.exists(jpkg_input): shutil.rmtree(jpkg_input)
        os.makedirs(jpkg_input)
        shutil.copy2(jar, os.path.join(jpkg_input, os.path.basename(jar)))

        pkg_type = "exe" if ext == ".exe" else "app-image"
        cmd = ["jpackage", "--input", jpkg_input, "--name", self.name, "--main-jar",
               os.path.basename(jar), "--type", pkg_type, "--dest", self.dist_dir]
        if self.args.icon and os.path.exists(self.args.icon): cmd.extend(["--icon", os.path.abspath(self.args.icon)])
        try:
            result = self._run(cmd)
        finally:
            shutil.rmtree(jpkg_input, ignore_errors=True)
        if result.returncode != 0: error("jpackage failed")

        # FIX: the old code assumed a single flat "{name}{ext}" output path.
        # That's correct for Windows ("--type exe" -> {name}.exe), but
        # "--type app-image" produces a "{name}.app" BUNDLE DIRECTORY on
        # macOS (not a bare "{name}" file) and a bare "{name}/" directory
        # on Linux — the macOS case never matched the old path, so a fully
        # successful jpackage run still reported "jpackage failed".
        if ext == ".exe":
            out = os.path.join(self.dist_dir, f"{self.name}{ext}")
            result_path = self._print_result(out) or error("jpackage failed")
        else:
            mac_bundle = os.path.join(self.dist_dir, f"{self.name}.app")
            plain_dir = os.path.join(self.dist_dir, self.name)
            if os.path.isdir(mac_bundle):
                inner = os.path.join(mac_bundle, "Contents", "MacOS", self.name)
                result_path = self._print_result(inner if os.path.exists(inner) else mac_bundle) or mac_bundle
            elif os.path.isdir(plain_dir):
                inner = os.path.join(plain_dir, "bin", self.name)
                result_path = self._print_result(inner if os.path.exists(inner) else plain_dir) or plain_dir
            else:
                error("jpackage failed")
        # Clean up intermediate build byproducts now embedded in the
        # packaged app — leaving them in dist_dir was just clutter.
        for leftover in (os.path.join(self.dist_dir, "classes"), os.path.join(self.dist_dir, "MANIFEST.MF"), jar):
            try:
                if os.path.isdir(leftover): shutil.rmtree(leftover, ignore_errors=True)
                elif os.path.isfile(leftover): os.remove(leftover)
            except OSError: pass
        return result_path

    def _compile_jar(self) -> str:
        java_files = glob.glob(os.path.join(self.project_dir, "**/*.java"), recursive=True)
        if not java_files: error("No Java files found")
        classes = os.path.join(self.dist_dir, "classes")
        os.makedirs(classes, exist_ok=True)

        argfile = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
                argfile = f.name
                for jf in java_files: f.write(f'"{jf}"\n')
            result = self._run(["javac", "-d", classes, f"@{argfile}"])
        finally:
            if argfile and os.path.exists(argfile): os.remove(argfile)
        if result.returncode != 0: error("Java compilation failed")

        main_class = self._find_main_class(java_files)
        jar = os.path.join(self.dist_dir, f"{self.name}.jar")
        manifest = os.path.join(self.dist_dir, "MANIFEST.MF")
        with open(manifest, 'w', encoding='utf-8') as f:
            f.write(f"Manifest-Version: 1.0\nMain-Class: {main_class or 'Main'}\n\n")
        result = self._run(["jar", "cvfm", jar, manifest, "-C", classes, "."])
        if result.returncode != 0: error("JAR creation failed")
        return jar

    def _find_main_class(self, java_files: List[str]) -> Optional[str]:
        for f in java_files:
            try:
                with open(f, 'r', encoding='utf-8', errors='ignore') as fh:
                    content = fh.read()
                if 'public static void main' in content:
                    package = None
                    for line in content.split('\n'):
                        m = re.match(r'package\s+([\w.]+)\s*;', line.strip())
                        if m: package = m.group(1); break
                    class_name = Path(f).stem
                    return f"{package}.{class_name}" if package else class_name
            except Exception: pass
        return None

    def _build_gradle(self) -> str:
        wrapper = os.path.join(self.project_dir, "gradlew.bat" if sys.platform == "win32" else "gradlew")
        # FIX: missing chmod — AndroidBuilder already guards against this
        # (a repo checked out from a zip download, rather than git clone,
        # loses the executable bit on Unix), but this sibling Gradle path
        # for plain Java/Kotlin/Scala projects didn't, and would fail with
        # "Permission denied" running ./gradlew on Linux/macOS.
        if sys.platform != "win32" and os.path.exists(wrapper):
            os.chmod(wrapper, 0o755)
        result = self._run([wrapper if os.path.exists(wrapper) else "gradle", "build", "-x", "test"])
        if result.returncode != 0: error("Gradle build failed")
        for pattern in ["build/libs/*.jar", "build/distributions/*.exe"]:
            matches = glob.glob(os.path.join(self.project_dir, pattern), recursive=True)
            # FIX: matches[0] took whatever glob happened to return first
            # (OS-dependent, not sorted) — build/libs/ commonly contains
            # both the real jar AND *-sources.jar / *-javadoc.jar siblings,
            # so this could ship a sources/javadoc jar instead of the
            # actual runnable one. Filter those out, then prefer the most
            # recently built file.
            real = [m for m in matches if not m.lower().endswith(("-sources.jar", "-javadoc.jar"))]
            matches = real or matches
            if matches:
                best = max(matches, key=os.path.getmtime)
                dest = os.path.join(self.dist_dir, os.path.basename(best))
                shutil.copy2(best, dest)
                return self._print_result(dest) or dest
        error("No Gradle output found")

    def _build_maven(self) -> str:
        result = self._run(["mvn", "package", "-DskipTests"])
        if result.returncode != 0: error("Maven build failed")
        matches = glob.glob(os.path.join(self.project_dir, "target/*.jar"))
        # FIX: same issue as Gradle above — target/ can contain
        # original-*.jar (left behind by the shade/assembly plugin),
        # *-sources.jar, and *-javadoc.jar alongside the real jar; picking
        # matches[0] blindly could ship the wrong one.
        real = [m for m in matches if not (os.path.basename(m).startswith("original-")
                or m.lower().endswith(("-sources.jar", "-javadoc.jar")))]
        matches = real or matches
        if matches:
            best = max(matches, key=os.path.getmtime)
            dest = os.path.join(self.dist_dir, os.path.basename(best))
            shutil.copy2(best, dest)
            return self._print_result(dest) or dest
        error("No Maven output found")


class AndroidBuilder(Builder):
    def build(self) -> str:
        self._require('java', 'javac')
        wrapper = os.path.join(self.project_dir, "gradlew.bat" if sys.platform == "win32" else "gradlew")
        if not os.path.exists(wrapper):
            if self.deps.is_installed('gradle'): wrapper = "gradle"
            else: error("No Gradle wrapper found.")
        if wrapper != "gradle" and sys.platform != "win32" and os.path.exists(wrapper):
            os.chmod(wrapper, 0o755)

        # FIX: give a clear, actionable error up front instead of letting
        # this fail deep inside a cryptic Gradle stack trace when the
        # Android SDK simply isn't configured.
        has_local_props = os.path.exists(os.path.join(self.project_dir, "local.properties"))
        has_sdk_env = bool(os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT"))
        if not has_local_props and not has_sdk_env:
            warn("Neither local.properties nor ANDROID_HOME/ANDROID_SDK_ROOT is set. "
                 "If the Gradle build fails below, configure the Android SDK location first.")

        self._clean_dist_artifacts(".apk")

        for task, variant in [("assembleRelease", "release"), ("assembleDebug", "debug")]:
            log(f"Running Gradle {task}...")
            result = self._run([wrapper, task, "--no-daemon"])
            if result.returncode == 0:
                apk = self._find_apk(variant)
                if apk:
                    dest = os.path.join(self.dist_dir, os.path.basename(apk))
                    shutil.copy2(apk, dest)
                    return self._print_result(dest) or dest
                else:
                    if task == "assembleRelease": warn("Release build succeeded but APK not found, trying debug...")
                    else: error("Gradle build succeeded but produced no APK")
            else:
                if task == "assembleRelease": warn("Release build failed, trying debug...")
                else: error("Gradle build failed — check Android SDK / Gradle setup")
        error("No APK found in build output")

    def _find_apk(self, variant: str = None) -> Optional[str]:
        """
        FIX: the previous version returned the FIRST .apk hit during an
        unordered os.walk. If a prior run had already produced e.g.
        app-debug.apk and this run's assembleRelease then succeeded, that
        stale debug APK could be returned instead of the one just built —
        silently shipping the wrong variant. Now: prefer a path containing
        the requested variant name, and among any remaining candidates,
        always take the most recently modified file (mirrors the mtime-based
        approach _find_dist_artifact already uses elsewhere in this file).
        """
        search_dirs = [os.path.join(self.project_dir, "app", "build", "outputs", "apk"),
                       os.path.join(self.project_dir, "build", "outputs", "apk")]
        candidates = []
        for search_dir in search_dirs:
            if os.path.exists(search_dir):
                for root, _, files in os.walk(search_dir):
                    for f in files:
                        if f.endswith(".apk"): candidates.append(os.path.join(root, f))
        if not candidates:
            # FIX (v2.3.3): this fallback must NOT reuse EXCLUDED_DIRS — that
            # set now contains 'build' and 'dist', and Gradle/Flutter APK
            # output always lives under a directory named "build". Reusing
            # it made this "search the whole project" fallback structurally
            # incapable of finding anything, defeating its purpose. Use a
            # minimal exclusion set instead, and still skip our own output dir.
            for root, dirs, files in os.walk(self.project_dir):
                dirs[:] = [d for d in dirs if d not in ARTIFACT_SEARCH_EXCLUDED_DIRS and os.path.join(root, d) != self.dist_dir]
                for f in files:
                    if f.endswith(".apk"): candidates.append(os.path.join(root, f))
        if not candidates: return None
        if variant:
            variant_matches = [c for c in candidates if variant in c.lower()]
            if variant_matches: candidates = variant_matches
        return max(candidates, key=os.path.getmtime)


class FlutterBuilder(Builder):
    def build(self) -> str:
        self._require('flutter')
        # FIX: `self.project.lang == LangType.ANDROID` here was dead code —
        # FlutterBuilder is only ever instantiated for LangType.FLUTTER/DART
        # (a detected LangType.ANDROID project is always routed to
        # AndroidBuilder instead, both via the BUILDERS dict and the
        # explicit --target-os android dispatch in main()), so this branch
        # could never actually be true.
        if self.target_os == "android": return self._build_apk()
        return self._build_desktop()

    def _build_apk(self) -> str:
        self._clean_dist_artifacts(".apk")
        result = self._run(["flutter", "build", "apk", "--release"])
        if result.returncode != 0: error("Flutter APK build failed")
        # FIX: hardcoded the exact literal filename "app-release.apk", which
        # doesn't exist for flavored builds (e.g. app-prod-release.apk) —
        # glob for any *.apk in the known output dir instead, preferring
        # the most recently built one.
        apk_dir = os.path.join(self.project_dir, "build", "app", "outputs", "flutter-apk")
        matches = glob.glob(os.path.join(apk_dir, "*.apk"))
        if matches:
            apk = max(matches, key=os.path.getmtime)
            dest = os.path.join(self.dist_dir, f"{self.name}.apk")
            shutil.copy2(apk, dest)
            return self._print_result(dest) or dest
        error("No Flutter APK found")

    def _build_desktop(self) -> str:
        build_target = "windows" if (self.target_os == "windows" or sys.platform == "win32") else "macos" if sys.platform == "darwin" else "linux"
        result = self._run(["flutter", "build", build_target, "--release"])
        if result.returncode != 0: error(f"Flutter {build_target} build failed")
        ext = exe_ext(self.target_os)
        arch = platform.machine().lower()  # e.g. 'x86_64', 'arm64', 'aarch64'
        arch_dir = "arm64" if arch in ("arm64", "aarch64") else "x64"
        build_dirs = [
            os.path.join(self.project_dir, "build", build_target, arch_dir, "runner", "Release"),
            os.path.join(self.project_dir, "build", build_target, "runner", "Release"),
            os.path.join(self.project_dir, "build", build_target, arch_dir, "release", "bundle"),  # Linux
            os.path.join(self.project_dir, "build", build_target, arch_dir, "bundle"),
            # macOS (Apple Silicon / Intel) uses different path structure
            os.path.join(self.project_dir, "build", build_target, "Build", "Products", "Release"),
        ]
        for d in build_dirs:
            if os.path.exists(d):
                for f in os.listdir(d):
                    full_path = os.path.join(d, f)
                    # FIX: os.access(path, os.X_OK) is true for almost any
                    # ordinary directory on Linux (the "execute" bit on a
                    # dir just means "traversable", nearly always set) — so
                    # this loop could match a subdirectory like "lib/"
                    # instead of the real binary and then crash inside
                    # shutil.copy2() (which cannot copy directories). The
                    # .app bundle case is a directory too, but it's handled
                    # explicitly below by name; anything else must be a file.
                    is_app_bundle = os.path.isdir(full_path) and f.endswith('.app')
                    if not is_app_bundle and os.path.isdir(full_path):
                        continue
                    if f.endswith(ext if ext else ".exe") or is_app_bundle or (not ext and os.path.isfile(full_path) and os.access(full_path, os.X_OK)):
                        dest = os.path.join(self.dist_dir, f)
                        # FIX: .app bundles on macOS are directories, use copytree
                        if is_app_bundle:
                            if os.path.exists(dest): shutil.rmtree(dest)
                            shutil.copytree(full_path, dest)
                            # Optionally extract the actual binary from the .app
                            inner_exe = os.path.join(dest, "Contents", "MacOS", os.path.splitext(f)[0])
                            if os.path.exists(inner_exe):
                                return self._print_result(inner_exe) or inner_exe
                        else:
                            shutil.copy2(full_path, dest)
                        return self._print_result(dest) or dest
        error("No Flutter executable found")


class LuaBuilder(Builder):
    def build(self) -> str:
        self._require('love')
        love_file = os.path.join(self.dist_dir, f"{self.name}.love")
        try:
            with zipfile.ZipFile(love_file, 'w', zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files in os.walk(self.project_dir):
                    dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
                    for f in files:
                        if f.endswith(('.lua', '.png', '.jpg', '.ogg', '.wav', '.ttf', '.json', '.xml')):
                            zf.write(os.path.join(root, f), os.path.relpath(os.path.join(root, f), self.project_dir))
            love_exe = shutil.which("love")
            if not love_exe: error("love executable not found. Install LOVE2D.")
            ext = exe_ext(self.target_os)
            out = os.path.join(self.dist_dir, f"{self.name}{ext}")
            with open(out, 'wb') as f, open(love_exe, 'rb') as le, open(love_file, 'rb') as lf:
                f.write(le.read())
                f.write(lf.read())
            if sys.platform != "win32": os.chmod(out, 0o755)
            return self._print_result(out) or error("Love2D build failed")
        finally:
            # FIX: clean up .love archive even on error
            if os.path.exists(love_file):
                try: os.remove(love_file)
                except OSError: pass


class GodotBuilder(Builder):
    def build(self) -> str:
        self._require('godot')
        cfg_path = os.path.join(self.project_dir, "export_presets.cfg")
        if not os.path.exists(cfg_path): error("No export_presets.cfg found. Configure exports in Godot first.")
        with open(cfg_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # FIX: this used to grab names[0] — whichever preset happened to be
        # listed FIRST in the file, with no regard for its actual platform.
        # If the developer's first configured preset was e.g. "Linux/X11"
        # while the user asked for a Windows build (or is running on
        # Windows natively), Godot would export a Linux binary into a file
        # literally named "*.exe" — broken, with no error at all. Parse
        # each [preset.N] block's name= AND platform=, and pick the one
        # that actually matches the requested/host target.
        presets = []  # list of (name, platform)
        cur_name = None
        for line in content.split('\n'):
            s = line.strip()
            if s.startswith('[preset.') and s.endswith(']'):
                cur_name = None
            m = re.match(r'name\s*=\s*"([^"]*)"', s)
            if m: cur_name = m.group(1)
            m = re.match(r'platform\s*=\s*"([^"]*)"', s)
            if m and cur_name is not None:
                presets.append((cur_name, m.group(1)))
                cur_name = None
        if not presets: error("No export presets defined in export_presets.cfg")

        if self.target_os == "windows" or (self.target_os == "native" and sys.platform == "win32"):
            wanted = "windows"
        elif self.target_os == "native" and sys.platform == "darwin":
            wanted = "macos"
        else:
            wanted = "linux"

        def matches(platform_str: str) -> bool:
            p = platform_str.lower()
            if wanted == "windows": return "windows" in p
            if wanted == "macos": return "macos" in p or "mac os" in p or "osx" in p
            return "linux" in p or "x11" in p

        export_preset = next((n for n, p in presets if matches(p)), None)
        if not export_preset:
            export_preset = presets[0][0]
            warn(f"No export preset matches target '{wanted}'; falling back to "
                 f"first configured preset '{export_preset}'. Configure a matching "
                 f"export preset in the Godot editor for a correct build.")

        ext = exe_ext(self.target_os)
        out = os.path.join(self.dist_dir, f"{self.name}{ext}")
        result = self._run(["godot", "--headless", "--path", self.project_dir, "--export-release", export_preset, out])
        if result.returncode != 0: error("Godot export failed. Ensure export templates are installed.")
        if sys.platform != "win32" and wanted != "windows" and os.path.exists(out): os.chmod(out, 0o755)
        return self._print_result(out) or error("Godot export failed")


class NimBuilder(Builder):
    def build(self) -> str:
        self._require('nim')
        entry = self.args.script or self.project.entry_point or "main.nim"
        ext = exe_ext(self.target_os)
        out = os.path.join(self.dist_dir, f"{self.name}{ext}")
        cross_windows = self.target_os == "windows" and sys.platform != "win32"
        cmd = ["nim", "c", "-d:release", "--opt:speed", "-o:" + out]
        if cross_windows:
            # FIX: previously this only changed the output filename to
            # "*.exe" without ever telling Nim to actually target Windows —
            # it silently compiled a native ELF/Mach-O binary and named it
            # .exe. Real cross-compilation needs --os/--cpu plus a mingw
            # compiler override; fail loudly if mingw isn't available
            # instead of shipping a mislabeled, non-functional file.
            mingw_gcc = shutil.which("x86_64-w64-mingw32-gcc")
            if not mingw_gcc:
                error("--target-os windows was requested but no mingw-w64 cross-compiler "
                      "(x86_64-w64-mingw32-gcc) was found. Install mingw-w64 to cross-build "
                      "Nim for Windows, or drop --target-os to build natively.")
            cmd += ["--os:windows", "--cpu:amd64", "--gcc.exe:x86_64-w64-mingw32-gcc",
                    "--gcc.linkerexe:x86_64-w64-mingw32-gcc"]
        if not self.args.console: cmd.append("--app:gui")
        cmd.append(os.path.join(self.project_dir, entry))
        result = self._run(cmd)
        if result.returncode != 0: error("Nim compilation failed")
        if sys.platform != "win32" and not cross_windows: os.chmod(out, 0o755)
        return self._print_result(out) or error("Nim compilation failed")


class ZigBuilder(Builder):
    def build(self) -> str:
        self._require('zig')
        ext = exe_ext(self.target_os)
        if os.path.exists(os.path.join(self.project_dir, "build.zig")):
            cmd = ["zig", "build", "-Doptimize=ReleaseFast"]
            if self.target_os == "windows" and sys.platform != "win32": cmd.append("-Dtarget=x86_64-windows-gnu")
            result = self._run(cmd)
            if result.returncode != 0: error("Zig build failed")
            out = os.path.join(self.project_dir, "zig-out", "bin", f"{self.name}{ext}")
            dest = os.path.join(self.dist_dir, f"{self.name}{ext}")
            if os.path.exists(out):
                shutil.copy2(out, dest)
                if sys.platform != "win32": os.chmod(dest, 0o755)
            return self._print_result(dest) or error("Zig build output not found")
        else:
            entry = self.args.script or self.project.entry_point or "main.zig"
            out = os.path.join(self.dist_dir, f"{self.name}{ext}")
            cmd = ["zig", "build-exe", "-O", "ReleaseFast"]
            if self.target_os == "windows" and sys.platform != "win32": cmd.extend(["-target", "x86_64-windows-gnu"])
            cmd.extend(["-femit-bin=" + out, os.path.join(self.project_dir, entry)])
            result = self._run(cmd)
            if result.returncode != 0: error("Zig compilation failed")
            if sys.platform != "win32": os.chmod(out, 0o755)
            return self._print_result(out) or error("Zig compilation failed")


class CrystalBuilder(Builder):
    def build(self) -> str:
        self._require('crystal')
        # FIX: previously this flag was silently ignored — a user asking for
        # --target-os windows on Linux/macOS got a native binary with no
        # warning that cross-compilation never happened.
        if self.target_os == "windows" and sys.platform != "win32":
            error("Crystal cross-compilation to Windows is not supported by this tool "
                  "(it requires a full MSVC toolchain on the target). Build on a Windows "
                  "machine, or drop --target-os to build natively for this host.")
        entry = self.args.script or self.project.entry_point or "main.cr"
        out = os.path.join(self.dist_dir, self.name)
        result = self._run(["crystal", "build", "--release", "--no-debug", "-o", out, os.path.join(self.project_dir, entry)])
        if result.returncode != 0: error("Crystal build failed")
        exe = out + ".exe" if sys.platform == "win32" else out
        if sys.platform != "win32" and os.path.exists(exe): os.chmod(exe, 0o755)
        return self._print_result(exe) or error("Crystal build failed")


class RubyBuilder(Builder):
    def build(self) -> str:
        self._require('ruby')
        # FIX: ocra is a Windows-only packaging tool by design (it bundles a
        # Windows Ruby runtime into a PE executable) — it cannot produce a
        # working Linux/macOS binary, and cannot cross-build a Windows exe
        # from a non-Windows host either. Previously this was invoked
        # unconditionally on every platform and would just fail confusingly
        # (or silently write a bogus, non-executable "out" file with no
        # .exe extension). Fail clearly up front instead.
        if sys.platform != "win32":
            error("Native executable packaging for Ruby (ocra) only works when running "
                  "on Windows itself — it cannot cross-build a Windows .exe from "
                  f"{sys.platform}, and cannot produce a Linux/macOS binary at all. "
                  "Run this build on a Windows machine, or distribute the script directly.")
        # FIX: previously this ran `gem install ocra` unconditionally on
        # every single build (slow, network-dependent, and a hard failure
        # if offline) instead of checking whether it's already present.
        if not shutil.which("ocra"):
            log("Installing ocra via gem...")
            gem_result = subprocess.run(["gem", "install", "ocra"], capture_output=True, text=True, errors="replace", timeout=120)
            if gem_result.returncode != 0:
                error(f"Failed to install ocra via gem: {gem_result.stderr.strip()}")
        entry = self.args.script or self.project.entry_point or "main.rb"
        ext = exe_ext(self.target_os)
        out = os.path.join(self.dist_dir, f"{self.name}{ext}")
        result = self._run(["ocra", "--windows", "--output", out, os.path.join(self.project_dir, entry)])
        if result.returncode != 0: error("OCRA build failed. Install manually: gem install ocra")
        return self._print_result(out) or error("OCRA build failed")


# ==================== BUILDER FACTORY ====================

BUILDERS = {
    LangType.PYTHON: PythonBuilder, LangType.NODE: NodeBuilder, LangType.ELECTRON: NodeBuilder,
    LangType.CPP: CppBuilder, LangType.C: CppBuilder, LangType.CSHARP: CSharpBuilder,
    LangType.GO: GoBuilder, LangType.RUST: RustBuilder, LangType.JAVA: JavaBuilder,
    LangType.KOTLIN: JavaBuilder, LangType.SCALA: JavaBuilder, LangType.FLUTTER: FlutterBuilder,
    LangType.DART: FlutterBuilder, LangType.LUA: LuaBuilder, LangType.LOVE2D: LuaBuilder,
    LangType.NIM: NimBuilder, LangType.ZIG: ZigBuilder, LangType.CRYSTAL: CrystalBuilder,
    LangType.RUBY: RubyBuilder, LangType.GODOT: GodotBuilder, LangType.ANDROID: AndroidBuilder,
}


# ==================== MAIN ====================

def main():
    parser = argparse.ArgumentParser(description=f"PolyBuild Pro v{VERSION} - Universal App & Game Builder")
    parser.add_argument("--project", "-p", help="Project directory")
    parser.add_argument("--script", "-s", help="Override entry point")
    parser.add_argument("--name", "-n", help="Output name")
    parser.add_argument("--icon", "-i", help="Path to .ico file")
    parser.add_argument("--output", "-o", default="dist", help="Output directory")
    parser.add_argument("--lang", choices=["python", "node", "electron", "cpp", "c", "csharp", "go", "rust", "java", "kotlin", "scala", "flutter", "dart", "lua", "love2d", "nim", "zig", "crystal", "ruby", "godot", "android"], help="Force language")
    parser.add_argument("--target-os", choices=["native", "windows", "android"], default="native", help="Target OS")
    parser.add_argument("--onefile", "-f", action="store_true", help="Single executable")
    parser.add_argument("--console", "-c", action="store_true", help="Keep console window")
    parser.add_argument("--devtools", action="store_true", help="Open DevTools in Electron builds")
    parser.add_argument("--backend", choices=["auto", "pyinstaller", "nuitka"], default="auto")
    parser.add_argument("--auto-detect", action="store_true", default=True)
    parser.add_argument("--no-auto-detect", dest="auto_detect", action="store_false")
    parser.add_argument("--hidden-imports", action="append")
    parser.add_argument("--add-data", action="append")
    parser.add_argument("--update", action="store_true", help="Update PolyBuild")
    parser.add_argument("--update-deps", action="store_true", help="Update project dependencies")
    parser.add_argument("--check-tools", action="store_true", help="Check build tools status")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    print(f"\n{Colors.CYAN}{Colors.BOLD}PolyBuild Pro v{VERSION} - Universal Builder{Colors.END}\n")

    if args.update:
        SelfUpdater.check_update(force=True); return
    if args.check_tools:
        deps = DependencyManager()
        for tool in sorted(deps.TOOLS.keys()):
            installed = deps.is_installed(tool)
            print(f"  {'✓' if installed else '✗'} {tool}")
        return

    project_dir = os.path.abspath(args.project or ".")
    if args.lang:
        lang_map = {k: getattr(LangType, k.upper()) for k in ["python", "node", "electron", "cpp", "c", "csharp", "go", "rust", "java", "kotlin", "scala", "flutter", "dart", "lua", "love2d", "nim", "zig", "crystal", "ruby", "godot", "android"]}
        detected = DetectedProject(lang_map[args.lang], 100, args.script or None, [], notes=["Forced by user"])
    else:
        detected = ProjectDetector(project_dir).detect()

    print(f"{'─'*40}")
    print(f"Language:     {detected.lang.name}")
    print(f"Confidence:   {detected.confidence}%")
    print(f"Target OS:    {args.target_os}")
    print(f"{'─'*40}\n")

    if detected.lang == LangType.UNKNOWN: error("Could not detect project type. Use --lang to force.")

    deps = DependencyManager()

    if args.update_deps:
        deps.update_project_deps(project_dir, detected.lang)

    builder_class = BUILDERS.get(detected.lang)

    if args.target_os == "android":
        if detected.lang in (LangType.FLUTTER, LangType.DART): builder_class = FlutterBuilder
        elif detected.lang in (LangType.JAVA, LangType.KOTLIN, LangType.ANDROID): builder_class = AndroidBuilder
        else: error(f"Android builds are not supported for {detected.lang.name}.")

    if not builder_class:
        # FIX: Unity, Unreal, and Perl are all actively detected by
        # ProjectDetector (Unreal at confidence 100 — the same max score as
        # Godot) but have no registered builder. Previously this produced a
        # generic, unhelpful "No builder available for X" message with no
        # explanation of why or what to do instead.
        UNSUPPORTED_NOTES = {
            LangType.UNITY: "Unity projects must be built via Unity's own Batchmode/CLI "
                             "(e.g. 'Unity -batchmode -executeMethod BuildScript.Build') "
                             "or the Unity Editor — polybuild does not automate this.",
            LangType.UNREAL: "Unreal Engine projects must be built via UnrealBuildTool/UAT "
                              "(e.g. RunUAT.sh/bat BuildCookRun) or the Unreal Editor — "
                              "polybuild does not automate this.",
            LangType.PERL: "No native Perl packager is wired up in this tool "
                           "(e.g. pp / PAR::Packer); distribute the script directly, "
                           "or package it manually.",
            LangType.GAMEMAKER: "GameMaker projects must be exported via the GameMaker IDE/CLI.",
            LangType.RENPY: "Ren'Py projects should be exported via the Ren'Py launcher's "
                             "own 'Build Distributions' feature.",
        }
        note = UNSUPPORTED_NOTES.get(detected.lang)
        error(f"No builder available for {detected.lang.name}." + (f" {note}" if note else ""))

    builder = builder_class(detected, args, deps)
    try:
        start_time = datetime.now()
        artifact_path = builder.build()
        elapsed = (datetime.now() - start_time).total_seconds()
        print(f"\n{Colors.GREEN}{Colors.BOLD}BUILD SUCCESSFUL")
        print(f"Output: {artifact_path}")
        print(f"Time: {elapsed:.1f}s{Colors.END}\n")
    except KeyboardInterrupt:
        warn("\nBuild interrupted by user"); sys.exit(1)
    except Exception as e:
        error(f"Build failed: {str(e)}")

if __name__ == "__main__":
    main()
