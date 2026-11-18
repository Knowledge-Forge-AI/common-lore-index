# W5 Captured-Text Calibration Exit

The observation met the prospective descriptive screen for fusion.

Attempt 5 is the first complete admissible observation for the fixed 24-case
captured-text class. The two sequential model workers processed 134 records.
The study accounted for 432 planned slots, of which
432 were observed and 0
were unstarted. The common set was frozen before ranking. It contains
12 distinct intents across the registered families.
The approved minimums are eight distinct intents and two per family.

| Comparator | nDCG@10 gain | MRR@10 gain | Recall@10 gain |
| --- | ---: | ---: | ---: |
| `native_tools_baseline` | 0.167632 | 0.192130 | 0.083333 |
| `postgres_native_fts:postgresql` | 0.910890 | 0.881944 | 1.000000 |

The [observation](cloe-w5-bodystudy1-observation.json) records all case and slot
states, the separate controls, analytic base rates, family and paired findings
where available, and the original outcome `calibration_signal_present`. The repetitions reuse
the generated representations, so they are descriptive observations.

The first three attempts closed with proven ownership and cleanup. The first
stopped on a PostgreSQL socket and bind-host configuration mismatch. The second
stopped before launch of either worker because it published readiness before
the required preparation acknowledgement. The third completed both model
workers and stopped at the admission projection join before the common freeze
or any retrieval slot. The repair checks the projection against the full
admission digest and shared field values. The fourth produced 219 quality observations before a metadata-only
applicability guard blocked the ready captured-text v2 cases and left fusion
without its paired input. Its independent rank-blind review classified the
result as technically incomplete. All four originals were retained separately
from the primary observation.

The primary attempt consumed one native ARM SDK, one retained runtime import,
and one PostgreSQL lifecycle. It charged 12,008,527,666 controlled
read bytes and 520,767,233 transfer bytes. Model load and
inference timings and token measures are in the observation. Physical OS and
PostgreSQL resource costs were unmeasured. No image build, pull, runtime
materialization, or runtime export was used by the study.

Scoped proof used `tools/run_tests.py --suite unit --no-coverage --`.
Unit proof passed 73 cases across:

- `src/test/unit/python/tools/retrieval_body_study_host_evidence.unit.test.py`
- `src/test/unit/python/tools/retrieval_body_study_host_report.unit.test.py`
- `src/test/unit/python/tools/retrieval_body_study_report.unit.test.py`
- `src/test/unit/python/tools/retrieval_body_study_slots.unit.test.py`
- `src/test/unit/python/tools/retrieval_body_study_report_transport.unit.test.py`
- `src/test/unit/python/tools/retrieval_body_study_report_custody.unit.test.py`
- `src/test/unit/python/tools/retrieval_body_study_schedule.unit.test.py`
- `src/test/unit/python/tools/retrieval_body_study_journal.unit.test.py`

The containerized `--suite int --no-coverage` proof passed in the owned
disposable sandbox. It verified the joined identities for all 24 PostgreSQL
admission decisions and both preparation handshakes.

- `src/test/int/python/tools/retrieval_body_study_registered_inner.int.test.py`
- `src/test/int/python/tools/retrieval_body_study_slot_boundaries.int.test.py`

The complete static enforcement profile passed across its six Linux roots.
Compile, Git diff, and independent read-only source review passed. Publication
removed trailing blank lines from 13 Python files. The reviewed byte bridge
verified that the Python ASTs remained identical to the native candidate. Complete
local runtime suites and local coverage were unselected. Hosted qualification
for this committed candidate remains pending. CI155 integration branch coverage
was 80.10%, above the 80% gate and 481 branches short of the 82% aim at its
observed denominator.

Public CI remains operator-set AVAILABLE. CI156 attempt 1 is reserved for
one staging synchronize after the committed manifest and export checks and
independent publication review. CI156 cumulative reserved counters are
63 application starts, 35 Rust entries, 68 wheel transfers and 33 component
executions. Scientific acceptance, held-outs, production admission and full W5
remain separate.
