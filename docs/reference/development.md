# Development and CI

From the repository root, install the development dependencies recorded in `uv.lock`:

```bash
uv sync --locked --group dev
```

Python 3.12 is selected by `.python-version`. Runtime dependencies include PyYAML for
the YAML authoring frontend. Run the same five checks as CI:

```bash
uv run --locked ruff check src tests examples scripts
uv run --locked ruff format --check src tests examples scripts
uv run --locked mypy
uv run --locked pytest
uv run --locked mkdocs build --strict
```

The test suite covers Python and YAML definitions through the common compiler, real
local subprocess execution, staging and output validation, retained scripts, retries,
finalizers, restart recovery, and mocked Slurm operations. Bash, Zsh, and Fish should
be installed for full shell coverage; tests for unavailable shells are skipped locally.
The strict documentation build checks site generation and documentation links. Preview
changes with `uv run mkdocs serve`.

## GitHub Actions

The workflow is `.github/workflows/ci.yml`. It runs on pushes, pull requests, and manual
dispatch. It uses Ubuntu 24.04, Python 3.12, and uv 0.13.1, installs Zsh/Fish alongside
Bash, verifies all three shells are available, and synchronizes locked development
dependencies before running the checks above. Outdated runs for the same branch are
cancelled when a new run starts. The workflow has read-only repository permissions.

The workflow builds the documentation without publishing it. Its definition is present;
check the repository's Actions page for actual hosted results after committing and
pushing. Local verification does not confirm that a hosted run has passed.

## Qualification still required

Mocked scheduler tests do not qualify a real Slurm site. Site profiles, separate
database/artifact roots, accounting and lost-acknowledgement discovery, and real-cluster
smoke tests remain pending. YAML v1 covers flat static graphs; nested workflows and
conditional controls use the Python API. See [architecture and status](architecture.md)
for milestone coverage and [operations](../guide/operations.md) for schema compatibility,
uncertainty, and recovery limits.
