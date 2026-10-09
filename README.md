# PolyBuild Pro v3.1

PolyBuild Pro is a single-file, cross-platform project packaging tool that turns software projects into native executables, installers, or runnable binaries for the target platform.

It is designed for developers who want one universal build workflow for multiple languages without manually managing dozens of vendor-specific commands. PolyBuild automatically detects the project type, chooses a suitable build backend, installs missing tools when needed, and produces a build artifact with clear output, validation, and rollback safety.

- Single file: `polybuild.py`
- No mandatory pip dependencies
- Runs on Windows, Linux, and macOS
- Requires Python 3.9+
- Works as a standalone CLI or as a subprocess-driven module from another app such as a GUI toolbox

---

## Why PolyBuild?

Most build tools are language-specific, platform-specific, or require a lot of setup before they can produce a usable artifact. PolyBuild unifies that process behind one interface.

Instead of learning separate commands for Python, Node, Go, Rust, Java, .NET, C/C++, Flutter, and more, you can point PolyBuild at a project and let it:

- detect the language and project structure
- choose a matching build strategy
- install missing dependencies when allowed
- handle cross-compilation rules safely
- produce a single executable when possible
- validate output before reporting success

---

## Main Features

- Auto-detection of project type using build files, source files, and directory patterns
- Support for 25+ languages and environments through a unified interface
- Missing tool detection and installation via apt, brew, winget, choco, pip, gem, and cpan when appropriate
- Interactive terminal wizard with smart defaults and cancel support
- Smart CI mode with non-interactive behavior and safe defaults
- Self-update support with checksum validation and rollback protection
- Timeout-aware subprocess execution for every build and tool check
- Clear failure explanation instead of misleading binaries
- Honest cross-compilation behavior with platform-specific checks

---

## Requirements

- Python 3.9 or newer
- Platform build tools are detected and installed only when required
- Optional: a GUI app or toolbox can drive PolyBuild through `subprocess`

---

## Installation

PolyBuild is distributed as a single script with no mandatory installation step.

```bash
python polybuild.py --help
```

On Linux/macOS, you can also make it executable:

```bash
chmod +x polybuild.py
./polybuild.py --help
```

---

## Quick Start

```bash
# Interactive mode
python polybuild.py

# Auto-detect the project and build a single-file executable
python polybuild.py -p ./myapp -f

# Force language and target another OS
python polybuild.py -p . --lang go --target-os windows

# Check all required tools
python polybuild.py --check-tools

# CI mode: no questions, assume yes
python polybuild.py -p . -y --quick
```

---

## Supported Languages

PolyBuild supports a broad set of major languages and frameworks.

### Interpreted / Scripted

- Python
- Node.js
- Ruby
- Perl
- Lua / LÖVE

### Native compiled

- C / C++
- Rust
- Go
- Nim
- Zig
- Crystal

### Frameworks and app stacks

- .NET / C# / F# / VB
- Java
- Kotlin
- Scala
- Flutter
- Dart
- Electron
- Android
- Godot

### Guidance mode for engines that do not build directly

- Unity
- Unreal Engine
- GameMaker
- Ren'Py

These are detected and resolved through clear instructions rather than fake build output.

---

## Automatic Detection System

PolyBuild uses a confidence-based detection model that evaluates:

- build files such as `Cargo.toml`, `go.mod`, `.csproj`, `pom.xml`, `pubspec.yaml`, `package.json`
- source files and project structure
- directories that strongly indicate a framework or engine
- project metadata and content patterns

Examples:

- `package.json` with `electron` strongly suggests Electron rather than plain Node
- `pubspec.yaml` with `flutter:` suggests Flutter, otherwise Dart
- Gradle projects with `.kt` files point toward Kotlin
- `.scala` files indicate Scala
- CMake and Makefiles help prioritize C vs C++

Excluded directories such as `.git`, `node_modules`, `venv`, `dist`, `build`, and `target` are ignored during scanning.

---

## Command-Line Reference

### Basic options

| Option | Description |
|---|---|
| `--project, -p DIR` | Project directory |
| `--script, -s FILE` | Entry point (optional) |
| `--name, -n NAME` | Output artifact name |
| `--icon, -i FILE` | `.ico`, `.png`, or `.icns` file |
| `--output, -o DIR` | Output directory |
| `--lang LANG` | Force a language |
| `--target-os OS` | `native`, `windows`, `linux`, `macos`, `android` |

### Build control

| Option | Description |
|---|---|
| `--onefile, -f` | Build a single executable file |
| `--console, -c` / `--no-console` | Show or hide the console window |
| `--backend` | `auto`, `pyinstaller`, `nuitka` |

### Advanced options

| Option | Description |
|---|---|
| `--hidden-imports MOD` | Hidden imports (repeatable) |
| `--add-data SRC=DEST` | Data files to bundle (repeatable) |

### Management and workflow

| Option | Description |
|---|---|
| `--update` | Update PolyBuild itself |
| `--update-deps` | Update project dependencies before build |
| `--check-tools` | Show status for the required build tools |
| `--verbose, -v` | Verbose command output |
| `--interactive, -I` | Force the interactive wizard |
| `--quick` | Skip questions and use defaults |
| `--yes, -y` | Confirm all prompts automatically |
| `--help, -h` | Show help |
| `--version` | Show version |

---

## Dependency Management

PolyBuild checks every required tool before building. If a tool is missing, it can install it automatically when possible.

Supported package managers include:

- Linux: `apt-get`
- macOS: `brew`
- Windows: `winget` and `choco`
- Language-level fallbacks: `pip`, `gem`, `cpan`

This process is designed to be explicit and safe:

- installation happens only with approval in interactive mode or with `-y`
- in CI without a TTY, PolyBuild prints the required install command instead of guessing
- if installation fails, the user sees a clear manual command to run

---

## Interactive Wizard

When `--project` is not supplied and the process is attached to a terminal, PolyBuild launches an interactive wizard.

Features:

- arrow-key menus in TTY mode
- numbered menus in CI or piped environments
- smart defaults for each field
- quick cancel with Esc or Ctrl+C
- full summary before final confirmation
- automatic detection preview before build starts

---

## Self-Update

PolyBuild includes a safe self-update mechanism.

It checks for a newer version at startup, and `--update` performs the following:

1. downloads the new script
2. validates SHA-256
3. verifies syntax with `ast.parse()`
4. verifies compileability with `py_compile`
5. replaces the current file atomically
6. rolls back automatically if validation fails

This makes the update path resilient to partial downloads or corrupted replacement files.

---

## Reliability and Security

PolyBuild is built with safety in mind:

- every subprocess call includes a timeout
- external output is read with safe encoding handling
- failure is treated as failure, not success
- cross-compilation checks are explicit and honest
- temporary files are cleaned up automatically
- output is prevented from being written outside the target project or output directory
- ANSI colors are handled safely and degrade gracefully in unsupported terminals

Examples of safety rules:

- Rust, C/C++, Nim, and similar builds check for required toolchains such as mingw-w64 before claiming a Windows target is valid
- Crystal refuses Windows cross-compiles
- Ruby/ocra refuses execution outside Windows
- Flutter desktop builds are refused on mismatched host OSes

---

## Toolbox Integration

PolyBuild is designed to be launched directly by another application through `subprocess`.

```python
import subprocess

proc = subprocess.run(
    ["python", "polybuild.py", "-p", project_dir, "-n", app_name, "-y", "-v"],
    capture_output=True,
    text=True,
    timeout=1200,
)

success = proc.returncode == 0
print(proc.stdout)
print(proc.stderr)
```

This makes it useful for GUI tools, project managers, and desktop utilities that want a single packaging backend behind a nicer UI.

---

## CI Usage

```bash
# GitHub Actions / generic CI
python polybuild.py -p ./src -n myapp -f -y --quick

# Check build tools before running builds
python polybuild.py --check-tools

# Disable update check for faster startup
POLYBUILD_NO_UPDATE_CHECK=1 python polybuild.py -p . -y
```

CI notes:

- without `-y`, a non-TTY environment will not install missing tools automatically
- it prints manual installation commands instead of silently failing
- guidance-only engines finish with clean messages instead of fake build output

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Missing tools cannot be skipped | Run the suggested install command or allow auto-install in a TTY |
| Entry point not found | Use `-s main.py` or `-s index.js` |
| Could not determine language | Use `--lang python` or another explicit value |
| Cross-compile needs mingw | Install the required toolchain first |
| PyInstaller fails on dynamic imports | Add `--hidden-imports` |
| The app does nothing after build | Remove `--no-console` or rebuild with standard defaults |
| Update fails due to hash mismatch | Do not replace the file; the update is safely rejected |
| Old terminal shows strange symbols | PolyBuild falls back to ASCII-safe output automatically |

---

## Project Structure

PolyBuild is intentionally kept as a single-file project.

- `polybuild.py` — all logic in one script
- build detection and dispatch
- tool checks and installers
- UI flow and argument parsing
- project-specific builders
- self-update and safety checks

---

## License

MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the “Software”), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
