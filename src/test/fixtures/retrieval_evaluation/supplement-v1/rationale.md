# sup1-calibration-v1 fixture rationale

Phase CLOE-W5-SUPPLEMENT1, corpus `sup1-corpus-v1`, graph `sup1-calibration`,
generation 1, calibration only. These files are reviewed data candidates.
Nothing was executed, queried, imported or tested to write them. No
predecessor catalog, held-out value or earlier per-task rank was read or used.
The parent computes all hashes.

## Corpus

The corpus is a small synthetic seed-lending library. Each code file has
one function with at most one parameter and a leading intent comment. There
are no module docstrings, classes or decorators. Code files have no imports.
Each test file has exactly one import, of the repository-owned function it
tests. Each Markdown file has one `#` heading and one short paragraph.
Paths are binding-relative and carry no alias prefix.

| Alias | Role | Path | Definition / heading |
|---|---|---|---|
| code | code | catalog/seed_label.py | `format_seed_label(lot)` |
| code | code | catalog/germination_rate.py | `seed_germination_rate(trial)` |
| code | code | lending/checkout_limit.py | `remaining_checkout_balance(member)` |
| code | code | lending/overdue_reminder.py | `days_until_overdue_reminder(loan)` |
| code | code | storage/humidity_alert.py | `humidity_exceeds_threshold(reading)` |
| code | code | storage/packet_expiry.py | `packet_is_expired(packet)` |
| code | code | exchange/swap_balance.py | `swap_credit_balance(ledger)` |
| code | code | exchange/donation_intake.py | `normalize_donor_variety_name(name)` |
| code | code | reports/inventory_summary.py | `summarize_inventory_by_family(stock)` |
| code | code | reports/viability_forecast.py | `forecast_viable_packets(packets)` |
| denied | denied | members/contact_roster.py | `member_contact_roster(members)` |
| denied | denied | members/volunteer_shift_notes.py | `volunteer_shift_notes(shift)` |
| checks | test_source | tests/test_seed_label_format.py | `test_seed_label_format` |
| checks | test_source | tests/test_germination_rate_ratio.py | `test_germination_rate_ratio` |
| checks | test_source | tests/test_checkout_balance_limit.py | `test_checkout_balance_limit` |
| checks | test_source | tests/test_overdue_reminder_days.py | `test_overdue_reminder_days` |
| checks | test_source | tests/test_humidity_alert_threshold.py | `test_humidity_alert_threshold` |
| checks | test_source | tests/test_packet_expiry_viability_years.py | `test_packet_expiry_viability_years` |
| checks | test_source | tests/test_swap_credit_balance.py | `test_swap_credit_balance` |
| checks | test_source | tests/test_donor_variety_name_normalization.py | `test_donor_variety_name_normalization` |
| docs | design | design/storage-humidity.md | `# Storage humidity thresholds` |
| docs | design | design/overdue-reminders.md | `# Overdue reminder schedule` |
| docs | design | design/swap-credits.md | `# Swap credit rules` |
| docs | design | design/germination-reporting.md | `# Germination rate reporting` |

The corpus has 24 files: 10 public code, 2 denied code, 8 test sources and 4
design files. The two denied files contain only an inert
`SYNTHETIC-DENIED-FIXTURE` comment marker and a trivial function. They hold no
credential, identity or real data, and they are never gold targets.

Each test asserts concrete values checked by direct arithmetic inspection against the code it
tests (for example, 15 sprouted of 20 planted gives 0.75, a borrowing limit of
2 with 3 open loans clamps to 0, and `'  amish-paste '` normalizes to
`'Amish Paste'`). The tests are inert data and are never run. Their
`tests/test_NAME.py` paths are not canonical `*.test.py` owners.

## Case-to-source truth table

All golds are file-level: span, canonical key, evidence key and link kind are
null. Each gold pairs a provided binding ID with a binding-relative path.
Grades come from file content and names, never from rankings.

| Case | Kind / family | Bindings | Gold files (grade) | Distinguishing metadata |
|---|---|---|---|---|
| 01 | positive / exact_symbol | code | catalog/seed_label.py (3) | name `format_seed_label` |
| 02 | positive / partial name | code | exchange/donation_intake.py (3) | name fragment `donor_variety` |
| 03 | positive / exact_symbol | code | storage/packet_expiry.py (3) | name `packet_is_expired` |
| 04 | positive / partial name | code | lending/checkout_limit.py (3) | name suffix `checkout_balance` (path differs) |
| 05 | positive / behavior | code | storage/humidity_alert.py (3) | storage path; name has humidity, threshold |
| 06 | positive / behavior | code | exchange/swap_balance.py (3) | exchange path; name has swap, credit |
| 07 | positive / behavior | code | reports/viability_forecast.py (3) | forecast path; name has viable |
| 08 | positive / behavior | code | reports/inventory_summary.py (3) | inventory path; name has inventory, family |
| 09 | positive / code+design | code, docs | design/storage-humidity.md (3), storage/humidity_alert.py (3) | heading and name share humidity/threshold |
| 10 | positive / code+design | code, docs | design/overdue-reminders.md (3), lending/overdue_reminder.py (3) | heading and name share overdue reminder |
| 11 | positive / code+design | code, docs | design/swap-credits.md (3), exchange/swap_balance.py (3) | heading and name share swap credit |
| 12 | positive / code+design | code, docs | design/germination-reporting.md (3), catalog/germination_rate.py (3) | heading and name share germination rate |
| 13 | positive / tests | checks | tests/test_packet_expiry_viability_years.py (3) | test path/name |
| 14 | positive / tests | checks | tests/test_donor_variety_name_normalization.py (3) | test path/name |
| 15 | positive / tests | checks | tests/test_overdue_reminder_days.py (3) | test path/name |
| 16 | positive / tests | checks | tests/test_checkout_balance_limit.py (3) | test path/name |
| 17 | absent | code, checks, docs | none | no name, path, heading or body contains sms or text message |
| 18 | absent | code, checks, docs | none | no name, path, heading or body contains barcode or scanner |
| 19 | ambiguous | code | lending/checkout_limit.py (2), exchange/swap_balance.py (2) | only two code names contain balance |
| 20 | ambiguous | code | storage/packet_expiry.py (2), reports/viability_forecast.py (2) | only two code names contain packet |
| 21 | privacy | denied | none | denied binding only |
| 22 | privacy | code, denied | none | public plus denied binding |
| 23 | unsupported U1 | code | storage/humidity_alert.py (3) | body literal on line 1 comment |
| 24 | unsupported U2 | code | catalog/seed_label.py (3), catalog/germination_rate.py (3) | only two code names contain seed |

The positive gold sets are pairwise distinct. Some code files appear in two
positive families: humidity_alert in 05 and 09, and swap_balance in 06 and 11.
In each pair, one case is behavior-to-code and the other needs a separate
heading document, so the intent and gold set differ. Both ambiguous gold sets
and the U2 set differ from every positive set.

## Predicates checked by direct reading (not execution)

- Literal uniqueness inside the code binding. `seed` appears only in
  `format_seed_label` and `seed_germination_rate`. `packet` appears only in
  packet_expiry.py and viability_forecast.py. `balance` appears only in
  checkout_limit.py and swap_balance.py. `sixty percent relative humidity`
  appears only on line 1 of humidity_alert.py. Other code comments avoid these
  tokens on purpose, so the native body scan and the metadata intent agree on
  candidates for cases 19, 20, 23 and 24.
- Absent tokens. `sms`, `text message`, `barcode` and `scanner` do not occur
  in any public file's name, path, heading or body.
- Design targets. Each design gold is a standalone Markdown file whose single
  heading line carries the question's distinguishing terms. The prose only
  restates the heading, so no answer depends on prose. There is no
  frontmatter, ADR/skill-shaped path, `doc.page`-only target, or docstring
  relabelled as a document.
- Test targets. Gold tests sit under `tests/` in the `checks` binding, and
  their function names repeat the file stem.
- Email/feed path predicates. No path or alias contains mail, email, inbox,
  feed or message terms.

## Per-arm expectations (from the supplied source)

- Positives, absent and ambiguous cases (`ranked_discovery`, metadata stratum,
  intended `stored_metadata`):
  - Native completes on the captured_text surface. Every selected binding is
    body-authorized, so the effective mode becomes `literal_captured_text`,
    and the fallback (`native_git_grep_fixed_string_scan`) is required.
  - The graph uses navigation_search on the stored_metadata surface with no
    fallback.
  - FTS runs `ranked_terms` on captured_text with no fallback.
  - Both exact vectors are eligible under `_query_decision`, since `ranked_positive`
    and `absent_answer` are allowed. Fusion follows public-text-v2.
- Privacy cases 21 and 22. W5 admission runs before every callback. Declared
  binding privacy denial, regardless of body, returns refused with
  `binding_privacy_not_authorized` for all six arms. None of these cases
  relies on the unfenced native primitive's empty AVAILABLE result.
- U1 (case 23: `literal_captured_text`, body stratum):
  - Native completes with no fallback: the effective mode equals the requested
    mode on the captured surface.
  - FTS uses plan style `literal_captured_text`, so `_resolve_lexical_style_and_fallback`
    gives no fallback. This was a deliberate competent choice for an exact
    phrase; a `ranked_terms` plan would instead disclose
    `lexical_fts_ranked_terms_for_prose`.
  - The graph is unavailable with `captured_text_surface_unsupported_by_metadata_graph`.
    The existing adapter precheck looks up aliases; raw full IDs miss that lookup.
    New preparation must resolve and bind the alias view after evaluator admission;
    this data makes no claim that the old facade verified supplement policy.
  - Both vectors and fusion are unavailable with `metadata_representation_not_comparable`.
- U2 (case 24: `current_literal_enumeration`, metadata stratum):
  - The graph completes over current observations on stored_metadata.
  - Native completes with the required fallback to `literal_captured_text`.
  - FTS completes with the required fallback `lexical_captured_text_literal_scan`.
  - Both vectors and fusion are unavailable with `unsupported_semantic_query_mode`.
- Unavailable and refused arms record `actual_surface` as `none`, matching the
  envelope builders. These are capability expectations only, not expected
  hits, ranks or scores.

## Pre-extraction capacity forecast (superseded by the measured static inventory below)

- Candidate node/source pairs per representation, admitted public bindings
  only. Assume up to three nodes per code file (file, module, function), up to
  four per test file (adding one possible import reference), and up to three
  per Markdown file (file, doc.page, doc.section). That gives 10×3 + 8×4 + 4×3
  = 74. If the two denied files were counted anyway, the total would be 80,
  which equals the 80-pair ceiling without exceeding it. Actual counts come
  from existing extraction at inventory recheck.
- Semantic query records. 20 per representation: 16 positive, 2 absent and 2
  ambiguous. Privacy cases are refused and U1/U2 are unavailable. The worst
  case is 2×(74+20) = 188 combined records, under the 500-record cap.
  Repetitions reuse the prepared exchange.
- Slots. 24 tasks × 6 arms × 3 repetitions = 432.
- Size. Every file is a few hundred bytes, and every question is one sentence
  under 25 words. That suggests the content fits well within the 512-token and
  4096-byte excerpt limits. Byte size does not prove token count; tokenizer
  receipts must confirm it.

## Unknowns and exclusions

- `selected_bindings` holds the provided full `bind1:` IDs, as the shapes
  require. W5 admission and `_query_decision` accept that form. The existing
  native, FTS and graph adapters call `binding_id_for_alias` on each entry, so
  the supplement preparation seam must resolve IDs. That is implementation
  work, not something this data establishes.
- The graph plan holds one `graph_query` term per case, chosen as the most
  distinguishing name or heading token. Navigation-search tokenization and
  path matching are unverified. For two-target cases (09–12, 19, 20, 24), one
  term may surface only part of the gold set. That is a disclosed arm
  property, not a handicap.
- FTS tokenization of underscore names, such as whether `donor_variety`
  splits, is unverified.
- `expected_capability_state` is the legacy default: `available` except for
  the two privacy cases (`refused`). For U1 and U2 the per-arm expectations
  are authoritative, and this default implies nothing about unavailable arms.
- The original pre-extraction node-kind uncertainty is superseded by the
  static inventory below. Accepted-store and runtime inventories remain unproved.
- Excluded: native/runtime availability, accepted-store reachability, real
  token counts, any score or relevance forecast, and any native, scientific,
  source or publication qualification claim.


## Parent corrections before the input pin

The unchanged corpus was statically extracted and canonicalized, without any
query, target import/execution, model or accepted database. The inventory has
80 canonical nodes, 72 observed node/binding pairs including denied material,
66 public pairs as a v1 upper bound and 40 public pairs with observed spans
as a v2 upper bound. Every positive gold file has a public function/test or
heading-derived span; all four Markdown section spans are heading line 1.
Conservatively 2*(66+24)=180 records is below the unchanged 500 cap. These
are structural facts, not accepted-store/native/tokenizer evidence.

Case 20 now asks broadly which code judges packets against their viability
window, making the two equal-grade discovery targets explicit. Cases 19/20
metadata_basis contains symbol fields only; behavior is explained in gold
rationale, rather than labelled a metadata field. Case 23 intentionally keeps
the source-supported literal_captured_text FTS style and no fallback.

Positive cross-family term overlap includes 13/03, 14/02, 15/10, 16/04,
09/05 and 11/06. This is a small structured calibration corpus; the 16
positive intents and repeated measurements are not independent statistical
samples. No query plan was adjusted using results.

The expectation actual_surface is a prospective input-surface category,
not an observation or a new QueryHit enumeration. canonical-node-v1 uses
stored_metadata; public-text-v2 uses captured_text from observed spans;
fusion combines captured-text FTS with that public-text vector. The qualified
arm ID separately identifies the representation. Source excerpts supporting
the representation and fusion paths accompany this correction review.

The old FTS evaluation facade constructs build_mixed_v1_projection_policy;
it cannot establish this supplement's policy. The new preparation seam must
derive the request using the maintained lexical request/fallback primitives
and the actual frozen supplement policy. Native/graph alias views must be
bound to the original full-ID task and remain behind W5 admission. No old
adapter or kernel is changed. Actual request execution is excluded here.


## Final parent data disposition

The independent corrected review found no blocking data defect. The original
capacity forecast above is historical and superseded by the measured static
inventory. Test source files begin with imports; the leading intent-comment
description applies to code files. Correlation also includes control19 with
04/06/11 (term overlap16), control20 with03/07 (term overlap13), control23
with05/09 and control24 with01/12. None is an independent sample claim.

The maintained public_text_applicability predicate in
tools/retrieval_public_text_admission.py confirms the v2 mode/reason order:
body/captured-text intent is metadata_representation_not_comparable, then a
non-ranked_discovery mode is unsupported_semantic_query_mode. Its caller
first uses admit_w5_task and then body policy. The source implementation must
exercise that maintained predicate with distinct synthetic tests. No query
was executed to confirm these source branches. Case23 graph_query remains
absent, as permitted. The forecast's integer-year inclusive viability window
is consistent with asking about next year; no target code or gold changed.

Parent changes after review are these rationale clarifications only. Catalog
and corpus remain byte-identical to the independently reviewed corrected
candidate. The data pin is an input freeze, not source/native qualification.


## Explicit input-pin revision 2

The first input pin used repository as the policy source-kind label. The
source-binding configuration and manifest use folder. This model-free revision
corrects only that label and its policy/input digests, retaining the original
pin and all original bytes as superseded phase evidence. Catalog, queries,
golds, corpus, binding IDs, privacy and retention are byte-identical or
semantically unchanged. No query, score, model or tokenizer result was used.
The original pin is sha256:1465a62dc0fca623d024cad33aad592e3f11402dc3df2fad6575ae50c0b8256f.
Source production already underway received that old hash as metadata only,
with no actual supplement task/gold/corpus grant. No resulting source had
been adopted or qualified. Final source review and freeze bind this explicit
revision, and cannot treat the old hash as current qualification.
