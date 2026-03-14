# GRIMX

**GCC Runtime & Installation Manager - Cross Platform**

A minimal developer tool for reproducible C and C++ environments.

---

## Installation

```bash
pip install grimx
```

That's it. The `grimx` command is now available globally.

---

## Quick Start

```bash
grimx new hello_world
cd hello_world
grimx install fmt
grimx build
grimx test
grimx run
```

---

## Commands

| Command | Description |
|---|---|
| `grimx new <name>` | Scaffold a new project |
| `grimx new <name> --type c` | Scaffold a C project (default: cpp) |
| `grimx install <pkg>` | Install a dependency |
| `grimx install` | Restore all dependencies from lock file |
| `grimx build` | Build the project via CMake |
| `grimx test` | Run tests via CTest |
| `grimx run` | Run the compiled application |

---

## Project Structure

```
my_project/
  src/            source files
  include/        project headers
  tests/          unit tests
  cmake/          optional cmake modules
  CMakeLists.txt
  grimx.config
  grimx.lock
```

---

## Project Types

```bash
grimx new my_app --type c            # C application
grimx new my_app --type cpp          # C++ application (default)
grimx new my_fw  --type embedded-c   # Embedded C
grimx new my_fw  --type embedded-cpp # Embedded C++
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

MIT — see [LICENSE](LICENSE).
