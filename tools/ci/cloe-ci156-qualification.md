# CI156 Qualification and W5 Lexical Diagnostic Exit

[CI156 attempt 1](https://github.com/Knowledge-Forge-AI/common-lore-index/actions/runs/44375716879) is `EXECUTED_PASS` for staging `2572d486277e20f59b56b5cd238fbe7ddb629b01`
over public main `39cb2b3452bfd32b05d38f842c0f891614cbf58d`. The executed merge
was `f3a95573f0e5f601bcf63c724cb62355219b0a21` with tree
`0fc2e671cd9b2439ea8252d3aee3218720aa114a` and source SHA-256
`6bbd7cce8a2b30033da55a1eaa5278a4c083e291744f2280339e02e15f0ada77`.

All nine jobs passed. The same-attempt checks verified the merge parents,
canonical smoke/M/A staging obligations, native AMD and Rust reports, system
admission and export, and three first-use plus five installed host-bundle
checks. The Rust manifest covered 273 committed source members. The staging
and system ZIPs matched the GitHub artifact digests
`ffc54cf2a3fdb5fce88f611d5d58475cc4eb026535d1871e14e867ff41161b0e`
and `a4bcfa851c4f36d556e978d07761c7457d378fcb53b1a3c09568324ecd503313`.

| Population | Tests | Lines | Branches |
| --- | --- | --- | --- |
| Hosted unit | 20,695 passed, 40 skipped | 72,606 / 78,241 (92.80%) | 22,028 / 25,308 (87.04%) |
| Hosted integration M | 2,626 passed, 12 skipped | 69,797 / 78,241 (89.21%) | 20,281 / 25,308 (80.14%) |
| Hosted integration A | 5 passed, cleanup passed | Unmeasured by role | Unmeasured by role |

Both hard floors passed. The 82% integration branch target remains 472
branches short at this denominator. Cumulative application, Rust qualifier,
wheel transfer and native component execution counts are 63/35/68/33.

## PostgreSQL lexical diagnostic

The registered corpus was published into disposable SQLite and PostgreSQL
through the existing integration harness. One PostgreSQL generation held
22 lexical chunks from all 22 selected public files across two bindings. A
single bounded read applied the maintained ranked-terms predicate to those
indexed vectors for the 12 frozen questions. All 12 predicates were nonempty
and matched zero chunks. Both the web-search branch and the plain alias branch
independently matched zero chunks for every question.

The web query equalled the all-term conjunction for 11 questions. Every
question had 3–9 literal-query lexemes absent from the indexed ranked vectors
and at least two literal-query lexemes present in captured-text vectors.
One web expression differed from the plain conjunction. Its exact predicate
also matched zero chunks. These results reproduce the loss before candidate
ranking with the expected index populated. The diagnostic reads no golds,
ranks or metrics, and supplies no alternative retrieval scores.

The original [calibration result](cloe-w5-bodystudy1-exit.md) remains the
first admissible observation under ADR 0206. Its positive screen and mixed
absence controls retain their original interpretation. The evidence supports
prospective examination of comparator competence. A new evaluated treatment,
matching held-outs, scientific acceptance and production vector admission
require separate operator selection.

## Verification

One exact integration diagnostic ran through `tools/run_tests.py --suite int
--no-coverage --pg-container-port 55433 -- src/test/int/python/tools/retrieval_body_fts_census.int.test.py -s`.
The first invocation reached the disposable index and failed while reading
multiline JSON through a scalar helper. The corrected single-line JSONB
readback passed and cleaned up. The temporary diagnostic owner and exact
receipts are retained privately. Complete local suites and local coverage
were unselected. Hosted capacity remains AVAILABLE. This local checkpoint
requires no remote write or trigger.
