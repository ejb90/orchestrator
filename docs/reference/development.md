# Development and CI

From the repository root, install the development dependencies recorded in `uv.lock`:

```bash
uv sync --locked --group dev
```

Python 3.12 is selected by `.python-version`. Runtime dependencies include PyYAML for
the YAML authoring frontend and Rich Click for help and error presentation. Rich also
styles run states and dependency views. JSON and raw task output paths bypass terminal
formatting. Run the same five checks as CI:

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

## API documentation

The [API reference](api/index.md) uses `mkdocstrings[python]`, installed with the locked
development dependencies. Its Python handler reads `src/` using static analysis
(`allow_inspection: false`), so documenting Click commands does not execute them.
Every declared module-level class/function and its methods is listed, including private
helpers and members without docstrings. Constructor signatures are merged into class
entries, and expandable source code is included.

Write docstrings in Google style, with `Args`, `Returns`, `Raises`, and `Attributes`
sections as appropriate. Signatures and annotations come directly from the source;
new members in existing documented modules appear automatically on rebuild.

To document a new module, add a page under `docs/reference/api/` containing a directive:

```markdown
# My module

::: orchestrator.my_module
```

Add that page to the API reference navigation in `mkdocs.yml` and the overview's module
list. Cross-reference objects with Markdown such as
`[Workflow][orchestrator.api.Workflow]`. The strict MkDocs build in CI validates the
reference collection and links alongside the narrative guides. Internal helpers are
documented for contributors, without implying a stable public API.

## GitHub Actions

The workflow is `.github/workflows/ci.yml`. It runs on pushes, pull requests, and manual
dispatch. It uses Ubuntu 24.04, Python 3.12, and uv 0.13.1, installs Zsh/Fish alongside
Bash, verifies all three shells are available, and synchronizes locked development
dependencies before running the checks above. Outdated runs for the same branch are
cancelled when a new run starts. The workflow has read-only repository permissions.

The workflow builds the documentation without publishing it. Its definition is present;
check the repository's Actions page for actual hosted results after committing and
pushing. Local verification does not confirm that a hosted run has passed.

??? info "Theory: Testing invariants versus qualifying a deployment"

    Real local subprocess and SQLite tests exercise execution and persistence. A
    fake scheduler can expose crash and ordering cases deterministically, but cannot
    establish how a particular cluster, filesystem, or accounting service behaves.
    Qualification needs evidence from that environment as well.

    Read more: [Evidence and scope of guarantees](../fundamentals/philosophy.md#prefer-evidence-to-guesses).

## Qualification still required

Mocked scheduler tests do not qualify a real Slurm site. Site profiles, accounting and lost-acknowledgement discovery, and real-cluster
smoke tests remain pending. YAML v1 covers flat static graphs; nested workflows and
conditional controls use the Python API. See [architecture and status](architecture.md)
for milestone coverage and [operations](../guide/operations.md) for schema compatibility,
uncertainty, and recovery limits.
