# Test execution

`executor.py` selects the h2load image using `archtype` in `env.yaml`.
Server images use the versions in their build folders' `version.txt` files.
Prepare these images with `pre_build_containers()` first.

## Local or remote server

Install the Python dependencies from the project root:

```bash
python3 -m pip install -r requirements.txt
```

`run_test.py` reads the root `env.yaml`. The default `remote: false` keeps
both server containers and h2load on the current machine. To use a Linux
server, fill in your connection settings:

```yaml
archtype: arm
remote: true
username: "your-linux-user"
password: "your-password"
ip_address: "192.0.2.10"
ssh_port: 22
case_1_enabled: true
case_2_enabled: false
case_3_enabled: false
case_4_enabled: false
```

Set `archtype` to match the local machine running h2load:

- `amd64`: use `tvsjsdock/h2load-http3:latest`. Preparation pulls the image
  for `linux/amd64` only if it is missing locally. When h2load bandwidth is
  configured, use the versioned `h2load-http3-amd64-netem` image instead:
  a small layer on that image installs iproute2 without rebuilding h2load.
- `arm`: use `h2load-http3-arm64:<version>`, where `<version>` comes from
  `h2load-native/version.txt`. Preparation builds it for `linux/arm64` only
  if that version is missing locally.

The default is `arm`. This setting applies to the local load generator;
remote server images are built using the Linux server's native architecture.

## Container limits

Set `container_limits` in the root `env.yaml`. These settings apply to every
case and iteration. Omitted values and `null` leave that resource unlimited.
The checked-in template and local configuration start with all limits disabled.
For example (choose values suitable for your experiment):

```yaml
container_limits:
  h2load:
    memory: "1g"
    cpus: 2.0
    bandwidth_mbps: 100
  http2:
    memory: "512m"
    cpus: 1.0
    bandwidth_mbps: 100
  http3:
    memory: "512m"
    cpus: 1.0
    bandwidth_mbps: 100
```

- `memory`: Docker size string (`512m` = 512 MiB, `1g` = 1 GiB), at least
  6 MiB. Sets `--memory` and the same `--memory-swap` value to disable swap
  for that container. Exceeding the limit can cause an out-of-memory kill.
- `cpus`: CPU quota in cores, at least `0.01`. `0.5` allows half of one core's
  CPU time; `2.0` allows two cores' worth. This is not CPU affinity/pinning.
- `bandwidth_mbps`: positive megabits per second (decimal), e.g. `12.5`.
  Applies to all outgoing traffic on the container's `eth0`, shared across
  connections. h2load controls client-to-server traffic (including ACKs);
  http2/http3 control server-to-client traffic (including responses).
  These are separate directional limits, not one combined bidirectional cap.

The existing delay and packet loss are combined with bandwidth in one command:

```sh
tc qdisc replace dev eth0 root netem delay 10ms loss 1% rate 100mbit
```

Without a bandwidth limit the `rate` argument is omitted. Netem emulates a link
rate; timer granularity, queues, and protocol overhead affect observed application
throughput. Queue overflow can also drop packets even with configured loss 0%.
See the [netem manual](https://man7.org/linux/man-pages/man8/tc-netem.8.html)
and [Docker resource limits](https://docs.docker.com/engine/containers/resource_constraints/).

Limits follow each container's execution host: h2load stays local, while the
servers follow `remote`. The selected limits are printed to the run's logs.
Versioned server/ARM images rebuild automatically when their new version is
missing. For AMD64 h2load with bandwidth enabled, the runner mounts the local
`h2load-native/docker-entrypoint.sh` read-only and runs it with `/bin/sh` instead
of the image's entrypoint. It applies delay/loss/rate, then executes h2load once.
The original registry image lacks `tc`, so preparation automatically builds
`h2load-http3-amd64-netem:<version>` from `h2load-amd64/Dockerfile` when needed.
Its version is stored in `h2load-amd64/version.txt`; an existing version skips
the build. This adds iproute2 while preserving the original h2load binary.
Missing `tc` or failed network configuration stops execution rather than
running an unshaped test. Use the AMD64 binary on an AMD64 machine; its execution
under ARM emulation is not guaranteed.

## Connection setup

Use the actual server address. Keep passwords quoted; an empty password uses
your SSH agent or local SSH keys instead. `env.yaml` is ignored by Git;
`env.example.yaml` is the template for a fresh checkout. Connect once using
`ssh your-linux-user@your-server-ip` (add `-p PORT` for a different SSH port)
and verify the server key so it is saved in `~/.ssh/known_hosts`. The runner
requires that verified key and does not automatically trust unknown servers.

The Linux machine needs SSH/SFTP access, `tar`, and Docker accessible to that
user without an interactive sudo prompt. Allow TCP 8443 for HTTPS and UDP
9443 for QUIC from the machine running h2load. Docker must also be running
locally. Then start the configured cases with:

```bash
python3 run_test.py
```

`case_1_enabled`, `case_2_enabled`, `case_3_enabled`, and `case_4_enabled` select which scripts run.
Set a flag to `true` to enable its case or `false` to skip it. Enabled cases run
in order (1, 2, 3, 4) in the same dated results folder. Missing flags default to
case 1 enabled and cases 2, 3, and 4 disabled. If all are disabled,
the runner logs that there is nothing to run and skips Docker and analysis.

Each `execute_case_N.py` reads its `case_N` parameter block in `env.yaml`.
All protocols and numeric test inputs are configured there. The scripts contain
no payload/client/delay/loss selections. `env.example.yaml` documents the full
structure; current selections were moved from the scripts without expanding
the shortened case 1 and case 2 runs.

For example, edit case 4 directly:

```yaml
case_4:
  protocols: [h2, h3]
  payloads: [20]
  clients: [20]
  delays: [0, 10, 50, 100]
  packet_losses: [0, 1, 2]
  total_requests: 5000
  max_streams: 10
  iterations: 5
```

Use explicit, nonempty lists for `protocols`, `payloads` (KB), `clients`,
`delays` (ms), and `packet_losses` (%). A single value, such as `[20]`, keeps
that input fixed. For example, `clients: [2, 10, 25, 50, 100]` runs exactly
those five client counts. Ranges such as `1-100` or `range(1, 101)` are not
accepted. Every combination of the five lists runs in the listed order.
`total_requests`, `max_streams`, and `iterations` are positive integers.

Validation rejects empty lists, duplicates, unsupported protocols, invalid
numbers, missing fields, and unknown fields before Docker starts. Every enabled
case needs its complete parameter block. The YAML is loaded once by `run_test.py`
so a run uses one consistent configuration. Direct module execution (for example,
`python3 -m test_executor.execute_case_2`) loads the same YAML parameters.
Each case logs all selected inputs and estimated remaining time. Summary-table
selection filters remain specific to their reports; changing execution lists
does not change those filters.
Analysis, trimmed means, and percentiles are saved separately for each enabled case. The case 1
summary table and all three `case_1_*_graph_creator.py` scripts run only when
case 1 is enabled and read its case-local CSV. These flags control `run_test.py`; directly invoking a case script
runs that case explicitly.

In remote mode, image preparation pulls or builds h2load locally according to
`archtype` and builds the two server images on Linux using its native
architecture. The server checks its own image cache for each version in
`version.txt`; existing versions skip the build. Missing server images are built from uploaded server sources,
payloads, and the QUIC server's certificate files when present. Temporary
remote build files are removed afterward. The SSH credentials and test
results are not included in these uploads.

Server containers and their resource reads run over the shared SSH connection.
h2load still runs locally, targeting the configured server IP, and all result
files remain in the local `test_results` folder. `resource.log` measures the
remote **server container**, including CPU, memory, and network counters;
it does not include the local h2load container's resource usage. Each resource
record includes `server_host` to identify where it was collected.

Call it within the initialization context to group cases under one dated run:

```python
from test_executor.executor import execute
from test_executor.pre_build_containers import pre_build_containers
from test_executor.pre_init_test import pre_init_test
from test_executor.server_host import server_connection

with pre_init_test(), server_connection():
    pre_build_containers()
    execute(
        folder_name="payload-tests", protocol="h2", payload=20,
        total_request=5000, client=20, max_streams=10,
        delay=0, packet_loss=0, iterations=5,
    )
```

The output layout is:

```text
test_results/<date-time>/
  logs.txt
  case_1/
    analysis.csv
    trimmed_mean.csv
    percentiles.csv
    case_1_summary_table.csv
    resource_graphs/
    h2load_graphs/
    percentile_graphs/
    h2-20-5000-20-10-0-0/
      001/
        summary.txt
        requests.log
        resource.log
      002/
        summary.txt
        requests.log
        resource.log
      ...
  case_2/
    analysis.csv
    trimmed_mean.csv
    percentiles.csv
    h2-20-5000-1-10-0-0/
      001/
        summary.txt
        requests.log
        resource.log
```

The parameter folder order is protocol, payload (KB), total requests, clients,
maximum streams, delay (ms), packet loss (%). Each iteration starts a fresh
server and saves h2load stdout and stderr to `NNN/summary.txt` and the run's log.
It also passes `--log-file` to h2load, saving per-request records in the matching
`NNN/requests.log`. The iteration directory is mounted into the client
container, so request logs remain on the host after container cleanup.
The [h2load log format](https://nghttp2.org/documentation/h2load.1.html#cmdoption-log-file)
contains tab-separated request start time, HTTP status, and response duration.
If h2load reports `0 started, 0 done, 0 succeeded`, the executor discards that
attempt's iteration directory (summary, request log, and resource log) and reruns
the same iteration with fresh containers. It allows **three total attempts**
per iteration. After the third empty attempt, the case stops with an error;
earlier successful iterations remain. Discarded output and retry messages are
still visible in the run's `logs.txt`. The executor waits 500 ms before each
retry and between normal iterations. Other failures retain partial output and
error details and stop the case.
Repeating a case in the same run continues iteration folder numbering without
overwriting. Existing folders and earlier flat-format result files retain their
iteration numbers; previously saved files are not moved.

## Resource capture

Each iteration samples the **server container** in a background thread and
writes a JSON array of records to `NNN/resource.log`. Each appended record
includes a comma separator when needed and restores the closing `]`, so the
file can be loaded with `json.load()` after each completed write, including
when capture fails. A baseline is read after server
readiness, immediately before launching h2load. Reads are scheduled every
500 ms while h2load runs; a final read happens after it exits, before container
cleanup. Even tests shorter than 500 ms receive baseline and final records.
The measured window includes h2load container launch/exit overhead.

Each record includes a timestamp, actual elapsed/interval/read durations, the
raw CPU counter in nanoseconds, CPU time since the previous sample, total CPU
time consumed since the baseline (nanoseconds and seconds), interval CPU
percentage, memory bytes, and cumulative network received/transmitted bytes
(excluding loopback). The final record contains total measured CPU time:

`cpu_consumed_seconds = (final_cpu_counter_ns - baseline_cpu_counter_ns) / 1e9`

CPU percentage is based on counter change divided by actual elapsed time;
100% represents one fully used core and multi-core usage can exceed 100%.
The baseline percentage is null because no interval has elapsed.

The sampler reads [cgroup v2 CPU accounting](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html)
(`usage_usec`, converted to nanoseconds), or
[cgroup v1 CPU accounting](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v1/cpuacct.html)
(`cpuacct.usage`, already nanoseconds), through `docker exec`. Memory is the
raw cgroup usage including cache; it may differ from `docker stats`' displayed
memory. These reads add monitoring overhead inside the measured container.
500 ms is a target interval: Docker execution and SSH latency can delay readings, and
missed ticks are skipped. Each read has a 5-second timeout. Read failures are
logged and fail the iteration; a decreasing CPU counter is treated as an error.

Running the executor on its own creates a new timestamped run automatically:

```bash
python3 -m test_executor.executor --folder-name payload-tests \
  --protocol h2 --payload 20 -n 5000 -c 20 -m 10 \
  --delay 0 --packet-loss 0 --iterations 5
```

The executor requires already-built images; it does not automatically build them.

## CSV analysis

After all enabled cases complete, `run_test.py` processes each enabled case
separately. `pre_analysis()` writes `test_results/<date-time>/<case>/analysis.csv`
using semicolons (`;`) as the column separator. Each CSV contains only its case's
iterations, in protocol/payload order and then numeric iteration order.
All derived CSVs and graphs stay inside their related case folder; only the
run's `logs.txt` stays at the dated run level. Iteration logs stay in their
existing parameter/iteration folders. The summary table and graph generators
remain specific to case 1.

The first columns are `test_id`, `iteration_id`, `protocol`, `payload`,
`total_requests`, `clients`, `max_stream`, `delay`, and `packet_loss`.
The test ID combines the dated run, case folder, and parameter folder; all
iterations of that case share the same ID. Iteration IDs are numeric (folder
`001` becomes `1`). Payload is KB, delay is ms, and packet loss is percent.

Columns prefixed `h2load_` contain the summary's duration, request rate,
throughput, TLS information, request/status counts, traffic byte counts, UDP
counts when present, and all four statistics plus the within-SD percentage
for each timing table row. H2load duration columns end in `_ms` and are numeric
milliseconds, including `h2load_finished_ms`. Req/s columns remain requests per
second; they are rates, not durations. Resource counters retain their explicitly
named units from `resource.log`.
Throughput retains the displayed number and a separate `h2load_throughput_unit`
column (for example, `MB/s`). Traffic bytes use the exact parenthesized counts.

Columns prefixed `resource_` contain every field of the last resource record
whose `type` is `final`. Baseline and periodic records are excluded. Resource
units and meanings remain unchanged: for example, `cpu_consumed_seconds` is
the total CPU time over the measured window, while `cpu_percent` describes
the final sampling interval. Missing/null values are blank, not zero.

Missing or malformed logs retain the iteration's input row and any available
metrics, with details in `analysis_error` and the console/run log. Exporting
again replaces the CSV without appending duplicate rows. To analyze an existing
run without Docker or SSH:

```bash
python3 -m test_executor.pre_analysis test_results/<date-time>/case_1
python3 -m test_executor.pre_analysis test_results/<date-time>/case_2
```

The three CSV analysis commands accept either a case folder directly or a dated
run folder with `--case case_1`, `--case case_2`, `--case case_3`, or `--case case_4` (default: `case_1`).

## Trimmed means

`run_test.py` runs `execute_trimmed_mean()` after `pre_analysis()` and saves
`test_results/<date-time>/<case>/trimmed_mean.csv`, also separated by semicolons.
It contains one aggregate row per `test_id` and leaves `analysis.csv` unchanged.

The algorithm ranks iterations by `h2load_time_for_request_mean_ms`
(the **mean** in h2load's **time for request** row). It discards exactly one
lowest and one highest iteration, then averages every numeric measurement
over the same remaining iterations. Five valid iterations therefore produce
means from three iterations. With fewer than three valid iterations, it uses
all available iterations without trimming. Ties are resolved by iteration ID:
the lowest ID is discarded at the minimum end and the highest at the maximum.

Three additional columns contain JSON lists of iteration IDs:

- `min_dismiss`: the iteration discarded for the lowest request-time mean.
- `max_dissmiss`: the iteration discarded for the highest request-time mean.
- `mean_of_iterations`: the iterations used for all numeric means.

The original column names are preserved. `test_id` and input parameters stay
the same, and `iteration_id` becomes `trimmed_mean` to identify the aggregate.
Counts, byte counters, timings, percentages, and other numeric measurements
are averaged and exported with exactly six decimal places. Input parameters
retain their original values. Throughput is converted to `B/s`
before averaging so differing display units do not produce an incorrect mean.
Shared text metadata stays as text; differing metadata becomes a JSON list.

Iterations with missing or invalid ranking values are excluded and reported
in `analysis_error`. If another numeric metric is missing or invalid among
the retained iterations, that mean is blank with an error explanation; the
script does not silently average it over a different subset. Columns that
are blank for every retained iteration remain blank. Existing analysis
warnings are also carried into the aggregate row.

To regenerate the trimmed means from an existing analysis CSV:

```bash
python3 -m test_executor.execute_trimmed_mean test_results/<date-time>/case_1
```

## Request latency percentiles

`run_test.py` next runs `95_99_percentile.py`, after both analysis steps.
It reads each iteration's `requests.log` and writes `percentiles.csv` with
the input parameters, iteration ID, p95, p99, sample counts, and trim decision.
It saves this file and updates `trimmed_mean.csv` in the same case folder.
Both CSV outputs use semicolons and six decimal places for percentile values.

The third tab-separated field in the [h2load request log](https://nghttp2.org/documentation/h2load.1.html#cmdoption-log-file)
is response duration in microseconds. Percentiles use completed HTTP responses
(including HTTP error responses); failed streams marked with status `-1` are
excluded and counted in `failed_streams`. Durations are sorted and percentiles
use linear interpolation at `(sample_count - 1) * percentile / 100`, equivalent
to Excel's `PERCENTILE.INC`. Output durations are converted to milliseconds.

Within each test, the step discards the iteration with the lowest p99 and the
one with the highest p99. It averages **both p95 and p99** over the same retained
iterations, using unrounded values until export. It adds these two columns to
the matching `test_id` and input parameters in `trimmed_mean.csv`:

- `h2load_time_for_request_p95_ms`
- `h2load_time_for_request_p99_ms`

These are means of per-iteration percentiles, not percentiles of pooled requests.
The existing metrics and their three trim-audit columns retain their original
request-mean-based selection. Percentile selection is recorded separately in
`percentiles.csv` as `percentile_trim_role`: `min_dismiss`, `max_dissmiss`, or
`mean_of_iterations`. Missing, empty, or malformed request logs have blank
percentiles, an `excluded` role, and details in `percentile_error`.
With fewer than three valid iterations, all valid iterations are used. Ties
use iteration IDs as in the existing trimmed-mean step.

To update an existing trimmed-mean file without rerunning benchmarks:

```bash
python3 test_executor/95_99_percentile.py test_results/<date-time>/case_1
```

Run this command again after regenerating `trimmed_mean.csv`; the regular
trimmed-mean exporter rebuilds that file and the percentile step adds its two
columns afterward. Repeating the percentile step replaces its values without
adding duplicate columns or rows.

## Case 1 summary table

The final step in `run_test.py` is `case_1_summary_table()`. It reads
`case_1/trimmed_mean.csv` and creates `case_1/case_1_summary_table.csv` with semicolon-separated
columns: Payload, Protocol, Time For Request(Mean), Time For Connect(Mean),
Time To 1st Byte(Mean), Req/s (Mean), and H2 Load Req/s.

Only `case_1` rows with payloads **1, 5, 20, 100, and 256 KB** are included,
sorted by payload and then protocol. Summary measurements use three decimal
places; payload sizes remain integers. Other analysis CSVs retain their precision.
Timing cells display `mean ms`; rates display only their mean, without standard
deviation. Standard deviation columns remain in `analysis.csv`, `average.csv`,
and `trimmed_mean.csv`. Missing means leave blank cells. H2 Load Req/s uses the
overall rate from h2load's finished summary (`h2load_req_per_sec`).
Req/s (Mean) uses the mean from
h2load's per-client req/s statistics row (`h2load_req_per_sec_mean`). Missing
source values remain blank, and unavailable payload/protocol combinations
are not invented. Each matching test remains a separate row.

To create the table for an existing run:

```bash
python3 -m test_executor.case_1_summary_table test_results/<date-time>
```

## Resource graphs

When case 1 is enabled, `run_test.py` calls `case_1_resource_graph_creator()`. It reads only case 1 rows from
`trimmed_mean.csv` and creates four line graphs, each with h2 and h3 series
and payload (KB) on the x-axis. Every available payload is included.

- CPU usage (%): `resource_cpu_percent`, from the final sampling interval.
- CPU counter (ms): `resource_cpu_counter_ns / 1000000`, the final cumulative
  CPU time since container start. Saved as `cpu_counter.png` and `cpu_counter.svg`.
- Memory usage (MiB): `resource_memory_bytes / 1048576`, final memory including cache.
- Network usage (MiB): `(resource_network_rx_bytes + resource_network_tx_bytes) / 1048576`,
  the final cumulative received and transmitted counters.

The values are already trimmed means across iterations. CPU percentage here
is not the average over the whole test; it is the final interval percentage
saved by resource capture. Missing or invalid measurements leave gaps rather
than becoming zero. A missing protocol is indicated on the graph.

Graphs are saved as PNG and SVG under
`case_1/resource_graphs/n<requests>-c<clients>-m<streams>-delay<ms>-loss<percent>/`
inside the dated run folder. Different case 1 test settings get
separate graph sets. Duplicate payload/protocol entries within identical
settings cause an error instead of being silently combined.

Matplotlib is included in the root `requirements.txt`. To create graphs from
an existing run without rerunning benchmarks:

```bash
python3 -m test_executor.case_1_resource_graph_creator test_results/<date-time>
```

## H2load graphs

When case 1 is enabled, `run_test.py` also calls `case_1_h2load_graph_creator()` to create a single PNG with
four panels in a 2×2 layout. Every panel uses payload (KB) on the x-axis and
separate h2 and h3 lines. The means come directly from `trimmed_mean.csv`:

- Requests per second: `h2load_req_per_sec_mean` (per-client mean from h2load's
  statistics table).
- Time to first byte: `h2load_time_to_1st_byte_mean_ms`.
- Connection time: `h2load_time_for_connect_mean_ms`.
- Time for request: `h2load_time_for_request_mean_ms`.

The timing panels show milliseconds. Graphs display means only, without SD
bands or error bars. Missing means leave gaps. The same presentation applies
to the four-series trimmed-versus-average graphs; source SD columns are preserved.

The combined image is saved at
`case_1/h2load_graphs/n<requests>-c<clients>-m<streams>-delay<ms>-loss<percent>/h2load_summary.png`
inside the dated run. Only case 1 rows are plotted; different test settings
are plotted separately and all available case 1 payloads are included.

To create the image from an existing run:

```bash
python3 -m test_executor.case_1_h2load_graph_creator test_results/<date-time>
```

## Percentile graphs

`case_1_percentile_graph_creator()` runs automatically after the other graph steps
when case 1 is enabled, and reads only case 1 rows.
It reads `h2load_time_for_request_p95_ms` and `h2load_time_for_request_p99_ms`
from `trimmed_mean.csv` and produces one PNG with two panels: p95 and p99.
Both panels use payload (KB) on the x-axis, latency (ms) on the y-axis, and
separate h2 and h3 lines. Every available payload is included.

Values are plotted as saved: the averages of per-iteration percentiles after
p99-based trimming. No percentiles are recalculated or re-trimmed. Missing
values leave gaps. Different case 1 test settings have separate images at
`case_1/percentile_graphs/n<requests>-c<clients>-m<streams>-delay<ms>-loss<percent>/percentile_summary.png`.

To generate these graphs for an existing run with percentile columns:

```bash
python3 -m test_executor.case_1_percentile_graph_creator test_results/<date-time>
```

## Case 2 h2load graphs

When case 2 is enabled, `run_test.py` calls `h2load_graph_creator.py` after
its CSV analysis. It reads `case_2/trimmed_mean.csv` and creates a four-panel
PNG with **number of clients** on the x-axis and separate h2 and h3 lines:
requests per second (per-client mean), time to first byte, connection time,
and time for request. Timing values are milliseconds. Only mean lines are
shown, as in case 1. Client counts are sorted numerically.

Different payloads and other fixed test settings get separate images at
`case_2/h2load_graphs/n<requests>-p<payload>-m<streams>-delay<ms>-loss<percent>/h2load_summary.png`.
Missing measurements leave gaps; duplicate client/protocol rows with identical
settings are rejected. To regenerate without running benchmarks:

```bash
python3 -m test_executor.h2load_graph_creator test_results/<date-time>/case_2
```

## Case 2 percentile graphs

When case 2 is enabled, `run_test.py` runs `percentile_graph_creator.py` after
its h2load graph step. It reads p95 and p99 from `case_2/trimmed_mean.csv`
and produces one PNG with two panels, with **number of clients** on the x-axis
and request latency in milliseconds on the y-axis. Both panels show h2 and h3
lines. Values are the saved p99-trimmed means of per-iteration percentiles.

Client counts are sorted numerically. Each fixed payload and combination of
other settings gets a separate image at
`case_2/percentile_graphs/n<requests>-p<payload>-m<streams>-delay<ms>-loss<percent>/percentile_summary.png`.
Missing values leave gaps; duplicate client/protocol entries with identical
settings are rejected. To generate from an existing run without benchmarks:

```bash
python3 -m test_executor.percentile_graph_creator test_results/<date-time>/case_2
```

## Case 2 resource graphs

When case 2 is enabled, `run_test.py` runs `resource_graph_creator.py` after
its CSV analysis. It reads `case_2/trimmed_mean.csv` and plots **number of
clients** on the x-axis with separate h2 and h3 lines. The four resource
graphs use the same units and meanings as case 1: final-interval CPU usage (%),
cumulative CPU counter (ms), memory (MiB), and received + transmitted network
usage (MiB).

Each fixed payload and combination of other settings gets PNG and SVG files at
`case_2/resource_graphs/n<requests>-p<payload>-m<streams>-delay<ms>-loss<percent>/`.
Client counts are sorted numerically; missing values leave gaps. To generate
from an existing run without benchmarks:

```bash
python3 -m test_executor.resource_graph_creator test_results/<date-time>/case_2
```

## Case 2 summary table

When case 2 is enabled, `run_test.py` calls `case_2_summary_table()` after the
CSV analysis and before its graphs. It reads `case_2/trimmed_mean.csv` and
writes `case_2/case_2_summary_table.csv` using semicolons.

The first column is `client_size` instead of `Payload`. Only client counts
**2, 10, 25, 50, and 100** are included for h2 and h3, sorted by client count
and then protocol. Missing combinations are omitted. The remaining columns
and formatting match case 1: timing means use `ms`, rates
remain req/s, and measurements use three decimal places. Each matching test
remains a separate row. Source CSVs retain their original precision.

```bash
python3 -m test_executor.case_2_summary_table test_results/<date-time>/case_2
```

## Case 3 delay sweep

`execute_case_3.py` reads the `case_3` YAML block. The supplied block lists
each delay from **1 through 100 ms** explicitly, with both h2 and h3, a **20 KB
payload**, **20 clients**, 5,000 requests, 10 streams, zero loss, and five
iterations. These supplied values produce 200 combinations and 1,000 iterations.
Edit the lists or scalar values in YAML to change the run. Progress messages include the current delay, elapsed time,
and estimated remaining time based on completed tests.

Set `case_3_enabled: true` in `env.yaml` to include it in `python3 run_test.py`.
It is disabled by default. Results are saved under `test_results/<date-time>/case_3/`,
including the iteration folders, `analysis.csv`, `trimmed_mean.csv`, and
`percentiles.csv`. Case 3 also generates the graphs described below.

## Case 3 graphs by delay

When case 3 is enabled, its three graph generators run after CSV analysis.
They read `case_3/trimmed_mean.csv`, plot **delay in milliseconds** on the x-axis,
and show separate h2 and h3 lines:

- `case_3_h2load_graph_creator.py`: one four-panel PNG for per-client req/s,
  time to first byte, connection time, and request time, showing means only.
- `case_3_percentile_graph_creator.py`: one two-panel PNG for p95 and p99 latency.
- `case_3_resource_graph_creator.py`: CPU usage (%), CPU counter (ms), memory
  (MiB), and total received + transmitted network usage (MiB), in PNG and SVG.

Outputs stay in `case_3/h2load_graphs`, `case_3/percentile_graphs`, and
`case_3/resource_graphs`, under `n<requests>-p<payload>-c<clients>-m<streams>-loss<percent>/`.
Different fixed settings get separate graph sets. Delays are sorted numerically,
missing measurements leave gaps, and duplicate delay/protocol entries with the
same settings are rejected. The CSV values are preserved.

To regenerate graphs without running benchmarks:

```bash
python3 -m test_executor.case_3_h2load_graph_creator test_results/<date-time>/case_3
python3 -m test_executor.case_3_percentile_graph_creator test_results/<date-time>/case_3
python3 -m test_executor.case_3_resource_graph_creator test_results/<date-time>/case_3
```

## Case 3 summary table

`case_3_summary_table.py` reads `case_3/trimmed_mean.csv` and writes
`case_3/case_3_summary_table.csv`. It replaces `Payload` with `delay` and
selects **0, 50, and 100 ms** for both h2 and h3, sorted by delay and protocol.
The table keeps case 1's other columns, three decimal places, and timing `ms`. It runs automatically when case 3 is enabled.
Missing combinations are omitted. Add `0` to `case_3.delays` in YAML to include
a zero-delay test; its summary row appears only when that data exists.

```bash
python3 -m test_executor.case_3_summary_table test_results/<date-time>/case_3
```

## Case 4 delay and packet loss combinations

`execute_case_4.py` reads the `case_4` YAML block. The supplied values run every
combination of delays **0, 10, 50, 100 ms**
and packet losses **0, 1, 2%**, for both h2 and h3. Each test uses a **20 KB
payload**, **5,000 requests**, **20 clients**, **10 maximum concurrent streams**,
and **five iterations**. There are 24 test combinations and 120 iterations.
Progress messages include delay, loss, and estimated remaining time.

Enable it with `case_4_enabled: true` in `env.yaml`, then run `python3 run_test.py`.
The flag defaults to false. Iteration logs and the three analysis CSVs
(`analysis.csv`, `trimmed_mean.csv`, and `percentiles.csv`) are saved inside
`test_results/<date-time>/case_4/`.

## Case 4 summary table

When case 4 is enabled, `run_test.py` calls `case_4_summary_table()` after
CSV analysis. It reads `case_4/trimmed_mean.csv` and saves
`case_4/case_4_summary_table.csv`, using semicolons.

Only **delay = 10 ms** records for **h2 and h3** are included. The first column
is `packet_loss` (percent) instead of `delay`; the case 4 sweep supplies
0, 1, and 2% loss. Rows are sorted by packet loss, then protocol. Missing
combinations are omitted. All other columns and formatting match case 3:
three decimal places and timing values in `ms`, without standard deviations.
The source CSV remains unchanged.

```bash
python3 -m test_executor.case_4_summary_table test_results/<date-time>/case_4
```

## Case 4 resource summary table

`case_4_resource_summary_table.py` adds a second table after the h2load summary
when case 4 is enabled. It reads `case_4/trimmed_mean.csv` and writes
`case_4/case_4_resource_summary_table.csv` with semicolon-separated columns:
Protocol, packet_loss, CPU Counter (ms), Memory Usage (MiB), and Network Usage (MiB).
It uses the same **10 ms delay** filter for h2 and h3, sorted by packet loss and
protocol. Resource values use three decimal places; missing or invalid values
are blank. Units are in the column headers; packet loss is percent.

CPU counter is `resource_cpu_counter_ns / 1000000`, the trimmed mean of the
final cumulative CPU time since container start, converted to milliseconds.
Memory is `resource_memory_bytes / 1048576`. Network usage is total received
plus transmitted bytes divided by 1048576. These have the same meanings as the
resource graphs. A missing network counter leaves the total blank. The first
summary and source CSV remain unchanged.

```bash
python3 -m test_executor.case_4_resource_summary_table test_results/<date-time>/case_4
```

## Case 4 summary across all delays

`case_4_all_delays_summary_table.py` adds an unfiltered h2load summary alongside
the existing 10 ms table. It reads `case_4/trimmed_mean.csv` and writes
`case_4/case_4_all_delays_summary_table.csv`. Its first columns are `delay`
(milliseconds), `packet_loss` (percent), and `Protocol`, followed by the same
h2load columns as `case_4_summary_table.py`.

All available delays for h2 and h3 are included, sorted by delay, packet loss,
and protocol. The CSV uses semicolons and preserves the summary formatting:
three decimal places and timing `ms`, without standard deviations. It runs
automatically when case 4 is enabled. The existing 10 ms summary remains
separate, and no missing combinations are invented.

```bash
python3 -m test_executor.case_4_all_delays_summary_table test_results/<date-time>/case_4
```

## Case 4 resource summary across all delays

`case_4_all_delays_resource_summary_table.py` reads `case_4/trimmed_mean.csv`
and creates `case_4/case_4_all_delays_resource_summary_table.csv` alongside
the existing 10 ms resource summary. It runs automatically when case 4 is enabled.

Columns are Protocol, delay (milliseconds), packet_loss (percent), CPU Counter
(ms), Memory Usage (MiB), and Network Usage (MiB). All available delays for h2
and h3 are included, sorted by delay, packet loss, and protocol. The table
uses the same CPU counter conversion, total received + transmitted network
usage, three decimal places, and semicolon separator as the 10 ms table.
Missing or invalid measurements remain blank. Existing tables and source data
are preserved.

```bash
python3 -m test_executor.case_4_all_delays_resource_summary_table test_results/<date-time>/case_4
```

## Averages without trimming

Run `python3 average.py test_results/<date-time>` from the project root, or
pass a single `case_N` folder. The implementation is in `test_executor/average.py`.
It reads each case's `analysis.csv` and iteration `requests.log` files and writes
`average.csv` alongside `trimmed_mean.csv`. Existing trimmed reports are preserved.

Every iteration is retained. Numeric metrics, including SD values, are averaged
with six decimal places; fixed test inputs remain unchanged. `min_dismiss` and
`max_dissmiss` are empty lists and `mean_of_iterations` lists every iteration.
P95/p99 use the existing linear interpolation within each iteration, then average
all iteration percentiles without trimming. They are not pooled-request percentiles.
Missing/invalid numeric values leave that metric blank with an analysis warning;
if any iteration's request log is invalid, the percentile averages are blank.
`run_analyse.py` also runs this exporter for every processed case after the
percentile step. The existing graphs still use trimmed means.

## H2load trimmed-versus-average comparisons

Every case now also produces `h2load_graphs/<settings>/h2load_summary_comparison.png`.
Each of its four panels compares **h2 trimmed**, **h3 trimmed**, **h2 average**,
and **h3 average**, using `trimmed_mean.csv` and `average.csv` respectively.
The original `h2load_summary.png` remains separate. Cases 1–3 retain their
payload/client/delay axes and mean-only line graphs; case 4 retains
delay/packet-loss groups with four bars per group. Metrics and units match
the original graphs, including per-client mean req/s.

Both `run_test.py` and `run_analyse.py` generate averages and these comparisons
for every selected case. To draw comparisons from existing CSVs only:

```bash
python3 -m test_executor.h2load_comparison_graph_creator test_results/<date-time>
```

A single `case_N` folder is also accepted. Both CSV files are required.

## Untrimmed percentile details and comparison graphs

`percentiles_average.csv` has the same per-iteration columns as `percentiles.csv`.
All valid iterations have `percentile_trim_role=mean_of_iterations`; neither the
minimum nor maximum p99 iteration is discarded. Missing/invalid request logs
remain visible as `excluded` with an error. Failed streams are counted separately
and do not supply latency samples. Percentiles still use linear interpolation
and milliseconds, rounded to six decimal places.

`average.py` writes this file while calculating `average.csv`, so both
`run_test.py` and `run_analyse.py` produce it for every case. It can also be
generated independently:

```bash
python3 -m test_executor.percentiles_average test_results/<date-time>
```

`percentile_summary_average_graph_creator.py` creates a two-panel p95/p99 PNG
with h2/h3 trimmed and h2/h3 average series. It reads the retained iteration
rows from `percentiles.csv` and all iteration rows from `percentiles_average.csv`,
then averages each test's percentiles. These are means of iteration percentiles,
not pooled-request percentiles. An incomplete untrimmed test is shown as a gap.
Cases 1–3 preserve their payload/client/delay axes; case 4 uses delay/loss bar
groups. Files are saved to
`case_N/percentile_graphs/<settings>/percentile_summary_average.png`.
Existing percentile graphs and trimmed exports are preserved.

Both runners generate these graphs automatically. To draw them from existing CSVs:

```bash
python3 -m test_executor.percentile_summary_average_graph_creator test_results/<date-time>
```

Both commands also accept a single `case_N` folder.

## Regenerate existing results

From the project root, pass a dated results folder to rebuild its analysis,
trimmed means, percentiles, untrimmed averages (`average.csv`), summary tables, and graphs:

```bash
python3 run_analyse.py test_results/<date-time>
```

To regenerate just one case:

```bash
python3 run_analyse.py test_results/<date-time>/case_4
```

The script discovers cases 1–4 in the supplied folder, independently of the
current `env.yaml` switches. It reads the saved iteration logs, replaces the
generated CSVs and graphs inside each case, and appends progress and errors
to the existing run's `logs.txt`. Raw iteration files are preserved. No Docker
containers or benchmarks are started. Keep the original parameter/iteration
folder structure; CSV files alone are insufficient to rebuild all reports.

## Case 4 grouped h2load bar graphs

`case_4_h2load_graph_creator.py` runs automatically after the case 4 summary
tables. It reads `case_4/trimmed_mean.csv` and creates one PNG with four panels:

- H2load req/s (Mean): `h2load_req_per_sec_mean`, the per-client mean
  from h2load's req/s statistics row, averaged across the retained iterations.
- Mean time to first byte (ms).
- Mean connection time (ms).
- Mean time for request (ms).

Each panel has paired h2 and h3 bars for every available **delay / packet-loss**
combination, sorted numerically. X-axis ticks show packet loss in percent, with a shared delay label beneath
each group. Gaps separate delay groups.
The default case 4 produces 12 groups and 24 bars per panel. Payload stays in
the subtitle. Different payloads, request counts, clients, or stream limits
get separate images so they are never mixed. All values are trimmed means;
missing or invalid values have an × marker instead of a zero-valued bar.

Output: `case_4/h2load_graphs/n<requests>-p<payload>-c<clients>-m<streams>/h2load_summary.png`.
Source CSVs remain unchanged. To regenerate without benchmarks:

```bash
python3 -m test_executor.case_4_h2load_graph_creator test_results/<date-time>/case_4
```
