# Practical workflows

Run these file-processing examples from the repository root after
`uv sync --locked --group dev`. They need Linux, Bash, `awk`, and standard shell
utilities, without third-party application tools.

## Clean sensor readings and report temperatures

| File | Purpose |
| --- | --- |
| `examples/data/sensor_readings.csv` | Measurements with station, temperature, and quality status |
| `examples/jobs/clean_readings.sh` | Keep the header and rows whose status is `ok` |
| `examples/jobs/temperature_report.sh` | Write accepted count, mean, minimum, and maximum |
| `examples/sensor_report.yaml` | Static workflow definition |
| `examples/sensor_report.py` | Equivalent Python workflow definition |

The input contains:

```csv
station,temperature_c,status
north,18.5,ok
north,19.5,ok
south,22.0,ok
south,99.0,bad
north,20.0,ok
south,23.0,ok
north,,missing
```

The cleaner uses a normal terminal command:

```bash
mkdir -p results
awk -F, 'NR == 1 || $3 == "ok"' inputs/readings.csv > results/clean.csv
```

The scripts handle this fixture's simple, unquoted CSV layout. For general CSV data,
replace the commands with CSV-aware tools. The report script fails if no accepted rows
remain, rather than reporting a successful mean for an empty dataset.

### Read the YAML contracts

The cleaner's reusable task definition declares the script and its file contracts:

```yaml
tasks:
  clean_readings:
    script:
      path: jobs/clean_readings.sh
    inputs:
      - name: readings
        kind: file
        source: data/sensor_readings.csv
        destination: inputs/readings.csv
    outputs:
      - name: readings
        kind: file
        source: results/clean.csv
```

Before execution, the orchestrator copies the source CSV to `inputs/readings.csv` inside
the cleaner's isolated attempt directory. After successful execution it checks that
`results/clean.csv` exists and publishes it as the `readings` output. Literal source and
script paths resolve relative to the YAML file.

The `temperature_report` task declares an input staged at `inputs/clean.csv` and a
`summary` output from `results/report.txt`. The named steps connect the tasks:

```yaml
nodes:
  clean:
    task: clean_readings
  report:
    task: temperature_report
    inputs:
      readings: {$ref: clean.readings}
outputs:
  clean_csv: {$ref: clean.readings}
  report: {$ref: report.summary}
```

The output reference passes the cleaned CSV to `report` and creates its dependency.
Top-level `outputs` exposes both resulting artifact references to the caller.

??? info "Theory: Definitions, invocations, and dataflow"

    `clean_readings` and `temperature_report` describe reusable work; `clean` and `report`
    are the steps using those definitions. The output reference creates the dependency and
    passes the cleaned file. The second step gets its own staged copy. Top-level `outputs`
    exposes both artifact references to the caller.

    Read more: [Bindings and execution identity](../fundamentals/definitions.md#binding-connects-names-to-data).

These are excerpts: the complete `examples/sensor_report.yaml` also includes `version: 1`,
`name: sensor_report`, and the report task.

### Use the Python equivalent

`examples/sensor_report.py` creates the same contracts using `Script`, `TaskInput`, and
`TaskOutput`, with source paths based on `__file__`. Its composition is:

```python
workflow = Workflow("sensor_report")
clean = workflow.add("clean", clean_readings)
report = workflow.add("report", temperature_report, inputs={"readings": clean.out("readings")})
workflow.expose(clean_csv=clean.out("readings"), report=report.out("summary"))
```

Both definitions compile to the same specification and produce a cleaned CSV with five
readings and a report with a mean of 20.6°C and a range of 18.5–23.0°C. The
[quick start](quickstart.md) shows execution and inspection commands.

Copy the definition and scripts into your project, update the input `source`, and adjust
the application commands. Keep script paths consistent with the staging destinations.
If you move the YAML file, update its relative script/input paths. Start a new run after
edits; resuming uses the stored specification and captured scripts.

## Summarize an application log

`examples/log_summary.yaml` runs `examples/jobs/summarize_log.sh` over
`examples/data/application.log`. It stages the file at `inputs/application.log` and
publishes `results/summary.txt` as the workflow's `report` artifact.

```bash
uv run orchestrator plan examples/log_summary.yaml --tree
uv run orchestrator run examples/log_summary.yaml --allow-local
uv run orchestrator logs RUN_ID log_summary/summarize
```

Use this run's printed ID. The result is:

```text
Application log summary
Entries: 6
Warnings: 1
Errors: 2
```

The fixture uses whitespace-separated timestamp, level, and message fields. The script
counts `WARN` and `ERROR` in the second field. Logged errors do not fail the summarizer:
its job is to report them. A missing essential input fails before the script runs.

## Learn composition separately

The [workflow guide](../guide/workflows.md) and [DAG gallery](../guide/dags.md) retain the
small `diamond`, `nested`, `branches`, and `convergence` examples. Their arithmetic tasks
isolate dependency and control behavior. Learn those patterns there, then apply them to
your application scripts.
