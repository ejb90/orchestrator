# Data and execution contracts

## A successful computation needs a usable result

A command can exit with status zero while producing an empty, missing, or invalid file.
A task contract describes what consumers may rely on: named inputs and outputs, file or
directory kinds, requiredness, staging/collection rules, and optional application validators.

For the sensor report, success means more than `awk` finishing. The cleaner must publish
the declared CSV, and the reporter must publish its text report. A validator can add
domain checks where existence alone is insufficient. Validation only establishes the
properties actually checked; it does not prove the scientific correctness of a result.

Keep these contracts separate from control policies. A validator accepts or rejects an
output in the worker. The coordinator decides which branch or descendant may use it.

## Small values and artifact references

Small values fit in serialized records. Large datasets are represented by references
to files or directories, keeping the coordinator's state independent of dataset size.
The database records identity, metadata, and paths rather than embedding scientific arrays.

An artifact reference is a location and contract, not an immutable content-addressed
object. Its contents can change if other work modifies the underlying files. Current
staging and collection do not provide automatic cross-run caching or universal checksums.

## Copying and linking express ownership choices

Copy staging gives an attempt its own input file. Linking shares the original target,
so writes through the link can affect other work. Copying a directory preserves internal
symlinks in the current implementation, which can still point to shared data.

Output validation happens before collection and success publication. Collection refuses
existing destinations, and a set of output copies is not one atomic transaction. This
makes attempt-local paths easier to reason about than shared absolute publication paths.
Practical collision and partial-copy rules are in the [artifact guide](../guide/tasks.md).

## Scripts make the execution boundary visible

A managed script calls the runner to stage inputs, execute application commands, validate
and collect outputs, and publish a result. The application subprocess's exit status and
the output contract both contribute to the result. Shell-specific failure handling matters:
an earlier failed command can be hidden by a later success if the shell program allows it.

Workers publish results rather than updating coordinator state. This separates application
execution from orchestration decisions and lets the coordinator validate a result's run,
node, and attempt identity before accepting it.

## Directories are namespaces, not dependency edges

Each attempt has a unique location for its inputs, script, logs, and results. Reusing one
task or retrying one step therefore does not reuse another attempt's publication paths.
A parent-relative base describes a logical scope's filesystem configuration, not a data
dependency on another task's working directory.

The directory base, execution backend, and durable database location are separate choices.
Choosing `/dev/shm` changes where files live; it does not provide a scheduler allocation,
transport data to a compute node, or make memory-backed storage durable. Bases are pinned
at run creation, and attempts receive their own identities beneath them.

See [directory configuration](../guide/directories.md), [script authoring](../guide/scripts.md),
and [recovery](durability.md) for operational consequences.
