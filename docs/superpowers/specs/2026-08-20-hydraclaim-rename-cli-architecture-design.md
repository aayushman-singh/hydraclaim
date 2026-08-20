# HydraClaim rename, CLI, and architecture design

## User result

### Why

The product already presents itself as HydraClaim, but its local repository name and one internal graph label still use TrustGraph terms. Users also cannot install one stable command from the Python Package Index (PyPI). Some graph reads grow with the full graph, some errors silently select other data, and several modules repeat HydraDB knowledge.

### Success criteria

1. A user installs `hydraclaim==0.2.0` from public PyPI.
2. A user runs `hydraclaim --version` and sees `0.2.0`.
3. A user runs each supported subcommand through one `hydraclaim` command.
4. Help text identifies required files, settings, and external systems.
5. Invalid input or an external failure stops the operation with an explicit error.
6. An answer contains a structured route, text, citations, classification, and probe result.
7. A probe reads only claims and relations for its selected subject and optional predicate.
8. Oracle ingestion and extracted-claim ingestion use one graph-write module.
9. CLI, HTTP, benchmark, and graph views use one claim-read module.
10. The local repository path is `C:/Repo/hydraclaim`.

### Non-goals

- Do not redesign the web interface.
- Do not change the claim vocabulary or reconciliation rules.
- Do not change the HydraDB product or its supported query dialect.
- Do not rewrite Git history.
- Do not add a CLI framework.
- Do not add silent alternative behavior.

### Hard constraints

- Support Python 3.11, 3.12, and 3.13.
- Keep the existing Python module command forms.
- Use only the verified HydraDB query dialect.
- Keep claim writes idempotent.
- Keep answer creation deterministic after classification.
- Keep abstention as a valid answer result.
- Use GitHub Actions and PyPI Trusted Publishing.
- Keep commits atomic and use conventional commit messages.

## Product name

The product name is HydraClaim. The Python package, PyPI project, installed command, GitHub repository, web copy, and deployment names use `hydraclaim` or `HydraClaim` as applicable.

The local repository folder changes from `C:/Repo/trustgraph` to `C:/Repo/hydraclaim` after all repository work is complete. The temporary graph label changes from `TGProbe` to `HydraClaimProbe`. This label does not store user claims. Git history remains unchanged.

## CLI interface

The package installs one `hydraclaim` command. It supplies these subcommands:

1. `ask`
2. `serve`
3. `schema`
4. `generate`
5. `ingest`
6. `extract`
7. `evaluate`
8. `pipeline`
9. `benchmark`
10. `longmemeval`

Each command implementation accepts an explicit argument list. The installed command and `python -m` form call the same implementation. The root command supplies `--help` and `--version`.

The project uses the Python standard library for argument parsing. It does not add Typer, Click, or another CLI framework.

## Architecture

### Nodes and directed relations

```text
question
  -> classification
  -> scoped claim read
  -> probe
  -> route
  -> structured answer
  -> CLI or HTTP rendering

document
  -> validation or extraction
  -> reconciliation plan
  -> graph write
  -> HydraDB
```

### Claim-read module

The claim-read module has two entry points:

- Read claims for a subject, optional predicate, activity state, time, and limit.
- Answer one question with explicit classification options.

The module hides entity reads, claim reads, relation reads, probe calculation, chain ordering, route selection, conflict scoring, and structured answer creation.

All claim and relation queries use the selected claim identifiers. No probe query scans all graph relations. Claim results use a stable typed shape. The structured answer contains the route, answer text, citations, classification, and probe result.

The CLI, HTTP interface, benchmark, and graph view do not define claim query text.

### Graph-write module

The graph-write module has two entry points:

- Ingest one validated scenario document.
- Apply one deterministic reconciliation plan.

The module hides string-to-graph identifier conversion, scalar property conversion, HydraDB query text, existence checks, claim writes, relation writes, status closure, and write order.

The module validates the full operation before the first write. It creates claims before relations. It creates each directed relation once. Repeated ingestion produces no duplicate claim or relation.

Reconciliation decides which claims supersede or conflict. The graph-write module records those decisions. The two modules do not import private names from each other.

### HydraDB seam

The existing HydraDB query interface remains the external-system seam. Production uses the HTTP adapter. Tests use a recording adapter. The design does not add a repository class, a raw query escape hatch, or another pass-through adapter.

### Schema dialect

One module owns supported HydraDB query forms and scalar rules. The schema document contains only verified queries or is generated from the same source. It does not contain `IS NULL`, `length()`, or undirected relation matches.

## Error behavior

Classification uses an explicit `heuristic` or `llm` mode. The default CLI mode is `heuristic`. The `llm` mode requires complete language-model settings and stops on any language-model error. The system does not change modes during a request.

Invalid dates, timestamps, claim values, scenario data, or command arguments stop the operation. The system does not use the current clock or an unprocessed value as a substitute.

HydraDB and language-model operations do not retry inside HydraClaim. A failed write stops all dependent writes. Logs include the input, failed step, current state, exception type, and full traceback. HTTP responses contain a stable error code and concise message. CLI failures write a concise message to standard error and return a nonzero exit code.

Schema verification fails when a probe or cleanup operation fails.

Abstention is not an error. It explicitly states the subject and predicate that the system searched.

## Verification

### Behavior tests

- Test scoped reads with unrelated claims and relations present.
- Test that query count and query scope do not grow with unrelated graph data.
- Test claim ordering, time filters, conflict ranking, and supersession chains.
- Test structured answers for `FAST`, `DEEP`, and `ABSTAIN` routes.
- Test graph-write order and repeated ingestion with a recording adapter.
- Test oracle and extracted-claim writes through the same module.
- Test all explicit failure modes and confirm that dependent work stops.
- Test HTTP request size, question size, rate limit, error mapping, and graph result shape.
- Test each CLI subcommand and exit code.

### Package tests

1. Run Ruff checks and formatting.
2. Run the full test suite.
3. Build the wheel and source archive.
4. Run `twine check` on both files.
5. Inspect archive contents.
6. Install the wheel in a clean virtual environment.
7. Run the root command, version command, and all subcommand help commands.
8. Run offline fixture commands.
9. Verify explicit configuration errors for commands that need HydraDB or a language model.

Continuous integration runs these checks on Python 3.11, 3.12, and 3.13 on Windows and Linux.

## Publication

Version `0.2.0` is the first public CLI release. Package metadata is the one version source. The CLI reads the installed version from package metadata.

The `publish.yml` workflow builds one artifact set after tests pass. It stores and then publishes the same files. It runs only for a `v*` tag and uses the GitHub `pypi` environment with OpenID Connect permission.

The PyPI pending publisher must use these exact values:

- Owner: `aayushman-singh`
- Repository: `hydraclaim`
- Workflow: `publish.yml`
- Environment: `pypi`

The release sequence is:

1. Complete local verification.
2. Push all atomic commits to `main`.
3. Confirm that GitHub checks pass.
4. Configure the pending trusted publisher on PyPI.
5. Create and push tag `v0.2.0`.
6. Confirm successful publication.
7. Install `hydraclaim==0.2.0` from public PyPI in a clean environment.
8. Rename the local repository folder.
