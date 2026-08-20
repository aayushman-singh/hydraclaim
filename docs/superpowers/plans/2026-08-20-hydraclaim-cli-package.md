# HydraClaim CLI Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish one installable HydraClaim command with ten subcommands and verified package artifacts.

**Architecture:** A small dispatcher selects a command and passes the remaining argument list to the existing command implementation. Each command parser accepts an explicit argument list, so the installed command and Python module form share one behavior path.

**Tech Stack:** Python 3.11+, argparse, importlib.metadata, PyPA build, Twine, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-20-hydraclaim-rename-cli-architecture-design.md`

## Global Constraints

- Support Python 3.11, 3.12, and 3.13.
- Keep all existing Python module command forms.
- Add no CLI framework or runtime dependency.
- Use `0.2.0` for the first public CLI release.
- Run `ruff check --fix . && ruff format .` before each Python commit.
- Use atomic conventional commits.

---

### Task 1: Argument-list command implementations

**Files:**
- Modify: `hydraclaim/ask.py`
- Modify: `hydraclaim/serve.py`
- Modify: `hydraclaim/schema.py`
- Modify: `hydraclaim/generate/__main__.py`
- Modify: `hydraclaim/ingest.py`
- Modify: `hydraclaim/extract.py`
- Modify: `hydraclaim/evaluate.py`
- Modify: `hydraclaim/pipeline.py`
- Modify: `hydraclaim/benchmark.py`
- Modify: `hydraclaim/longmemeval.py`
- Test: `tests/test_cli_modules.py`

**Interfaces:**
- Produces: `main(argv: Sequence[str] | None = None) -> int | None` in every command module.

- [ ] **Step 1: Write parameterized parser tests**

```python
@pytest.mark.parametrize("module_name", COMMAND_MODULES)
def test_command_help_accepts_explicit_argv(module_name):
    module = importlib.import_module(module_name)
    with pytest.raises(SystemExit) as exc:
        module.main(["--help"])
    assert exc.value.code == 0
```

- [ ] **Step 2: Run the tests**

Run: `python -m pytest tests/test_cli_modules.py -v`
Expected: FAIL because current `main` functions do not accept an argument list.

- [ ] **Step 3: Pass `argv` to each parser**

```python
def main(argv: Sequence[str] | None = None) -> int | None:
    parser = argparse.ArgumentParser(prog="hydraclaim <command>")
    args = parser.parse_args(argv)
    # Existing command behavior follows.
```

Keep `if __name__ == "__main__": raise SystemExit(main())` in each module. Preserve command behavior and replace module-style `prog` text with installed-command text.

- [ ] **Step 4: Run all command tests**

Run: `python -m pytest tests/test_cli_modules.py tests/test_benchmark.py tests/test_serve.py -v`
Expected: PASS. Ask command cases live in `tests/test_cli_modules.py`.

- [ ] **Step 5: Format and commit**

```bash
ruff check --fix .
ruff format .
git add hydraclaim tests/test_cli_modules.py
git commit -m "refactor: accept explicit CLI arguments"
```

### Task 2: Unified command dispatcher

**Files:**
- Create: `hydraclaim/cli.py`
- Modify: `pyproject.toml`
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `hydraclaim.cli.main(argv: Sequence[str] | None = None) -> int`.
- Produces: `[project.scripts] hydraclaim = "hydraclaim.cli:main"`.

- [ ] **Step 1: Write failing dispatcher tests**

```python
def test_root_version(capsys):
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "hydraclaim 0.2.0"

@pytest.mark.parametrize("command", COMMANDS)
def test_subcommand_help(command):
    with pytest.raises(SystemExit) as exc:
        main([command, "--help"])
    assert exc.value.code == 0

def test_unknown_command_returns_usage_error(capsys):
    assert main(["unknown"]) == 2
    assert "unknown command" in capsys.readouterr().err
```

- [ ] **Step 2: Run the dispatcher tests**

Run: `python -m pytest tests/test_cli.py -v`
Expected: FAIL because `hydraclaim.cli` does not exist.

- [ ] **Step 3: Implement the command registry**

```python
COMMANDS = {
    "ask": "hydraclaim.ask",
    "serve": "hydraclaim.serve",
    "schema": "hydraclaim.schema",
    "generate": "hydraclaim.generate.__main__",
    "ingest": "hydraclaim.ingest",
    "extract": "hydraclaim.extract",
    "evaluate": "hydraclaim.evaluate",
    "pipeline": "hydraclaim.pipeline",
    "benchmark": "hydraclaim.benchmark",
    "longmemeval": "hydraclaim.longmemeval",
}
```

Use `importlib.metadata.version("hydraclaim")` for the version. Print root help when no arguments are present. Return `2` for a command usage error. Do not catch operational exceptions.

- [ ] **Step 4: Run dispatcher and metadata tests**

Run: `python -m pytest tests/test_cli.py -v`
Expected: PASS for all ten commands.

- [ ] **Step 5: Format and commit**

```bash
ruff check --fix .
ruff format .
git add hydraclaim/cli.py pyproject.toml tests/test_cli.py
git commit -m "feat: add unified HydraClaim CLI"
```

### Task 3: Package metadata and archive content

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/test_package_metadata.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: wheel and source archive for `hydraclaim==0.2.0`.

- [ ] **Step 1: Write metadata assertions**

```python
def test_project_metadata():
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    assert project["name"] == "hydraclaim"
    assert project["version"] == "0.2.0"
    assert project["scripts"]["hydraclaim"] == "hydraclaim.cli:main"
    assert project["requires-python"] == ">=3.11"
```

- [ ] **Step 2: Run metadata tests**

Run: `python -m pytest tests/test_package_metadata.py -v`
Expected: FAIL while the project version remains `0.1.0`.

- [ ] **Step 3: Complete package metadata**

Set version `0.2.0`. Add a Hatchling build backend and explicit package inclusion. Add `build/`, `dist/`, and `.venv-package-test/` to `.gitignore`. Keep license, README, classifiers, project links, and the runtime dependency.

- [ ] **Step 4: Build and inspect artifacts**

Run: `python -m build`
Expected: one wheel and one source archive in `dist/`.

Run: `python -m twine check dist/*`
Expected: both files pass.

Run: `python -c "import zipfile,glob; p=glob.glob('dist/*.whl')[0]; names=zipfile.ZipFile(p).namelist(); assert any(n.endswith('hydraclaim/cli.py') for n in names); assert not any('__pycache__' in n or n.startswith('tests/') for n in names)"`
Expected: exit code 0.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .gitignore tests/test_package_metadata.py
git commit -m "build: prepare HydraClaim package artifacts"
```

### Task 4: Clean-install acceptance test and documentation

**Files:**
- Create: `scripts/verify-package.ps1`
- Modify: `README.md`
- Modify: `docs/DEMO.md`
- Test: `tests/test_package_metadata.py`

**Interfaces:**
- Consumes: `dist/hydraclaim-0.2.0-py3-none-any.whl`.
- Produces: a clean installation check for all public CLI commands.

- [ ] **Step 1: Add a failing documentation command check**

```python
def test_readme_uses_installed_command():
    text = Path("README.md").read_text(encoding="utf-8")
    assert "pip install hydraclaim" in text
    assert "hydraclaim ask" in text
    assert "hydraclaim serve" in text
```

- [ ] **Step 2: Run the documentation test**

Run: `python -m pytest tests/test_package_metadata.py -v`
Expected: FAIL because the README uses module commands.

- [ ] **Step 3: Add clean-install verification**

```powershell
$ErrorActionPreference = "Stop"
$venv = Join-Path $PSScriptRoot "../.venv-package-test"
python -m venv $venv
$python = Join-Path $venv "Scripts/python.exe"
$cli = Join-Path $venv "Scripts/hydraclaim.exe"
& $python -m pip install --no-index --find-links dist hydraclaim==0.2.0
& $cli --version
foreach ($command in @("ask","serve","schema","generate","ingest","extract","evaluate","pipeline","benchmark","longmemeval")) {
    & $cli $command --help
}
```

Update user procedures to use `pip install hydraclaim` and the installed command. Keep module commands only in a compatibility note.

- [ ] **Step 4: Run package acceptance**

Run: `powershell -ExecutionPolicy Bypass -File scripts/verify-package.ps1`
Expected: version `0.2.0` and successful help output for ten commands.

Run: `python -m pytest tests/ -v`
Expected: PASS.

Run: `ruff check --fix . && ruff format . && git diff --check`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add scripts/verify-package.ps1 README.md docs/DEMO.md tests/test_package_metadata.py
git commit -m "docs: add installable CLI workflow"
```
