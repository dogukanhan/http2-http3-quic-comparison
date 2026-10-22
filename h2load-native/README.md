docker buildx build \
  --platform linux/arm64 \
  --progress=plain \
  --no-cache \
  -t h2load-http3-arm64 .


  docker run --rm h2load-http3-arm64 \
  -n1000 -c10 -m10 --npn-list=h3 https://host.docker.internal:4433/

## Resource benchmark runner

From the repository root (requires Python 3.9+, `openpyxl`, Docker, and the
same server/h2load images as `excel_runner.py`):

```bash
python3 h2load-native/excel_runner_resource.py \
  --protocol h2 --payload 1 \
  -n 10000 -c 10 -m 10 \
  --delay 0 --packet-loss 0 \
  --iterations 5 --capture-delay-ms 10
```

The default workbook is `h2load-native/ResourceTestResults.xlsx`. Close it in
Excel before running. Override it with `--workbook`; relative paths resolve from
the repository root, as in the original runner. Protocol `h3` is also supported.
`--delay` controls network impairment; `--capture-delay-ms` controls resource
polling. Iterations default to 3; capture delay defaults to 10 ms.

Each iteration arms the capture worker **before** launching a fresh server
container. Once `docker run` confirms startup, the worker begins collecting
`docker stats --no-stream --format '{{json .}}'` while the main thread waits for
server readiness and then runs h2load. Summaries therefore include available
startup/readiness samples as well as load-test samples. No measurements exist
before the container starts, and the first moments of startup are not guaranteed
to be captured. Failed startup also stops the waiting worker.
Only the server container is measured. Docker's collection time
and refresh cadence limit the actual sampling rate: 10 ms is a pause between
completed commands, **not 100 independent measurements per second**. Polls do
not overlap. On completion, the runner waits for the last in-flight poll before
removing the container; this snapshot may include time after the load test.
Short runs may produce only one sample, for which STD is zero. Use longer tests
to collect a useful distribution. Each stats command times out after 15 seconds
(configurable with `--stats-timeout`). Snapshots with missing or invalid CPU
readings are discarded. If an iteration has no valid CPU samples, or all its
CPU samples are 0%, the entire test is rerun with a fresh container under the
same iteration number. Failed attempts are excluded from Excel and the final
mean. The limit is 10 total attempts per iteration; configure it with
`--max-attempts` (1 disables retries). Reaching the limit aborts without writing
results from this invocation. Other capture or command failures abort immediately.
Zero CPU samples are retained when the attempt also has a positive CPU sample;
a zero minimum or zero STD alone does not trigger a retry.

The workbook retains its two header rows and existing input columns. Its
resource groups are populated in this order, with numeric Min/Max/Mean/STD:

| Group | Value summarized across Docker snapshots |
| --- | --- |
| CPU | CPU percentage points (125.5 means 125.5%; can exceed 100 on multiple cores) |
| Memory | Used memory in bytes, using Docker's displayed memory accounting |
| NET IO | Received + transmitted bytes, Docker's cumulative container counter |

Memory and NET IO columns display whole bytes with thousands separators,
including Min/Max/Mean/STD and the final mean row. Stored numeric precision is
preserved. Each successful save also applies this display format to existing
memory and NET IO rows. CPU retains three decimal places.

NET IO is not bytes per second or the delta between samples. Values include
traffic since container creation, including readiness checks. Docker's SI and
IEC size strings are normalized to bytes; precision is limited by Docker's
formatted output. See the [Docker stats documentation](https://docs.docker.com/reference/cli/docker/container/stats/).

STD is the population standard deviation across captured snapshots. After the
requested iterations, a final row labelled `mean` combines each summary column
using the original runner's trimmed mean: with three or more iterations, remove
one smallest and one largest value and average the rest; otherwise average all
values. This summarizes only the current invocation, not earlier workbook rows,
and is not a pooled standard deviation. Raw samples and h2load performance
metrics are not written to Excel. Repeated identical cases reuse their test
number and continue iteration numbering; changing any input, including network
delay, creates a new test number.

Run the resource runner tests without starting Docker:

```bash
python3 -m unittest discover -s h2load-native -p 'test_excel_runner_resource.py' -v
```

## Bulk resource benchmarks

```bash
python3 h2load-native/run_excelc_case_resource.py
```

Edit `test_case_1()` in this script to configure the sweep, as with
`run_excel_case.py`. By default it runs 53 payload cases: 1 KB, 5–255 KB in
5 KB steps, and 256 KB. Each case uses h2, 5,000 requests, 20 clients, 10 streams,
zero network delay/loss, and 5 iterations. It invokes `excel_runner_resource.py`
sequentially, saving to its default `ResourceTestResults.xlsx`, with progress and
remaining-time estimates. Each case receives its own final mean row.

The `run_excel_case()` helper also accepts `capture_delay_ms`, `max_attempts`,
`stats_timeout`, `workbook`, and `h2load_image`. The bulk run stops if a case fails
or exhausts its retries; results from earlier completed cases remain saved.
