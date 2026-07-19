# PolyBuild Pro v2.0

> **Universal App & Game EXE Builder** — Auto-detects 20+ programming languages and game engines, self-updates, and auto-manages dependencies.

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Supported Languages & Engines](#supported-languages--engines)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Command-Line Reference](#command-line-reference)
- [Configuration File](#configuration-file)
- [Language-Specific Guides](#language-specific-guides)
- [Game Engine Workflows](#game-engine-workflows)
- [Self-Updating](#self-updating)
- [Dependency Management](#dependency-management)
- [Troubleshooting](#troubleshooting)
- [Environment Variables](#environment-variables)
- [Contributing](#contributing)
- [License](#license)

---

## Overview

PolyBuild Pro eliminates the friction of building distributable Windows executables from source code. Drop it into any project folder and it will:

1. **Auto-detect** the programming language, framework, or game engine
2. **Check & install** missing build toolchains automatically
3. **Update** project dependencies to their latest compatible versions
4. **Compile & package** everything into a polished, windowed `.exe`

No manual configuration required for most projects.

---

## Features

| Feature | Description |
|---------|-------------|
| **🔍 Auto-Detection** | Scans project files and scores confidence for 20+ language/ecosystem types |
| **🛠️ Auto-Installation** | Missing compiler? PolyBuild installs it via `pip`, `npm`, or Chocolatey |
| **🔄 Self-Updating** | Checks for new versions on startup; one-flag self-patch (`--update`) |
| **📦 Dependency Sync** | Updates `requirements.txt`, `Cargo.toml`, `package.json`, `go.mod`, etc. before building |
| **🎮 Game Engines** | Native export support for Godot, LÖVE2D; detection for Unity, Unreal, GameMaker, Ren'Py |
| **🪟 Windowed Output** | Automatically applies `-H=windowsgui`, `--windows-disable-console`, or equivalent flags |
| **📁 One-File Mode** | Single portable `.exe` via `--onefile` (where the toolchain supports it) |
| **⚡ Smart Backends** | Python projects automatically choose between PyInstaller (compatibility) and Nuitka (speed) |

---

## Supported Languages & Engines

### General-Purpose Languages

| Language | Build Tool | One-File | Icon | Notes |
|----------|-----------|----------|------|-------|
| **Python** | PyInstaller / Nuitka | ✅ | ✅ | Auto-detects tkinter, PyGame, PyQt |
| **Node.js** | `pkg` | ✅ | ❌ | Bundles Node runtime |
| **Electron** | `electron-builder` | ✅ | ✅ | Portable or NSIS installer |
| **C** | GCC / Clang / CMake | ⚠️ | ❌ | Static linking supported |
| **C++** | CMake → Make / MSVC | ⚠️ | ❌ | vcpkg integration |
| **C#** | `dotnet publish` | ✅ | ✅ | Self-contained, trimmed |
| **Go** | `go build` | ✅ | ❌ | Native `-H=windowsgui` |
| **Rust** | `cargo build --release` | ⚠️ | ❌ | Static CRT linking |
| **Java** | `jpackage` / Maven / Gradle | ✅ | ✅ | Native image via `jpackage` |
| **Kotlin** | Gradle / Maven | ✅ | ✅ | JVM ecosystem |
| **Scala** | SBT / Gradle / Maven | ✅ | ✅ | JVM ecosystem |
| **Flutter / Dart** | `flutter build windows` | ❌ | ✅ | Includes required DLLs |
| **Lua** | LÖVE2D bundling | ✅ | ❌ | Concatenates `love.exe` + `.love` |
| **Nim** | `nim c` | ✅ | ❌ | `--app:gui` for windowed |
| **Zig** | `zig build-exe` | ✅ | ❌ | Cross-compilation ready |
| **Crystal** | `crystal build` | ✅ | ❌ | `--release --no-debug` |
| **Ruby** | OCRA gem | ✅ | ❌ | Auto-installs OCRA |
| **Perl** | Detection only | — | — | Manual build guidance |

### Game Engines

| Engine | Detection | Export Strategy |
|--------|-----------|-----------------|
| **Godot** | `project.godot`, `.tscn` | `godot --export-release "Windows Desktop"` |
| **Unity** | `Assets/`, `.unity`, `Packages/manifest.json` | Project detection + build guidance |
| **Unreal Engine** | `.uproject`, `Source/`, `Content/` | Project detection + build guidance |
| **GameMaker** | `.yyp`, `.gml` | Project detection |
| **Ren'Py** | `script.rpy`, `options.rpy` | Visual novel detection |
| **LÖVE2D** | `main.lua`, `conf.lua` | Zips game → appends to `love.exe` |

---

## Installation

### Prerequisites

- **Windows 10/11** (primary target; some features work on Linux/macOS for cross-compilation)
- **Python 3.8+** (to run PolyBuild itself)
- **Internet connection** (for auto-installation of missing tools)

### Setup

1. Download `polybuild.py` to your project or a folder in your `PATH`:
   ```powershell
   # Using curl
   curl -O https://raw.githubusercontent.com/polybuild/polybuild/main/polybuild.py

   # Or place it in a global tools folder
   mkdir C:\Tools
   move polybuild.py C:\Tools   setx PATH "%PATH%;C:\Tools"
   ```

2. (Optional) Create a shorthand alias:
   ```powershell
   # PowerShell profile
   function polybuild { python C:\Tools\polybuild.py @args }
   ```

---

## Quick Start

### 1. Basic Auto-Build

Navigate to any project folder and run:

```bash
python polybuild.py
```

PolyBuild will detect the language, install missing tools, and output `dist\ProjectName.exe`.

### 2. Build with Custom Name & Icon

```bash
python polybuild.py --name MyAwesomeApp --icon assets/icon.ico --onefile
```

### 3. Force a Specific Language

```bash
python polybuild.py --lang rust --name server
```

### 4. Update Dependencies Before Building

```bash
python polybuild.py --update-deps --onefile
```

### 5. Initialize a Config File

```bash
python polybuild.py --init
```

This creates `polybuild.json` in the current directory for reproducible builds.

---

## Command-Line Reference

### Core Arguments

| Flag | Short | Description | Default |
|------|-------|-------------|---------|
| `--project` | `-p` | Path to project directory | `.` (current) |
| `--script` | `-s` | Override entry-point file | Auto-detected |
| `--name` | `-n` | Output EXE name | Folder name |
| `--icon` | `-i` | Path to `.ico` file | None |
| `--output` | `-o` | Output directory | `dist` |
| `--lang` | — | Force language detection | Auto-detect |

### Build Options

| Flag | Short | Description |
|------|-------|-------------|
| `--onefile` | `-f` | Package everything into a single `.exe` |
| `--console` | `-c` | Keep the console window (disable `--windowed`) |
| `--backend` | — | Python backend: `auto`, `pyinstaller`, `nuitka` | `auto` |
| `--auto-detect` | — | Auto-detect hidden imports & data files | Enabled |
| `--no-auto-detect` | — | Disable auto-detection |
| `--hidden-imports` | — | Add hidden imports (repeatable) |
| `--add-data` | — | Add data files `src;dst` (repeatable) |

### Maintenance Flags

| Flag | Description |
|------|-------------|
| `--update` | Update PolyBuild itself to the latest version |
| `--update-deps` | Update project dependencies before building |
| `--check-tools` | Display install status of all known build tools |
| `--init` | Create a `polybuild.json` template |
| `--verbose` / `-v` | Show full compiler output |

---

## Configuration File

When you run `polybuild.py --init`, the following `polybuild.json` is generated:

```json
{
  "version": "2.0.0",
  "project_dir": ".",
  "name": "MyApp",
  "icon": "assets/icon.ico",
  "onefile": true,
  "console": false,
  "backend": "auto",
  "auto_detect": true,
  "update_deps": true
}
```

### Loading Config

PolyBuild automatically reads `polybuild.json` if it exists. You can also chain overrides:

```bash
# Uses polybuild.json but overrides the name
python polybuild.py --name OverrideName
```

---

## Language-Specific Guides

### Python

**Best for:** GUI apps, PyGame titles, data tools, utilities

**Backends:**
- **PyInstaller** (default): Maximum compatibility, bundles everything
- **Nuitka** (`--backend nuitka`): Compiles Python to C first — faster runtime, smaller in some cases

**Auto-Detection:**
- Hidden imports for `sklearn`, `pandas`, `numpy`, `PIL`, `matplotlib`, `cryptography`, `pygame`
- Data folders: `assets/`, `resources/`, `data/`, `templates/`, `images/`, `sounds/`, `fonts/`

```bash
python polybuild.py --backend nuitka --onefile --icon app.ico
```

### Node.js / Electron

**Best for:** Desktop apps, SPAs, cross-platform tools

**Node.js** uses `pkg` to create a single executable.
**Electron** uses `electron-builder` with portable or NSIS targets.

```bash
# Electron app
python polybuild.py --lang electron --onefile
```

### C / C++

**Best for:** High-performance games, system tools

Build priority:
1. **CMake** (if `CMakeLists.txt` exists)
2. **Make** (if `Makefile` exists)
3. **Direct GCC** (fallback)

```bash
python polybuild.py --lang cpp
```

### C# / .NET

**Best for:** Windows desktop apps, Unity-adjacent tools

Uses `dotnet publish` with:
- Self-contained runtime
- Single-file publishing (`--onefile`)
- Assembly trimming for smaller size

```bash
python polybuild.py --lang csharp --onefile --name MyWinApp
```

### Go

**Best for:** Microservices, CLI tools, lightweight servers

Automatically sets:
- `GOOS=windows`
- `GOARCH=amd64`
- `-H=windowsgui` (unless `--console`)

```bash
python polybuild.py --lang go --onefile
```

### Rust

**Best for:** Systems programming, game engines, high-performance apps

Auto-detects target triple and installs missing cross-compile targets via `rustup`.

```bash
python polybuild.py --lang rust
```

### Java

**Best for:** Enterprise apps, Android tooling, cross-platform utilities

Priority:
1. `jpackage` (JDK 14+, creates native `.exe`)
2. Gradle (`build.gradle`)
3. Maven (`pom.xml`)

```bash
python polybuild.py --lang java --onefile
```

### Flutter

**Best for:** Cross-platform mobile/desktop apps

Runs `flutter build windows` and copies the Release bundle plus required DLLs to `dist/`.

```bash
python polybuild.py --lang flutter --name MyFlutterApp
```

---

## Game Engine Workflows

### Godot

PolyBuild detects `project.godot` and runs headless export:

```bash
python polybuild.py -p ./my-godot-game --name SpaceShooter
```

**Prerequisite:** Export templates must be installed in Godot.

### LÖVE2D

For LÖVE2D games, PolyBuild:
1. Zips all `.lua`, image, audio, and font files into a `.love` archive
2. Appends the archive to a copy of `love.exe`
3. Produces a single runnable `.exe`

```bash
python polybuild.py -p ./love-game --name Platformer
```

### Unity / Unreal / GameMaker / Ren'Py

These engines are **detected** and their project structure identified. Because their build pipelines require editor interaction or specific SDK configurations, PolyBuild will:

- Confirm the engine type
- List detected build files
- Provide guidance on the correct editor-based export steps

For automated CI/CD with these engines, consider their dedicated CLI tools (e.g., `Unity -batchmode`, `Unreal Build Tool`).

---

## Self-Updating

PolyBuild checks for updates silently on every run (5-second timeout, no network = no problem).

### Manual Update

```bash
python polybuild.py --update
```

**Process:**
1. Fetches remote version manifest
2. Compares with local `VERSION`
3. Downloads new script if newer version exists
4. Verifies SHA-256 hash (if provided)
5. Backs up current script to `.backup`
6. Replaces in-place

### Disable Checks

If you want to skip the update check, use `--check-tools` or work offline.

---

## Dependency Management

### What Gets Updated

| Ecosystem | Files Scanned | Update Command |
|-----------|--------------|----------------|
| Python | `requirements.txt`, `Pipfile` | `pip install -U -r requirements.txt`, `pipenv update` |
| Node.js | `package.json`, `yarn.lock` | `npm update` / `yarn upgrade` |
| Rust | `Cargo.toml` | `cargo update` |
| Go | `go.mod` | `go get -u ./...`, `go mod tidy` |
| Java | `pom.xml`, `build.gradle` | `mvn versions:use-latest-versions`, `gradle --refresh-dependencies` |
| C# | `.csproj` | `dotnet restore --force-evaluate` |
| C++ | `vcpkg.json` | `vcpkg upgrade` |

### Usage

```bash
# Update deps AND build
python polybuild.py --update-deps

# Config-only: always update before building
# In polybuild.json:
#   "update_deps": true
```

---

## Troubleshooting

### "No recognizable project files found"

- Verify you are in the project root (where `main.py`, `Cargo.toml`, `package.json`, etc. lives)
- Use `--lang` to force detection: `python polybuild.py --lang python`

### "Tool X not found"

PolyBuild attempts auto-installation via:
- **pip** (Python packages like PyInstaller, Nuitka, meson)
- **npm** (Node packages like `pkg`, `electron-builder`)
- **Chocolatey** (System tools like CMake, Go, Rust, Godot)

If auto-install fails:
1. Install Chocolatey: https://chocolatey.org/install
2. Or install the tool manually and ensure it's on `PATH`

### PyInstaller builds are too large

- Use `--backend nuitka` for Python (smaller in some cases)
- Use `--onefile` with UPX compression (if `upx` is installed)
- Exclude unnecessary modules with `--exclude-module`

### "CMake configuration failed"

- Ensure Visual Studio Build Tools or MinGW is installed
- On Windows, PolyBuild defaults to `Visual Studio 17 2022` generator
- Override by running CMake manually with `-G` first

### Godot export fails

- Install export templates in Godot: **Editor → Manage Export Templates**
- Ensure the preset name matches `"Windows Desktop"`

### LÖVE2D: "love.exe not found"

- Install LÖVE2D: https://love2d.org/
- Ensure `love.exe` is on `PATH` or installed to `C:\Program Files\LOVE\`

---

## Environment Variables

PolyBuild respects and sets the following variables during builds:

| Variable | Used By | Purpose |
|----------|---------|---------|
| `GOOS` | Go | Force Windows target |
| `GOARCH` | Go | Force amd64 architecture |
| `RUSTFLAGS` | Rust | Static CRT linking (`-C target-feature=+crt-static`) |
| `CC` / `CXX` | C/C++ | Compiler selection |
| `CFLAGS` / `CXXFLAGS` | C/C++ | Optimization flags (`-O2`) |

---

## Contributing

Contributions are welcome! Priority areas:

- Additional language support (Haskell, OCaml, etc.)
- Improved detection heuristics for edge-case project structures
- CI/CD integration examples (GitHub Actions, Azure DevOps)
- Better Unity / Unreal headless build automation

Please open an issue before major refactors.

---

## License

MIT License — free for personal and commercial use.

---

<p align="center">
  <sub>Built with ❤️ for developers who just want their code to run everywhere.</sub>
</p>
