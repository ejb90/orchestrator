# Durable execution and recovery

## Model execution as state transitions

A state machine describes allowed changes and the evidence required for them. A task
can wait for dependencies, become active, and then succeed, fail, or be cancelled.
Skipping means it was not executed. An attempt additionally records submission and
observation details. `Uncertain` records missing evidence rather than inventing an outcome.

Keeping these records separate matters for retries: a failed attempt can return its
task to waiting when policy permits another try. It does not erase the first attempt
or immediately make the whole task terminal. The implementation's exact transitions
are in the [architecture reference](../reference/architecture.md#transition-semantics).

## Transactions have a boundary

SQLite can commit a state update and its event together, so the history and snapshot
agree about that update. A revision check and exclusive local coordinator ownership
protect the supported single-host coordination model.

The database cannot commit an operating-system process launch or Slurm submission in
the same transaction. There is a crash window:

```text
persist intent → submit job → persist acknowledgement
                     ↑
             accepted, then coordinator crashes
```

After restart, an intent without an acknowledgement does not establish whether the job
was accepted. The coordinator must reconcile observations for that attempt. Persisting
intent first makes the planned action identifiable; it does not create exactly-once
execution across the database and scheduler.

## Identity makes observations meaningful

Results include run, node, and attempt IDs. A late result from an older attempt cannot
replace the current attempt's outputs, and observing the same accepted result again
does not authorize another execution.

Local process checks use boot identity and start ticks as well as PID, because PIDs
can be reused. Slurm job IDs and result files provide different evidence; disappearance
from the queue is not proof of successful completion. Scheduler accounting and lost-job-ID
discovery are still incomplete in the current adapter.

Atomic result publication writes a temporary file and renames it, avoiding a reader
mistaking a partially written record for a complete one. Atomic visibility is distinct
from surviving power loss or volatile-storage loss; filesystem durability guarantees
remain limited. Preserving SQLite cannot restore results deleted from scratch storage.

## Retry is different from resume

**Resume** observes and continues the same stored run. It uses the existing specification,
captured scripts, pinned directory bases, and known attempt paths. Completed work remains
complete; outstanding work must be reconciled before it can authorize descendants.

**Retry** starts a new isolated attempt after a confirmed failure in a retryable category,
within the attempt limit and after its backoff. Uncertain submission does not qualify.
Each retry has new paths and a new attempt identity, while the task definition is preserved.

Retry isolation does not undo external effects. A task might append to a shared file
and fail before reporting completion. Repeating it could append twice. **Idempotence**
means repeating an operation has the same externally visible effect as doing it once;
task authors must arrange this when their workflows need it. The orchestrator does not
promise exactly-once application effects.

## Cancellation and cleanup also need evidence

A cancellation request and a confirmed cancelled outcome are different facts. A task
may finish before a signal or scheduler request takes effect. The coordinator preserves
that observed success rather than rewriting history to match the request.

Required finalizers express cleanup work that remains eligible when a scope closes,
fails, or is cancelled, provided it was entered and its dependencies permit execution.
They cannot guarantee cleanup while the coordinator is down; resumption is needed.
They also cannot repair arbitrary external side effects automatically.

See [operations](../guide/operations.md) to inspect failures and uncertainty, and
[execution directories](../guide/directories.md#retries-resume-and-control-decisions)
for the consequences of losing scratch storage.
