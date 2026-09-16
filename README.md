# PolyBuild Pro v3.1

**A comprehensive command-line tool that converts any programming project into a native executable (EXE / APK / Binary) for the target system** — with automatic language detection, an interactive terminal UI, support for more than 25 programming languages, and automatic dependency installation.

- Single file: `polybuild.py` — runs directly.
- No mandatory pip dependencies (Standard Library only).
- Works on **Windows / Linux / macOS** with **Python 3.9+**.
- Can be driven as an internal module by another tool (a GUI Toolbox) via `subprocess`.

---

## Features

| Feature | Details |
|---|---|
| Auto-detection | Confidence-score system weighing build files, sources, and directories |
| 25+ languages/environments | A dedicated builder per language behind one unified interface |
| Dependency installation | Detects missing tools and installs them via apt / brew / winget / choco / pip / gem / cpan |
| Interactive wizard | Arrow-key (↑/↓) menus on TTY, numbered menus on CI, with smart defaults |
| Safe self-update | Triple validation (SHA-256 + ast + py_compile) with automatic rollback |
| Reliability | Every subprocess has an explicit timeout; no misleading binary on cross-compile failure |

---

## Requirements

- Python **3.9** or newer (no mandatory external dependencies).
- Per-language build tools are detected and installed only when needed:
  - Example: building C requires `gcc`, building Rust requires `cargo`, and so on.
- Optional: a Toolbox GUI app to drive PolyBuild through `subprocess`.

## Installation

No installation — it is a single file:

```bash
# Download polybuild.py and run it directly
python polybuild.py --help
```

Make it executable on Linux/macOS:

```bash
chmod +x polybuild.py
./polybuild.py --help
```

---

## Quick Start

```bash
# 1) Interactive wizard (starts automatically when --project is not given, on a terminal)
python polybuild.py

# 2) Auto-detect + one-file build
python polybuild.py -p ./myapp -f

# 3) Force the language and target another OS
python polybuild.py -p . --lang go --target-os windows

# 4) Check the status of all tools
python polybuild.py --check-tools

# 5) CI mode: no questions, assume "yes"
python polybuild.py -p . -y --quick
```

Example of a successful build output:

```
╔════════════════════════════════════════════════════════════════════╗
║PolyBuild Pro v3.1 — Build completed successfully                   ║
╠════════════════════════════════════════════════════════════════════╣
║ File                   : py_app                                    ║
║ Path                   : /path/to/dist/py_app                      ║
║ Size                   : 11.3 MB                                   ║
║ Time                   : 13.8s                                     ║
║ Language               : Python                                    ║
║ Tool                   : PyInstaller                               ║
╚════════════════════════════════════════════════════════════════════╝
```

---

## Command-Line Reference

### Basic

| Option | Description |
|---|---|
| `--project, -p DIR` | Project directory |
| `--script, -s FILE` | Entry point (optional) |
| `--name, -n NAME` | Output artifact name |
| `--icon, -i FILE` | `.ico` / `.png` / `.icns` file |
| `--output, -o DIR` | Output directory (default: `dist` inside the project) |
| `--lang LANG` | Force the language (skips detection) |
| `--target-os OS` | `native` / `windows` / `linux` / `macos` / `android` |

### Build control

| Option | Description |
|---|---|
| `--onefile, -f` | Single executable file |
| `--console, -c` / `--no-console` | Show/hide the console window (default: show) |
| `--devtools` | Open DevTools in Electron (sets `ELECTRON_DEVTOOLS=1` — requires support in main.js) |
| `--backend` | `auto` / `pyinstaller` / `nuitka` (for Python) |

### Advanced

| Option | Description |
|---|---|
| `--hidden-imports MOD` | Hidden imports (repeatable) |
| `--add-data SRC=DEST` | Data files (repeatable) |

### Management

| Option | Description |
|---|---|
| `--update` | Update PolyBuild itself |
| `--update-deps` | Update the project's dependencies before building |
| `--check-tools` | Show the status of every tool |
| `--verbose, -v` | Verbose output |
| `--interactive, -I` | Force the interactive wizard |
| `--quick` | Skip questions, use defaults |
| `--yes, -y` | Assume "yes" for all questions (CI) |
| `--help, -h` / `--version` | Help / version |

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Success (including "detect without build" languages that print guidance) |
| `1` | Build or update failure |
| `2` | Invalid arguments |
| `130` | Cancelled by the user (Esc / Ctrl+C) |

---

## Supported Languages (26)

### Interpreted / Scripted

| Language | Tool | Output | Notes |
|---|---|---|---|
| Python | PyInstaller or Nuitka (`--backend`) | exe/bin or folder | `--onefile` / `--add-data` / `--hidden-imports` supported |
| Node.js | pkg (via npx, no global install) | standalone exe/bin | Requires `node` + `npm` |
| Ruby | ocra | exe | **Windows only** — clearly refused elsewhere |
| Perl | PAR::Packer (`pp`) | exe/bin | When absent: direct distribution as a ready-made folder |
| Lua / LÖVE | Fusion into the love binary | exe / `.love` | The official fusion technique (zip + binary) |

### Native compiled

| Language | Tool | Output | Notes |
|---|---|---|---|
| C / C++ | CMake, then Make, then direct gcc/g++ | exe/bin | Automatic priority based on the project's files |
| Rust | cargo | exe/bin | Cross to Windows via `rustup target` + mingw |
| Go | go build | exe/bin | Cross-compilation built in via `GOOS/GOARCH` |
| Nim | nim c | exe/bin | Cross to Windows via mingw |
| Zig | zig build / build-exe | exe/bin | Cross-compilation built into the compiler (no extra tools) |
| Crystal | crystal build | exe/bin | **Native builds only** — explicitly refuses Windows cross-compile |

### Frameworks

| Framework | Tool | Output | Notes |
|---|---|---|---|
| .NET (C#/F#/VB) | dotnet publish | self-contained exe | `PublishSingleFile` + automatic RID |
| Java | Gradle / Maven / javac+jar / jpackage | jar or app image | `--onefile` invokes jpackage when available |
| Kotlin | Gradle (kts) or kotlinc | jar | |
| Scala | scala-cli or sbt | jar (assembly) | |
| Flutter | flutter build | apk / exe / app / bundle | Desktop builds on the same OS only |
| Dart | dart compile exe | exe/bin | |
| Electron | electron-builder (npx) | installer/executable | A minimal `--config` is passed automatically when missing |
| Android | Gradle wrapper | apk | Checks JDK + `ANDROID_SDK_ROOT`/`local.properties` |
| Godot | godot --export-release | exe/apk | Reads `export_presets.cfg` and picks a suitable preset |

### Detect without build (official guidance)

| Engine | How it is detected | What the tool does |
|---|---|---|
| Unity | The `Assets` + `ProjectSettings` directories | Prints the official build steps + the batchmode automation command |
| Unreal | A `.uproject` file | Prints Packaging steps + the RunUAT command |
| GameMaker | A `.yyp` / `.project.gmx` file | Prints the IDE steps |
| Ren'Py | A `game/` directory with `.rpy` files | Prints the Ren'Py Launcher steps |

These are handled with a clear guidance message — **the tool never produces a misleading file**.

---

## Automatic Detection System

Works with a confidence-score system with explicit weights:

- **High scores** for build files: `Cargo.toml` (+60), `go.mod` (+60), `project.godot` (+70), `.csproj` (+60), `AndroidManifest.xml` (+65) ...
- **Medium scores** for source files with a per-language cap: `.py` (+20 then +3 per extra file) ...
- **High scores** for telling directories: `Assets` + `ProjectSettings` = Unity (+75).
- **Smart content rules**:
  - `package.json` containing `electron` → Electron (85 points) instead of Node.
  - `pubspec.yaml` containing `flutter:` → Flutter, otherwise Dart.
  - Gradle without a Manifest + `.kt` files → Kotlin; `.scala` → Scala; `.java` → Java.
  - CMake/Make: compares the number of `.c` vs `.cpp` files to pick C or C++.
- **Excluded directories**: `.git, node_modules, __pycache__, target, build, dist, venv, .gradle, ...` are never scanned.
- On a tie the higher score wins; on total failure → `UNKNOWN` with a "use --lang" message.
- Performance guards: 5000 files and 7 levels of depth maximum.

Show the evidence with `-v`:

```bash
python polybuild.py -p ./myapp -v
```

---

## Dependency Management

- Every tool (`gcc`, `cmake`, `node`, `cargo`, `dotnet`, ...) is checked with `shutil.which` plus an internal cache.
- Missing tools are installed automatically when possible:
  - **Linux**: `apt-get` (with `sudo` only when actually needed — outside root).
  - **macOS**: `brew`.
  - **Windows**: `winget` then `choco`.
  - **Language-level fallback**: `pip` / `gem` / `cpan` (e.g. installing PyInstaller automatically via pip).
- **Bundled tools** (`npm` with `node`, `gem` with `ruby`, ...): after installing the parent, the child is genuinely re-checked on disk — never assumed successful.
- Auto-install policy: with explicit consent (`-y`) or on an interactive TTY. On CI without a TTY and without `-y`, only the manual install command is printed (safe behavior).
- When installation fails → a clear message with the manual install command for your system.
- `--check-tools` prints a status table for all tools (34 tools) with install commands.

---

## Interactive Wizard

Starts automatically when `--project` is not given and stdin is a terminal (or forcibly with `-I`):

- **Arrow-key (↑/↓) menus** on TTY, **numbered** menus on CI (via pipes/scripts).
- Detection result previewed in a box before you decide.
- A smart default in every field — **Enter = default**.
- Quick cancel with **Esc** (or Ctrl+C) with exit code 130.
- A full summary in a Unicode box before the final confirmation.
- A unified `_getch()`: `msvcrt` on Windows, `termios + tty` on Unix, and `input()` when no TTY.

Example of scripted usage (numbered mode via a pipe):

```bash
printf "./myproject\n\n\n\n\n\n\n" | python polybuild.py -I
```

---

## Self-Update

### Environment variables

| Variable | Purpose |
|---|---|
| `POLYBUILD_VERSION_URL` | JSON URL declaring the latest release: `{"version":"3.1.1","sha256":"<hash>","url":"<script URL>"}` |
| `POLYBUILD_UPDATE_URL` | Download URL of the new script (used when `url` is absent from the JSON) |
| `POLYBUILD_NO_UPDATE_CHECK` | Set to `1` to disable the version check at startup |

### How it works

1. At startup: a quick non-blocking check (short timeout; its failure never blocks the tool) — prints a hint if a newer version exists.
2. `--update`: downloads the new script, then **triple validation**:
   - **SHA-256** match against what the server declares — **no trust in any content without an explicit hash** (constant-time comparison via `hmac.compare_digest`).
   - `ast.parse()` to verify syntactic integrity.
   - `py_compile.compile()` to verify compilability.
3. Replacement: a `.backup` copy, then an atomic `os.replace`, then re-compilation check in place.
4. Any failure → **automatic rollback** from `.backup`; the current version stays.

Hosting example:

```
https://example.com/polybuild/version.json  →  {"version":"3.1.1","sha256":"abc...","url":"https://example.com/polybuild/polybuild.py"}
```

---

## Security & Reliability

- **Every `subprocess.run` has an explicit timeout** — nothing runs without a time limit (60s queries, 180s intermediate, 600s packages, 900s heavy compiles).
- `errors="replace"` on every external output read — no `UnicodeDecodeError` from oddly-encoded tools.
- Failure handling via `returncode` — no command is ever assumed successful.
- **False-positive protection** for bundled tools: after installing the parent, the child is re-checked from disk.
- **Honest cross-compilation**:
  - C/C++/Nim/Rust from Linux/macOS to Windows → checks mingw-w64 first and fails clearly with the install command when missing, instead of delivering a misleading binary.
  - Crystal → explicitly refuses Windows cross-compile (native builds only).
  - Ruby/ocra → refuses to run outside Windows.
  - Flutter desktop → refuses building from a different OS (apk works from any OS).
  - Go and Zig → cross-compilation built in and trusted within the compilers themselves.
- Temporary files always cleaned via `finally` (the `.polybuild_tmp` folder is removed after every build).
- Never writes files outside the project/`dist` directory.
- Reconfigures stdout/stderr to UTF-8 at startup — no encoding crashes on Windows with symbols or non-Latin text.
- Safe ANSI color detection: respects `NO_COLOR`, supports Windows Terminal/VT via `SetConsoleMode`, and falls back to ASCII alternatives (`+ x ! ->`) when Unicode is unavailable.

---

## Toolbox Integration

PolyBuild is designed to be invoked as an internal module from a GUI tool via `subprocess`:

```python
import subprocess, json

proc = subprocess.run(
    ["python", "polybuild.py", "-p", project_dir, "-n", app_name, "-y", "-v"],
    capture_output=True, text=True, timeout=1200,
)
success = (proc.returncode == 0)
print(proc.stdout)   # output suitable for display in a GUI
```

Recommendations for integrators:

- Use `-y` (CI mode) for non-interactive runs, and read `returncode`.
- Use `-v` for verbose output to show in the tool's log.
- Set `POLYBUILD_NO_UPDATE_CHECK=1` to speed up startup on repeated invocation.
- Build errors are printed to **stderr** with the `[✗]` prefix — easy to isolate by reading the two streams separately.

---

## CI Usage

```bash
# GitHub Actions — Python onefile build, no questions asked
python polybuild.py -p ./src -n myapp -f -y --quick

# GitLab CI — check the image's tools first, fail early with install commands
python polybuild.py --check-tools && python polybuild.py -p . -y

# Disable the update check to speed up startup
POLYBUILD_NO_UPDATE_CHECK=1 python polybuild.py -p . -y
```

CI notes:
- Without `-y` and on a non-TTY: no tools are installed automatically — manual install commands are printed and the build fails with exit code 1.
- "Detect without build" languages (Unity and friends) finish successfully with exit code 0 and the printed guidance.

---

## Troubleshooting

| Problem | Cause and fix |
|---|---|
| "Missing tools that cannot be skipped" | Install the tool with the shown command, or run on a TTY so installation is automatic |
| "Entry point not found" | Specify it explicitly: `-s main.py` or `-s index.js` |
| "Could not determine the project language" | Force it: `--lang python` (the full list is printed with the error) |
| "Cross-compiling to Windows requires mingw-w64" | `sudo apt-get install -y mingw-w64` then retry |
| PyInstaller failed on a dynamically imported module | Add `--hidden-imports MODULE` (repeatable) |
| The exe "does nothing" for a CLI app | Built without a console? Remove `--no-console` or rebuild with defaults |
| Gradle wrapper lacks execute permission | The tool handles it automatically via `sh gradlew` |
| `--update` fails on a SHA-256 mismatch | The server-declared hash does not match the file — nothing is ever replaced (safe rollback) |
| Strange symbols in an old terminal | The tool falls back to ASCII automatically — make sure the terminal supports UTF-8 |

---

## Acceptance Criteria — Implementation Status

- ✅ Works out of the box: `python polybuild.py --help`
- ✅ Detects 26 languages/environments accurately (score system with smart content rules).
- ✅ Actually builds: Python, Node, C, C++, Go, Rust, C#, Java, Flutter (+ Kotlin/Scala/Nim/Zig/Dart/...).
- ✅ Every builder produces a genuinely runnable file (tested: C, C++, Python/PyInstaller, Node/pkg).
- ✅ No crash when a tool is missing → red message + manual install command.
- ✅ Interactive wizard: arrows on TTY, numbered on CI, Esc to cancel, summary before confirmation.
- ✅ Self-update with triple validation and rollback works (tested: success and rejection).
- ✅ Correct cross-compile handling (mingw checked before building; Crystal/Ruby explicit refusal).
- ✅ Every subprocess has an explicit timeout.
- ✅ File size ≈ 2800 organized lines (< 3500) in 15 numbered sections.
- ✅ Short `# [fix]` comments explain every critical point.
- ✅ No mandatory pip dependencies (Standard Library only).

---

## Internal Structure (single file, 15 sections)

| # | Section |
|---|---|
| 1 | Header and imports |
| 2 | Version constants and configuration |
| 3 | Terminal tools (ANSI colors + symbols + Unicode boxes) |
| 4 | Helpers (exe_ext, version_compare, run_cmd, ...) |
| 5 | Types (LangType, DetectedProject, exceptions) |
| 6 | SelfUpdater (triple validation + rollback) |
| 7 | BaseToolInstaller (apt/brew/winget/choco/pip/gem/cpan) |
| 8 | DependencyManager (cache + bundled tools + check-tools) |
| 9 | ProjectDetector (score system + entry point per language) |
| 10 | BaseBuilder (build template + _require + _run + _print_result) |
| 11 | Every builder separate (26 builders) |
| 12 | BUILDERS factory dict |
| 13 | InteractiveUI (the interactive wizard) |
| 14 | build_arg_parser() |
| 15 | main() |

---

## License

MIT — use, modify, and distribute freely.
