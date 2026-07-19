#!/usr/bin/env python3
"""
PolyBuild Pro v2.0 - Universal App & Game EXE Builder
Auto-detects 20+ languages, self-updates, auto-manages dependencies
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
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Tuple, Callable
from enum import Enum, auto
from datetime import datetime


# ==================== VERSION & UPDATE ====================
VERSION = "2.0.0"
UPDATE_URL = "https://raw.githubusercontent.com/polybuild/polybuild/main/polybuild.py"
VERSION_CHECK_URL = "https://raw.githubusercontent.com/polybuild/polybuild/main/version.json"


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


# ==================== SELF-UPDATE SYSTEM ====================

class SelfUpdater:
    """Handles checking for updates and self-patching."""
    
    @staticmethod
    def check_update(force: bool = False) -> bool:
        """Check if newer version exists online. Returns True if updated."""
        try:
            log("Checking for PolyBuild updates...")
            req = urllib.request.Request(
                VERSION_CHECK_URL,
                headers={'User-Agent': 'PolyBuild-Updater'}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                remote_info = json.loads(resp.read().decode('utf-8'))
            
            remote_version = remote_info.get('version', '0.0.0')
            if SelfUpdater._version_compare(remote_version, VERSION) > 0:
                print(f"\n{Colors.YELLOW}╔═══════════════════════════════════════════════════╗")
                print(f"║  Update available: v{VERSION} → v{remote_version}{' ' * (21 - len(VERSION) - len(remote_version))}║")
                print(f"║  {remote_info.get('changelog', 'Bug fixes and improvements.')[:47]:<47} ║")
                print(f"╚═══════════════════════════════════════════════════╝{Colors.END}\n")
                
                if force or input("Update now? [Y/n]: ").lower() in ('', 'y', 'yes'):
                    return SelfUpdater._perform_update(remote_info)
                return False
            else:
                dim(f"PolyBuild is up to date (v{VERSION})")
                return False
        except Exception as e:
            warn(f"Could not check for updates: {e}")
            return False
    
    @staticmethod
    def _version_compare(v1: str, v2: str) -> int:
        """Compare two version strings. Returns >0 if v1 is newer."""
        def normalize(v):
            return [int(x) for x in re.sub(r'[^0-9.]', '', v).split('.')]
        n1, n2 = normalize(v1), normalize(v2)
        return (n1 > n2) - (n1 < n2)
    
    @staticmethod
    def _perform_update(info: dict) -> bool:
        """Download and replace current script."""
        try:
            log("Downloading update...")
            req = urllib.request.Request(
                info.get('download_url', UPDATE_URL),
                headers={'User-Agent': 'PolyBuild-Updater'}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                new_code = resp.read().decode('utf-8')
            
            # Verify hash if provided
            if 'sha256' in info:
                if hashlib.sha256(new_code.encode()).hexdigest() != info['sha256']:
                    error("Update verification failed (hash mismatch)")
            
            # Backup current script
            script_path = os.path.abspath(sys.argv[0])
            backup_path = script_path + ".backup"
            shutil.copy2(script_path, backup_path)
            
            # Write new version
            with open(script_path, 'w', encoding='utf-8') as f:
                f.write(new_code)
            
            success(f"Updated to v{info['version']}! Restart to use new version.")
            return True
        except Exception as e:
            error(f"Update failed: {e}")
            return False


# ==================== DEPENDENCY MANAGER ====================

class DependencyManager:
    """Auto-detects, checks, installs and updates build dependencies."""
    
    # Registry of known tools and install methods
    TOOLS = {
        # Python ecosystem
        'python': {'check': ['python', '--version'], 'type': 'runtime'},
        'pip': {'check': ['pip', '--version'], 'type': 'python_tool'},
        'pyinstaller': {'check': ['pyinstaller', '--version'], 'install': 'pip', 'pkg': 'pyinstaller', 'type': 'python_tool'},
        'nuitka': {'check': ['python', '-m', 'nuitka', '--version'], 'install': 'pip', 'pkg': 'nuitka', 'type': 'python_tool'},
        'cx_freeze': {'check': ['python', '-m', 'cx_Freeze', '--version'], 'install': 'pip', 'pkg': 'cx_Freeze', 'type': 'python_tool'},
        
        # Node.js ecosystem
        'node': {'check': ['node', '--version'], 'type': 'runtime'},
        'npm': {'check': ['npm', '--version'], 'type': 'runtime'},
        'pkg': {'check': ['pkg', '--version'], 'install': 'npm', 'pkg': 'pkg', 'global': True, 'type': 'node_tool'},
        'electron-builder': {'check': ['npx', 'electron-builder', '--version'], 'install': 'npm', 'pkg': 'electron-builder', 'global': False, 'type': 'node_tool'},
        'nexe': {'check': ['npx', 'nexe', '--version'], 'install': 'npm', 'pkg': 'nexe', 'global': False, 'type': 'node_tool'},
        
        # C/C++
        'gcc': {'check': ['gcc', '--version'], 'install': 'choco', 'pkg': 'mingw', 'type': 'compiler'},
        'g++': {'check': ['g++', '--version'], 'install': 'choco', 'pkg': 'mingw', 'type': 'compiler'},
        'clang': {'check': ['clang', '--version'], 'type': 'compiler'},
        'cmake': {'check': ['cmake', '--version'], 'install': 'choco', 'pkg': 'cmake', 'type': 'build_tool'},
        'meson': {'check': ['meson', '--version'], 'install': 'pip', 'pkg': 'meson', 'type': 'build_tool'},
        'ninja': {'check': ['ninja', '--version'], 'install': 'choco', 'pkg': 'ninja', 'type': 'build_tool'},
        'make': {'check': ['make', '--version'], 'type': 'build_tool'},
        
        # .NET / C#
        'dotnet': {'check': ['dotnet', '--version'], 'install': 'choco', 'pkg': 'dotnet-sdk', 'type': 'sdk'},
        'msbuild': {'check': ['msbuild', '/version'], 'type': 'build_tool'},
        
        # Go
        'go': {'check': ['go', 'version'], 'install': 'choco', 'pkg': 'golang', 'type': 'sdk'},
        
        # Rust
        'cargo': {'check': ['cargo', '--version'], 'install': 'script', 'script': 'https://win.rustup.rs/', 'type': 'sdk'},
        'rustc': {'check': ['rustc', '--version'], 'type': 'sdk'},
        
        # Java
        'java': {'check': ['java', '--version'], 'install': 'choco', 'pkg': 'openjdk', 'type': 'runtime'},
        'javac': {'check': ['javac', '--version'], 'type': 'compiler'},
        'mvn': {'check': ['mvn', '--version'], 'install': 'choco', 'pkg': 'maven', 'type': 'build_tool'},
        'gradle': {'check': ['gradle', '--version'], 'install': 'choco', 'pkg': 'gradle', 'type': 'build_tool'},
        
        # Flutter / Dart
        'flutter': {'check': ['flutter', '--version'], 'install': 'manual', 'url': 'https://docs.flutter.dev/get-started/install/windows', 'type': 'sdk'},
        'dart': {'check': ['dart', '--version'], 'type': 'sdk'},
        
        # Kotlin
        'kotlinc': {'check': ['kotlinc', '-version'], 'install': 'choco', 'pkg': 'kotlin', 'type': 'compiler'},
        
        # Nim
        'nim': {'check': ['nim', '--version'], 'install': 'choco', 'pkg': 'nim', 'type': 'sdk'},
        
        # Zig
        'zig': {'check': ['zig', 'version'], 'install': 'choco', 'pkg': 'zig', 'type': 'sdk'},
        
        # Lua
        'lua': {'check': ['lua', '-v'], 'type': 'runtime'},
        'love': {'check': ['love', '--version'], 'install': 'choco', 'pkg': 'love', 'type': 'runtime'},
        
        # Ruby
        'ruby': {'check': ['ruby', '--version'], 'install': 'choco', 'pkg': 'ruby', 'type': 'runtime'},
        
        # Game Engines
        'godot': {'check': ['godot', '--version'], 'install': 'choco', 'pkg': 'godot', 'type': 'engine'},
        'unity': {'check': ['cmd', '/c', 'echo', 'unity'], 'type': 'engine'},  # Special handling
        
        # Misc
        'upx': {'check': ['upx', '--version'], 'install': 'choco', 'pkg': 'upx', 'type': 'tool'},
        'git': {'check': ['git', '--version'], 'type': 'tool'},
    }
    
    def __init__(self):
        self.cache = {}
        self.choco_available = None
    
    def is_installed(self, tool: str) -> bool:
        """Check if a tool is installed (with caching)."""
        if tool in self.cache:
            return self.cache[tool]
        
        info = self.TOOLS.get(tool)
        if not info:
            self.cache[tool] = False
            return False
        
        try:
            result = subprocess.run(
                info['check'], 
                capture_output=True, 
                text=True, 
                timeout=10,
                shell=(sys.platform == 'win32' and len(info['check']) > 2)
            )
            installed = result.returncode in (0, 1)  # Some tools return 1 for --version
            self.cache[tool] = installed
            return installed
        except:
            self.cache[tool] = False
            return False
    
    def ensure(self, *tools: str, auto_install: bool = True) -> Dict[str, bool]:
        """Ensure all listed tools are installed. Returns status dict."""
        results = {}
        missing = []
        
        for tool in tools:
            if self.is_installed(tool):
                results[tool] = True
            else:
                results[tool] = False
                missing.append(tool)
        
        if missing and auto_install:
            log(f"Missing tools: {', '.join(missing)}")
            for tool in missing:
                results[tool] = self._install(tool)
        
        return results
    
    def _install(self, tool: str) -> bool:
        """Attempt to install a tool automatically."""
        info = self.TOOLS.get(tool, {})
        method = info.get('install', 'manual')
        
        log(f"Installing {tool} via {method}...")
        
        try:
            if method == 'pip':
                pkg = info.get('pkg', tool)
                cmd = [sys.executable, "-m", "pip", "install", "--upgrade", pkg]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                return result.returncode == 0
            
            elif method == 'npm':
                pkg = info.get('pkg', tool)
                cmd = ["npm", "install"]
                if info.get('global', False):
                    cmd.append("-g")
                cmd.append(pkg)
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                return result.returncode == 0
            
            elif method == 'choco':
                if not self._has_choco():
                    warn("Chocolatey not available. Cannot auto-install.")
                    return False
                pkg = info.get('pkg', tool)
                result = subprocess.run(
                    ["choco", "install", pkg, "-y", "--no-progress"],
                    capture_output=True, text=True, timeout=300
                )
                return result.returncode == 0
            
            elif method == 'script':
                warn(f"Please install {tool} manually from: {info.get('script', 'official website')}")
                return False
            
            elif method == 'manual':
                warn(f"Please install {tool} manually from: {info.get('url', 'official website')}")
                return False
            
            return False
        except Exception as e:
            warn(f"Failed to install {tool}: {e}")
            return False
    
    def _has_choco(self) -> bool:
        if self.choco_available is not None:
            return self.choco_available
        try:
            result = subprocess.run(["choco", "--version"], capture_output=True, timeout=5)
            self.choco_available = result.returncode == 0
            return self.choco_available
        except:
            self.choco_available = False
            return False
    
    def update_project_deps(self, project_dir: str, lang: 'LangType'):
        """Update project-specific dependencies."""
        log("Updating project dependencies...")
        
        if lang in (LangType.PYTHON,):
            self._update_python_deps(project_dir)
        elif lang in (LangType.NODE, LangType.ELECTRON):
            self._update_node_deps(project_dir)
        elif lang == LangType.RUST:
            self._update_rust_deps(project_dir)
        elif lang == LangType.GO:
            self._update_go_deps(project_dir)
        elif lang == LangType.JAVA:
            self._update_java_deps(project_dir)
        elif lang == LangType.CSHARP:
            self._update_csharp_deps(project_dir)
        elif lang == LangType.CPP:
            self._update_cpp_deps(project_dir)
    
    def _update_python_deps(self, project_dir: str):
        req_file = os.path.join(project_dir, "requirements.txt")
        if os.path.exists(req_file):
            subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_file, "--upgrade"], 
                         capture_output=True)
            success("Updated Python requirements")
        
        if os.path.exists(os.path.join(project_dir, "Pipfile")):
            if self.is_installed('pipenv'):
                subprocess.run(["pipenv", "update"], cwd=project_dir, capture_output=True)
                success("Updated Pipfile dependencies")
    
    def _update_node_deps(self, project_dir: str):
        if os.path.exists(os.path.join(project_dir, "package.json")):
            if os.path.exists(os.path.join(project_dir, "yarn.lock")):
                subprocess.run(["yarn", "upgrade"], cwd=project_dir, capture_output=True)
                success("Updated Yarn dependencies")
            else:
                subprocess.run(["npm", "update"], cwd=project_dir, capture_output=True)
                success("Updated npm dependencies")
    
    def _update_rust_deps(self, project_dir: str):
        if os.path.exists(os.path.join(project_dir, "Cargo.toml")):
            subprocess.run(["cargo", "update"], cwd=project_dir, capture_output=True)
            success("Updated Cargo dependencies")
    
    def _update_go_deps(self, project_dir: str):
        if os.path.exists(os.path.join(project_dir, "go.mod")):
            subprocess.run(["go", "get", "-u", "./..."], cwd=project_dir, capture_output=True)
            subprocess.run(["go", "mod", "tidy"], cwd=project_dir, capture_output=True)
            success("Updated Go modules")
    
    def _update_java_deps(self, project_dir: str):
        if os.path.exists(os.path.join(project_dir, "pom.xml")) and self.is_installed('mvn'):
            subprocess.run(["mvn", "versions:use-latest-versions"], cwd=project_dir, capture_output=True)
            success("Updated Maven dependencies")
        elif os.path.exists(os.path.join(project_dir, "build.gradle")) and self.is_installed('gradle'):
            subprocess.run(["gradle", "dependencies", "--refresh-dependencies"], cwd=project_dir, capture_output=True)
            success("Refreshed Gradle dependencies")
    
    def _update_csharp_deps(self, project_dir: str):
        if self.is_installed('dotnet'):
            subprocess.run(["dotnet", "restore", "--force-evaluate"], cwd=project_dir, capture_output=True)
            success("Restored .NET dependencies")
    
    def _update_cpp_deps(self, project_dir: str):
        if os.path.exists(os.path.join(project_dir, "vcpkg.json")):
            vcpkg = os.path.join(project_dir, "vcpkg", "vcpkg.exe")
            if os.path.exists(vcpkg):
                subprocess.run([vcpkg, "upgrade"], cwd=project_dir, capture_output=True)
                success("Updated vcpkg dependencies")
            else:
                warn("vcpkg.json found but vcpkg not available in project")


# ==================== PROJECT DETECTION ====================

class LangType(Enum):
    # General
    PYTHON = auto()
    NODE = auto()
    ELECTRON = auto()
    CPP = auto()
    C = auto()
    CSHARP = auto()
    GO = auto()
    RUST = auto()
    JAVA = auto()
    KOTLIN = auto()
    SCALA = auto()
    FLUTTER = auto()
    DART = auto()
    
    # Scripting
    LUA = auto()
    LOVE2D = auto()
    RUBY = auto()
    PERL = auto()
    
    # Systems
    NIM = auto()
    ZIG = auto()
    CRYSTAL = auto()
    
    # Game Engines
    GODOT = auto()
    UNITY = auto()
    UNREAL = auto()
    GAMEMAKER = auto()
    RENPY = auto()
    
    UNKNOWN = auto()


@dataclass
class DetectedProject:
    lang: LangType
    confidence: int
    entry_point: Optional[str]
    build_files: List[str]
    framework: Optional[str] = None
    game_engine: Optional[str] = None
    notes: List[str] = field(default_factory=list)
    
    def to_dict(self):
        return {
            'lang': self.lang.name,
            'confidence': self.confidence,
            'entry_point': self.entry_point,
            'build_files': self.build_files,
            'framework': self.framework,
            'game_engine': self.game_engine,
            'notes': self.notes
        }


class ProjectDetector:
    """Advanced multi-language project detector with game engine support."""
    
    def __init__(self, project_dir: str):
        self.dir = os.path.abspath(project_dir)
        self.files = set()
        self._scan()
    
    def _scan(self):
        for root, _, filenames in os.walk(self.dir):
            for f in filenames:
                rel = os.path.relpath(os.path.join(root, f), self.dir)
                self.files.add(rel.replace("\\", "/").lower())
    
    def _has(self, pattern: str) -> bool:
        p = pattern.lower()
        return any(f.endswith(p) or f == p or p in f for f in self.files)
    
    def _count(self, pattern: str) -> int:
        p = pattern.lower()
        return sum(1 for f in self.files if p in f)
    
    def _find(self, *patterns: str) -> Optional[str]:
        for p in patterns:
            pl = p.lower()
            for f in self.files:
                if f.endswith(pl) or f == pl:
                    return f
        return None
    
    def detect(self) -> DetectedProject:
        candidates = []
        
        # ========== GAME ENGINES (highest priority) ==========
        
        # Godot
        godot_score = 0
        if self._has("project.godot"):
            godot_score += 100
        if self._has(".tscn") or self._has(".tres"):
            godot_score += 30
        if self._has(".gd"):
            godot_score += 20
        if godot_score > 0:
            candidates.append(DetectedProject(
                LangType.GODOT, godot_score, "project.godot", ["project.godot"],
                game_engine="Godot", notes=["Godot Engine project detected"]
            ))
        
        # Unity
        unity_score = 0
        if self._has("assets") and self._has("projectsettings"):
            unity_score += 60
        if self._has(".unity"):
            unity_score += 30
        if self._has(".cs") and self._count(".cs") > 5:
            unity_score += 20
        if self._has("packages/manifest.json"):
            unity_score += 40
        if unity_score > 0:
            candidates.append(DetectedProject(
                LangType.UNITY, unity_score, None, [],
                game_engine="Unity", notes=["Unity project detected"]
            ))
        
        # Unreal Engine
        unreal_score = 0
        if self._has(".uproject"):
            unreal_score += 100
        if self._has("source") and (self._has(".cpp") or self._has(".h")):
            unreal_score += 30
        if self._has("content") and self._has("config"):
            unreal_score += 20
        if unreal_score > 0:
            candidates.append(DetectedProject(
                LangType.UNREAL, unreal_score, None, [f for f in self.files if f.endswith(".uproject")],
                game_engine="Unreal Engine", notes=["Unreal Engine project detected"]
            ))
        
        # GameMaker
        gm_score = 0
        if self._has(".yyp"):
            gm_score += 100
        if self._has(".gml"):
            gm_score += 30
        if gm_score > 0:
            candidates.append(DetectedProject(
                LangType.GAMEMAKER, gm_score, None, [f for f in self.files if f.endswith(".yyp")],
                game_engine="GameMaker", notes=["GameMaker Studio project detected"]
            ))
        
        # Ren'Py
        renpy_score = 0
        if self._has("script.rpy") or self._has("options.rpy"):
            renpy_score += 100
        if self._has(".rpy"):
            renpy_score += 20
        if renpy_score > 0:
            candidates.append(DetectedProject(
                LangType.RENPY, renpy_score, None, [],
                game_engine="Ren'Py", notes=["Ren'Py visual novel detected"]
            ))
        
        # Love2D
        love_score = 0
        if self._has("main.lua"):
            love_score += 80
        if self._has("conf.lua"):
            love_score += 20
        lua_count = self._count(".lua")
        if lua_count > 0:
            love_score += min(lua_count * 3, 20)
        if love_score > 0:
            candidates.append(DetectedProject(
                LangType.LOVE2D, love_score, "main.lua", [],
                game_engine="LÖVE", notes=[f"Love2D game with {lua_count} Lua files"]
            ))
        
        # ========== GENERAL LANGUAGES ==========
        
        # Python
        py_score = 0
        py_entry = None
        py_builds = []
        
        if self._has("requirements.txt"):
            py_score += 30; py_builds.append("requirements.txt")
        if self._has("pyproject.toml"):
            py_score += 25; py_builds.append("pyproject.toml")
        if self._has("setup.py"):
            py_score += 20; py_builds.append("setup.py")
        if self._has("pipfile"):
            py_score += 15; py_builds.append("Pipfile")
        if self._has("setup.cfg"):
            py_score += 10; py_builds.append("setup.cfg")
        
        py_count = self._count(".py")
        if py_count > 0:
            py_score += min(py_count * 3, 25)
            py_entry = self._find("main.py", "app.py", "run.py", "gui.py", "__main__.py", "start.py", "game.py")
            if py_entry:
                py_score += 10
        
        if py_score > 0:
            notes = [f"{py_count} Python files"]
            if self._has("pygame") or self._find("pygame"):
                notes.append("PyGame detected")
            elif self._has("tkinter") or self._has("pyqt") or self._has("pyside") or self._has("kivy"):
                notes.append("GUI framework detected")
            candidates.append(DetectedProject(LangType.PYTHON, py_score, py_entry, py_builds, notes=notes))
        
        # Node.js / Electron
        node_score = 0
        node_entry = None
        node_builds = []
        is_electron = False
        
        if self._has("package.json"):
            node_score += 40; node_builds.append("package.json")
            try:
                with open(os.path.join(self.dir, "package.json"), 'r', encoding='utf-8') as f:
                    pkg = json.load(f)
                    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                    if "electron" in deps:
                        is_electron = True; node_score += 35
                    if any(x in deps for x in ["react", "vue", "angular"]):
                        node_score += 5
            except:
                pass
        
        if self._has("package-lock.json"):
            node_score += 10
        if self._has("yarn.lock"):
            node_score += 10
        if self._has("webpack.config.js"):
            node_score += 5
        
        js_count = self._count(".js") + self._count(".ts") + self._count(".jsx") + self._count(".tsx")
        if js_count > 0:
            node_score += min(js_count, 20)
            node_entry = self._find("main.js", "index.js", "app.js", "main.ts", "index.ts", "electron.js")
        
        if node_score > 0:
            lang = LangType.ELECTRON if is_electron else LangType.NODE
            candidates.append(DetectedProject(
                lang, node_score, node_entry, node_builds,
                framework="Electron" if is_electron else None,
                notes=[f"{js_count} JS/TS files"]
            ))
        
        # C/C++
        cpp_score = 0
        cpp_entry = None
        cpp_builds = []
        
        if self._has("cmakelists.txt"):
            cpp_score += 40; cpp_builds.append("CMakeLists.txt")
        if self._has("makefile"):
            cpp_score += 30; cpp_builds.append("Makefile")
        if self._has("meson.build"):
            cpp_score += 30; cpp_builds.append("meson.build")
        if self._has("configure.ac"):
            cpp_score += 20; cpp_builds.append("configure.ac")
        if self._has("scons"):
            cpp_score += 20; cpp_builds.append("SConstruct")
        if self._has("premake5.lua"):
            cpp_score += 25; cpp_builds.append("premake5.lua")
        if self._has("vcpkg.json"):
            cpp_score += 15; cpp_builds.append("vcpkg.json")
        
        c_count = self._count(".c")
        cpp_count = self._count(".cpp") + self._count(".cc") + self._count(".cxx")
        h_count = self._count(".h") + self._count(".hpp")
        
        if cpp_count > 0:
            cpp_score += min(cpp_count * 3, 25)
        if c_count > 0:
            cpp_score += min(c_count * 2, 15)
        if h_count > 0:
            cpp_score += min(h_count, 10)
        
        if cpp_count > 0 or c_count > 0:
            cpp_entry = self._find("main.cpp", "main.c", "winmain.cpp")
            lang = LangType.CPP if cpp_count > c_count else LangType.C
            candidates.append(DetectedProject(
                lang, cpp_score, cpp_entry, cpp_builds,
                notes=[f"{cpp_count} C++ / {c_count} C files"]
            ))
        
        # C#
        cs_score = 0
        cs_entry = None
        cs_builds = []
        
        for f in self.files:
            if f.endswith(".csproj"):
                cs_score += 50; cs_builds.append(f)
            if f.endswith(".sln"):
                cs_score += 30; cs_builds.append(f)
        
        cs_count = self._count(".cs")
        if cs_count > 0:
            cs_score += min(cs_count * 2, 20)
            cs_entry = self._find("program.cs", "main.cs", "game.cs", "app.cs")
        
        if cs_score > 0:
            framework = None
            if any("monogame" in f for f in self.files):
                framework = "MonoGame"
            elif any("unity" in f for f in self.files):
                framework = "Unity Script"
            candidates.append(DetectedProject(
                LangType.CSHARP, cs_score, cs_entry, cs_builds,
                framework=framework, notes=[f"{cs_count} C# files"]
            ))
        
        # Go
        go_score = 0
        go_entry = None
        go_builds = []
        
        if self._has("go.mod"):
            go_score += 50; go_builds.append("go.mod")
        if self._has("go.sum"):
            go_score += 10; go_builds.append("go.sum")
        
        go_count = self._count(".go")
        if go_count > 0:
            go_score += min(go_count * 3, 30)
            go_entry = self._find("main.go", "cmd/main.go", "game.go")
        
        if go_score > 0:
            candidates.append(DetectedProject(
                LangType.GO, go_score, go_entry, go_builds,
                notes=[f"{go_count} Go files"]
            ))
        
        # Rust
        rust_score = 0
        rust_entry = None
        rust_builds = []
        
        if self._has("cargo.toml"):
            rust_score += 50; rust_builds.append("Cargo.toml")
        if self._has("cargo.lock"):
            rust_score += 10; rust_builds.append("Cargo.lock")
        
        rs_count = self._count(".rs")
        if rs_count > 0:
            rust_score += min(rs_count * 3, 30)
            rust_entry = self._find("main.rs", "lib.rs", "game.rs")
        
        if rust_score > 0:
            candidates.append(DetectedProject(
                LangType.RUST, rust_score, rust_entry, rust_builds,
                notes=[f"{rs_count} Rust files"]
            ))
        
        # Java
        java_score = 0
        java_entry = None
        java_builds = []
        
        if self._has("pom.xml"):
            java_score += 40; java_builds.append("pom.xml")
        if self._has("build.gradle"):
            java_score += 40; java_builds.append("build.gradle")
        if self._has("build.gradle.kts"):
            java_score += 40; java_builds.append("build.gradle.kts")
        
        java_count = self._count(".java")
        if java_count > 0:
            java_score += min(java_count * 2, 20)
            java_entry = self._find("main.java", "app.java", "game.java")
        
        if java_score > 0:
            candidates.append(DetectedProject(
                LangType.JAVA, java_score, java_entry, java_builds,
                notes=[f"{java_count} Java files"]
            ))
        
        # Kotlin
        kt_score = 0
        if self._has(".kt") or self._has(".kts"):
            kt_count = self._count(".kt") + self._count(".kts")
            kt_score += min(kt_count * 5, 40)
            if self._has("build.gradle.kts"):
                kt_score += 30
            if kt_score > 0:
                candidates.append(DetectedProject(
                    LangType.KOTLIN, kt_score, None, [],
                    notes=[f"{kt_count} Kotlin files"]
                ))
        
        # Scala
        scala_score = 0
        if self._has(".scala") or self._has(".sbt"):
            scala_count = self._count(".scala")
            scala_score += min(scala_count * 5, 40)
            if self._has("build.sbt"):
                scala_score += 40
            if scala_score > 0:
                candidates.append(DetectedProject(
                    LangType.SCALA, scala_score, None, [],
                    notes=[f"{scala_count} Scala files"]
                ))
        
        # Flutter/Dart
        flutter_score = 0
        flutter_entry = None
        flutter_builds = []
        
        if self._has("pubspec.yaml"):
            flutter_score += 50; flutter_builds.append("pubspec.yaml")
            if self._has("lib/main.dart"):
                flutter_score += 20; flutter_entry = "lib/main.dart"
        
        dart_count = self._count(".dart")
        if dart_count > 0:
            flutter_score += min(dart_count * 2, 20)
        
        if flutter_score > 0:
            candidates.append(DetectedProject(
                LangType.FLUTTER, flutter_score, flutter_entry, flutter_builds,
                notes=[f"{dart_count} Dart files"]
            ))
        
        # Nim
        nim_score = 0
        if self._has(".nim") or self._has(".nims") or self._has(".nimble"):
            nim_count = self._count(".nim")
            nim_score += min(nim_count * 10, 50)
            if self._has(".nimble"):
                nim_score += 30
            if nim_score > 0:
                candidates.append(DetectedProject(
                    LangType.NIM, nim_score, self._find("main.nim", "game.nim"), [],
                    notes=[f"{nim_count} Nim files"]
                ))
        
        # Zig
        zig_score = 0
        if self._has(".zig") or self._has("build.zig"):
            zig_count = self._count(".zig")
            zig_score += min(zig_count * 10, 50)
            if self._has("build.zig"):
                zig_score += 40
            if zig_score > 0:
                candidates.append(DetectedProject(
                    LangType.ZIG, zig_score, self._find("main.zig"), [],
                    notes=[f"{zig_count} Zig files"]
                ))
        
        # Crystal
        crystal_score = 0
        if self._has(".cr") or self._has("shard.yml"):
            cr_count = self._count(".cr")
            crystal_score += min(cr_count * 10, 50)
            if self._has("shard.yml"):
                crystal_score += 40
            if crystal_score > 0:
                candidates.append(DetectedProject(
                    LangType.CRYSTAL, crystal_score, self._find("main.cr", "game.cr"), [],
                    notes=[f"{cr_count} Crystal files"]
                ))
        
        # Ruby
        ruby_score = 0
        if self._has(".rb") or self._has("gemfile"):
            rb_count = self._count(".rb")
            ruby_score += min(rb_count * 5, 30)
            if self._has("gemfile"):
                ruby_score += 30
            if ruby_score > 0:
                candidates.append(DetectedProject(
                    LangType.RUBY, ruby_score, self._find("main.rb", "game.rb"), [],
                    notes=[f"{rb_count} Ruby files"]
                ))
        
        # Perl
        perl_score = 0
        if self._has(".pl") or self._has(".pm"):
            pl_count = self._count(".pl") + self._count(".pm")
            perl_score += min(pl_count * 5, 30)
            if perl_score > 0:
                candidates.append(DetectedProject(
                    LangType.PERL, perl_score, self._find("main.pl"), [],
                    notes=[f"{pl_count} Perl files"]
                ))
        
        # Pick best
        if not candidates:
            return DetectedProject(LangType.UNKNOWN, 0, None, [], 
                notes=["No recognizable project files found. Supported: Python, Node, C/C++, C#, Go, Rust, Java, Kotlin, Flutter, Lua/Love2D, Nim, Zig, Crystal, Ruby, Godot, Unity, Unreal, GameMaker, Ren'Py"])
        
        best = max(candidates, key=lambda x: x.confidence)
        
        # If Unity was detected but it's actually a C# project (not full Unity)
        if best.lang == LangType.UNITY and best.confidence < 80:
            # Check if it's just a C# project with Unity-like structure
            pass  # Keep as Unity
        
        return best


# ==================== BUILDERS ====================

class Builder:
    """Base builder with dependency management."""
    
    def __init__(self, project: DetectedProject, args, deps: DependencyManager):
        self.project = project
        self.args = args
        self.deps = deps
        self.project_dir = os.path.abspath(args.project or ".")
        self.dist_dir = os.path.abspath(args.output or "dist")
        os.makedirs(self.dist_dir, exist_ok=True)
        self.name = args.name or Path(self.project_dir).name
    
    def build(self) -> str:
        raise NotImplementedError
    
    def _run(self, cmd: List[str], cwd: str = None, env=None, shell: bool = False) -> subprocess.CompletedProcess:
        if self.args.verbose:
            log(f"Executing: {' '.join(cmd)}")
        return subprocess.run(
            cmd, cwd=cwd or self.project_dir,
            capture_output=not self.args.verbose,
            text=True, env=env or os.environ.copy(),
            shell=shell, timeout=600
        )
    
    def _find_file(self, pattern: str) -> Optional[str]:
        matches = glob.glob(os.path.join(self.project_dir, pattern), recursive=True)
        return matches[0] if matches else None
    
    def _print_result(self, exe_path: str):
        if os.path.exists(exe_path):
            size = os.path.getsize(exe_path) / (1024*1024)
            success(f"EXE built successfully!")
            info(f"Location: {exe_path}")
            info(f"Size: {size:.2f} MB")
            return exe_path
        return None


class PythonBuilder(Builder):
    def build(self) -> str:
        self.deps.ensure('python', 'pip')
        
        backend = self.args.backend or "auto"
        if backend == "auto":
            if self.deps.is_installed('nuitka') and not self.args.onefile:
                backend = "nuitka"
                info("Selected: Nuitka (better performance)")
            else:
                backend = "pyinstaller"
                info("Selected: PyInstaller (maximum compatibility)")
        
        self.deps.ensure('pyinstaller' if backend == 'pyinstaller' else 'nuitka')
        
        if backend == "nuitka":
            return self._build_nuitka()
        return self._build_pyinstaller()
    
    def _build_pyinstaller(self) -> str:
        script = self._resolve_entry()
        cmd = [sys.executable, "-m", "PyInstaller", script, "--noconfirm", "--clean"]
        
        if not self.args.console:
            cmd.append("--windowed")
        cmd.append("--onefile" if self.args.onefile else "--onedir")
        cmd.extend(["--name", self.name, "--distpath", self.dist_dir])
        
        work = os.path.join(self.dist_dir, "build")
        cmd.extend(["--workpath", work, "--specpath", work])
        
        if self.args.icon and os.path.exists(self.args.icon):
            cmd.extend(["--icon", os.path.abspath(self.args.icon)])
        
        # Auto-detect
        if self.args.auto_detect:
            for h in self._detect_hidden_imports():
                cmd.extend(["--hidden-import", h])
            sep = ";" if sys.platform == "win32" else ":"
            for src, dst in self._detect_data_files():
                cmd.extend(["--add-data", f"{src}{sep}{dst}"])
        
        for hi in (self.args.hidden_imports or []):
            cmd.extend(["--hidden-import", hi])
        
        result = self._run(cmd)
        if result.returncode != 0:
            error("PyInstaller build failed")
        
        exe = os.path.join(self.dist_dir, self.name, f"{self.name}.exe")
        if self.args.onefile:
            exe = os.path.join(self.dist_dir, f"{self.name}.exe")
        return self._print_result(exe) or error("Build output not found")
    
    def _build_nuitka(self) -> str:
        script = self._resolve_entry()
        out = os.path.join(self.dist_dir, f"{self.name}.exe")
        
        cmd = [sys.executable, "-m", "nuitka", "--standalone", "--lto=yes", "--jobs=4"]
        if not self.args.console:
            cmd.append("--windows-disable-console")
        if self.args.icon:
            cmd.append(f"--windows-icon-from-ico={os.path.abspath(self.args.icon)}")
        if self._uses_tkinter():
            cmd.append("--enable-plugin=tk-inter")
        
        cmd.extend([f"--output-dir={self.dist_dir}", f"--output-filename={self.name}.exe", script])
        result = self._run(cmd)
        return self._print_result(out) or error("Nuitka build failed")
    
    def _resolve_entry(self) -> str:
        if self.args.script:
            return os.path.abspath(self.args.script)
        if self.project.entry_point:
            return os.path.join(self.project_dir, self.project.entry_point)
        py_files = glob.glob(os.path.join(self.project_dir, "*.py"))
        if py_files:
            return py_files[0]
        error("No Python entry point found")
    
    def _uses_tkinter(self) -> bool:
        for root, _, files in os.walk(self.project_dir):
            for f in files:
                if f.endswith(".py"):
                    try:
                        with open(os.path.join(root, f), 'r', errors='ignore') as file:
                            if 'tkinter' in file.read():
                                return True
                    except:
                        pass
        return False
    
    def _detect_hidden_imports(self) -> List[str]:
        hidden = set()
        patterns = {
            'sklearn': ['sklearn.utils._typedefs'],
            'pandas': ['pandas._libs.tslibs.timedeltas'],
            'numpy': ['numpy.core._dtype_ctypes'],
            'PIL': ['PIL._tkinter_finder'],
            'matplotlib': ['matplotlib.backends.backend_tkagg'],
            'cryptography': ['cryptography.hazmat.backends.openssl'],
            'sqlalchemy': ['sqlalchemy.ext.baked'],
            'pygame': ['pygame'],
        }
        for root, _, files in os.walk(self.project_dir):
            for f in files:
                if f.endswith(".py"):
                    try:
                        with open(os.path.join(root, f), 'r', errors='ignore') as file:
                            content = file.read()
                            for pkg, imports in patterns.items():
                                if pkg in content:
                                    hidden.update(imports)
                    except:
                        pass
        return list(hidden)
    
    def _detect_data_files(self) -> List[Tuple[str, str]]:
        found = []
        patterns = ['*.json', '*.yaml', '*.yml', '*.xml', '*.ini', '*.cfg',
                   '*.txt', '*.html', '*.css', '*.js', '*.ico', '*.png', '*.jpg', '*.ttf',
                   'assets/**/*', 'resources/**/*', 'data/**/*', 'templates/**/*',
                   'images/**/*', 'sounds/**/*', 'music/**/*', 'fonts/**/*']
        for pattern in patterns:
            for match in glob.glob(os.path.join(self.project_dir, pattern), recursive=True):
                if os.path.isfile(match):
                    rel = os.path.relpath(os.path.dirname(match), self.project_dir)
                    found.append((match, rel if rel != '.' else ''))
        return found


class NodeBuilder(Builder):
    def build(self) -> str:
        self.deps.ensure('node', 'npm')
        
        if self.project.lang == LangType.ELECTRON:
            return self._build_electron()
        return self._build_node()
    
    def _build_node(self) -> str:
        self.deps.ensure('pkg')
        
        entry = self.args.script or self.project.entry_point or "index.js"
        if not os.path.exists(os.path.join(self.project_dir, entry)):
            error(f"Entry point not found: {entry}")
        
        # Install deps if needed
        if os.path.exists(os.path.join(self.project_dir, "package.json")):
            if not os.path.exists(os.path.join(self.project_dir, "node_modules")):
                log("Installing npm dependencies...")
                self._run(["npm", "install"])
        
        out = os.path.join(self.dist_dir, f"{self.name}.exe")
        cmd = ["pkg", entry, "--target", "node18-win-x64", "--output", out]
        
        # Compress with UPX if available
        if self.deps.is_installed('upx') and self.args.onefile:
            cmd.append("--compress", "GZip")
        
        result = self._run(cmd)
        return self._print_result(out) or error("pkg build failed")
    
    def _build_electron(self) -> str:
        self.deps.ensure('electron-builder')
        
        pkg_path = os.path.join(self.project_dir, "package.json")
        with open(pkg_path, 'r') as f:
            pkg = json.load(f)
        
        # Inject build config if missing
        if "build" not in pkg:
            pkg["build"] = {
                "appId": f"com.polybuild.{self.name}",
                "productName": self.name,
                "directories": {"output": self.dist_dir},
                "win": {
                    "target": "portable" if self.args.onefile else "nsis",
                    "icon": self.args.icon or "assets/icon.ico"
                }
            }
            with open(pkg_path, 'w') as f:
                json.dump(pkg, f, indent=2)
        
        if not os.path.exists(os.path.join(self.project_dir, "node_modules")):
            self._run(["npm", "install"])
        
        cmd = ["npx", "electron-builder", "--win", "--x64", "--publish", "never"]
        result = self._run(cmd)
        
        # Find output
        for f in os.listdir(self.dist_dir):
            if f.endswith(".exe"):
                return self._print_result(os.path.join(self.dist_dir, f)) or error("Output issue")
        error("Electron build produced no EXE")


class CppBuilder(Builder):
    def build(self) -> str:
        if self._has_cmake():
            self.deps.ensure('cmake')
            return self._build_cmake()
        elif self._has_makefile():
            self.deps.ensure('make', 'gcc')
            return self._build_make()
        else:
            self.deps.ensure('gcc')
            return self._build_direct()
    
    def _has_cmake(self) -> bool:
        return os.path.exists(os.path.join(self.project_dir, "CMakeLists.txt"))
    
    def _has_makefile(self) -> bool:
        return os.path.exists(os.path.join(self.project_dir, "Makefile"))
    
    def _build_cmake(self) -> str:
        build_dir = os.path.join(self.project_dir, "build")
        os.makedirs(build_dir, exist_ok=True)
        
        gen = "Visual Studio 17 2022" if sys.platform == "win32" else "Unix Makefiles"
        cmd = ["cmake", "..", f"-G{gen}", "-DCMAKE_BUILD_TYPE=Release"]
        result = self._run(cmd, cwd=build_dir)
        if result.returncode != 0:
            error("CMake configuration failed")
        
        result = self._run(["cmake", "--build", ".", "--config", "Release"], cwd=build_dir)
        if result.returncode != 0:
            error("CMake build failed")
        
        exe = self._find_exe_in(build_dir)
        if exe:
            dest = os.path.join(self.dist_dir, f"{self.name}.exe")
            shutil.copy2(exe, dest)
            return self._print_result(dest) or dest
        error("No EXE found in build output")
    
    def _build_make(self) -> str:
        env = os.environ.copy()
        env["CC"] = "gcc"
        env["CXX"] = "g++"
        env["CFLAGS"] = "-O2"
        env["CXXFLAGS"] = "-O2"
        
        result = self._run(["make", "-j4"], env=env)
        if result.returncode != 0:
            error("Make build failed")
        
        exe = self._find_exe_in(self.project_dir)
        if exe:
            dest = os.path.join(self.dist_dir, f"{self.name}.exe")
            shutil.copy2(exe, dest)
            return self._print_result(dest) or dest
        error("No EXE found")
    
    def _build_direct(self) -> str:
        entry = self.args.script or self.project.entry_point
        if not entry:
            error("No C/C++ entry point found")
        
        compiler = "g++" if entry.endswith(".cpp") else "gcc"
        out = os.path.join(self.dist_dir, f"{self.name}.exe")
        
        cmd = [compiler, "-O2", "-o", out, os.path.join(self.project_dir, entry)]
        for f in glob.glob(os.path.join(self.project_dir, "*.c*")):
            if os.path.basename(f) != os.path.basename(entry):
                cmd.append(f)
        
        result = self._run(cmd)
        return self._print_result(out) or error("Compilation failed")
    
    def _find_exe_in(self, directory: str) -> Optional[str]:
        for root, _, files in os.walk(directory):
            for f in files:
                if f.endswith(".exe"):
                    return os.path.join(root, f)
        return None


class CSharpBuilder(Builder):
    def build(self) -> str:
        self.deps.ensure('dotnet')
        
        csproj = None
        for f in self.project.build_files:
            if f.endswith(".csproj"):
                csproj = f
                break
        if not csproj:
            csproj = self._find_file("*.csproj")
        
        if not csproj:
            error("No .csproj file found")
        
        out = os.path.join(self.dist_dir, f"{self.name}.exe")
        cmd = [
            "dotnet", "publish", csproj,
            "-c", "Release", "-r", "win-x64",
            "--self-contained", "true",
            "-p:PublishSingleFile=true" if self.args.onefile else "",
            "-p:PublishTrimmed=true",
            "-p:EnableCompressionInSingleFile=true" if self.args.onefile else "",
            "-o", self.dist_dir
        ]
        cmd = [c for c in cmd if c]
        
        result = self._run(cmd)
        return self._print_result(out) or error("dotnet publish failed")


class GoBuilder(Builder):
    def build(self) -> str:
        self.deps.ensure('go')
        
        entry = self.args.script or self.project.entry_point or "."
        out = os.path.join(self.dist_dir, f"{self.name}.exe")
        
        env = os.environ.copy()
        env["GOOS"] = "windows"
        env["GOARCH"] = "amd64"
        
        ldflags = "-s -w"
        if not self.args.console:
            ldflags += " -H=windowsgui"
        if self.args.onefile:
            ldflags += " -extldflags=-static"
        
        cmd = ["go", "build", f"-ldflags={ldflags}", "-o", out]
        if os.path.isdir(os.path.join(self.project_dir, entry)):
            cmd.append(entry)
        else:
            cmd.append(os.path.join(self.project_dir, entry))
        
        result = self._run(cmd, env=env)
        return self._print_result(out) or error("Go build failed")


class RustBuilder(Builder):
    def build(self) -> str:
        self.deps.ensure('cargo', 'rustc')
        
        env = os.environ.copy()
        env["RUSTFLAGS"] = "-C target-feature=+crt-static"
        
        # Check Windows target
        check = subprocess.run(["rustup", "target", "list", "--installed"], 
                             capture_output=True, text=True)
        if "x86_64-pc-windows-gnu" not in check.stdout and sys.platform != "win32":
            log("Installing Windows cross-compile target...")
            subprocess.run(["rustup", "target", "add", "x86_64-pc-windows-gnu"])
            target = "x86_64-pc-windows-gnu"
        else:
            target = "x86_64-pc-windows-msvc" if sys.platform == "win32" else "x86_64-pc-windows-gnu"
        
        cmd = ["cargo", "build", "--release", "--target", target]
        result = self._run(cmd, env=env)
        
        if result.returncode != 0:
            warn("Cross-compile failed, trying native...")
            cmd = ["cargo", "build", "--release"]
            result = self._run(cmd, env=env)
            if result.returncode != 0:
                error("Cargo build failed")
            target = None
        
        crate_name = self._get_crate_name()
        target_dir = os.path.join(self.project_dir, "target", 
                                  target or "release", f"{crate_name}.exe")
        if not os.path.exists(target_dir):
            target_dir = os.path.join(self.project_dir, "target", "release", crate_name)
        
        dest = os.path.join(self.dist_dir, f"{self.name}.exe")
        if os.path.exists(target_dir):
            shutil.copy2(target_dir, dest)
        return self._print_result(dest) or error("Rust build output not found")
    
    def _get_crate_name(self) -> str:
        try:
            with open(os.path.join(self.project_dir, "Cargo.toml"), 'r') as f:
                for line in f:
                    if line.startswith("name"):
                        return line.split("=")[1].strip().strip('"').strip("'")
        except:
            pass
        return self.name


class JavaBuilder(Builder):
    def build(self) -> str:
        self.deps.ensure('javac', 'java')
        
        if self.deps.is_installed('jpackage') and self.args.onefile:
            return self._build_jpackage()
        elif self._has_gradle():
            self.deps.ensure('gradle')
            return self._build_gradle()
        elif self._has_maven():
            self.deps.ensure('mvn')
            return self._build_maven()
        else:
            return self._build_manual()
    
    def _has_gradle(self) -> bool:
        return os.path.exists(os.path.join(self.project_dir, "build.gradle")) or \
               os.path.exists(os.path.join(self.project_dir, "build.gradle.kts"))
    
    def _has_maven(self) -> bool:
        return os.path.exists(os.path.join(self.project_dir, "pom.xml"))
    
    def _build_jpackage(self) -> str:
        jar = self._compile_jar()
        out = os.path.join(self.dist_dir, f"{self.name}.exe")
        
        cmd = [
            "jpackage", "--input", self.dist_dir,
            "--name", self.name,
            "--main-jar", os.path.basename(jar),
            "--type", "exe", "--dest", self.dist_dir,
            "--win-console" if self.args.console else "--win-shortcut",
            "--win-menu", "--win-dir-chooser"
        ]
        if self.args.icon:
            cmd.extend(["--icon", os.path.abspath(self.args.icon)])
        
        result = self._run(cmd)
        return self._print_result(out) or error("jpackage failed")
    
    def _compile_jar(self) -> str:
        java_files = glob.glob(os.path.join(self.project_dir, "**/*.java"), recursive=True)
        if not java_files:
            error("No Java files found")
        
        classes = os.path.join(self.dist_dir, "classes")
        os.makedirs(classes, exist_ok=True)
        
        self._run(["javac", "-d", classes] + java_files)
        
        main_class = None
        for f in java_files:
            with open(f, 'r', errors='ignore') as file:
                if 'public static void main' in file.read():
                    main_class = Path(f).stem
                    break
        
        jar = os.path.join(self.dist_dir, f"{self.name}.jar")
        manifest = os.path.join(self.dist_dir, "MANIFEST.MF")
        with open(manifest, 'w') as f:
            f.write(f"Manifest-Version: 1.0\nMain-Class: {main_class or 'Main'}\n\n")
        
        self._run(["jar", "cvfm", jar, manifest, "-C", classes, "."])
        return jar
    
    def _build_gradle(self) -> str:
        wrapper = os.path.join(self.project_dir, "gradlew.bat" if sys.platform == "win32" else "gradlew")
        cmd = [wrapper if os.path.exists(wrapper) else "gradle", "build"]
        self._run(cmd)
        
        for pattern in ["build/libs/*.jar", "build/distributions/*.exe"]:
            matches = glob.glob(os.path.join(self.project_dir, pattern), recursive=True)
            if matches:
                dest = os.path.join(self.dist_dir, os.path.basename(matches[0]))
                shutil.copy2(matches[0], dest)
                return self._print_result(dest) or dest
        error("No Gradle output found")
    
    def _build_maven(self) -> str:
        self._run(["mvn", "package", "-DskipTests"])
        matches = glob.glob(os.path.join(self.project_dir, "target/*.jar"))
        if matches:
            dest = os.path.join(self.dist_dir, os.path.basename(matches[0]))
            shutil.copy2(matches[0], dest)
            return self._print_result(dest) or dest
        error("No Maven output found")
    
    def _build_manual(self) -> str:
        jar = self._compile_jar()
        # Create batch wrapper or use launch4j if available
        warn("Creating basic JAR output. Use --onefile for native EXE via jpackage.")
        return jar


class FlutterBuilder(Builder):
    def build(self) -> str:
        self.deps.ensure('flutter')
        
        result = self._run(["flutter", "build", "windows", "--release"])
        if result.returncode != 0:
            error("Flutter build failed")
        
        build_dirs = [
            os.path.join(self.project_dir, "build", "windows", "x64", "runner", "Release"),
            os.path.join(self.project_dir, "build", "windows", "runner", "Release")
        ]
        
        for d in build_dirs:
            if os.path.exists(d):
                for f in os.listdir(d):
                    if f.endswith(".exe"):
                        dest = os.path.join(self.dist_dir, f)
                        shutil.copy2(os.path.join(d, f), dest)
                        # Copy DLLs
                        for dll in glob.glob(os.path.join(d, "*.dll")):
                            shutil.copy2(dll, self.dist_dir)
                        return self._print_result(dest) or dest
        error("No Flutter EXE found")


class LuaBuilder(Builder):
    def build(self) -> str:
        self.deps.ensure('love')
        
        # Create .love file then bundle with love.exe
        love_file = os.path.join(self.dist_dir, f"{self.name}.love")
        
        with zipfile.ZipFile(love_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(self.project_dir):
                for f in files:
                    if f.endswith('.lua') or f.endswith('.png') or f.endswith('.jpg') or \
                       f.endswith('.ogg') or f.endswith('.wav') or f.endswith('.ttf') or \
                       f.endswith('.json') or f.endswith('.xml'):
                        full = os.path.join(root, f)
                        arc = os.path.relpath(full, self.project_dir)
                        zf.write(full, arc)
        
        # Find love.exe
        love_exe = shutil.which("love")
        if not love_exe:
            # Try common locations
            for path in ["C:\\Program Files\\LOVE\\love.exe", "C:\\Program Files (x86)\\LOVE\\love.exe"]:
                if os.path.exists(path):
                    love_exe = path
                    break
        
        if not love_exe:
            error("love.exe not found. Install LÖVE2D.")
        
        # Combine love.exe + game.love
        out = os.path.join(self.dist_dir, f"{self.name}.exe")
        with open(love_exe, 'rb') as f:
            love_data = f.read()
        with open(love_file, 'rb') as f:
            game_data = f.read()
        
        with open(out, 'wb') as f:
            f.write(love_data)
            f.write(game_data)
        
        os.remove(love_file)
        return self._print_result(out) or error("Love2D build failed")


class GodotBuilder(Builder):
    def build(self) -> str:
        self.deps.ensure('godot')
        
        # Godot exports via command line
        export_preset = "Windows Desktop"  # Common preset name
        out = os.path.join(self.dist_dir, f"{self.name}.exe")
        
        cmd = [
            "godot", "--headless", "--path", self.project_dir,
            "--export-release", export_preset, out
        ]
        result = self._run(cmd)
        return self._print_result(out) or error("Godot export failed. Ensure export template is installed.")


class NimBuilder(Builder):
    def build(self) -> str:
        self.deps.ensure('nim')
        
        entry = self.args.script or self.project.entry_point or "main.nim"
        out = os.path.join(self.dist_dir, f"{self.name}.exe")
        
        cmd = ["nim", "c", "-d:release", "--opt:speed", "-o:" + out]
        if not self.args.console:
            cmd.append("--app:gui")
        cmd.append(os.path.join(self.project_dir, entry))
        
        result = self._run(cmd)
        return self._print_result(out) or error("Nim compilation failed")


class ZigBuilder(Builder):
    def build(self) -> str:
        self.deps.ensure('zig')
        
        if os.path.exists(os.path.join(self.project_dir, "build.zig")):
            cmd = ["zig", "build", "-Drelease-fast=true", "-Dtarget=x86_64-windows-gnu"]
            result = self._run(cmd)
            out = os.path.join(self.project_dir, "zig-out", "bin", f"{self.name}.exe")
        else:
            entry = self.args.script or self.project.entry_point or "main.zig"
            out = os.path.join(self.dist_dir, f"{self.name}.exe")
            cmd = ["zig", "build-exe", "-O", "ReleaseFast", "-target", "x86_64-windows-gnu",
                   "-o", out, os.path.join(self.project_dir, entry)]
            result = self._run(cmd)
        
        return self._print_result(out) or error("Zig build failed")


class CrystalBuilder(Builder):
    def build(self) -> str:
        self.deps.ensure('crystal')
        
        entry = self.args.script or self.project.entry_point or "main.cr"
        out = os.path.join(self.dist_dir, self.name)
        
        cmd = ["crystal", "build", "--release", "--no-debug", "-o", out,
               os.path.join(self.project_dir, entry)]
        result = self._run(cmd)
        
        exe = out + ".exe" if sys.platform == "win32" else out
        return self._print_result(exe) or error("Crystal build failed")


class RubyBuilder(Builder):
    def build(self) -> str:
        warn("Ruby to EXE requires OCRA. Attempting install...")
        self.deps.ensure('ruby')
        
        # Try OCRA
        try:
            subprocess.run(["gem", "install", "ocra"], capture_output=True, timeout=60)
        except:
            pass
        
        entry = self.args.script or self.project.entry_point or "main.rb"
        out = os.path.join(self.dist_dir, f"{self.name}.exe")
        
        cmd = ["ocra", "--windows", "--output", out, os.path.join(self.project_dir, entry)]
        result = self._run(cmd)
        return self._print_result(out) or error("OCRA build failed. Install manually: gem install ocra")


# ==================== BUILDER FACTORY ====================

BUILDERS = {
    LangType.PYTHON: PythonBuilder,
    LangType.NODE: NodeBuilder,
    LangType.ELECTRON: NodeBuilder,
    LangType.CPP: CppBuilder,
    LangType.C: CppBuilder,
    LangType.CSHARP: CSharpBuilder,
    LangType.GO: GoBuilder,
    LangType.RUST: RustBuilder,
    LangType.JAVA: JavaBuilder,
    LangType.KOTLIN: JavaBuilder,
    LangType.SCALA: JavaBuilder,
    LangType.FLUTTER: FlutterBuilder,
    LangType.DART: FlutterBuilder,
    LangType.LUA: LuaBuilder,
    LangType.LOVE2D: LuaBuilder,
    LangType.NIM: NimBuilder,
    LangType.ZIG: ZigBuilder,
    LangType.CRYSTAL: CrystalBuilder,
    LangType.RUBY: RubyBuilder,
    LangType.GODOT: GodotBuilder,
}


# ==================== MAIN ====================

def main():
    parser = argparse.ArgumentParser(
        description=f"PolyBuild Pro v{VERSION} - Universal App & Game EXE Builder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Supported Languages & Engines:
  Python, Node.js, Electron, C/C++, C#, Go, Rust, Java, Kotlin, Scala
  Flutter/Dart, Lua, LÖVE2D, Nim, Zig, Crystal, Ruby, Perl
  Godot, Unity, Unreal Engine, GameMaker, Ren'Py

Examples:
  polybuild                          # Auto-detect & build
  polybuild -p ./my-game --name Game # Build project as Game.exe
  polybuild --onefile --icon app.ico # Single EXE with icon
  polybuild --backend nuitka         # Use Nuitka for Python
  polybuild --update-deps            # Update all project dependencies
  polybuild --check-tools            # Check installed build tools
  polybuild --update                 # Update PolyBuild itself
        """
    )
    
    # Core
    parser.add_argument("--project", "-p", help="Project directory")
    parser.add_argument("--script", "-s", help="Override entry point")
    parser.add_argument("--name", "-n", help="Output EXE name")
    parser.add_argument("--icon", "-i", help="Path to .ico file")
    parser.add_argument("--output", "-o", default="dist", help="Output directory")
    parser.add_argument("--lang", choices=[
        "python", "node", "electron", "cpp", "c", "csharp", "go", "rust",
        "java", "kotlin", "scala", "flutter", "dart", "lua", "love2d",
        "nim", "zig", "crystal", "ruby", "perl", "godot", "unity", "unreal",
        "gamemaker", "renpy"
    ], help="Force language (skip auto-detect)")
    
    # Build options
    parser.add_argument("--onefile", "-f", action="store_true", help="Single executable")
    parser.add_argument("--console", "-c", action="store_true", help="Keep console window")
    parser.add_argument("--backend", choices=["auto", "pyinstaller", "nuitka"], default="auto")
    parser.add_argument("--auto-detect", action="store_true", default=True)
    parser.add_argument("--no-auto-detect", dest="auto_detect", action="store_false")
    parser.add_argument("--hidden-imports", action="append")
    parser.add_argument("--add-data", action="append")
    
    # Maintenance
    parser.add_argument("--update", action="store_true", help="Update PolyBuild to latest version")
    parser.add_argument("--update-deps", action="store_true", help="Update project dependencies before build")
    parser.add_argument("--check-tools", action="store_true", help="Check all build tools status")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--init", action="store_true", help="Create polybuild.json config")
    
    args = parser.parse_args()
    
    # Banner
    print(f"""{Colors.CYAN}{Colors.BOLD}
    ╔═══════════════════════════════════════════════════════════════╗
    ║  POLYBUILD PRO v{VERSION:<8} - Universal EXE Builder            ║
    ║  Auto-detects 20+ languages | Self-updating | Auto-deps       ║
    ╚═══════════════════════════════════════════════════════════════╝{Colors.END}
    """)
    
    # Self-update
    if args.update:
        SelfUpdater.check_update(force=True)
        return
    
    # Check for updates silently first
    if not args.check_tools:
        SelfUpdater.check_update(force=False)
    
    # Init config
    if args.init:
        template = {
            "version": VERSION,
            "project_dir": ".",
            "name": "MyApp",
            "icon": "assets/icon.ico",
            "onefile": True,
            "console": False,
            "backend": "auto",
            "auto_detect": True,
            "update_deps": True
        }
        with open("polybuild.json", "w") as f:
            json.dump(template, f, indent=2)
        success("Created polybuild.json")
        return
    
    # Check tools
    if args.check_tools:
        deps = DependencyManager()
        tools = list(deps.TOOLS.keys())
        print(f"\n{Colors.BOLD}Build Tool Status:{Colors.END}\n")
        for tool in sorted(tools):
            installed = deps.is_installed(tool)
            status = f"{Colors.GREEN}✓ INSTALLED{Colors.END}" if installed else f"{Colors.RED}✗ MISSING{Colors.END}"
            info_type = deps.TOOLS[tool].get('type', 'tool')
            print(f"  {status:<20} {tool:<20} ({info_type})")
        return
    
    # Determine project
    project_dir = os.path.abspath(args.project or ".")
    if not os.path.isdir(project_dir):
        error(f"Directory not found: {project_dir}")
    
    # Detect or force language
    if args.lang:
        lang_map = {
            "python": LangType.PYTHON, "node": LangType.NODE, "electron": LangType.ELECTRON,
            "cpp": LangType.CPP, "c": LangType.C, "csharp": LangType.CSHARP,
            "go": LangType.GO, "rust": LangType.RUST, "java": LangType.JAVA,
            "kotlin": LangType.KOTLIN, "scala": LangType.SCALA, "flutter": LangType.FLUTTER,
            "dart": LangType.DART, "lua": LangType.LUA, "love2d": LangType.LOVE2D,
            "nim": LangType.NIM, "zig": LangType.ZIG, "crystal": LangType.CRYSTAL,
            "ruby": LangType.RUBY, "perl": LangType.PERL, "godot": LangType.GODOT,
            "unity": LangType.UNITY, "unreal": LangType.UNREAL,
            "gamemaker": LangType.GAMEMAKER, "renpy": LangType.RENPY
        }
        detected = DetectedProject(lang_map[args.lang], 100, args.script or None, [], notes=["Forced by user"])
        info(f"Language forced: {args.lang}")
    else:
        log(f"Scanning: {project_dir}")
        detector = ProjectDetector(project_dir)
        detected = detector.detect()
    
    # Display detection
    print(f"\n{Colors.BOLD}{'─'*50}{Colors.END}")
    print(f"{Colors.BOLD}Detection Results:{Colors.END}")
    print(f"  Language:     {Colors.CYAN}{detected.lang.name}{Colors.END}")
    print(f"  Confidence:   {detected.confidence}%")
    print(f"  Entry Point:  {detected.entry_point or 'Auto-detect'}")
    print(f"  Build Files:  {', '.join(detected.build_files) or 'N/A'}")
    if detected.framework:
        print(f"  Framework:    {detected.framework}")
    if detected.game_engine:
        print(f"  Game Engine:  {Colors.MAGENTA}{detected.game_engine}{Colors.END}")
    if detected.notes:
        for note in detected.notes:
            print(f"  Note:         {note}")
    print(f"{Colors.BOLD}{'─'*50}{Colors.END}\n")
    
    if detected.lang == LangType.UNKNOWN:
        error("Could not detect project type. Use --lang to force.")
    
    if detected.confidence < 30:
        warn("Low confidence detection. Verify with --lang or check project structure.")
    
    # Dependency management
    deps = DependencyManager()
    
    if args.update_deps:
        deps.update_project_deps(project_dir, detected.lang)
    
    # Build
    builder_class = BUILDERS.get(detected.lang)
    if not builder_class:
        error(f"No builder available for {detected.lang.name}")
    
    builder = builder_class(detected, args, deps)
    
    try:
        start_time = datetime.now()
        exe_path = builder.build()
        elapsed = (datetime.now() - start_time).total_seconds()
        
        print(f"\n{Colors.GREEN}{Colors.BOLD}╔═══════════════════════════════════════════════════╗")
        print(f"║  BUILD SUCCESSFUL                                 ║")
        print(f"║  Output: {exe_path[-45:]:<45} ║")
        print(f"║  Time:   {elapsed:.1f}s{' '*40} ║")
        print(f"╚═══════════════════════════════════════════════════╝{Colors.END}\n")
        
    except KeyboardInterrupt:
        warn("\nBuild interrupted by user")
        sys.exit(1)
    except Exception as e:
        error(f"Build failed: {str(e)}")


if __name__ == "__main__":
    main()