# GRIM

**GNU Runtime & Installation Manager**

A minimal developer tool for reproducible C and C++ environments.

---

## Installation

```bash
pip install grim
```

That's it. The `grim` command is now available globally.

---

## Quick Start

```bash
grim new hello_world
cd hello_world
grim install fmt
grim build
grim test
grim run
```

---

## Commands

| Command | Description |
|---|---|
| `grim new <name>` | Scaffold a new project |
| `grim new <name> --type c` | Scaffold a C project (default: cpp) |
| `grim install <pkg>` | Install a dependency |
| `grim install` | Restore all dependencies from lock file |
| `grim build` | Build the project via CMake |
| `grim test` | Run tests via CTest |
| `grim run` | Run the compiled application |

---

## Project Structure

```
my_project/
  src/            source files
  include/        project headers
  tests/          unit tests
  cmake/          optional cmake modules
  CMakeLists.txt
  grim.config
  grim.lock
```

---

## Project Types

```bash
grim new my_app --type c            # C application
grim new my_app --type cpp          # C++ application (default)
grim new my_fw  --type embedded-c   # Embedded C
grim new my_fw  --type embedded-cpp # Embedded C++
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

MIT — see [LICENSE](LICENSE).
