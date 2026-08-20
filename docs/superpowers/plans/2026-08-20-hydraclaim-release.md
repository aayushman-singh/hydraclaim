# HydraClaim Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Verify HydraClaim on supported systems, publish version 0.2.0 to PyPI, and finish the local product rename.

**Architecture:** Continuous integration tests the supported Python and operating-system matrix. A tag-only workflow downloads the exact tested package artifacts and publishes them through PyPI Trusted Publishing.

**Tech Stack:** GitHub Actions, Python 3.11-3.13, PyPA build, Twine, PyPI OpenID Connect, Git.

**Spec:** `docs/superpowers/specs/2026-08-20-hydraclaim-rename-cli-architecture-design.md`

## Global Constraints

- Publish only after all core and CLI plan tasks pass.
- Publish only version `0.2.0` from tag `v0.2.0`.
- Use the GitHub `pypi` environment.
- Store no PyPI token.
- Never force-push.
- Rename the local folder only after public installation verification.

---

### Task 1: Continuous integration matrix

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Produces: test results for Python 3.11, 3.12, and 3.13 on Ubuntu and Windows.

- [ ] **Step 1: Add workflow syntax validation**

Run: `python -m pip install pyyaml && python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml', encoding='utf-8'))"`
Expected before creation: FAIL because the file does not exist.

- [ ] **Step 2: Create the matrix workflow**

```yaml
name: CI
on:
  pull_request:
  push:
    branches: [main]
jobs:
  test:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest]
        python-version: ["3.11", "3.12", "3.13"]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v6
      - uses: actions/setup-python@v6
        with:
          python-version: ${{ matrix.python-version }}
      - run: python -m pip install -e . pytest ruff build twine
      - run: ruff check .
      - run: ruff format --check .
      - run: python -m pytest tests/ -v
      - run: python -m build
      - run: python -m twine check dist/*
```

- [ ] **Step 3: Validate and commit**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml', encoding='utf-8'))"`
Expected: exit code 0.

```bash
git add .github/workflows/ci.yml
git commit -m "ci: test supported Python versions"
```

### Task 2: Trusted publication workflow

**Files:**
- Create: `.github/workflows/publish.yml`

**Interfaces:**
- Consumes: tag `v0.2.0` and GitHub environment `pypi`.
- Produces: the public PyPI release `hydraclaim==0.2.0`.

- [ ] **Step 1: Create a tag-only build and publish workflow**

```yaml
name: Publish
on:
  push:
    tags: ["v*"]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
        with:
          persist-credentials: false
      - uses: actions/setup-python@v6
        with:
          python-version: "3.13"
      - run: python -m pip install build twine
      - run: python -m build
      - run: python -m twine check dist/*
      - uses: actions/upload-artifact@v5
        with:
          name: python-package-distributions
          path: dist/
  publish:
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: pypi
      url: https://pypi.org/p/hydraclaim
    permissions:
      id-token: write
    steps:
      - uses: actions/download-artifact@v6
        with:
          name: python-package-distributions
          path: dist/
      - uses: pypa/gh-action-pypi-publish@release/v1
```

- [ ] **Step 2: Validate workflow restrictions**

Run: `rg -n "tags:|environment:|id-token: write|gh-action-pypi-publish" .github/workflows/publish.yml`
Expected: all four restrictions appear.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/publish.yml
git commit -m "ci: publish tagged releases to PyPI"
```

### Task 3: Local release gate and push

**Files:**
- No file changes.

**Interfaces:**
- Consumes: clean `main` with all prior tasks committed.
- Produces: pushed commits with passing GitHub checks.

- [ ] **Step 1: Run the full local gate**

Run: `ruff check --fix . && ruff format . && python -m pytest tests/ -v && python -m build && python -m twine check dist/*`
Expected: all commands pass.

- [ ] **Step 2: Run the clean-install gate**

Run: `powershell -ExecutionPolicy Bypass -File scripts/verify-package.ps1`
Expected: version `0.2.0` and successful help output for ten commands.

- [ ] **Step 3: Confirm repository state**

Run: `git status --short --branch`
Expected: clean `main`, ahead of `origin/main` only by the planned commits.

- [ ] **Step 4: Push**

Run: `git push origin main`
Expected: push succeeds without force.

- [ ] **Step 5: Confirm GitHub checks**

Run: `gh run list --workflow ci.yml --branch main --limit 1`
Expected: the latest run completes with `success`.

### Task 4: Configure PyPI and publish

**Files:**
- No repository file changes.

**Interfaces:**
- Produces: public `hydraclaim==0.2.0`.

- [ ] **Step 1: Configure the pending publisher in PyPI**

Use these exact values in the PyPI account publishing page:

```text
PyPI project: hydraclaim
Owner: aayushman-singh
Repository: hydraclaim
Workflow: publish.yml
Environment: pypi
```

Expected: PyPI lists one pending GitHub publisher with these values.

- [ ] **Step 2: Create and push the release tag**

Run: `git tag -a v0.2.0 -m "release: HydraClaim 0.2.0"`

Run: `git push origin v0.2.0`
Expected: the tag push starts `publish.yml`.

- [ ] **Step 3: Confirm publication workflow**

Run: `gh run list --workflow publish.yml --limit 1`
Expected: the latest run completes with `success`.

- [ ] **Step 4: Verify public metadata**

Run: `python -m pip index versions hydraclaim`
Expected: version `0.2.0` appears.

- [ ] **Step 5: Verify a public clean install**

```powershell
python -m venv .venv-pypi-test
& .venv-pypi-test/Scripts/python.exe -m pip install --no-cache-dir hydraclaim==0.2.0
& .venv-pypi-test/Scripts/hydraclaim.exe --version
& .venv-pypi-test/Scripts/hydraclaim.exe ask --help
```

Expected: installation succeeds and the command reports `hydraclaim 0.2.0`.

### Task 5: Rename the local repository folder

**Files:**
- Rename outside Git: `C:/Repo/trustgraph` to `C:/Repo/hydraclaim`.

**Interfaces:**
- Produces: the final local project path `C:/Repo/hydraclaim`.

- [ ] **Step 1: Verify source and target paths**

```powershell
$source = (Resolve-Path -LiteralPath "C:/Repo/trustgraph").Path
$target = "C:/Repo/hydraclaim"
if ($source -ne "C:\Repo\trustgraph") { throw "unexpected source: $source" }
if (Test-Path -LiteralPath $target) { throw "target already exists: $target" }
```

- [ ] **Step 2: Leave the source directory and rename it**

Run from `C:/Repo`:

```powershell
Move-Item -LiteralPath "C:/Repo/trustgraph" -Destination "C:/Repo/hydraclaim"
```

- [ ] **Step 3: Verify the renamed repository**

Run from `C:/Repo/hydraclaim`: `git status --short --branch`
Expected: clean `main` tracking `origin/main`.

Run from `C:/Repo/hydraclaim`: `git remote -v`
Expected: both remote lines use `aayushman-singh/hydraclaim.git`.
