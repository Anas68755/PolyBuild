# PolyBuild Pro v2.3.3

> **Universal App & Game Builder** — Auto-detects 20+ programming languages and game engines, installs missing build tools, and packages your project into a distributable binary.

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Supported Languages & Engines](#supported-languages--engines)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Command-Line Reference](#command-line-reference)
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

PolyBuild Pro eliminates the friction of building distributable executables from source code. Drop it into any project folder and it will:

1. **Auto-detect** the programming language, framework, or game engine
2. **Check & install** missing build toolchains automatically
3. **Compile & package** everything into a ready-to-run binary

By default it builds a **native binary for whatever OS you run it on** — Linux produces a Linux binary, macOS produces a macOS binary, Windows produces a `.exe`. Pass `--target-os windows` to cross-compile a Windows `.exe` from Linux/macOS, or `--target-os android` to package an Android APK.

No manual configuration is required for most projects — just run it in your project root.

---

## Features

| Feature | Description |
|---------|-------------|
| **🔍 Auto-Detection** | Scans project files and scores confidence for 20+ language/ecosystem types |
| **🛠️ Auto-Installation** | Missing compiler? PolyBuild installs it via `pip`, `npm`, Chocolatey/winget (Windows), Homebrew (macOS), or apt-get (Linux) |
| **🌐 Web App Bundling** | Plain `index.html` sites and bundler-based apps (Vite/CRA/webpack) are auto-wrapped in a generated Electron shell and compiled |
| **🎯 Multi-Target Output** | Native binary (default), Windows `.exe` cross-compile, or Android `.apk` — controlled by `--target-os` |
| **🔄 Self-Updating** | One-flag self-patch (`--update`) once update URLs are configured |
| **📦 Dependency Sync** | Updates `requirements.txt`, `Cargo.toml`, `package.json`, `go.mod`, etc. before building |
| **🎮 Game Engines** | Native export support for Godot and LÖVE2D; project detection for Unity and Unreal |
| **🪟 Windowed Output** | Applies `-H=windowsgui`, `--windows-disable-console`, or equivalent flags when targeting Windows |
| **📁 One-File Mode** | Single portable executable via `--onefile`, where the toolchain supports it |
| **⚡ Smart Backends** | Python projects automatically choose between PyInstaller (compatibility) and Nuitka (speed) |

---

## Supported Languages & Engines

### General-Purpose Languages

| Language | Build Tool | One-File | Notes |
|----------|-----------|----------|-------|
| **Python** | PyInstaller / Nuitka | ✅ | Icon support via `--icon` |
| **Node.js (CLI script)** | `pkg` | ✅ | Bundles the Node runtime; needs a `.js`/`.ts` entry point |
| **Web App (`index.html`)** | Auto-generated Electron wrapper → `electron-builder` | ✅ | Runs `npm run build` first if a build script exists |
| **Electron** | `electron-builder` | ✅ | Portable or NSIS installer; supports `--devtools` for debugging |
| **C / C++** | CMake → Make → direct GCC/Clang | ⚠️ | Tries each build system in that priority order |
| **C#** | `dotnet publish` | ✅ | Self-contained runtime; single-file with `--onefile` |
| **Go** | `go build` | ✅ | Sets `-H=windowsgui` automatically when targeting Windows |
| **Rust** | `cargo build --release` | ⚠️ | Cross-compiling requires the Rust target already installed |
| **Java** | `jpackage` / Gradle / Maven | ✅ | `jpackage` used only with `--onefile`; falls back to a plain JAR otherwise |
| **Kotlin** | Gradle (JVM toolchain) | ✅ | Auto-detected from `.kt`/`.kts` files |
| **Scala** | SBT / Gradle / Maven | ✅ | Auto-detected from `.scala` files |
| **Flutter / Dart** | `flutter build <platform>` or `flutter build apk` | ❌ | Desktop or Android output depending on `--target-os` |
| **Lua** | LÖVE2D bundling | ✅ | Concatenates the `love` executable with a zipped `.love` archive |
| **Nim** | `nim c` | ✅ | `--app:gui` applied for windowed output |
| **Zig** | `zig build` / `zig build-exe` | ✅ | Uses `-Doptimize=ReleaseFast` |
| **Crystal** | `crystal build` | ✅ | `--release --no-debug` |
| **Ruby** | OCRA gem | ✅ | Auto-installs OCRA |
| **Android (Java/Kotlin)** | Gradle `assembleRelease` (falls back to `assembleDebug`) | — | Requires `--target-os android` |

### Game Engines

| Engine | Detection | Export Strategy |
|--------|-----------|-----------------|
| **Godot** | `project.godot` | Headless export of the first preset found in `export_presets.cfg` |
| **Unity** | `Assets/` + `ProjectSettings/` directories | Project detection |
| **Unreal Engine** | `.uproject` | Project detection |
| **LÖVE2D** | `main.lua` | Zips the game and appends it to the `love` executable |

---

## Installation

### Prerequisites

- **Python 3.8+** (to run PolyBuild itself)
- **Internet connection** (for auto-installation of missing tools)
- A package manager PolyBuild can use to auto-install missing toolchains:
  - Windows: **winget** or **Chocolatey**
  - macOS: **Homebrew**
  - Linux: **apt-get** (Debian/Ubuntu-based distros)

  On other platforms/distros, PolyBuild will tell you what to install manually and where to get it.

- To cross-compile to Windows from Linux/macOS (`--target-os windows`), the relevant cross toolchain must be present for your language (e.g. `mingw-w64` for Go/C/C++, or the `x86_64-pc-windows-gnu` Rust target added via `rustup target add`).
- To build Android APKs (`--target-os android`), a Gradle wrapper (`gradlew`) should exist in the project, or Gradle must be installed and on `PATH`.

### Setup

1. Download `polybuild.py` to your project or a folder on your `PATH`:
   ```bash
   curl -O https://raw.githubusercontent.com/polybuild/polybuild/main/polybuild.py
   ```

2. (Optional) Create a shorthand alias, e.g. in your shell profile:
   ```bash
   alias polybuild="python3 /path/to/polybuild.py"
   ```

---

## Quick Start

### 1. Basic Auto-Build (native output)

```bash
python polybuild.py
```

PolyBuild detects the language, installs missing tools, and outputs `dist/ProjectName` (or `dist/ProjectName.exe` on Windows).

### 2. Build with a Custom Name & Icon

```bash
python polybuild.py --name MyAwesomeApp --icon assets/icon.ico --onefile
```

### 3. Force a Specific Language

```bash
python polybuild.py --lang rust --name server
```

### 4. Cross-Compile to Windows from Linux/macOS

```bash
python polybuild.py --target-os windows --name MyApp
```

### 5. Build an Android APK

```bash
python polybuild.py --target-os android
```

### 6. Update Project Dependencies Before Building

```bash
python polybuild.py --update-deps --onefile
```

---

## Command-Line Reference

### Core Arguments

| Flag | Short | Description | Default |
|------|-------|-------------|---------|
| `--project` | `-p` | Path to project directory | `.` (current) |
| `--script` | `-s` | Override entry-point file | Auto-detected |
| `--name` | `-n` | Output binary name | Folder name |
| `--icon` | `-i` | Path to `.ico` file | None |
| `--output` | `-o` | Output directory | `dist` |
| `--lang` | — | Force language detection | Auto-detect |
| `--target-os` | — | `native`, `windows`, or `android` | `native` |

### Build Options

| Flag | Short | Description |
|------|-------|-------------|
| `--onefile` | `-f` | Package everything into a single executable, where supported |
| `--console` | `-c` | Keep the console window (disable windowed mode) |
| `--devtools` | — | Open DevTools automatically in generated Electron web-app wrappers |
| `--backend` | — | Python backend: `auto` (default), `pyinstaller`, or `nuitka` |

### Maintenance Flags

| Flag | Description |
|------|-------------|
| `--update` | Update PolyBuild itself to the latest version |
| `--update-deps` | Update project dependencies before building |
| `--check-tools` | Print install status of every build tool PolyBuild knows about |
| `--verbose` / `-v` | Show full compiler/tool output instead of suppressing it |

---

## Language-Specific Guides

### Python

**Best for:** GUI apps, PyGame titles, data tools, utilities

**Backends:**
- **PyInstaller** (default): maximum compatibility, bundles everything
- **Nuitka** (`--backend nuitka`, also chosen automatically when it's installed and `--onefile` isn't set): compiles Python to C first, for faster startup

```bash
python polybuild.py --backend nuitka --onefile --icon app.ico
```

### Node.js / Electron / Web Apps

**Best for:** Desktop apps, SPAs, cross-platform tools, plain HTML/CSS/JS sites

PolyBuild picks one of three paths depending on what it finds:

- **Node.js CLI script** (entry point is a `.js`/`.ts` file) → bundled with `pkg`.
- **Web app** (entry point is `index.html`) → if a `build` script exists in `package.json`, PolyBuild runs `npm run build` and picks up the compiled output from `dist/`, `build/`, `out/`, or `public/`; either way the static site is staged inside an auto-generated Electron shell and packaged with `electron-builder`.
- **Electron app** (project already declares `electron` as a dependency) → built directly with `electron-builder`, using your existing `package.json` `build` config where present.

```bash
# Node.js CLI script
python polybuild.py --lang node

# Plain index.html site or a Vite/CRA/webpack app
python polybuild.py --name MySite --icon assets/icon.ico

# Already-configured Electron app, with DevTools open for debugging
python polybuild.py --lang electron --onefile --devtools
```

### C / C++

**Best for:** High-performance games, system tools

Build priority:
1. **CMake** (if `CMakeLists.txt` exists)
2. **Make** (if `Makefile` exists)
3. **Direct GCC/Clang** (fallback for a flat single-directory project)

```bash
python polybuild.py --lang cpp
```

### C# / .NET

**Best for:** Windows desktop apps, cross-platform CLI tools

Uses `dotnet publish` with a self-contained runtime, and single-file publishing when `--onefile` is passed. The runtime identifier is chosen automatically based on your OS and `--target-os`.

```bash
python polybuild.py --lang csharp --onefile --name MyApp
```

### Go

**Best for:** Microservices, CLI tools, lightweight servers

Automatically sets `GOOS=windows`, `GOARCH=amd64`, and `-H=windowsgui` when targeting Windows (unless `--console` is passed).

```bash
python polybuild.py --lang go --onefile --target-os windows
```

### Rust

**Best for:** Systems programming, game engines, high-performance apps

```bash
python polybuild.py --lang rust
```

Cross-compiling to Windows from Linux/macOS requires the `x86_64-pc-windows-gnu` target:

```bash
rustup target add x86_64-pc-windows-gnu
```

### Java / Kotlin / Scala

**Best for:** Enterprise apps, JVM-ecosystem tools

Build priority: `jpackage` (only with `--onefile`) → Gradle (`build.gradle`) → Maven (`pom.xml`) → plain `javac` + `jar` fallback.

```bash
python polybuild.py --lang kotlin --onefile
```

### Flutter

**Best for:** Cross-platform mobile/desktop apps

```bash
# Desktop build (native OS)
python polybuild.py --lang flutter --name MyFlutterApp

# Android APK
python polybuild.py --lang flutter --target-os android
```

---

## Game Engine Workflows

### Godot

PolyBuild detects `project.godot` and runs a headless export using the first preset found in `export_presets.cfg`:

```bash
python polybuild.py -p ./my-godot-game --name SpaceShooter
```

**Prerequisites:**
- Export templates must be installed in Godot (**Editor → Manage Export Templates**).
- At least one export preset must be configured (**Project → Export**). If you have multiple presets, make sure the one you want built is listed first.

### LÖVE2D

For LÖVE2D games, PolyBuild:
1. Zips all `.lua`, image, audio, and font files into a `.love` archive
2. Appends the archive to a copy of the `love` executable
3. Produces a single runnable executable

```bash
python polybuild.py -p ./love-game --name Platformer
```

### Unity / Unreal

These engines are **detected** and their project structure identified, but their build pipelines require editor interaction or specific SDK configurations that PolyBuild doesn't automate. For CI/CD with these engines, use their own CLI tools (`Unity -batchmode`, Unreal Build Tool).

---

## Self-Updating

Self-update needs two environment variables pointing at where your update manifest and script live:

```bash
export POLYBUILD_UPDATE_URL="https://example.com/polybuild.py"
export POLYBUILD_VERSION_URL="https://example.com/version.json"
```

### Manual Update

```bash
python polybuild.py --update
```

**Process:**
1. Fetches the remote version manifest
2. Compares it with the local version
3. Downloads the new script if a newer version is available
4. Verifies the SHA-256 hash, if the manifest provides one
5. Backs up the current script to `polybuild.py.backup`
6. Writes the new script in place, then verifies it byte-compiles cleanly — automatically restoring the backup if it doesn't

---

## Dependency Management

### What Gets Updated (via `--update-deps`)

| Ecosystem | Files Scanned | Update Command |
|-----------|--------------|----------------|
| Python | `requirements.txt`, `Pipfile` | `pip install -U -r requirements.txt`, `pipenv update` |
| Node.js | `package.json`, `yarn.lock` | `npm update` / `yarn upgrade` |
| Rust | `Cargo.toml` | `cargo update` |
| Go | `go.mod` | `go get -u ./...`, `go mod tidy` |
| Java/Kotlin/Scala | `pom.xml`, `build.gradle` | `mvn versions:use-latest-versions`, `gradle --refresh-dependencies` |
| C# | `.csproj` | `dotnet restore --force-evaluate` |
| C++ | `vcpkg.json` | `vcpkg upgrade` |
| Flutter | `pubspec.yaml` | `flutter pub upgrade` |

```bash
python polybuild.py --update-deps
```

---

## Troubleshooting

### "No recognizable project files found" / "Could not detect project type"

- Verify you are in the project root (where `main.py`, `Cargo.toml`, `package.json`, etc. lives)
- Force detection with `--lang`, e.g. `python polybuild.py --lang python`

### "Tool X not found"

PolyBuild attempts auto-installation via:
- **pip** (Python packages like PyInstaller, Nuitka)
- **npm** (Node packages like `pkg`, `electron`, `electron-builder`)
- **winget / Chocolatey** on Windows
- **Homebrew** on macOS
- **apt-get** on Linux (Debian/Ubuntu-based)

Run `python polybuild.py --check-tools` to see the install status of every tool PolyBuild knows about. If auto-install fails, install the tool manually and make sure it's on `PATH`.

### PyInstaller builds are too large

- Try `--backend nuitka`
- Use `--onefile` with UPX compression, if `upx` is installed

### "CMake configuration failed"

- Ensure Visual Studio Build Tools or MinGW is installed
- On Windows, PolyBuild defaults to the `Visual Studio 17 2022` generator

### Godot export fails

- Install export templates: **Editor → Manage Export Templates**
- Make sure `export_presets.cfg` has at least one preset defined

### "love executable not found"

- Install LÖVE2D: https://love2d.org/
- Ensure the `love` executable is on `PATH`

---

## Environment Variables

| Variable | Used By | Purpose |
|----------|---------|---------|
| `POLYBUILD_UPDATE_URL` | Self-updater | URL to download the new `polybuild.py` from |
| `POLYBUILD_VERSION_URL` | Self-updater | URL to a JSON version manifest |
| `GOOS` | Go | Set automatically when targeting Windows |
| `GOARCH` | Go | Set automatically when targeting Windows |
| `CC` / `CXX` | C/C++ (Makefile builds) | Compiler selection |
| `CFLAGS` / `CXXFLAGS` | C/C++ (Makefile builds) | Optimization flags (`-O2`) |

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
