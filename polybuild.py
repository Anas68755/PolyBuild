#!/usr/bin/env python3
"""
PolyBuild Pro v2.3.1 - Universal App & Game Builder (EXE / APK / Native)
Auto-detects 25+ languages, self-updates, auto-manages & installs dependencies.

FIXES (v2.3.1):
  - Java: Uses @argfile to prevent command-line length crashes on large projects
  - Rust: Fallback to recursive search for binaries in workspaces/multi-bin crates
  - Electron: Prioritizes top-level installers over win-unpacked/ executables
  - Android: Fixed assembleRelease fallback logic and stale APK discovery in dist/
  - Python: Better fallback entry point detection (avoids picking utils.py)
  - C++: Restrictive globbing to prevent compiling .cache/.class files
  - Electron: Forces output directory to dist/ even if user config exists
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
from typing import List, Dict, Optional, Tuple, Set
from enum import Enum, auto
from datetime import datetime


# ==================== VERSION & UPDATE ====================
VERSION = "2.3.1"
UPDATE_URL = os.environ.get("POLYBUILD_UPDATE_URL", "")
VERSION_CHECK_URL = os.environ.get("POLYBUILD_VERSION_URL", "")


class Colors:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    MAGENTA = '\033[95m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    END = '\033[0m'


def log(msg, color=Colors.BLUE):
    print(f"{color}[*] {msg}{Colors.END}")

def success(msg):
    print(f"{Colors.GREEN}[✓] {msg}{Colors.END}")

def warn(msg):
    print(f"{Colors.YELLOW}[!] {msg}{Colors.END}")

def error(msg):
    print(f"{Colors.RED}[✗] {msg}{Colors.END}")
    sys.exit(1)

def info(msg):
    print(f"{Colors.CYAN}[i] {msg}{Colors.END}")

def dim(msg):
    print(f"{Colors.DIM}{msg}{Colors.END}")


# ==================== HELPERS ====================

EXCLUDED_DIRS: Set[str] = {
    '.git', 'node_modules', '__pycache__', '.venv', 'venv', 'env',
    '.idea', '.vscode', 'target', 'zig-cache', 'zig-out',
    '.gradle', '.android', 'DerivedData', '.cargo', 'cache',
    '.next', '.nuxt', '.svelte-kit', '.angular',
    'Pods', '.symlinks', 'dist', 'build',
}


def exe_ext(target_os: str = "native") -> str:
    if target_os == "windows":
        return ".exe"
    if target_os == "native":
        return ".exe" if sys.platform == "win32" else ""
    return ""


# ==================== SELF-UPDATE SYSTEM ====================

class SelfUpdater:
    @staticmethod
    def check_update(force: bool = False) -> bool:
        if not VERSION_CHECK_URL or not UPDATE_URL:
            return False
        try:
            req = urllib.request.Request(VERSION_CHECK_URL, headers={'User-Agent': 'PolyBuild-Updater'})
            with urllib.request.urlopen(req, timeout=5) as resp:
                remote_data = json.loads(resp.read().decode('utf-8'))
            remote_version = remote_data.get('version', '0.0.0')
            if SelfUpdater._version_compare(remote_version, VERSION) > 0:
                pad = max(0, 21 - len(VERSION) - len(remote_version))
                print(f"\n{Colors.YELLOW}╔═══════════════════════════════════════════════════╗")
                print(f"║  Update available: v{VERSION} → v{remote_version}{' ' * pad}║")
                print(f"║  {remote_data.get('changelog', 'Bug fixes and improvements.')[:47]:<47} ║")
                print(f"╚═══════════════════════════════════════════════════╝{Colors.END}\n")
                if force or input("Update now? [Y/n]: ").lower() in ('', 'y', 'yes'):
                    return SelfUpdater._perform_update(remote_data)
            return False
        except Exception:
            return False

    @staticmethod
    def _version_compare(v1: str, v2: str) -> int:
        n1 = [int(x) for x in re.sub(r'[^0-9.]', '', v1).split('.')]
        n2 = [int(x) for x in re.sub(r'[^0-9.]', '', v2).split('.')]
        return (n1 > n2) - (n1 < n2)

    @staticmethod
    def _perform_update(update_data: dict) -> bool:
        try:
            req = urllib.request.Request(update_data.get('download_url', UPDATE_URL), headers={'User-Agent': 'PolyBuild-Updater'})
            with urllib.request.urlopen(req, timeout=30) as resp:
                new_code = resp.read().decode('utf-8')
            if 'sha256' in update_data and hashlib.sha256(new_code.encode()).hexdigest() != update_data['sha256']:
                error("Update verification failed (hash mismatch)")
            ast.parse(new_code)
            script_path = os.path.abspath(sys.argv[0])
            shutil.copy2(script_path, script_path + ".backup")
            with open(script_path, 'w', encoding='utf-8') as f:
                f.write(new_code)
            success(f"Updated to v{update_data['version']}! Restart to use new version.")
            return True
        except Exception as e:
            error(f"Update failed: {e}")
            return False


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
        result = subprocess.run(sudo + ["apt-get", "install", "-y"] + package.split(), capture_output=True, text=True, timeout=300)
        return result.returncode == 0

    def install_via_brew(self, package: str) -> bool:
        if not self._brew_available: return False
        result = subprocess.run(["brew", "install"] + package.split(), capture_output=True, text=True, timeout=300)
        return result.returncode == 0

    def install_via_winget(self, package_id: str) -> bool:
        if not self._winget_available: return False
        result = subprocess.run(["winget", "install", "--id", package_id, "-e", "--accept-source-agreements", "--accept-package-agreements"], capture_output=True, text=True, timeout=300)
        return result.returncode == 0

    def install_via_choco(self, package: str) -> bool:
        if not self._choco_available: return False
        result = subprocess.run(["choco", "install", package, "-y", "--no-progress"], capture_output=True, text=True, timeout=300)
        return result.returncode == 0

    def install_via_pkgmgr(self, apt_pkg=None, brew_pkg=None, choco_pkg=None, winget_id=None) -> bool:
        if sys.platform == "win32":
            return (winget_id and self.install_via_winget(winget_id)) or (choco_pkg and self.install_via_choco(choco_pkg))
        elif sys.platform == "darwin":
            return brew_pkg and self.install_via_brew(brew_pkg)
        elif sys.platform.startswith("linux"):
            return apt_pkg and self.install_via_apt(apt_pkg)
        return False

    def install_nodejs(self) -> bool:
        if self.install_via_pkgmgr(apt_pkg="nodejs npm", brew_pkg="node", choco_pkg="nodejs", winget_id="OpenJS.NodeJS"): return True
        return False

    def install_python(self) -> bool: return False
    def install_git(self) -> bool: return self.install_via_pkgmgr(apt_pkg="git", brew_pkg="git", choco_pkg="git", winget_id="Git.Git")
    def install_go(self) -> bool: return self.install_via_pkgmgr(apt_pkg="golang-go", brew_pkg="go", choco_pkg="golang", winget_id="GoLang.Go")
    def install_rust(self) -> bool: return self.install_via_pkgmgr(brew_pkg="rust", choco_pkg="rust", winget_id="Rustlang.Rustup")
    def install_dotnet(self) -> bool: return self.install_via_pkgmgr(apt_pkg="dotnet-sdk-8.0", brew_pkg="dotnet-sdk", choco_pkg="dotnet-sdk", winget_id="Microsoft.DotNet.SDK.8")
    def install_java(self) -> bool: return self.install_via_pkgmgr(apt_pkg="openjdk-21-jdk", brew_pkg="openjdk", choco_pkg="openjdk", winget_id="EclipseAdoptium.Temurin.21.JDK")
    def install_cmake(self) -> bool: return self.install_via_pkgmgr(apt_pkg="cmake", brew_pkg="cmake", choco_pkg="cmake", winget_id="Kitware.CMake")
    def install_mingw(self) -> bool: return self.install_via_pkgmgr(apt_pkg="build-essential", brew_pkg="gcc", choco_pkg="mingw")
    def install_flutter(self) -> bool: return self.install_via_pkgmgr(brew_pkg="--cask flutter", winget_id="Google.Flutter")
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
        'python': {'check': ['python', '--version'], 'install_fn': 'python'},
        'pip': {'check': ['pip', '--version']},
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
        if tool in self.BUNDLED_TOOLS and self.is_installed(self.BUNDLED_TOOLS[tool]):
            self.cache[tool] = True
            return True

        tool_info = self.TOOLS.get(tool)
        if not tool_info:
            self.cache[tool] = False
            return False

        try:
            result = subprocess.run(tool_info['check'], capture_output=True, text=True, timeout=10, shell=(sys.platform == 'win32'))
            installed = result.returncode == 0
            self.cache[tool] = installed
            return installed
        except Exception:
            self.cache[tool] = False
            return False

    def ensure(self, *tools: str, auto_install: bool = True, cwd: str = None) -> Dict[str, bool]:
        results = {}
        missing = []
        for tool in tools:
            if self.is_installed(tool):
                results[tool] = True
            else:
                results[tool] = False
                missing.append(tool)

        if missing and auto_install:
            for tool in missing:
                results[tool] = self._install(tool, cwd=cwd)
                for bundled, parent in self.BUNDLED_TOOLS.items():
                    if parent == tool and results[tool]:
                        self.cache[bundled] = True
                        if bundled in missing: results[bundled] = True
        return results

    def _install(self, tool: str, cwd: str = None) -> bool:
        tool_info = self.TOOLS.get(tool, {})
        if tool in self.BUNDLED_TOOLS:
            parent = self.BUNDLED_TOOLS[tool]
            if self.is_installed(parent):
                self.cache[tool] = True
                return True
            return self._install(parent, cwd=cwd)

        install_fn = tool_info.get('install_fn')
        if install_fn:
            installer_method = getattr(self.base_installer, f"install_{install_fn}", None)
            if installer_method:
                result = installer_method()
                if result:
                    self.cache[tool] = True
                    for bundled, parent in self.BUNDLED_TOOLS.items():
                        if parent == tool: self.cache[bundled] = True
                return result
            return False

        method = tool_info.get('install', 'manual')
        try:
            if method == 'pip':
                cmd = [sys.executable, "-m", "pip", "install", "--upgrade", tool_info.get('pkg', tool)]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                if result.returncode == 0:
                    self.cache[tool] = True
                    return True
                return False
            elif method == 'npm':
                pkg = tool_info.get('pkg', tool)
                if not self.is_installed('node') and not self._install('node'): return False
                cmd = ["npm", "install"]
                cmd.append("-g" if tool_info.get('global', False) else "--no-save")
                cmd.append(pkg)
                install_cwd = cwd if (cwd and not tool_info.get('global', False)) else None
                if install_cwd: os.makedirs(install_cwd, exist_ok=True)
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=180, cwd=install_cwd, shell=(sys.platform == 'win32'))
                if result.returncode == 0:
                    self.cache[tool] = True
                    return True
                return False
            elif method == 'pkgmgr':
                if self.base_installer.install_via_pkgmgr(apt_pkg=tool_info.get('apt_pkg'), brew_pkg=tool_info.get('brew_pkg'), choco_pkg=tool_info.get('choco_pkg'), winget_id=tool_info.get('winget_id')):
                    self.cache[tool] = True
                    return True
                return False
            return False
        except Exception:
            return False


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
                if fl == pl or fl.endswith("/" + pl):
                    return f
        return None

    def _find_all(self, pattern: str) -> List[str]:
        pl = pattern.lower()
        return [f for f in self.files if f.lower() == pl or f.lower().endswith("/" + pl)]

    def detect(self) -> DetectedProject:
        candidates = []
        
        godot_score = 100 if self._has("project.godot") else 0
        if godot_score: candidates.append(DetectedProject(LangType.GODOT, godot_score, "project.godot", ["project.godot"], game_engine="Godot"))
        
        unity_score = 60 if (self._has_dir("assets") and self._has_dir("projectsettings")) else 0
        if unity_score: candidates.append(DetectedProject(LangType.UNITY, unity_score, None, [], game_engine="Unity"))
        
        unreal_score = 100 if self._has(".uproject") else 0
        if unreal_score: candidates.append(DetectedProject(LangType.UNREAL, unreal_score, None, [], game_engine="Unreal Engine"))
        
        love_score = 80 if self._has("main.lua") else 0
        if love_score: candidates.append(DetectedProject(LangType.LOVE2D, love_score, "main.lua", [], game_engine="LÖVE"))

        android_score = 0
        android_builds = []
        if self._has("AndroidManifest.xml"):
            android_score += 80; android_builds.append("AndroidManifest.xml")
        for gf in self._find_all("build.gradle") + self._find_all("build.gradle.kts"):
            try:
                with open(os.path.join(self.dir, gf), 'r', encoding='utf-8') as fh:
                    if 'com.android.application' in fh.read() or 'com.android.tools.build' in fh.read():
                        android_score += 50; android_builds.append(gf); break
            except Exception: pass
        if android_score > 0: candidates.append(DetectedProject(LangType.ANDROID, android_score, None, android_builds))

        py_score = 30 if self._has("requirements.txt") else 0
        py_count = self._count(".py")
        if py_count > 0:
            py_score += min(py_count * 3, 25)
            py_entry = self._find("main.py", "app.py", "run.py", "gui.py", "__main__.py", "start.py", "game.py")
            if py_entry: py_score += 10
            candidates.append(DetectedProject(LangType.PYTHON, py_score, py_entry, ["requirements.txt"] if py_score else []))

        node_score = 0; node_entry = None; is_electron = False
        pkg_jsons = self._find_all("package.json")
        if pkg_jsons:
            node_score += 40
            for pj in pkg_jsons:
                try:
                    with open(os.path.join(self.dir, pj), 'r', encoding='utf-8') as f:
                        if "electron" in {**json.load(f).get("dependencies", {}), **json.load(f).get("devDependencies", {})}:
                            is_electron = True; node_score += 35; break
                except Exception: pass
        js_count = self._count(".js") + self._count(".ts") + self._count(".jsx") + self._count(".tsx")
        if js_count > 0:
            node_score += min(js_count, 20)
            node_entry = self._find("main.js", "index.js", "app.js", "main.ts", "index.ts", "electron.js")
        if node_score > 0:
            candidates.append(DetectedProject(LangType.ELECTRON if is_electron else LangType.NODE, node_score, node_entry, ["package.json"], framework="Electron" if is_electron else None))

        cpp_score = 40 if self._has("CMakeLists.txt") else 0
        c_count = self._count(".c"); cpp_count = self._count(".cpp") + self._count(".cc") + self._count(".cxx")
        if cpp_count > 0 or c_count > 0:
            cpp_entry = self._find("main.cpp", "main.c", "winmain.cpp")
            candidates.append(DetectedProject(LangType.CPP if cpp_count >= c_count else LangType.C, cpp_score, cpp_entry, ["CMakeLists.txt"] if cpp_score else []))

        cs_score = 50 if self._has(".csproj") else 0
        if cs_score: candidates.append(DetectedProject(LangType.CSHARP, cs_score, self._find("Program.cs", "Main.cs"), [f for f in self.files if f.lower().endswith(".csproj")]))

        go_score = 50 if self._has("go.mod") else 0
        if go_score: candidates.append(DetectedProject(LangType.GO, go_score, self._find("main.go"), ["go.mod"]))

        rust_score = 50 if self._has("Cargo.toml") else 0
        if rust_score: candidates.append(DetectedProject(LangType.RUST, rust_score, self._find("main.rs", "lib.rs"), ["Cargo.toml"]))

        java_score = 40 if self._has("pom.xml") or self._has("build.gradle") else 0
        if java_score: candidates.append(DetectedProject(LangType.JAVA, java_score, self._find("Main.java"), ["pom.xml"] if self._has("pom.xml") else ["build.gradle"]))

        flutter_score = 50 if self._has("pubspec.yaml") else 0
        if flutter_score: candidates.append(DetectedProject(LangType.FLUTTER, flutter_score, "lib/main.dart" if self._has("lib/main.dart") else None, ["pubspec.yaml"]))

        nim_score = 30 if self._has(".nimble") else 0
        if nim_score: candidates.append(DetectedProject(LangType.NIM, nim_score, self._find("main.nim"), []))
        
        zig_score = 40 if self._has("build.zig") else 0
        if zig_score: candidates.append(DetectedProject(LangType.ZIG, zig_score, self._find("main.zig"), ["build.zig"]))

        if not candidates:
            return DetectedProject(LangType.UNKNOWN, 0, None, [], notes=["No recognizable project files found."])
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

    def _run(self, cmd: List[str], cwd: str = None, env=None, shell: bool = None) -> subprocess.CompletedProcess:
        if self.args.verbose: log(f"Executing: {' '.join(cmd)}")
        if shell is None: shell = (sys.platform == 'win32')
        return subprocess.run(cmd, cwd=cwd or self.project_dir, capture_output=not self.args.verbose, text=True, env=env or os.environ.copy(), shell=shell, timeout=600)

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
                os.remove(full)

    def _find_dist_artifact(self, ext: str) -> Optional[str]:
        """FIX: Prioritize top-level artifacts to avoid picking unpacked exes."""
        candidates = []
        # 1. Search top-level of dist_dir
        for f in os.listdir(self.dist_dir):
            full = os.path.join(self.dist_dir, f)
            if os.path.isfile(full) and f.lower().endswith(ext):
                candidates.append(full)
        if candidates:
            return max(candidates, key=lambda x: os.path.getmtime(x))
        # 2. Search subdirectories, skipping staging/unpacked dirs
        for root, dirs, files in os.walk(self.dist_dir):
            dirs[:] = [d for d in dirs if not d.startswith('_') and d.lower() not in ('win-unpacked', 'mac', 'linux')]
            for f in files:
                if f.lower().endswith(ext):
                    candidates.append(os.path.join(root, f))
        if candidates:
            return max(candidates, key=lambda x: os.path.getmtime(x))
        return None


class PythonBuilder(Builder):
    def build(self) -> str:
        self.deps.ensure('python', 'pip')
        backend = self.args.backend or "auto"
        if backend == "auto":
            backend = "nuitka" if self.deps.is_installed('nuitka') and not self.args.onefile else "pyinstaller"
        self.deps.ensure(backend)
        return self._build_nuitka() if backend == "nuitka" else self._build_pyinstaller()

    def _build_pyinstaller(self) -> str:
        script = self._resolve_entry()
        cmd = [sys.executable, "-m", "PyInstaller", script, "--noconfirm", "--clean"]
        if not self.args.console: cmd.append("--windowed")
        cmd.append("--onefile" if self.args.onefile else "--onedir")
        cmd.extend(["--name", self.name, "--distpath", self.dist_dir])
        if self.args.icon and os.path.exists(self.args.icon): cmd.extend(["--icon", os.path.abspath(self.args.icon)])
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
        cmd.extend([f"--output-dir={self.dist_dir}", f"--output-filename={self.name}{ext}", script])
        result = self._run(cmd)
        if result.returncode != 0: error("Nuitka build failed")
        return self._print_result(out) or error("Nuitka build failed")

    def _resolve_entry(self) -> str:
        if self.args.script: return os.path.abspath(self.args.script)
        if self.project.entry_point: return os.path.join(self.project_dir, self.project.entry_point)
        # FIX: Explicit fallback search instead of just glob[0]
        for name in ("main.py", "app.py", "run.py", "start.py", "game.py", "__main__.py"):
            p = os.path.join(self.project_dir, name)
            if os.path.exists(p): return p
        py_files = glob.glob(os.path.join(self.project_dir, "*.py"))
        if py_files: return py_files[0]
        error("No Python entry point found")


class NodeBuilder(Builder):
    def build(self) -> str:
        self.deps.ensure('node', 'npm')
        if self.project.lang == LangType.ELECTRON: return self._build_electron()
        entry = self.args.script or self.project.entry_point
        if entry and entry.lower().endswith((".html", ".htm")): return self._build_web_app()
        return self._build_node()

    def _build_node(self) -> str:
        self.deps.ensure('pkg')
        entry = self.args.script or self.project.entry_point or "index.js"
        ext = exe_ext(self.target_os)
        out = os.path.join(self.dist_dir, f"{self.name}{ext}")
        cmd = ["pkg", entry, "--output", out, "--target", "node18-win-x64" if self.target_os == "windows" else f"node18-{sys.platform}-x64"]
        result = self._run(cmd)
        if result.returncode != 0: error("pkg build failed")
        return self._print_result(out) or error("pkg build failed")

    def _build_web_app(self) -> str:
        pkg_path = os.path.join(self.project_dir, "package.json")
        if os.path.exists(pkg_path):
            if not os.path.exists(os.path.join(self.project_dir, "node_modules")):
                self._run(["npm", "install"])
            if "build" in json.load(open(pkg_path)).get("scripts", {}):
                self._run(["npm", "run", "build"])

        stage_dir = os.path.join(self.dist_dir, "_electron_stage")
        if os.path.exists(stage_dir): shutil.rmtree(stage_dir)
        app_dir = os.path.join(stage_dir, "app")
        shutil.copytree(self.project_dir, app_dir, ignore=shutil.ignore_patterns("node_modules", ".git"))

        with open(os.path.join(app_dir, "main.js"), 'w') as f:
            f.write(f"const {{ app, BrowserWindow }} = require('electron'); app.whenReady().then(() => {{ new BrowserWindow({{width:1280,height:800}}).loadFile('index.html'); }});")

        build_config = {"appId": f"com.polybuild.{self.name}", "productName": self.name, "directories": {"output": self.dist_dir}, "win": {"target": "portable" if self.args.onefile else "nsis"}}
        with open(os.path.join(app_dir, "package.json"), 'w') as f:
            json.dump({"name": self.name.lower(), "version": "1.0.0", "main": "main.js", "build": build_config}, f)

        self._clean_dist_artifacts(".exe")
        self._run(["npm", "install", "--no-save", "electron", "electron-builder"], cwd=app_dir)
        result = self._run(["npx", "electron-builder", "--win", "--x64", "--publish", "never"], cwd=app_dir)
        if result.returncode != 0: error("electron-builder failed")
        shutil.rmtree(stage_dir, ignore_errors=True)
        exe = self._find_dist_artifact(".exe")
        return self._print_result(exe) or error("Electron build produced no EXE")

    def _build_electron(self) -> str:
        pkg_path = os.path.join(self.project_dir, "package.json")
        pkg = json.load(open(pkg_path))
        
        # FIX: Always generate a config to force output directory to self.dist_dir
        config = pkg.get("build", {})
        config["directories"] = {"output": self.dist_dir}
        config_path = os.path.join(self.dist_dir, "electron-builder-config.json")
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)

        if not os.path.exists(os.path.join(self.project_dir, "node_modules")):
            self._run(["npm", "install"])

        self._clean_dist_artifacts(".exe")
        cmd = ["npx", "--yes", "electron-builder", "--win", "--x64", "--publish", "never", "--config", config_path]
        result = self._run(cmd)
        if result.returncode != 0: error("electron-builder failed")
        exe = self._find_dist_artifact(".exe")
        return self._print_result(exe) or error("Electron build produced no EXE")


class CppBuilder(Builder):
    def build(self) -> str:
        if os.path.exists(os.path.join(self.project_dir, "CMakeLists.txt")):
            self.deps.ensure('cmake')
            build_dir = os.path.join(self.project_dir, "build")
            os.makedirs(build_dir, exist_ok=True)
            gen = "Visual Studio 17 2022" if sys.platform == "win32" else "Unix Makefiles"
            self._run(["cmake", "..", f"-G{gen}", "-DCMAKE_BUILD_TYPE=Release"], cwd=build_dir)
            self._run(["cmake", "--build", ".", "--config", "Release"], cwd=build_dir)
            ext = exe_ext(self.target_os)
            exe = self._find_exe_in(build_dir)
            if exe:
                dest = os.path.join(self.dist_dir, f"{self.name}{ext}")
                shutil.copy2(exe, dest)
                return self._print_result(dest) or dest
            error("No executable found in build output")
        else:
            self.deps.ensure('gcc')
            entry = self.args.script or self.project.entry_point or "main.cpp"
            compiler = "g++" if entry.endswith((".cpp", ".cc", ".cxx")) else "gcc"
            ext = exe_ext(self.target_os)
            out = os.path.join(self.dist_dir, f"{self.name}{ext}")
            cmd = [compiler, "-O2", "-o", out, os.path.join(self.project_dir, entry)]
            # FIX: Restrict globbing to specific extensions to prevent compiling .cache or .class
            for pattern in ["*.c", "*.cpp", "*.cc", "*.cxx"]:
                for f in glob.glob(os.path.join(self.project_dir, pattern)):
                    if os.path.basename(f) != os.path.basename(entry): cmd.append(f)
            result = self._run(cmd)
            if result.returncode != 0: error("Compilation failed")
            if sys.platform != "win32": os.chmod(out, 0o755)
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
        self.deps.ensure('dotnet')
        csproj = next((f for f in self.project.build_files if f.endswith(".csproj")), self._find_file("*.csproj"))
        if not csproj: error("No .csproj file found")
        rid = "win-x64" if self.target_os == "windows" else "linux-x64" if sys.platform.startswith("linux") else "osx-x64"
        cmd = ["dotnet", "publish", csproj, "-c", "Release", "-r", rid, "--self-contained", "true", "-o", self.dist_dir]
        if self.args.onefile: cmd.extend(["-p:PublishSingleFile=true", "-p:EnableCompressionInSingleFile=true"])
        result = self._run(cmd)
        if result.returncode != 0: error("dotnet publish failed")
        ext = exe_ext(self.target_os)
        exe_path = next((os.path.join(self.dist_dir, f) for f in os.listdir(self.dist_dir) if os.path.isfile(os.path.join(self.dist_dir, f)) and f.lower().endswith(ext)), None)
        return self._print_result(exe_path) or error("dotnet publish output not found")


class GoBuilder(Builder):
    def build(self) -> str:
        self.deps.ensure('go')
        entry = self.args.script or self.project.entry_point or "."
        ext = exe_ext(self.target_os)
        out = os.path.join(self.dist_dir, f"{self.name}{ext}")
        env = os.environ.copy()
        if self.target_os == "windows": env["GOOS"] = "windows"; env["GOARCH"] = "amd64"
        ldflags = "-s -w"
        if not self.args.console and (self.target_os == "windows" or sys.platform == "win32"): ldflags += " -H=windowsgui"
        cmd = ["go", "build", f"-ldflags={ldflags}", "-o", out, entry if os.path.isdir(os.path.join(self.project_dir, entry)) else os.path.join(self.project_dir, entry)]
        result = self._run(cmd, env=env)
        if result.returncode != 0: error("Go build failed")
        if sys.platform != "win32": os.chmod(out, 0o755)
        return self._print_result(out) or error("Go build failed")


class RustBuilder(Builder):
    def build(self) -> str:
        self.deps.ensure('cargo')
        target = "x86_64-pc-windows-gnu" if self.target_os == "windows" and sys.platform != "win32" else None
        cmd = ["cargo", "build", "--release"]
        if target: cmd.extend(["--target", target])
        result = self._run(cmd)
        if result.returncode != 0: error("Cargo build failed")
        
        ext = exe_ext(self.target_os)
        crate_name = self._get_crate_name()
        exe_name = f"{crate_name}{ext}"
        target_dir = os.path.join(self.project_dir, "target", target, "release", exe_name) if target else os.path.join(self.project_dir, "target", "release", exe_name)
        
        # FIX: Fallback to recursive search if the expected name doesn't exist (e.g. workspaces, multi-bin)
        if not os.path.exists(target_dir):
            search_dir = os.path.join(self.project_dir, "target", target, "release") if target else os.path.join(self.project_dir, "target", "release")
            for root, _, files in os.walk(search_dir):
                for f in files:
                    if f.lower().endswith(ext) and not f.startswith("lib") and not f.startswith("deps"):
                        target_dir = os.path.join(root, f)
                        break
        
        dest = os.path.join(self.dist_dir, f"{self.name}{ext}")
        if os.path.exists(target_dir):
            shutil.copy2(target_dir, dest)
            if sys.platform != "win32": os.chmod(dest, 0o755)
        return self._print_result(dest) or error("Rust build output not found")

    def _get_crate_name(self) -> str:
        try:
            with open(os.path.join(self.project_dir, "Cargo.toml"), 'r') as f:
                in_package = False
                for line in f.read().split('\n'):
                    s = line.strip()
                    if s.startswith('['): in_package = (s == '[package]')
                    if in_package:
                        m = re.match(r'name\s*=\s*["\'](.+?)["\']', s)
                        if m: return m.group(1).replace('-', '_')
        except Exception: pass
        return self.name.replace('-', '_')


class JavaBuilder(Builder):
    def build(self) -> str:
        self.deps.ensure('javac', 'java')
        if self.deps.is_installed('jpackage') and self.args.onefile: return self._build_jpackage()
        elif os.path.exists(os.path.join(self.project_dir, "build.gradle")): return self._build_gradle()
        elif os.path.exists(os.path.join(self.project_dir, "pom.xml")): return self._build_maven()
        else: return self._compile_jar()

    def _build_jpackage(self) -> str:
        jar = self._compile_jar()
        ext = exe_ext(self.target_os)
        out = os.path.join(self.dist_dir, f"{self.name}{ext}")
        cmd = ["jpackage", "--input", self.dist_dir, "--name", self.name, "--main-jar", os.path.basename(jar), "--type", "exe" if ext == ".exe" else "app-image", "--dest", self.dist_dir]
        result = self._run(cmd)
        if result.returncode != 0: error("jpackage failed")
        return self._print_result(out) or error("jpackage failed")

    def _compile_jar(self) -> str:
        java_files = glob.glob(os.path.join(self.project_dir, "**/*.java"), recursive=True)
        if not java_files: error("No Java files found")
        classes = os.path.join(self.dist_dir, "classes")
        os.makedirs(classes, exist_ok=True)
        
        # FIX: Use @argfile to avoid command line length limits
        argfile = os.path.join(self.dist_dir, "java_sources.txt")
        with open(argfile, 'w') as f:
            for jf in java_files: f.write(jf.replace('\\', '/') + "\n")
        result = self._run(["javac", "-d", classes, f"@{argfile}"])
        if result.returncode != 0: error("Java compilation failed")
        
        main_class = self._find_main_class(java_files)
        jar = os.path.join(self.dist_dir, f"{self.name}.jar")
        manifest = os.path.join(self.dist_dir, "MANIFEST.MF")
        with open(manifest, 'w') as f:
            f.write(f"Manifest-Version: 1.0\nMain-Class: {main_class or 'Main'}\n\n")
        result = self._run(["jar", "cvfm", jar, manifest, "-C", classes, "."])
        if result.returncode != 0: error("JAR creation failed")
        return jar

    def _find_main_class(self, java_files: List[str]) -> Optional[str]:
        for f in java_files:
            try:
                content = open(f, 'r', errors='ignore').read()
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
        result = self._run([wrapper if os.path.exists(wrapper) else "gradle", "build", "-x", "test"])
        if result.returncode != 0: error("Gradle build failed")
        for pattern in ["build/libs/*.jar", "build/distributions/*.exe"]:
            matches = glob.glob(os.path.join(self.project_dir, pattern), recursive=True)
            if matches:
                dest = os.path.join(self.dist_dir, os.path.basename(matches[0]))
                shutil.copy2(matches[0], dest)
                return self._print_result(dest) or dest
        error("No Gradle output found")

    def _build_maven(self) -> str:
        result = self._run(["mvn", "package", "-DskipTests"])
        if result.returncode != 0: error("Maven build failed")
        matches = glob.glob(os.path.join(self.project_dir, "target/*.jar"))
        if matches:
            dest = os.path.join(self.dist_dir, os.path.basename(matches[0]))
            shutil.copy2(matches[0], dest)
            return self._print_result(dest) or dest
        error("No Maven output found")


class AndroidBuilder(Builder):
    def build(self) -> str:
        self.deps.ensure('java', 'javac')
        wrapper = os.path.join(self.project_dir, "gradlew.bat" if sys.platform == "win32" else "gradlew")
        if not os.path.exists(wrapper):
            if self.deps.is_installed('gradle'): wrapper = "gradle"
            else: error("No Gradle wrapper found.")
        if sys.platform != "win32" and os.path.exists(wrapper): os.chmod(wrapper, 0o755)
        
        self._clean_dist_artifacts(".apk")
        
        # FIX: Clean up fallback logic to avoid false success on missing APK
        for task in ["assembleRelease", "assembleDebug"]:
            log(f"Running Gradle {task}...")
            result = self._run([wrapper, task, "--no-daemon"])
            if result.returncode == 0:
                apk = self._find_apk()
                if apk:
                    dest = os.path.join(self.dist_dir, os.path.basename(apk))
                    shutil.copy2(apk, dest)
                    return self._print_result(dest) or dest
                else:
                    if task == "assembleRelease":
                        warn("Release build succeeded but APK not found, trying debug...")
                    else:
                        error("Gradle build succeeded but produced no APK")
            else:
                if task == "assembleRelease":
                    warn("Release build failed, trying debug...")
                else:
                    error("Gradle build failed — check Android SDK / Gradle setup")
        error("No APK found in build output")

    def _find_apk(self) -> Optional[str]:
        search_dirs = [os.path.join(self.project_dir, "app", "build", "outputs", "apk"), os.path.join(self.project_dir, "build", "outputs", "apk")]
        for search_dir in search_dirs:
            if os.path.exists(search_dir):
                for root, _, files in os.walk(search_dir):
                    for f in files:
                        if f.endswith(".apk"): return os.path.join(root, f)
        # FIX: Exclude dist_dir from fallback to prevent finding old artifacts
        for root, dirs, files in os.walk(self.project_dir):
            dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS and os.path.join(root, d) != self.dist_dir]
            for f in files:
                if f.endswith(".apk"): return os.path.join(root, f)
        return None


class FlutterBuilder(Builder):
    def build(self) -> str:
        self.deps.ensure('flutter')
        if self.target_os == "android" or self.project.lang == LangType.ANDROID: return self._build_apk()
        return self._build_desktop()

    def _build_apk(self) -> str:
        self._clean_dist_artifacts(".apk")
        result = self._run(["flutter", "build", "apk", "--release"])
        if result.returncode != 0: error("Flutter APK build failed")
        apk = os.path.join(self.project_dir, "build", "app", "outputs", "flutter-apk", "app-release.apk")
        if os.path.exists(apk):
            dest = os.path.join(self.dist_dir, f"{self.name}.apk")
            shutil.copy2(apk, dest)
            return self._print_result(dest) or dest
        error("No Flutter APK found")

    def _build_desktop(self) -> str:
        build_target = "windows" if (self.target_os == "windows" or sys.platform == "win32") else "macos" if sys.platform == "darwin" else "linux"
        result = self._run(["flutter", "build", build_target, "--release"])
        if result.returncode != 0: error(f"Flutter {build_target} build failed")
        ext = exe_ext(self.target_os)
        build_dirs = [os.path.join(self.project_dir, "build", build_target, "x64", "runner", "Release"), os.path.join(self.project_dir, "build", build_target, "runner", "Release")]
        for d in build_dirs:
            if os.path.exists(d):
                for f in os.listdir(d):
                    if f.endswith(ext if ext else ".exe") or (not ext and os.access(os.path.join(d, f), os.X_OK)):
                        dest = os.path.join(self.dist_dir, f)
                        shutil.copy2(os.path.join(d, f), dest)
                        return self._print_result(dest) or dest
        error("No Flutter executable found")


class LuaBuilder(Builder):
    def build(self) -> str:
        self.deps.ensure('love')
        love_file = os.path.join(self.dist_dir, f"{self.name}.love")
        with zipfile.ZipFile(love_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(self.project_dir):
                dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
                for f in files:
                    if f.endswith(('.lua', '.png', '.jpg', '.ogg', '.wav', '.ttf', '.json', '.xml')):
                        zf.write(os.path.join(root, f), os.path.relpath(os.path.join(root, f), self.project_dir))
        love_exe = shutil.which("love")
        if not love_exe: error("love executable not found. Install LÖVE2D.")
        ext = exe_ext(self.target_os)
        out = os.path.join(self.dist_dir, f"{self.name}{ext}")
        with open(out, 'wb') as f:
            f.write(open(love_exe, 'rb').read())
            f.write(open(love_file, 'rb').read())
        os.remove(love_file)
        if sys.platform != "win32": os.chmod(out, 0o755)
        return self._print_result(out) or error("Love2D build failed")


class GodotBuilder(Builder):
    def build(self) -> str:
        self.deps.ensure('godot')
        cfg_path = os.path.join(self.project_dir, "export_presets.cfg")
        if not os.path.exists(cfg_path): error("No export_presets.cfg found. Configure exports in Godot first.")
        names = re.findall(r'name\s*=\s*"([^"]+)"', open(cfg_path, 'r').read())
        if not names: error("No export presets defined in export_presets.cfg")
        export_preset = names[0]
        ext = exe_ext(self.target_os)
        out = os.path.join(self.dist_dir, f"{self.name}{ext}")
        result = self._run(["godot", "--headless", "--path", self.project_dir, "--export-release", export_preset, out])
        if result.returncode != 0: error("Godot export failed. Ensure export templates are installed.")
        if sys.platform != "win32" and os.path.exists(out): os.chmod(out, 0o755)
        return self._print_result(out) or error("Godot export failed")


class NimBuilder(Builder):
    def build(self) -> str:
        self.deps.ensure('nim')
        entry = self.args.script or self.project.entry_point or "main.nim"
        ext = exe_ext(self.target_os)
        out = os.path.join(self.dist_dir, f"{self.name}{ext}")
        cmd = ["nim", "c", "-d:release", "--opt:speed", "-o:" + out]
        if not self.args.console: cmd.append("--app:gui")
        cmd.append(os.path.join(self.project_dir, entry))
        result = self._run(cmd)
        if result.returncode != 0: error("Nim compilation failed")
        if sys.platform != "win32": os.chmod(out, 0o755)
        return self._print_result(out) or error("Nim compilation failed")


class ZigBuilder(Builder):
    def build(self) -> str:
        self.deps.ensure('zig')
        ext = exe_ext(self.target_os)
        if os.path.exists(os.path.join(self.project_dir, "build.zig")):
            cmd = ["zig", "build", "-Doptimize=ReleaseFast"]
            if self.target_os == "windows" and sys.platform != "win32": cmd.append("-Dtarget=x86_64-windows-gnu")
            result = self._run(cmd)
            if result.returncode != 0: error("Zig build failed")
            out = os.path.join(self.project_dir, "zig-out", "bin", f"{self.name}{ext}")
            dest = os.path.join(self.dist_dir, f"{self.name}{ext}")
            if os.path.exists(out): shutil.copy2(out, dest)
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
        self.deps.ensure('crystal')
        entry = self.args.script or self.project.entry_point or "main.cr"
        out = os.path.join(self.dist_dir, self.name)
        result = self._run(["crystal", "build", "--release", "--no-debug", "-o", out, os.path.join(self.project_dir, entry)])
        if result.returncode != 0: error("Crystal build failed")
        exe = out + ".exe" if sys.platform == "win32" else out
        if sys.platform != "win32" and os.path.exists(exe): os.chmod(exe, 0o755)
        return self._print_result(exe) or error("Crystal build failed")


class RubyBuilder(Builder):
    def build(self) -> str:
        self.deps.ensure('ruby')
        subprocess.run(["gem", "install", "ocra"], capture_output=True, timeout=60)
        entry = self.args.script or self.project.entry_point or "main.rb"
        ext = exe_ext(self.target_os)
        out = os.path.join(self.dist_dir, f"{self.name}{ext}")
        result = self._run(["ocra", "--windows", "--output", out, os.path.join(self.project_dir, entry)])
        if result.returncode != 0: error("OCRA build failed. Install manually: gem install ocra")
        if sys.platform != "win32" and os.path.exists(out): os.chmod(out, 0o755)
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
    parser.add_argument("--lang", choices=["python", "node", "electron", "cpp", "c", "csharp", "go", "rust", "java", "kotlin", "scala", "flutter", "dart", "lua", "love2d", "nim", "zig", "crystal", "ruby", "perl", "godot", "android"], help="Force language")
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
        lang_map = {k: getattr(LangType, k.upper()) for k in ["python", "node", "electron", "cpp", "c", "csharp", "go", "rust", "java", "kotlin", "scala", "flutter", "dart", "lua", "love2d", "nim", "zig", "crystal", "ruby", "perl", "godot", "android"]}
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
    builder_class = BUILDERS.get(detected.lang)
    
    if args.target_os == "android":
        if detected.lang in (LangType.FLUTTER, LangType.DART): builder_class = FlutterBuilder
        elif detected.lang in (LangType.JAVA, LangType.KOTLIN, LangType.ANDROID): builder_class = AndroidBuilder
        else: error(f"Android builds are not supported for {detected.lang.name}.")

    if not builder_class: error(f"No builder available for {detected.lang.name}")

    builder = builder_class(detected, args, deps)
    try:
        start_time = datetime.now()
        artifact_path = builder.build()
        elapsed = (datetime.now() - start_time).total_seconds()
        print(f"\n{Colors.GREEN}{Colors.BOLD}BUILD SUCCESSFUL")
        print(f"Time: {elapsed:.1f}s{Colors.END}\n")
    except KeyboardInterrupt:
        warn("\nBuild interrupted by user"); sys.exit(1)
    except Exception as e:
        error(f"Build failed: {str(e)}")

if __name__ == "__main__":
    main()
