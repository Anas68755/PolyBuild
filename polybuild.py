#!/usr/bin/env python3
"""
PolyBuild Pro v2.3 - Universal App & Game Builder (EXE / APK / Native)
Auto-detects 25+ languages, self-updates, auto-manages & installs dependencies.

FIXES (v2.3):
  - Case-sensitive file paths preserved (no more silent lowercasing)
  - Native builds on Linux/macOS (no more forced Windows cross-compile)
  - LÖVE2D fused binaries get +x on Unix
  - winget/choco install return codes checked
  - is_installed() no longer false-positives on Windows
  - Java main-class respects package declarations
  - Zig uses modern -Doptimize=ReleaseFast
  - Rust crate name parsed from [package] section only
  - Godot export preset auto-detected from export_presets.cfg
  - Header-only C++ libraries detected
  - Electron: no longer mutates user's package.json
  - Electron: no longer pollutes user's project with electron-builder dep
  - Electron: staging dir cleaned up after build
  - Electron: exit codes checked, stale .exe no longer reported as success
  - Electron: recursive artifact search (handles win-unpacked/ nesting)
  - --console no longer opens DevTools; new --devtools flag for that
  - node_modules / .git / build caches excluded from scan
  - Nested package.json files checked for Electron detection
  - Self-update validates downloaded code with ast.parse before overwriting
  - ProjectDetector._has() correctly matches file extensions (fixes Kotlin, Scala, Perl, etc.)
  - ProjectDetector._has_dir() correctly checks for folder names (fixes Unity/Unreal detection)

NEW:
  - Android APK builds (Java/Kotlin/Flutter → gradlew assembleRelease)
  - --target-os flag (native | windows | android)
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
VERSION = "2.3.0"
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

# Directories to skip during scan — node_modules, .git, build caches
EXCLUDED_DIRS: Set[str] = {
    '.git', 'node_modules', '__pycache__', '.venv', 'venv', 'env',
    '.idea', '.vscode', 'target', 'zig-cache', 'zig-out',
    '.gradle', '.android', 'DerivedData', '.cargo', 'cache',
    '.next', '.nuxt', '.svelte-kit', '.angular',
    'Pods', '.symlinks',
}


def exe_ext(target_os: str = "native") -> str:
    """Return the executable file extension for the given target."""
    if target_os == "windows":
        return ".exe"
    if target_os == "native":
        return ".exe" if sys.platform == "win32" else ""
    return ""


# ==================== SELF-UPDATE SYSTEM ====================

class SelfUpdater:
    """Handles checking for updates and self-patching. Disabled if URLs not set."""

    @staticmethod
    def check_update(force: bool = False) -> bool:
        if not VERSION_CHECK_URL or not UPDATE_URL:
            dim("Self-update disabled. Set POLYBUILD_UPDATE_URL env var to enable.")
            return False

        try:
            log("Checking for PolyBuild updates...")
            req = urllib.request.Request(
                VERSION_CHECK_URL,
                headers={'User-Agent': 'PolyBuild-Updater'}
            )
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
            else:
                dim(f"PolyBuild is up to date (v{VERSION})")
                return False
        except Exception as e:
            warn(f"Could not check for updates: {e}")
            return False

    @staticmethod
    def _version_compare(v1: str, v2: str) -> int:
        def normalize(v):
            return [int(x) for x in re.sub(r'[^0-9.]', '', v).split('.')]
        n1, n2 = normalize(v1), normalize(v2)
        return (n1 > n2) - (n1 < n2)

    @staticmethod
    def _perform_update(update_data: dict) -> bool:
        try:
            log("Downloading update...")
            req = urllib.request.Request(
                update_data.get('download_url', UPDATE_URL),
                headers={'User-Agent': 'PolyBuild-Updater'}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                new_code = resp.read().decode('utf-8')

            if 'sha256' in update_data:
                if hashlib.sha256(new_code.encode()).hexdigest() != update_data['sha256']:
                    error("Update verification failed (hash mismatch)")

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

            try:
                import py_compile
                py_compile.compile(script_path, doraise=True)
            except Exception as rollback_err:
                warn(f"New script failed compile check, rolling back: {rollback_err}")
                shutil.copy2(backup_path, script_path)
                error("Update rolled back — downloaded code would not compile")
            return True
        except Exception as e:
            error(f"Update failed: {e}")
            return False


# ==================== BASE TOOL INSTALLER ====================

class BaseToolInstaller:
    """Installs base runtimes like Node.js, Python, Git, Chocolatey, winget packages."""

    def __init__(self):
        self._winget_checked = False
        self._winget_available = False
        self._choco_checked = False
        self._choco_available = False
        self._apt_checked = False
        self._apt_available = False
        self._brew_checked = False
        self._brew_available = False

    def _has_apt(self) -> bool:
        if sys.platform.startswith("linux") and not self._apt_checked:
            self._apt_available = shutil.which("apt-get") is not None
            self._apt_checked = True
        return self._apt_available

    def _has_brew(self) -> bool:
        if sys.platform == "darwin" and not self._brew_checked:
            self._brew_available = shutil.which("brew") is not None
            self._brew_checked = True
        return self._brew_available

    def install_via_apt(self, package: str) -> bool:
        if not self._has_apt():
            return False
        log(f"Installing {package} via apt-get...")
        try:
            sudo = [] if os.geteuid() == 0 else ["sudo"]
            subprocess.run(sudo + ["apt-get", "update", "-qq"], capture_output=True, timeout=180)
            result = subprocess.run(
                sudo + ["apt-get", "install", "-y"] + package.split(),
                capture_output=True, text=True, timeout=300
            )
            if result.returncode == 0:
                success(f"{package} installed via apt-get")
                return True
            warn(f"apt-get install output: {result.stderr}")
            return False
        except Exception as e:
            warn(f"apt-get install failed: {e}")
            return False

    def install_via_brew(self, package: str) -> bool:
        if not self._has_brew():
            return False
        log(f"Installing {package} via Homebrew...")
        try:
            result = subprocess.run(
                ["brew", "install"] + package.split(),
                capture_output=True, text=True, timeout=300
            )
            if result.returncode == 0:
                success(f"{package} installed via Homebrew")
                return True
            warn(f"brew install output: {result.stderr}")
            return False
        except Exception as e:
            warn(f"Homebrew install failed: {e}")
            return False

    def install_via_pkgmgr(self, apt_pkg: str = None, brew_pkg: str = None,
                           choco_pkg: str = None, winget_id: str = None) -> bool:
        if sys.platform == "win32":
            if winget_id and self.install_via_winget(winget_id):
                return True
            if choco_pkg and self.install_via_choco(choco_pkg):
                return True
        elif sys.platform == "darwin":
            if brew_pkg and self.install_via_brew(brew_pkg):
                return True
        elif sys.platform.startswith("linux"):
            if apt_pkg and self.install_via_apt(apt_pkg):
                return True
        return False

    def _has_winget(self) -> bool:
        if not self._winget_checked:
            try:
                result = subprocess.run(["winget", "--version"], capture_output=True, timeout=5)
                self._winget_available = result.returncode == 0
            except Exception:
                self._winget_available = False
            self._winget_checked = True
        return self._winget_available

    def _has_choco(self) -> bool:
        if not self._choco_checked:
            try:
                result = subprocess.run(["choco", "--version"], capture_output=True, timeout=5)
                self._choco_available = result.returncode == 0
            except Exception:
                self._choco_available = False
            self._choco_checked = True
        return self._choco_available

    def install_via_winget(self, package_id: str) -> bool:
        if not self._has_winget():
            return False
        log(f"Installing {package_id} via winget...")
        try:
            result = subprocess.run(
                ["winget", "install", "--id", package_id, "-e",
                 "--accept-source-agreements", "--accept-package-agreements"],
                capture_output=True, text=True, timeout=300
            )
            if result.returncode == 0:
                success(f"{package_id} installed via winget")
                return True
            warn(f"winget install failed (exit {result.returncode}): "
                 f"{result.stderr[:300] if result.stderr else 'no stderr'}")
            return False
        except Exception as e:
            warn(f"winget install failed: {e}")
            return False

    def install_via_choco(self, package: str) -> bool:
        if not self._has_choco():
            if not self.install_chocolatey():
                return False
        log(f"Installing {package} via Chocolatey...")
        try:
            result = subprocess.run(
                ["choco", "install", package, "-y", "--no-progress"],
                capture_output=True, text=True, timeout=300
            )
            if result.returncode == 0:
                success(f"{package} installed via Chocolatey")
                return True
            warn(f"choco install failed (exit {result.returncode}): "
                 f"{result.stderr[:300] if result.stderr else 'no stderr'}")
            return False
        except Exception as e:
            warn(f"Chocolatey install failed: {e}")
            return False

    def install_chocolatey(self) -> bool:
        if sys.platform != 'win32':
            return False
        log("Installing Chocolatey...")
        try:
            cmd = [
                'powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                '-Command',
                "Set-ExecutionPolicy Bypass -Scope Process -Force; "
                "[System.Net.ServicePointManager]::SecurityProtocol = "
                "[System.Net.ServicePointManager]::SecurityProtocol -bor 3072; "
                "iex ((New-Object System.Net.WebClient).DownloadString("
                "'https://community.chocolatey.org/install.ps1'))"
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode == 0:
                success("Chocolatey installed")
                os.environ["PATH"] = (os.environ.get("PATH", "")
                                      + r";C:\ProgramData\chocolatey\bin")
                self._choco_available = True
                return True
            warn(f"Chocolatey install output: {result.stderr}")
            return False
        except Exception as e:
            warn(f"Failed to install Chocolatey: {e}")
            return False

    def install_nodejs(self) -> bool:
        if sys.platform == 'win32':
            if self.install_via_winget("OpenJS.NodeJS"):
                self._refresh_node_paths()
                return True
            log("Downloading Node.js LTS installer...")
            installer_url = "https://nodejs.org/dist/v20.11.1/node-v20.11.1-x64.msi"
            installer_path = os.path.join(tempfile.gettempdir(), "node_installer.msi")
            try:
                urllib.request.urlretrieve(installer_url, installer_path)
                log("Running Node.js installer (silent)...")
                result = subprocess.run(
                    ["msiexec", "/i", installer_path, "/qn", "/norestart"],
                    capture_output=True, text=True, timeout=180
                )
                if result.returncode == 0:
                    success("Node.js installed")
                    self._refresh_node_paths()
                    return True
                warn(f"Node.js installer exited with code {result.returncode}")
            except Exception as e:
                warn(f"Failed to install Node.js: {e}")
        elif self.install_via_pkgmgr(apt_pkg="nodejs npm", brew_pkg="node"):
            return True
        warn("Automatic Node.js install failed. Install manually from https://nodejs.org/")
        return False

    def _refresh_node_paths(self):
        for node_path in [r"C:\Program Files\nodejs", r"C:\Program Files (x86)\nodejs"]:
            if os.path.exists(node_path) and node_path not in os.environ.get("PATH", ""):
                os.environ["PATH"] = node_path + os.pathsep + os.environ.get("PATH", "")
        npm_global = os.path.expandvars(r"%APPDATA%\npm")
        if os.path.exists(npm_global) and npm_global not in os.environ.get("PATH", ""):
            os.environ["PATH"] = npm_global + os.pathsep + os.environ.get("PATH", "")

    def install_python(self) -> bool:
        warn("Python is required to run PolyBuild but was not found.")
        print(f"\n{Colors.CYAN}Install Python 3.8+ from: https://www.python.org/downloads/{Colors.END}")
        print(f"{Colors.CYAN}Check 'Add Python to PATH' during installation.{Colors.END}\n")
        return False

    def install_git(self) -> bool:
        if self.install_via_pkgmgr(apt_pkg="git", brew_pkg="git",
                                   choco_pkg="git", winget_id="Git.Git"):
            return True
        warn("Please install Git from https://git-scm.com/downloads")
        return False

    def install_go(self) -> bool:
        if self.install_via_pkgmgr(apt_pkg="golang-go", brew_pkg="go",
                                   choco_pkg="golang", winget_id="GoLang.Go"):
            return True
        warn("Please install Go from https://go.dev/dl/")
        return False

    def install_rust(self) -> bool:
        if sys.platform == 'win32':
            if self.install_via_winget("Rustlang.Rustup"):
                return True
            log("Downloading Rust installer...")
            try:
                installer_path = os.path.join(tempfile.gettempdir(), "rustup-init.exe")
                urllib.request.urlretrieve("https://win.rustup.rs/x86_64", installer_path)
                result = subprocess.run([installer_path, "-y"],
                                         capture_output=True, text=True, timeout=180)
                if result.returncode == 0:
                    success("Rust installed via rustup")
                    cargo_home = os.path.expanduser("~\\.cargo\\bin")
                    if cargo_home not in os.environ.get("PATH", ""):
                        os.environ["PATH"] = cargo_home + os.pathsep + os.environ.get("PATH", "")
                    return True
            except Exception as e:
                warn(f"Rust install failed: {e}")
        else:
            log("Installing Rust via rustup.sh...")
            try:
                script_path = os.path.join(tempfile.gettempdir(), "rustup-init.sh")
                urllib.request.urlretrieve("https://sh.rustup.rs", script_path)
                os.chmod(script_path, 0o755)
                result = subprocess.run(
                    ["sh", script_path, "-y", "--default-toolchain", "stable"],
                    capture_output=True, text=True, timeout=300
                )
                if result.returncode == 0:
                    success("Rust installed via rustup")
                    cargo_home = os.path.expanduser("~/.cargo/bin")
                    if cargo_home not in os.environ.get("PATH", ""):
                        os.environ["PATH"] = cargo_home + os.pathsep + os.environ.get("PATH", "")
                    return True
                warn(f"rustup script exited with code {result.returncode}")
            except Exception as e:
                warn(f"Rust install failed: {e}")
        warn("Please install Rust from https://rustup.rs/")
        return False

    def install_dotnet(self) -> bool:
        if self.install_via_pkgmgr(apt_pkg="dotnet-sdk-8.0", brew_pkg="dotnet-sdk",
                                    choco_pkg="dotnet-sdk", winget_id="Microsoft.DotNet.SDK.8"):
            return True
        warn("Please install .NET SDK from https://dotnet.microsoft.com/download")
        return False

    def install_java(self) -> bool:
        if self.install_via_pkgmgr(apt_pkg="openjdk-21-jdk", brew_pkg="openjdk",
                                    choco_pkg="openjdk", winget_id="EclipseAdoptium.Temurin.21.JDK"):
            return True
        warn("Please install JDK from https://adoptium.net/")
        return False

    def install_cmake(self) -> bool:
        if self.install_via_pkgmgr(apt_pkg="cmake", brew_pkg="cmake",
                                    choco_pkg="cmake", winget_id="Kitware.CMake"):
            return True
        warn("Please install CMake from https://cmake.org/download/")
        return False

    def install_mingw(self) -> bool:
        if sys.platform.startswith('linux'):
            if self.install_via_apt("build-essential"):
                return True
        if self.install_via_pkgmgr(apt_pkg="build-essential", brew_pkg="gcc",
                                    choco_pkg="mingw", winget_id="MSYS2.MSYS2"):
            return True
        warn("Please install MinGW-w64 from https://www.mingw-w64.org/downloads/")
        return False

    def install_flutter(self) -> bool:
        if self.install_via_pkgmgr(brew_pkg="--cask flutter", winget_id="Google.Flutter"):
            return True
        if sys.platform.startswith('linux'):
            warn("Auto-install of Flutter on Linux: 'sudo snap install flutter --classic'")
        warn("Please install Flutter from https://docs.flutter.dev/get-started/install")
        return False

    def install_godot(self) -> bool:
        if self.install_via_pkgmgr(apt_pkg="godot3", brew_pkg="godot",
                                    choco_pkg="godot", winget_id="GodotEngine.GodotEngine"):
            return True
        warn("Please install Godot from https://godotengine.org/download")
        return False

    def install_love(self) -> bool:
        if self.install_via_pkgmgr(apt_pkg="love", brew_pkg="love",
                                    choco_pkg="love", winget_id="Love2D.Love2D"):
            return True
        warn("Please install LÖVE2D from https://love2d.org/")
        return False

    def install_nim(self) -> bool:
        if self.install_via_pkgmgr(apt_pkg="nim", brew_pkg="nim", choco_pkg="nim"):
            return True
        warn("Please install Nim from https://nim-lang.org/install.html")
        return False

    def install_zig(self) -> bool:
        if self.install_via_pkgmgr(brew_pkg="zig", choco_pkg="zig", winget_id="zig.zig"):
            return True
        if sys.platform.startswith('linux'):
            warn("Auto-install of Zig on Linux: 'sudo snap install zig --classic --beta'")
        warn("Please install Zig from https://ziglang.org/download/")
        return False

    def install_crystal(self) -> bool:
        if self.install_via_pkgmgr(apt_pkg="crystal", brew_pkg="crystal", choco_pkg="crystal"):
            return True
        warn("Please install Crystal from https://crystal-lang.org/install/")
        return False

    def install_ruby(self) -> bool:
        if self.install_via_pkgmgr(apt_pkg="ruby-full", brew_pkg="ruby",
                                    choco_pkg="ruby", winget_id="RubyInstallerTeam.Ruby.3.2"):
            return True
        warn("Please install Ruby from https://rubyinstaller.org/")
        return False

    def install_android_sdk(self) -> bool:
        """Install Android SDK command-line tools."""
        if sys.platform == 'win32':
            if self.install_via_winget("Google.AndroidStudio"):
                return True
        elif self.install_via_pkgmgr(apt_pkg="android-sdk", brew_pkg="--cask android-sdk"):
            return True
        warn("Please install Android SDK from https://developer.android.com/studio")
        return False


# ==================== DEPENDENCY MANAGER ====================

class DependencyManager:
    """Auto-detects, checks, installs and updates build dependencies."""

    TOOLS = {
        'python': {'check': ['python', '--version'], 'type': 'runtime', 'install_fn': 'python'},
        'pip': {'check': ['pip', '--version'], 'type': 'python_tool'},
        'pyinstaller': {'check': ['pyinstaller', '--version'], 'install': 'pip', 'pkg': 'pyinstaller', 'type': 'python_tool'},
        'nuitka': {'check': ['python', '-m', 'nuitka', '--version'], 'install': 'pip', 'pkg': 'nuitka', 'type': 'python_tool'},

        'node': {'check': ['node', '--version'], 'type': 'runtime', 'install_fn': 'nodejs'},
        'npm': {'check': ['npm', '--version'], 'type': 'runtime'},
        'pkg': {'check': ['pkg', '--version'], 'install': 'npm', 'pkg': 'pkg', 'global': True, 'type': 'node_tool'},
        'electron-builder': {'check': ['npx', 'electron-builder', '--version'], 'install': 'npm', 'pkg': 'electron-builder', 'global': False, 'type': 'node_tool'},
        'electron': {'check': ['npx', 'electron', '--version'], 'install': 'npm', 'pkg': 'electron', 'global': False, 'type': 'node_tool'},

        'gcc': {'check': ['gcc', '--version'], 'install_fn': 'mingw', 'type': 'compiler'},
        'g++': {'check': ['g++', '--version'], 'install_fn': 'mingw', 'type': 'compiler'},
        'clang': {'check': ['clang', '--version'], 'type': 'compiler'},
        'cmake': {'check': ['cmake', '--version'], 'install_fn': 'cmake', 'type': 'build_tool'},
        'meson': {'check': ['meson', '--version'], 'install': 'pip', 'pkg': 'meson', 'type': 'build_tool'},
        'ninja': {'check': ['ninja', '--version'], 'install': 'pkgmgr', 'choco_pkg': 'ninja', 'apt_pkg': 'ninja-build', 'brew_pkg': 'ninja', 'type': 'build_tool'},
        'make': {'check': ['make', '--version'], 'type': 'build_tool'},

        'dotnet': {'check': ['dotnet', '--version'], 'install_fn': 'dotnet', 'type': 'sdk'},
        'msbuild': {'check': ['msbuild', '/version'], 'type': 'build_tool'},

        'go': {'check': ['go', 'version'], 'install_fn': 'go', 'type': 'sdk'},

        'cargo': {'check': ['cargo', '--version'], 'install_fn': 'rust', 'type': 'sdk'},
        'rustc': {'check': ['rustc', '--version'], 'type': 'sdk'},

        'java': {'check': ['java', '--version'], 'install_fn': 'java', 'type': 'runtime'},
        'javac': {'check': ['javac', '--version'], 'type': 'compiler'},
        'jpackage': {'check': ['jpackage', '--version'], 'type': 'build_tool'},
        'mvn': {'check': ['mvn', '--version'], 'install': 'pkgmgr', 'choco_pkg': 'maven', 'apt_pkg': 'maven', 'brew_pkg': 'maven', 'type': 'build_tool'},
        'gradle': {'check': ['gradle', '--version'], 'install': 'pkgmgr', 'choco_pkg': 'gradle', 'apt_pkg': 'gradle', 'brew_pkg': 'gradle', 'type': 'build_tool'},

        'flutter': {'check': ['flutter', '--version'], 'install_fn': 'flutter', 'type': 'sdk'},
        'dart': {'check': ['dart', '--version'], 'type': 'sdk'},

        'kotlinc': {'check': ['kotlinc', '-version'], 'install': 'pkgmgr', 'choco_pkg': 'kotlin', 'apt_pkg': 'kotlin', 'brew_pkg': 'kotlin', 'type': 'compiler'},

        'nim': {'check': ['nim', '--version'], 'install_fn': 'nim', 'type': 'sdk'},
        'zig': {'check': ['zig', 'version'], 'install_fn': 'zig', 'type': 'sdk'},
        'lua': {'check': ['lua', '-v'], 'type': 'runtime'},
        'love': {'check': ['love', '--version'], 'install_fn': 'love', 'type': 'runtime'},
        'crystal': {'check': ['crystal', '--version'], 'install_fn': 'crystal', 'type': 'sdk'},
        'ruby': {'check': ['ruby', '--version'], 'install_fn': 'ruby', 'type': 'runtime'},
        'godot': {'check': ['godot', '--version'], 'install_fn': 'godot', 'type': 'engine'},
        'upx': {'check': ['upx', '--version'], 'install': 'pkgmgr', 'choco_pkg': 'upx', 'apt_pkg': 'upx', 'brew_pkg': 'upx', 'type': 'tool'},
        'git': {'check': ['git', '--version'], 'install_fn': 'git', 'type': 'tool'},
        # Android
        'aapt': {'check': ['aapt', 'version'], 'install': 'pkgmgr', 'apt_pkg': 'aapt', 'type': 'build_tool'},
        'adb': {'check': ['adb', 'version'], 'install_fn': 'android_sdk', 'type': 'tool'},
    }

    BUNDLED_TOOLS = {
        'npm': 'node',
        'npx': 'node',
        'javac': 'java',
        'jpackage': 'java',
        'rustc': 'cargo',
    }

    def __init__(self):
        self.cache = {}
        self.base_installer = BaseToolInstaller()

    def is_installed(self, tool: str) -> bool:
        if tool in self.cache:
            return self.cache[tool]

        if tool in self.BUNDLED_TOOLS:
            parent = self.BUNDLED_TOOLS[tool]
            if self.is_installed(parent):
                self.cache[tool] = True
                return True

        tool_info = self.TOOLS.get(tool)
        if not tool_info:
            self.cache[tool] = False
            return False

        try:
            result = subprocess.run(
                tool_info['check'],
                capture_output=True,
                text=True,
                timeout=10,
                shell=(sys.platform == 'win32')
            )
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
            log(f"Missing tools: {', '.join(missing)}")
            for tool in missing:
                results[tool] = self._install(tool, cwd=cwd)
                for bundled, parent in self.BUNDLED_TOOLS.items():
                    if parent == tool and results[tool]:
                        self.cache[bundled] = True
                        if bundled in missing:
                            results[bundled] = True
        return results

    def _install(self, tool: str, cwd: str = None) -> bool:
        tool_info = self.TOOLS.get(tool, {})

        if tool in self.BUNDLED_TOOLS:
            parent = self.BUNDLED_TOOLS[tool]
            if self.is_installed(parent):
                self.cache[tool] = True
                return True
            log(f"{tool} is bundled with {parent}. Installing {parent}...")
            return self._install(parent, cwd=cwd)

        install_fn = tool_info.get('install_fn')
        if install_fn:
            installer_method = getattr(self.base_installer, f"install_{install_fn}", None)
            if installer_method:
                result = installer_method()
                if result:
                    self.cache[tool] = True
                    for bundled, parent in self.BUNDLED_TOOLS.items():
                        if parent == tool:
                            self.cache[bundled] = True
                return result
            warn(f"No installer implemented for '{install_fn}' (tool: {tool})")
            return False

        method = tool_info.get('install', 'manual')
        log(f"Installing {tool} via {method}...")

        try:
            if method == 'pip':
                pkg = tool_info.get('pkg', tool)
                cmd = [sys.executable, "-m", "pip", "install", "--upgrade", pkg]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                if result.returncode == 0:
                    self.cache[tool] = True
                    return True
                warn(f"pip install failed: {result.stderr[-400:] if result.stderr else 'unknown'}")
                return False

            elif method == 'npm':
                pkg = tool_info.get('pkg', tool)
                if not self.is_installed('node'):
                    if not self._install('node'):
                        return False
                cmd = ["npm", "install"]
                if tool_info.get('global', False):
                    cmd.append("-g")
                else:
                    cmd.append("--no-save")
                cmd.append(pkg)
                install_cwd = cwd if (cwd and not tool_info.get('global', False)) else None
                if install_cwd:
                    os.makedirs(install_cwd, exist_ok=True)
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=180,
                    cwd=install_cwd, shell=(sys.platform == 'win32')
                )
                if result.returncode == 0:
                    self.cache[tool] = True
                    return True
                warn(f"npm install failed: {result.stderr[-400:] if result.stderr else 'unknown'}")
                return False

            elif method == 'pkgmgr':
                if self.base_installer.install_via_pkgmgr(
                    apt_pkg=tool_info.get('apt_pkg'), brew_pkg=tool_info.get('brew_pkg'),
                    choco_pkg=tool_info.get('choco_pkg'), winget_id=tool_info.get('winget_id')
                ):
                    self.cache[tool] = True
                    return True
                return False

            elif method == 'manual':
                url = tool_info.get('url', 'official website')
                warn(f"Please install {tool} manually from: {url}")
                return False
            return False
        except Exception as e:
            warn(f"Failed to install {tool}: {e}")
            return False

    def update_project_deps(self, project_dir: str, lang: 'LangType'):
        log("Updating project dependencies...")
        if lang == LangType.PYTHON:
            self._update_python_deps(project_dir)
        elif lang in (LangType.NODE, LangType.ELECTRON):
            self._update_node_deps(project_dir)
        elif lang == LangType.RUST:
            self._update_rust_deps(project_dir)
        elif lang == LangType.GO:
            self._update_go_deps(project_dir)
        elif lang in (LangType.JAVA, LangType.KOTLIN, LangType.ANDROID):
            self._update_java_deps(project_dir)
        elif lang == LangType.CSHARP:
            self._update_csharp_deps(project_dir)
        elif lang == LangType.CPP:
            self._update_cpp_deps(project_dir)
        elif lang in (LangType.FLUTTER, LangType.DART):
            self._update_flutter_deps(project_dir)

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
            subprocess.run(["gradle", "dependencies", "--refresh-dependencies"],
                          cwd=project_dir, capture_output=True)
            success("Refreshed Gradle dependencies")

    def _update_csharp_deps(self, project_dir: str):
        if self.is_installed('dotnet'):
            subprocess.run(["dotnet", "restore", "--force-evaluate"],
                          cwd=project_dir, capture_output=True)
            success("Restored .NET dependencies")

    def _update_cpp_deps(self, project_dir: str):
        if os.path.exists(os.path.join(project_dir, "vcpkg.json")):
            vcpkg = shutil.which("vcpkg")
            if not vcpkg:
                for candidate in ["vcpkg", "vcpkg.exe"]:
                    p = os.path.join(project_dir, "vcpkg", candidate)
                    if os.path.exists(p):
                        vcpkg = p
                        break
            if vcpkg:
                subprocess.run([vcpkg, "upgrade"], cwd=project_dir, capture_output=True)
                success("Updated vcpkg dependencies")
            else:
                warn("vcpkg.json found but vcpkg not available")

    def _update_flutter_deps(self, project_dir: str):
        if os.path.exists(os.path.join(project_dir, "pubspec.yaml")):
            subprocess.run(["flutter", "pub", "upgrade"], cwd=project_dir, capture_output=True)
            success("Updated Flutter dependencies")


# ==================== PROJECT DETECTION ====================

class LangType(Enum):
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
    LUA = auto()
    LOVE2D = auto()
    RUBY = auto()
    PERL = auto()
    NIM = auto()
    ZIG = auto()
    CRYSTAL = auto()
    GODOT = auto()
    UNITY = auto()
    UNREAL = auto()
    GAMEMAKER = auto()
    RENPY = auto()
    ANDROID = auto()
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
        # FIX: extension-only patterns (e.g. ".kt", ".uproject") must match by
        # actual file extension, same as _count() does — a bare "endswith" check
        # only ever matched a file literally named ".kt", never "Foo.kt".
        if p.startswith(".") and p.count(".") == 1:
            return any(os.path.splitext(f.lower())[1] == p for f in self.files)
        return any(f.lower() == p or f.lower().endswith("/" + p) for f in self.files)

    def _has_dir(self, dirname: str) -> bool:
        """Check whether any file lives under a top-level or nested folder named dirname."""
        d = dirname.lower()
        return any(
            part == d
            for f in self.files
            for part in f.lower().split("/")[:-1]
        )

    def _has_substr(self, pattern: str) -> bool:
        """Substring search — use sparingly, only for content-based detection."""
        p = pattern.lower()
        return any(p in f.lower() for f in self.files)

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
        """Find all files matching a pattern (original case preserved)."""
        pl = pattern.lower()
        return [f for f in self.files if f.lower() == pl or f.lower().endswith("/" + pl)]

    def detect(self) -> DetectedProject:
        candidates = []

        # ========== GAME ENGINES ==========
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

        unity_score = 0
        if self._has_dir("assets") and self._has_dir("projectsettings"):
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

        unreal_score = 0
        if self._has(".uproject"):
            unreal_score += 100
        if self._has_dir("source") and (self._has(".cpp") or self._has(".h")):
            unreal_score += 30
        if self._has_dir("content") and self._has_dir("config"):
            unreal_score += 20
        if unreal_score > 0:
            candidates.append(DetectedProject(
                LangType.UNREAL, unreal_score, None, [f for f in self.files if f.endswith(".uproject")],
                game_engine="Unreal Engine", notes=["Unreal Engine project detected"]
            ))

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

        # ========== ANDROID ==========
        android_score = 0
        android_builds = []
        if self._has("AndroidManifest.xml"):
            android_score += 80
            android_builds.append("AndroidManifest.xml")
        for gradle_file in self._find_all("build.gradle") + self._find_all("build.gradle.kts"):
            try:
                with open(os.path.join(self.dir, gradle_file), 'r', encoding='utf-8') as fh:
                    content = fh.read()
                    if 'com.android.application' in content or 'com.android.tools.build' in content:
                        android_score += 50
                        android_builds.append(gradle_file)
                        break
            except Exception:
                pass
        if self._has("gradle/wrapper/gradle-wrapper.properties"):
            android_score += 15
        if self._has("settings.gradle") or self._has("settings.gradle.kts"):
            android_score += 10
        if android_score > 0:
            candidates.append(DetectedProject(
                LangType.ANDROID, android_score, None, android_builds,
                notes=["Android project detected"]
            ))

        # ========== GENERAL LANGUAGES ==========
        py_score = 0
        py_entry = None
        py_builds = []
        if self._has("requirements.txt"):
            py_score += 30; py_builds.append("requirements.txt")
        if self._has("pyproject.toml"):
            py_score += 25; py_builds.append("pyproject.toml")
        if self._has("setup.py"):
            py_score += 20; py_builds.append("setup.py")
        if self._has("Pipfile"):
            py_score += 15; py_builds.append("Pipfile")
        py_count = self._count(".py")
        if py_count > 0:
            py_score += min(py_count * 3, 25)
            py_entry = self._find("main.py", "app.py", "run.py", "gui.py",
                                  "__main__.py", "start.py", "game.py")
            if py_entry:
                py_score += 10
        if py_score > 0:
            notes = [f"{py_count} Python files"]
            if self._has_substr("pygame"):
                notes.append("PyGame detected")
            elif self._has_substr("tkinter") or self._has_substr("pyqt") or self._has_substr("pyside") or self._has_substr("kivy"):
                notes.append("GUI framework detected")
            candidates.append(DetectedProject(LangType.PYTHON, py_score, py_entry, py_builds, notes=notes))

        node_score = 0
        node_entry = None
        node_builds = []
        is_electron = False
        pkg_jsons = self._find_all("package.json")
        if pkg_jsons:
            node_score += 40
            node_builds.append("package.json")
            for pj in pkg_jsons:
                try:
                    with open(os.path.join(self.dir, pj), 'r', encoding='utf-8') as f:
                        pkg = json.load(f)
                    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                    if "electron" in deps:
                        is_electron = True; node_score += 35
                        break
                except Exception:
                    pass
        if self._has("package-lock.json"):
            node_score += 10
        if self._has("yarn.lock"):
            node_score += 10
        js_count = self._count(".js") + self._count(".ts") + self._count(".jsx") + self._count(".tsx")
        if js_count > 0:
            node_score += min(js_count, 20)
            node_entry = self._find("main.js", "index.js", "app.js", "main.ts",
                                    "index.ts", "electron.js")
        has_index_html = self._has("index.html")
        web_notes = []
        if has_index_html and not node_entry:
            node_entry = self._find("index.html")
            if not node_builds:
                node_score += 25
                node_builds.append("index.html")
            else:
                node_score += 10
            web_notes.append("Web app entry point (index.html)")
        if node_score > 0:
            lang = LangType.ELECTRON if is_electron else LangType.NODE
            candidates.append(DetectedProject(
                lang, node_score, node_entry, node_builds,
                framework="Electron" if is_electron else None,
                notes=[f"{js_count} JS/TS files"] + web_notes
            ))

        cpp_score = 0
        cpp_entry = None
        cpp_builds = []
        if self._has("CMakeLists.txt"):
            cpp_score += 40; cpp_builds.append("CMakeLists.txt")
        if self._has("Makefile"):
            cpp_score += 30; cpp_builds.append("Makefile")
        if self._has("meson.build"):
            cpp_score += 30; cpp_builds.append("meson.build")
        if self._has("configure.ac"):
            cpp_score += 20; cpp_builds.append("configure.ac")
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
        if cpp_count > 0 or c_count > 0 or (h_count > 0 and cpp_score > 0):
            cpp_entry = self._find("main.cpp", "main.c", "winmain.cpp")
            lang = LangType.CPP if cpp_count >= c_count else LangType.C
            file_note = f"{cpp_count} C++ / {c_count} C / {h_count} header files"
            if cpp_count == 0 and c_count == 0 and h_count > 0:
                file_note = f"{h_count} header files (header-only)"
            candidates.append(DetectedProject(
                lang, cpp_score, cpp_entry, cpp_builds, notes=[file_note]
            ))

        cs_score = 0
        cs_entry = None
        cs_builds = []
        for f in self.files:
            fl = f.lower()
            if fl.endswith(".csproj"):
                cs_score += 50; cs_builds.append(f)
            if fl.endswith(".sln"):
                cs_score += 30; cs_builds.append(f)
        cs_count = self._count(".cs")
        if cs_count > 0:
            cs_score += min(cs_count * 2, 20)
            cs_entry = self._find("Program.cs", "Main.cs", "Game.cs", "App.cs")
        if cs_score > 0:
            framework = None
            if any("monogame" in f.lower() for f in self.files):
                framework = "MonoGame"
            candidates.append(DetectedProject(
                LangType.CSHARP, cs_score, cs_entry, cs_builds,
                framework=framework, notes=[f"{cs_count} C# files"]
            ))

        go_score = 0; go_entry = None; go_builds = []
        if self._has("go.mod"):
            go_score += 50; go_builds.append("go.mod")
        if self._has("go.sum"):
            go_score += 10; go_builds.append("go.sum")
        go_count = self._count(".go")
        if go_count > 0:
            go_score += min(go_count * 3, 30)
            go_entry = self._find("main.go", "cmd/main.go", "game.go")
        if go_score > 0:
            candidates.append(DetectedProject(LangType.GO, go_score, go_entry, go_builds,
                                              notes=[f"{go_count} Go files"]))

        rust_score = 0; rust_entry = None; rust_builds = []
        if self._has("Cargo.toml"):
            rust_score += 50; rust_builds.append("Cargo.toml")
        if self._has("Cargo.lock"):
            rust_score += 10; rust_builds.append("Cargo.lock")
        rs_count = self._count(".rs")
        if rs_count > 0:
            rust_score += min(rs_count * 3, 30)
            rust_entry = self._find("main.rs", "lib.rs", "game.rs")
        if rust_score > 0:
            candidates.append(DetectedProject(LangType.RUST, rust_score, rust_entry, rust_builds,
                                              notes=[f"{rs_count} Rust files"]))

        java_score = 0; java_entry = None; java_builds = []
        if self._has("pom.xml"):
            java_score += 40; java_builds.append("pom.xml")
        if self._has("build.gradle"):
            java_score += 40; java_builds.append("build.gradle")
        if self._has("build.gradle.kts"):
            java_score += 40; java_builds.append("build.gradle.kts")
        java_count = self._count(".java")
        if java_count > 0:
            java_score += min(java_count * 2, 20)
            java_entry = self._find("Main.java", "App.java", "Game.java")
        if java_score > 0:
            candidates.append(DetectedProject(LangType.JAVA, java_score, java_entry, java_builds,
                                              notes=[f"{java_count} Java files"]))

        kt_score = 0
        if self._has(".kt") or self._has(".kts"):
            kt_count = self._count(".kt") + self._count(".kts")
            kt_score += min(kt_count * 5, 40)
            if self._has("build.gradle.kts"):
                kt_score += 30
            if kt_score > 0:
                candidates.append(DetectedProject(LangType.KOTLIN, kt_score, None, [],
                                                  notes=[f"{kt_count} Kotlin files"]))

        scala_score = 0
        if self._has(".scala") or self._has(".sbt"):
            scala_count = self._count(".scala")
            scala_score += min(scala_count * 5, 40)
            if self._has("build.sbt"):
                scala_score += 40
            if scala_score > 0:
                candidates.append(DetectedProject(LangType.SCALA, scala_score, None, [],
                                                  notes=[f"{scala_count} Scala files"]))

        flutter_score = 0; flutter_entry = None; flutter_builds = []
        if self._has("pubspec.yaml"):
            flutter_score += 50; flutter_builds.append("pubspec.yaml")
            if self._has("lib/main.dart"):
                flutter_score += 20; flutter_entry = "lib/main.dart"
        dart_count = self._count(".dart")
        if dart_count > 0:
            flutter_score += min(dart_count * 2, 20)
        if flutter_score > 0:
            candidates.append(DetectedProject(LangType.FLUTTER, flutter_score, flutter_entry,
                                              flutter_builds, notes=[f"{dart_count} Dart files"]))

        nim_score = 0
        if self._has(".nim") or self._has(".nims") or self._has(".nimble"):
            nim_count = self._count(".nim")
            nim_score += min(nim_count * 10, 50)
            if self._has(".nimble"):
                nim_score += 30
            if nim_score > 0:
                candidates.append(DetectedProject(LangType.NIM, nim_score,
                                                  self._find("main.nim", "game.nim"), [],
                                                  notes=[f"{nim_count} Nim files"]))

        zig_score = 0
        if self._has(".zig") or self._has("build.zig"):
            zig_count = self._count(".zig")
            zig_score += min(zig_count * 10, 50)
            if self._has("build.zig"):
                zig_score += 40
            if zig_score > 0:
                candidates.append(DetectedProject(LangType.ZIG, zig_score,
                                                  self._find("main.zig"), [],
                                                  notes=[f"{zig_count} Zig files"]))

        crystal_score = 0
        if self._has(".cr") or self._has("shard.yml"):
            cr_count = self._count(".cr")
            crystal_score += min(cr_count * 10, 50)
            if self._has("shard.yml"):
                crystal_score += 40
            if crystal_score > 0:
                candidates.append(DetectedProject(LangType.CRYSTAL, crystal_score,
                                                  self._find("main.cr", "game.cr"), [],
                                                  notes=[f"{cr_count} Crystal files"]))

        ruby_score = 0
        if self._has(".rb") or self._has("Gemfile"):
            rb_count = self._count(".rb")
            ruby_score += min(rb_count * 5, 30)
            if self._has("Gemfile"):
                ruby_score += 30
            if ruby_score > 0:
                candidates.append(DetectedProject(LangType.RUBY, ruby_score,
                                                  self._find("main.rb", "game.rb"), [],
                                                  notes=[f"{rb_count} Ruby files"]))

        perl_score = 0
        if self._has(".pl") or self._has(".pm"):
            pl_count = self._count(".pl") + self._count(".pm")
            perl_score += min(pl_count * 5, 30)
            if perl_score > 0:
                candidates.append(DetectedProject(LangType.PERL, perl_score,
                                                  self._find("main.pl"), [],
                                                  notes=[f"{pl_count} Perl files"]))

        if not candidates:
            return DetectedProject(LangType.UNKNOWN, 0, None, [],
                notes=["No recognizable project files found. Supported: Python, Node, Electron, "
                       "C/C++, C#, Go, Rust, Java, Kotlin, Scala, Flutter, Lua/Love2D, Nim, Zig, "
                       "Crystal, Ruby, Perl, Godot, Unity, Unreal, GameMaker, Ren'Py, Android"])

        best = max(candidates, key=lambda x: x.confidence)
        return best


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

    def build(self) -> str:
        raise NotImplementedError

    def _run(self, cmd: List[str], cwd: str = None, env=None, shell: bool = None) -> subprocess.CompletedProcess:
        if self.args.verbose:
            log(f"Executing: {' '.join(cmd)}")
        if shell is None:
            shell = (sys.platform == 'win32')
        return subprocess.run(
            cmd, cwd=cwd or self.project_dir,
            capture_output=not self.args.verbose,
            text=True, env=env or os.environ.copy(),
            shell=shell, timeout=600
        )

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
        """Remove old artifacts with the given extension from dist_dir top level."""
        if not os.path.exists(self.dist_dir):
            return
        for f in os.listdir(self.dist_dir):
            full = os.path.join(self.dist_dir, f)
            if os.path.isfile(full) and f.lower().endswith(ext):
                os.remove(full)

    def _find_dist_artifact(self, ext: str) -> Optional[str]:
        """Recursively find the most recently modified artifact in dist_dir,
        skipping internal staging directories."""
        candidates = []
        for root, dirs, files in os.walk(self.dist_dir):
            dirs[:] = [d for d in dirs if not d.startswith('_')]
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
        ext = exe_ext(self.target_os)
        exe = os.path.join(self.dist_dir, self.name, f"{self.name}{ext}")
        if self.args.onefile:
            exe = os.path.join(self.dist_dir, f"{self.name}{ext}")
        return self._print_result(exe) or error("Build output not found")

    def _build_nuitka(self) -> str:
        script = self._resolve_entry()
        ext = exe_ext(self.target_os)
        out = os.path.join(self.dist_dir, f"{self.name}{ext}")
        cmd = [sys.executable, "-m", "nuitka", "--standalone", "--lto=yes", "--jobs=4"]
        if not self.args.console:
            cmd.append("--windows-disable-console")
        if self.args.icon:
            cmd.append(f"--windows-icon-from-ico={os.path.abspath(self.args.icon)}")
        if self._uses_tkinter():
            cmd.append("--enable-plugin=tk-inter")
        cmd.extend([f"--output-dir={self.dist_dir}",
                    f"--output-filename={self.name}{ext}", script])
        result = self._run(cmd)
        if result.returncode != 0:
            error("Nuitka build failed")
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
            if any(d in EXCLUDED_DIRS for d in os.path.relpath(root, self.project_dir).split(os.sep)):
                continue
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
            if any(d in EXCLUDED_DIRS for d in os.path.relpath(root, self.project_dir).split(os.sep)):
                continue
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
        entry = self.args.script or self.project.entry_point
        if entry and entry.lower().endswith((".html", ".htm")):
            return self._build_web_app()
        if not entry:
            html_fallback = self._find_file("index.html")
            js_fallback = os.path.exists(os.path.join(self.project_dir, "index.js"))
            if html_fallback and not js_fallback:
                return self._build_web_app()
        return self._build_node()

    def _build_node(self) -> str:
        self.deps.ensure('pkg')
        entry = self.args.script or self.project.entry_point or "index.js"
        if not os.path.exists(os.path.join(self.project_dir, entry)):
            error(f"Entry point not found: {entry}")
        if os.path.exists(os.path.join(self.project_dir, "package.json")):
            if not os.path.exists(os.path.join(self.project_dir, "node_modules")):
                log("Installing npm dependencies...")
                self._run(["npm", "install"])
        ext = exe_ext(self.target_os)
        out = os.path.join(self.dist_dir, f"{self.name}{ext}")
        cmd = ["pkg", entry, "--output", out]
        if self.target_os == "windows" or sys.platform == "win32":
            cmd.extend(["--target", "node18-win-x64"])
        elif sys.platform == "darwin":
            cmd.extend(["--target", "node18-macos-x64"])
        else:
            cmd.extend(["--target", "node18-linux-x64"])
        if self.deps.is_installed('upx') and self.args.onefile:
            cmd.extend(["--compress", "GZip"])
        result = self._run(cmd)
        if result.returncode != 0:
            error("pkg build failed")
        return self._print_result(out) or error("pkg build failed")

    def _build_web_app(self) -> str:
        """Build a web app into a desktop EXE via a generated Electron shell."""
        pkg_path = os.path.join(self.project_dir, "package.json")
        has_pkg = os.path.exists(pkg_path)
        pkg = {}
        if has_pkg:
            try:
                with open(pkg_path, 'r', encoding='utf-8') as f:
                    pkg = json.load(f)
            except Exception:
                pkg = {}
        web_root = self.project_dir

        if has_pkg and "build" in pkg.get("scripts", {}):
            if not os.path.exists(os.path.join(self.project_dir, "node_modules")):
                log("Installing npm dependencies...")
                self._run(["npm", "install"])
            log("Running 'npm run build'...")
            result = self._run(["npm", "run", "build"])
            if result.returncode != 0:
                error("npm run build failed")
            found_output = False
            for candidate in ("dist", "build", "out", "public"):
                candidate_path = os.path.join(self.project_dir, candidate)
                if os.path.exists(os.path.join(candidate_path, "index.html")):
                    web_root = candidate_path
                    found_output = True
                    break
            if not found_output:
                warn("Build script ran but no output directory with index.html was found")
                warn("Checked: dist/, build/, out/, public/ — shipping raw source as fallback")

        stage_dir = os.path.join(self.dist_dir, "_electron_stage")
        if os.path.exists(stage_dir):
            shutil.rmtree(stage_dir)
        app_dir = os.path.join(stage_dir, "app")
        shutil.copytree(web_root, app_dir,
                        ignore=shutil.ignore_patterns("node_modules", ".git"))

        icon_line = ""
        if self.args.icon and os.path.exists(self.args.icon):
            icon_line = f", icon: {json.dumps(os.path.abspath(self.args.icon))}"

        main_js = f"""const {{ app, BrowserWindow }} = require('electron');
const path = require('path');

function createWindow() {{
  const win = new BrowserWindow({{
    width: 1280,
    height: 800{icon_line},
    webPreferences: {{ contextIsolation: true }}
  }});
  win.loadFile(path.join(__dirname, 'index.html'));
  if ({str(bool(self.args.devtools)).lower()}) {{
    win.webContents.openDevTools();
  }}
}}

app.whenReady().then(createWindow);
app.on('window-all-closed', () => {{ if (process.platform !== 'darwin') app.quit(); }});
"""
        with open(os.path.join(app_dir, "main.js"), 'w', encoding='utf-8') as f:
            f.write(main_js)

        build_config = {
            "appId": f"com.polybuild.{self.name}",
            "productName": self.name,
            "directories": {"output": self.dist_dir},
            "files": ["**/*"],
            "win": {"target": "portable" if self.args.onefile else "nsis"}
        }
        if self.args.icon and os.path.exists(self.args.icon):
            build_config["win"]["icon"] = os.path.abspath(self.args.icon)

        wrapper_pkg = {
            "name": re.sub(r'[^a-z0-9-]', '-', self.name.lower()) or "polybuild-app",
            "version": "1.0.0",
            "private": True,
            "main": "main.js",
            "build": build_config
        }
        with open(os.path.join(app_dir, "package.json"), 'w', encoding='utf-8') as f:
            json.dump(wrapper_pkg, f, indent=2)

        self._clean_dist_artifacts(".exe")

        log("Installing Electron inside the build wrapper...")
        self._run(["npm", "install", "--no-save", "electron", "electron-builder"], cwd=app_dir)

        cmd = ["npx", "electron-builder", "--win", "--x64", "--publish", "never"]
        result = self._run(cmd, cwd=app_dir)

        if result.returncode != 0:
            error("electron-builder failed for web app wrapper")

        shutil.rmtree(stage_dir, ignore_errors=True)

        exe = self._find_dist_artifact(".exe")
        if not exe:
            error("Web app build produced no EXE")
        return self._print_result(exe) or error("Output issue")

    def _build_electron(self) -> str:
        """Build an existing Electron project without mutating its package.json."""
        pkg_path = os.path.join(self.project_dir, "package.json")
        with open(pkg_path, 'r', encoding='utf-8') as f:
            pkg = json.load(f)

        has_build_config = "build" in pkg
        config_path = None

        if not has_build_config:
            win_config = {"target": "portable" if self.args.onefile else "nsis"}
            if self.args.icon and os.path.exists(self.args.icon):
                win_config["icon"] = os.path.abspath(self.args.icon)
            elif os.path.exists(os.path.join(self.project_dir, "assets", "icon.ico")):
                win_config["icon"] = "assets/icon.ico"
            config = {
                "appId": f"com.polybuild.{self.name}",
                "productName": self.name,
                "directories": {"output": self.dist_dir},
                "win": win_config
            }
            config_path = os.path.join(self.dist_dir, "electron-builder-config.json")
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2)

        if not os.path.exists(os.path.join(self.project_dir, "node_modules")):
            log("Installing npm dependencies...")
            self._run(["npm", "install"])

        self._clean_dist_artifacts(".exe")

        cmd = ["npx", "--yes", "electron-builder"]
        if self.target_os == "windows" or sys.platform == "win32":
            cmd.extend(["--win", "--x64"])
        cmd.extend(["--publish", "never"])
        if config_path:
            cmd.extend(["--config", config_path])

        result = self._run(cmd)

        if result.returncode != 0:
            error("electron-builder failed")

        exe = self._find_dist_artifact(".exe")
        if not exe:
            error("Electron build produced no EXE")
        return self._print_result(exe) or error("Output issue")


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
            ext = exe_ext(self.target_os)
            dest = os.path.join(self.dist_dir, f"{self.name}{ext}")
            shutil.copy2(exe, dest)
            return self._print_result(dest) or dest
        error("No executable found in build output")

    def _build_make(self) -> str:
        env = os.environ.copy()
        env["CC"] = "gcc"; env["CXX"] = "g++"
        env["CFLAGS"] = "-O2"; env["CXXFLAGS"] = "-O2"
        result = self._run(["make", "-j4"], env=env)
        if result.returncode != 0:
            error("Make build failed")
        exe = self._find_exe_in(self.project_dir)
        if exe:
            ext = exe_ext(self.target_os)
            dest = os.path.join(self.dist_dir, f"{self.name}{ext}")
            shutil.copy2(exe, dest)
            return self._print_result(dest) or dest
        error("No executable found")

    def _build_direct(self) -> str:
        entry = self.args.script or self.project.entry_point
        if not entry:
            error("No C/C++ entry point found")
        compiler = "g++" if entry.endswith((".cpp", ".cc", ".cxx")) else "gcc"
        ext = exe_ext(self.target_os)
        out = os.path.join(self.dist_dir, f"{self.name}{ext}")
        cmd = [compiler, "-O2", "-o", out, os.path.join(self.project_dir, entry)]
        for f in glob.glob(os.path.join(self.project_dir, "*.c*")):
            if os.path.basename(f) != os.path.basename(entry):
                cmd.append(f)
        result = self._run(cmd)
        if result.returncode != 0:
            error("Compilation failed")
        if sys.platform != "win32":
            os.chmod(out, 0o755)
        return self._print_result(out) or error("Compilation failed")

    def _find_exe_in(self, directory: str) -> Optional[str]:
        ext = exe_ext(self.target_os)
        for name_variant in [self.name, "main", "Main", "a.out"]:
            expected = os.path.join(directory, f"{name_variant}{ext}")
            if os.path.exists(expected):
                return expected
        for root, _, files in os.walk(directory):
            for f in files:
                full = os.path.join(root, f)
                if ext:
                    if f.lower().endswith(ext):
                        return full
                else:
                    if os.access(full, os.X_OK) and not f.endswith(
                        ('.o', '.obj', '.a', '.lib', '.so', '.dll', '.dylib', '.pdb', '.txt')
                    ):
                        return full
        return None


class CSharpBuilder(Builder):
    def build(self) -> str:
        self.deps.ensure('dotnet')
        csproj = None
        for f in self.project.build_files:
            if f.endswith(".csproj"):
                csproj = f; break
        if not csproj:
            csproj = self._find_file("*.csproj")
        if not csproj:
            error("No .csproj file found")
        rid = self._get_rid()
        cmd = [
            "dotnet", "publish", csproj,
            "-c", "Release", "-r", rid,
            "--self-contained", "true",
        ]
        if self.args.onefile:
            cmd.extend(["-p:PublishSingleFile=true",
                        "-p:EnableCompressionInSingleFile=true"])
        cmd.extend(["-p:PublishTrimmed=true", "-o", self.dist_dir])
        result = self._run(cmd)
        if result.returncode != 0:
            error("dotnet publish failed")
        ext = exe_ext(self.target_os)
        exe_path = None
        for f in os.listdir(self.dist_dir):
            full = os.path.join(self.dist_dir, f)
            if not os.path.isfile(full):
                continue
            if ext and f.lower().endswith(ext):
                exe_path = full; break
            if not ext and os.access(full, os.X_OK) and not f.endswith(
                ('.dll', '.so', '.dylib', '.pdb', '.json')
            ):
                exe_path = full; break
        if not exe_path:
            exe_path = os.path.join(self.dist_dir, f"{self.name}{ext}")
        return self._print_result(exe_path) or error("dotnet publish output not found")

    def _get_rid(self) -> str:
        if self.target_os == "windows":
            return "win-x64"
        if self.target_os == "android":
            return "android-arm64"
        if sys.platform == "win32":
            return "win-x64"
        if sys.platform == "darwin":
            return "osx-arm64" if platform.machine() == "arm64" else "osx-x64"
        return "linux-x64"


class GoBuilder(Builder):
    def build(self) -> str:
        self.deps.ensure('go')
        entry = self.args.script or self.project.entry_point or "."
        ext = exe_ext(self.target_os)
        out = os.path.join(self.dist_dir, f"{self.name}{ext}")
        env = os.environ.copy()
        if self.target_os == "windows":
            env["GOOS"] = "windows"; env["GOARCH"] = "amd64"
        ldflags = "-s -w"
        if not self.args.console and (self.target_os == "windows" or
                                       (self.target_os == "native" and sys.platform == "win32")):
            ldflags += " -H=windowsgui"
        if self.args.onefile:
            ldflags += " -extldflags=-static"
        cmd = ["go", "build", f"-ldflags={ldflags}", "-o", out]
        if os.path.isdir(os.path.join(self.project_dir, entry)):
            cmd.append(entry)
        else:
            cmd.append(os.path.join(self.project_dir, entry))
        result = self._run(cmd, env=env)
        if result.returncode != 0:
            error("Go build failed")
        if sys.platform != "win32" and os.path.exists(out):
            os.chmod(out, 0o755)
        return self._print_result(out) or error("Go build failed")


class RustBuilder(Builder):
    def build(self) -> str:
        self.deps.ensure('cargo', 'rustc')
        env = os.environ.copy()
        env["RUSTFLAGS"] = "-C target-feature=+crt-static"
        target = None
        if self.target_os == "windows" and sys.platform != "win32":
            target = "x86_64-pc-windows-gnu"
            try:
                check = subprocess.run(["rustup", "target", "list", "--installed"],
                                        capture_output=True, text=True, timeout=30)
                if target not in check.stdout:
                    log(f"Installing Rust target {target}...")
                    subprocess.run(["rustup", "target", "add", target], timeout=180)
            except Exception as e:
                warn(f"Could not add cross-compile target: {e}")
        elif self.target_os == "windows" and sys.platform == "win32":
            target = None  # Native build on Windows
        cmd = ["cargo", "build", "--release"]
        if target:
            cmd.extend(["--target", target])
        result = self._run(cmd, env=env)
        if result.returncode != 0:
            error("Cargo build failed")
        crate_name = self._get_crate_name()
        ext = exe_ext(self.target_os)
        exe_name = f"{crate_name}{ext}"
        if target:
            target_dir = os.path.join(self.project_dir, "target", target, "release", exe_name)
        else:
            target_dir = os.path.join(self.project_dir, "target", "release", exe_name)
        dest = os.path.join(self.dist_dir, f"{self.name}{ext}")
        if os.path.exists(target_dir):
            shutil.copy2(target_dir, dest)
            if sys.platform != "win32":
                os.chmod(dest, 0o755)
        return self._print_result(dest) or error("Rust build output not found")

    def _get_crate_name(self) -> str:
        try:
            with open(os.path.join(self.project_dir, "Cargo.toml"), 'r') as f:
                content = f.read()
            in_package = False
            for line in content.split('\n'):
                stripped = line.strip()
                if stripped.startswith('['):
                    in_package = (stripped == '[package]')
                    continue
                if in_package:
                    match = re.match(r'name\s*=\s*["\'](.+?)["\']', stripped)
                    if match:
                        return match.group(1)
        except Exception:
            pass
        return self.name


class JavaBuilder(Builder):
    def build(self) -> str:
        self.deps.ensure('javac', 'java')
        if self.target_os == "android":
            error("Use AndroidBuilder for Android targets. Run with --target-os android.")
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
        ext = exe_ext(self.target_os)
        out = os.path.join(self.dist_dir, f"{self.name}{ext}")
        cmd = [
            "jpackage", "--input", self.dist_dir,
            "--name", self.name,
            "--main-jar", os.path.basename(jar),
            "--type", "exe" if ext == ".exe" else "app-image",
            "--dest", self.dist_dir,
            "--win-console" if self.args.console else "--win-shortcut",
            "--win-menu", "--win-dir-chooser"
        ]
        if self.args.icon:
            cmd.extend(["--icon", os.path.abspath(self.args.icon)])
        result = self._run(cmd)
        if result.returncode != 0:
            error("jpackage failed")
        return self._print_result(out) or error("jpackage failed")

    def _compile_jar(self) -> str:
        java_files = glob.glob(os.path.join(self.project_dir, "**/*.java"), recursive=True)
        if not java_files:
            error("No Java files found")
        classes = os.path.join(self.dist_dir, "classes")
        os.makedirs(classes, exist_ok=True)
        result = self._run(["javac", "-d", classes] + java_files)
        if result.returncode != 0:
            error("Java compilation failed")
        main_class = self._find_main_class(java_files)
        jar = os.path.join(self.dist_dir, f"{self.name}.jar")
        manifest = os.path.join(self.dist_dir, "MANIFEST.MF")
        with open(manifest, 'w') as f:
            f.write(f"Manifest-Version: 1.0\nMain-Class: {main_class or 'Main'}\n\n")
        result = self._run(["jar", "cvfm", jar, manifest, "-C", classes, "."])
        if result.returncode != 0:
            error("JAR creation failed")
        return jar

    def _find_main_class(self, java_files: List[str]) -> Optional[str]:
        for f in java_files:
            try:
                with open(f, 'r', errors='ignore') as file:
                    content = file.read()
                if 'public static void main' in content:
                    package = None
                    for line in content.split('\n'):
                        stripped = line.strip()
                        match = re.match(r'package\s+([\w.]+)\s*;', stripped)
                        if match:
                            package = match.group(1)
                            break
                    class_name = Path(f).stem
                    if package:
                        return f"{package}.{class_name}"
                    return class_name
            except Exception:
                pass
        return None

    def _build_gradle(self) -> str:
        wrapper = os.path.join(self.project_dir,
                               "gradlew.bat" if sys.platform == "win32" else "gradlew")
        cmd = [wrapper if os.path.exists(wrapper) else "gradle", "build", "-x", "test"]
        result = self._run(cmd)
        if result.returncode != 0:
            error("Gradle build failed")
        for pattern in ["build/libs/*.jar", "build/distributions/*.exe"]:
            matches = glob.glob(os.path.join(self.project_dir, pattern), recursive=True)
            if matches:
                dest = os.path.join(self.dist_dir, os.path.basename(matches[0]))
                shutil.copy2(matches[0], dest)
                return self._print_result(dest) or dest
        error("No Gradle output found")

    def _build_maven(self) -> str:
        result = self._run(["mvn", "package", "-DskipTests"])
        if result.returncode != 0:
            error("Maven build failed")
        matches = glob.glob(os.path.join(self.project_dir, "target/*.jar"))
        if matches:
            dest = os.path.join(self.dist_dir, os.path.basename(matches[0]))
            shutil.copy2(matches[0], dest)
            return self._print_result(dest) or dest
        error("No Maven output found")

    def _build_manual(self) -> str:
        jar = self._compile_jar()
        warn("Created basic JAR. Use --onefile for native EXE via jpackage.")
        return jar


class AndroidBuilder(Builder):
    """Builds Android APK from Java/Kotlin Gradle projects."""

    def build(self) -> str:
        self.deps.ensure('java', 'javac')
        wrapper = self._find_gradle_wrapper()
        if not wrapper:
            if self.deps.is_installed('gradle'):
                wrapper = "gradle"
            else:
                error("No Gradle wrapper found. Run 'gradle wrapper' or install Gradle.")
        if sys.platform != "win32" and os.path.exists(wrapper):
            os.chmod(wrapper, 0o755)
        for task in ["assembleRelease", "assembleDebug"]:
            log(f"Running Gradle {task}...")
            cmd = [wrapper, task, "--no-daemon"]
            result = self._run(cmd)
            if result.returncode == 0:
                apk = self._find_apk()
                if apk:
                    dest = os.path.join(self.dist_dir, os.path.basename(apk))
                    shutil.copy2(apk, dest)
                    return self._print_result(dest) or dest
            else:
                if task == "assembleRelease":
                    warn("Release build failed (likely missing signing config), trying debug...")
                else:
                    error("Gradle build failed — check Android SDK / Gradle setup")
        error("No APK found in build output")

    def _find_gradle_wrapper(self) -> Optional[str]:
        wrapper_name = "gradlew.bat" if sys.platform == "win32" else "gradlew"
        wrapper = os.path.join(self.project_dir, wrapper_name)
        if os.path.exists(wrapper):
            return wrapper
        return None

    def _find_apk(self) -> Optional[str]:
        search_dirs = [
            os.path.join(self.project_dir, "app", "build", "outputs", "apk"),
            os.path.join(self.project_dir, "build", "outputs", "apk"),
        ]
        for search_dir in search_dirs:
            if os.path.exists(search_dir):
                for root, _, files in os.walk(search_dir):
                    for f in files:
                        if f.endswith(".apk"):
                            return os.path.join(root, f)
        for root, dirs, files in os.walk(self.project_dir):
            dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
            for f in files:
                if f.endswith(".apk"):
                    return os.path.join(root, f)
        return None


class FlutterBuilder(Builder):
    def build(self) -> str:
        self.deps.ensure('flutter')
        if self.target_os == "android" or self.project.lang == LangType.ANDROID:
            return self._build_apk()
        return self._build_desktop()

    def _build_apk(self) -> str:
        result = self._run(["flutter", "build", "apk", "--release"])
        if result.returncode != 0:
            error("Flutter APK build failed")
        apk = os.path.join(self.project_dir, "build", "app", "outputs",
                           "flutter-apk", "app-release.apk")
        if os.path.exists(apk):
            dest = os.path.join(self.dist_dir, f"{self.name}.apk")
            shutil.copy2(apk, dest)
            return self._print_result(dest) or dest
        for root, _, files in os.walk(os.path.join(self.project_dir, "build")):
            for f in files:
                if f.endswith(".apk"):
                    dest = os.path.join(self.dist_dir, f)
                    shutil.copy2(os.path.join(root, f), dest)
                    return self._print_result(dest) or dest
        error("No Flutter APK found")

    def _build_desktop(self) -> str:
        build_target = "windows" if (self.target_os == "windows" or sys.platform == "win32") else \
                       "macos" if sys.platform == "darwin" else "linux"
        result = self._run(["flutter", "build", build_target, "--release"])
        if result.returncode != 0:
            error(f"Flutter {build_target} build failed")
        build_dirs = [
            os.path.join(self.project_dir, "build", build_target, "x64", "runner", "Release"),
            os.path.join(self.project_dir, "build", build_target, "runner", "Release"),
            os.path.join(self.project_dir, "build", build_target, "x64", "plugins"),
        ]
        ext = exe_ext(self.target_os)
        for d in build_dirs:
            if os.path.exists(d):
                for f in os.listdir(d):
                    if f.endswith(ext if ext else ".exe") or (
                        not ext and os.access(os.path.join(d, f), os.X_OK) and
                        not f.endswith(('.dll', '.so', '.dylib', '.pdb'))
                    ):
                        dest = os.path.join(self.dist_dir, f)
                        shutil.copy2(os.path.join(d, f), dest)
                        for dll in glob.glob(os.path.join(d, "*.dll")) + glob.glob(os.path.join(d, "*.so")):
                            shutil.copy2(dll, self.dist_dir)
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
                        full = os.path.join(root, f)
                        arc = os.path.relpath(full, self.project_dir)
                        zf.write(full, arc)
        love_exe = shutil.which("love")
        if not love_exe:
            for path in [r"C:\Program Files\LOVE\love.exe",
                         r"C:\Program Files (x86)\LOVE\love.exe",
                         "/usr/bin/love", "/usr/local/bin/love",
                         "/Applications/LOVE.app/Contents/MacOS/love"]:
                if os.path.exists(path):
                    love_exe = path; break
        if not love_exe:
            error("love executable not found. Install LÖVE2D.")
        ext = exe_ext(self.target_os)
        out = os.path.join(self.dist_dir, f"{self.name}{ext}")
        with open(love_exe, 'rb') as f:
            love_data = f.read()
        with open(love_file, 'rb') as f:
            game_data = f.read()
        with open(out, 'wb') as f:
            f.write(love_data)
            f.write(game_data)
        os.remove(love_file)
        if sys.platform != "win32":
            os.chmod(out, 0o755)
        return self._print_result(out) or error("Love2D build failed")


class GodotBuilder(Builder):
    def build(self) -> str:
        self.deps.ensure('godot')
        export_preset = self._get_export_preset()
        if not export_preset:
            error("No export presets found in export_presets.cfg. "
                  "Configure export presets in Godot's Project → Export menu first.")
        ext = exe_ext(self.target_os)
        out = os.path.join(self.dist_dir, f"{self.name}{ext}")
        cmd = [
            "godot", "--headless", "--path", self.project_dir,
            "--export-release", export_preset, out
        ]
        result = self._run(cmd)
        if result.returncode != 0:
            error(f"Godot export failed for preset '{export_preset}'. "
                  "Ensure export templates are installed.")
        if sys.platform != "win32" and os.path.exists(out):
            os.chmod(out, 0o755)
        return self._print_result(out) or error("Godot export failed")

    def _get_export_preset(self) -> Optional[str]:
        cfg_path = os.path.join(self.project_dir, "export_presets.cfg")
        if not os.path.exists(cfg_path):
            return None
        try:
            with open(cfg_path, 'r', encoding='utf-8') as f:
                content = f.read()
            names = re.findall(r'name\s*=\s*"([^"]+)"', content)
            if names:
                return names[0]
        except Exception:
            pass
        return None


class NimBuilder(Builder):
    def build(self) -> str:
        self.deps.ensure('nim')
        entry = self.args.script or self.project.entry_point or "main.nim"
        ext = exe_ext(self.target_os)
        out = os.path.join(self.dist_dir, f"{self.name}{ext}")
        cmd = ["nim", "c", "-d:release", "--opt:speed", "-o:" + out]
        if not self.args.console:
            cmd.append("--app:gui")
        cmd.append(os.path.join(self.project_dir, entry))
        result = self._run(cmd)
        if result.returncode != 0:
            error("Nim compilation failed")
        if sys.platform != "win32":
            os.chmod(out, 0o755)
        return self._print_result(out) or error("Nim compilation failed")


class ZigBuilder(Builder):
    def build(self) -> str:
        self.deps.ensure('zig')
        ext = exe_ext(self.target_os)
        if os.path.exists(os.path.join(self.project_dir, "build.zig")):
            cmd = ["zig", "build", "-Doptimize=ReleaseFast"]
            if self.target_os == "windows" and sys.platform != "win32":
                cmd.append("-Dtarget=x86_64-windows-gnu")
            result = self._run(cmd)
            if result.returncode != 0:
                error("Zig build failed")
            out = os.path.join(self.project_dir, "zig-out", "bin", f"{self.name}{ext}")
            dest = os.path.join(self.dist_dir, f"{self.name}{ext}")
            if os.path.exists(out):
                shutil.copy2(out, dest)
                if sys.platform != "win32":
                    os.chmod(dest, 0o755)
            return self._print_result(dest) or error("Zig build output not found")
        else:
            entry = self.args.script or self.project.entry_point or "main.zig"
            out = os.path.join(self.dist_dir, f"{self.name}{ext}")
            cmd = ["zig", "build-exe", "-O", "ReleaseFast"]
            if self.target_os == "windows" and sys.platform != "win32":
                cmd.extend(["-target", "x86_64-windows-gnu"])
            cmd.extend(["-femit-bin=" + out, os.path.join(self.project_dir, entry)])
            result = self._run(cmd)
            if result.returncode != 0:
                error("Zig compilation failed")
            if sys.platform != "win32":
                os.chmod(out, 0o755)
            return self._print_result(out) or error("Zig compilation failed")


class CrystalBuilder(Builder):
    def build(self) -> str:
        self.deps.ensure('crystal')
        entry = self.args.script or self.project.entry_point or "main.cr"
        out = os.path.join(self.dist_dir, self.name)
        cmd = ["crystal", "build", "--release", "--no-debug", "-o", out,
               os.path.join(self.project_dir, entry)]
        result = self._run(cmd)
        if result.returncode != 0:
            error("Crystal build failed")
        exe = out + ".exe" if sys.platform == "win32" else out
        if sys.platform != "win32" and os.path.exists(exe):
            os.chmod(exe, 0o755)
        return self._print_result(exe) or error("Crystal build failed")


class RubyBuilder(Builder):
    def build(self) -> str:
        warn("Ruby to EXE requires OCRA. Attempting install...")
        self.deps.ensure('ruby')
        try:
            subprocess.run(["gem", "install", "ocra"], capture_output=True, timeout=60)
        except Exception:
            pass
        entry = self.args.script or self.project.entry_point or "main.rb"
        ext = exe_ext(self.target_os)
        out = os.path.join(self.dist_dir, f"{self.name}{ext}")
        cmd = ["ocra", "--windows", "--output", out, os.path.join(self.project_dir, entry)]
        result = self._run(cmd)
        if result.returncode != 0:
            error("OCRA build failed. Install manually: gem install ocra")
        if sys.platform != "win32" and os.path.exists(out):
            os.chmod(out, 0o755)
        return self._print_result(out) or error("OCRA build failed")


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
    LangType.ANDROID: AndroidBuilder,
}


# ==================== MAIN ====================

def main():
    parser = argparse.ArgumentParser(
        description=f"PolyBuild Pro v{VERSION} - Universal App & Game Builder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Supported Languages & Engines:
  Python, Node.js, Electron, C/C++, C#, Go, Rust, Java, Kotlin, Scala
  Flutter/Dart, Lua, LÖVE2D, Nim, Zig, Crystal, Ruby, Perl
  Godot, Unity, Unreal Engine, GameMaker, Ren'Py, Android

Target Options (--target-os):
  native    Build for host OS (default)
  windows   Cross-compile to Windows EXE
  android   Build APK (Java/Kotlin/Flutter)

Examples:
  polybuild                                # Auto-detect & build for host OS
  polybuild -p ./my-game --name Game       # Build project as Game.exe
  polybuild --onefile --icon app.ico        # Single EXE with icon
  polybuild --backend nuitka                # Use Nuitka for Python
  polybuild --target-os android             # Build APK
  polybuild --target-os windows             # Cross-compile to Windows EXE
  polybuild --update-deps                   # Update all project dependencies
  polybuild --check-tools                   # Check installed build tools
  polybuild --update                        # Update PolyBuild itself
        """
    )

    parser.add_argument("--project", "-p", help="Project directory")
    parser.add_argument("--script", "-s", help="Override entry point")
    parser.add_argument("--name", "-n", help="Output name")
    parser.add_argument("--icon", "-i", help="Path to .ico file")
    parser.add_argument("--output", "-o", default="dist", help="Output directory")
    parser.add_argument("--lang", choices=[
        "python", "node", "electron", "cpp", "c", "csharp", "go", "rust",
        "java", "kotlin", "scala", "flutter", "dart", "lua", "love2d",
        "nim", "zig", "crystal", "ruby", "perl", "godot", "unity", "unreal",
        "gamemaker", "renpy", "android"
    ], help="Force language (skip auto-detection)")

    parser.add_argument("--target-os", choices=["native", "windows", "android"],
                        default="native",
                        help="Target OS: native (host), windows (EXE), android (APK)")
    parser.add_argument("--onefile", "-f", action="store_true", help="Single executable")
    parser.add_argument("--console", "-c", action="store_true",
                        help="Keep console window (PyInstaller/Nuitka/Go/etc.)")
    parser.add_argument("--devtools", action="store_true",
                        help="Open DevTools in Electron builds (debug only)")
    parser.add_argument("--backend", choices=["auto", "pyinstaller", "nuitka"], default="auto")
    parser.add_argument("--auto-detect", action="store_true", default=True)
    parser.add_argument("--no-auto-detect", dest="auto_detect", action="store_false")
    parser.add_argument("--hidden-imports", action="append")
    parser.add_argument("--add-data", action="append")

    parser.add_argument("--update", action="store_true", help="Update PolyBuild to latest version")
    parser.add_argument("--update-deps", action="store_true",
                        help="Update project dependencies before build")
    parser.add_argument("--check-tools", action="store_true",
                        help="Check all build tools status")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--init", action="store_true",
                        help="Create polybuild.json config")

    args = parser.parse_args()

    print(f"""{Colors.CYAN}{Colors.BOLD}
    ╔═══════════════════════════════════════════════════════════════╗
    ║  POLYBUILD PRO v{VERSION:<8} - Universal Builder                  ║
    ║  Auto-detects 25+ languages | Self-updating | Auto-deps       ║
    ║  EXE + APK + Native builds for any platform                  ║
    ╚═══════════════════════════════════════════════════════════════╝{Colors.END}
    """)

    if args.update:
        SelfUpdater.check_update(force=True)
        return

    if not args.check_tools:
        SelfUpdater.check_update(force=False)

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
            "update_deps": True,
            "target_os": "native"
        }
        with open("polybuild.json", "w") as f:
            json.dump(template, f, indent=2)
        success("Created polybuild.json")
        return

    if args.check_tools:
        deps = DependencyManager()
        tools = list(deps.TOOLS.keys())
        print(f"\n{Colors.BOLD}Build Tool Status:{Colors.END}\n")
        for tool in sorted(tools):
            installed = deps.is_installed(tool)
            status = (f"{Colors.GREEN}✓ INSTALLED{Colors.END}" if installed
                      else f"{Colors.RED}✗ MISSING{Colors.END}")
            tool_type = deps.TOOLS[tool].get('type', 'tool')
            print(f"  {status:<20} {tool:<20} ({tool_type})")
        return

    project_dir = os.path.abspath(args.project or ".")
    if not os.path.isdir(project_dir):
        error(f"Directory not found: {project_dir}")

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
            "gamemaker": LangType.GAMEMAKER, "renpy": LangType.RENPY,
            "android": LangType.ANDROID
        }
        detected = DetectedProject(lang_map[args.lang], 100, args.script or None, [],
                                   notes=["Forced by user"])
        info(f"Language forced: {args.lang}")
    else:
        log(f"Scanning: {project_dir}")
        detector = ProjectDetector(project_dir)
        detected = detector.detect()

    print(f"\n{Colors.BOLD}{'─'*55}{Colors.END}")
    print(f"{Colors.BOLD}Detection Results:{Colors.END}")
    print(f"  Language:     {Colors.CYAN}{detected.lang.name}{Colors.END}")
    print(f"  Confidence:   {detected.confidence}%")
    print(f"  Entry Point:  {detected.entry_point or 'Auto-detect'}")
    print(f"  Build Files:  {', '.join(detected.build_files) or 'N/A'}")
    if detected.framework:
        print(f"  Framework:    {detected.framework}")
    if detected.game_engine:
        print(f"  Game Engine:  {Colors.MAGENTA}{detected.game_engine}{Colors.END}")
    print(f"  Target OS:    {Colors.YELLOW}{args.target_os}{Colors.END}")
    if detected.notes:
        for note in detected.notes:
            print(f"  Note:         {note}")
    print(f"{Colors.BOLD}{'─'*55}{Colors.END}\n")

    if detected.lang == LangType.UNKNOWN:
        error("Could not detect project type. Use --lang to force.")

    if detected.confidence < 30:
        warn("Low confidence detection. Verify with --lang or check project structure.")

    deps = DependencyManager()

    if args.update_deps:
        deps.update_project_deps(project_dir, detected.lang)

    if args.target_os == "android":
        if detected.lang in (LangType.FLUTTER, LangType.DART):
            builder_class = FlutterBuilder
        elif detected.lang in (LangType.JAVA, LangType.KOTLIN, LangType.ANDROID):
            builder_class = AndroidBuilder
        else:
            error(f"Android builds are not supported for {detected.lang.name}. "
                  "Supported: Java, Kotlin, Flutter/Dart, Android projects.")
    else:
        builder_class = BUILDERS.get(detected.lang)

    if not builder_class:
        error(f"No builder available for {detected.lang.name}")

    builder = builder_class(detected, args, deps)

    try:
        start_time = datetime.now()
        artifact_path = builder.build()
        elapsed = (datetime.now() - start_time).total_seconds()

        print(f"\n{Colors.GREEN}{Colors.BOLD}╔═══════════════════════════════════════════════════╗")
        print(f"║  BUILD SUCCESSFUL                                 ║")
        print(f"║  Output: {artifact_path[-45:]:<45} ║")
        print(f"║  Time:   {elapsed:.1f}s{' ' * max(0, 40 - len(f'{elapsed:.1f}s'))} ║")
        print(f"╚═══════════════════════════════════════════════════╝{Colors.END}\n")

    except KeyboardInterrupt:
        warn("\nBuild interrupted by user")
        sys.exit(1)
    except SystemExit:
        raise
    except Exception as e:
        error(f"Build failed: {str(e)}")


if __name__ == "__main__":
    main()
