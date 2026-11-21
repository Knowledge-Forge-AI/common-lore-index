# W5 prospective public lexical comparator v1

[ADR 0212](../adr/2026/11/0212-w5-prospective-lexical-comparator.md)
selects this source and competence phase. `public-lexical-comparator-v1`
identifies the complete planning rule below. The historical calibration and
its lexical request treatment remain linked to ADR 0206.

## Query planning

The caller declares the input shape before execution. The planner receives
a validated lexical request with one or more explicit source bindings and
retains its path scope, request mode and result bound. The PostgreSQL service
still requires body authorization and fences with the existing live policy
and accepted generation checks. Each request has an offset of zero and a
result limit between one and ten. This bounds returned hits independently of
the existing 5,000-candidate budget and statement timeout. The planner
requires nonempty UTF-8 text of at most 256 bytes, without NUL or fallback
disclosure. A refusal identifies the applicable rule without echoing text.

| Input shape | Required service style | Preparation |
| --- | --- | --- |
| `question` | `ranked_terms` | Select terms and join them with uppercase ` OR `. |
| `identifier_or_path` | `ranked_terms` | Preserve the exact request. Use the service's existing identifier and path aliases. |
| `literal` | `literal_captured_text` | Preserve the exact request, including punctuation, case and whitespace. |

Question inputs accept printable ASCII English only. Lowercase, then tokenize
maximal contiguous ASCII alphanumeric runs. Keep complete words of 3–32
characters beginning with a letter. Drop the function words below, retain the
first occurrence of each remaining word, and select at most the first four
in input order. Refuse when there are no terms. Insert only the fixed
separator. Quotes and hyphens act as token separators, so the planner retains
eligible words without interpreting quotes or applying negation. Prefixes,
dictionary expansion and corpus-derived weighting are outside this rule. The unchanged service
parses the result using `websearch_to_tsquery` with `pg_catalog.simple` and
keeps its normal ranked-terms alias branch.

```text
a about am an and are as at be been being but by can could did do does for
from had has have how i if in into is it its may might must no nor not of on
or our shall should so than that the their them then there these they this
those through to under up upon us was we were what when where which who whom
why will with would you your
```

## Frozen developer competence checks

The fixture creates new synthetic documents under `competence/` in the
existing disposable PostgreSQL publication harness. File and query witnesses
were authored for this contract before proof. No body study file, question,
gold, score, rank or selectivity signal supplies a witness or planning term.
Check capability state independently of hit presence.

| Witness | Expected observation |
| --- | --- |
| Question with `heliotrope` and `copper` in disjoint files | Both public files returned through the disjunction. The original conjunctive request has zero hits in scope. |
| Explicit `RelayQueueMarker` against separated words | The identifier target is returned through the existing alias branch. |
| Explicit `EXACT.FRAME/17` | Only the verbatim target is returned, excluding the punctuation near-match. |
| Question with no matching term | An available result with an empty hit set on the accepted generation. |
| Shared `amber` term and two plausible files | Both files are observed without forcing a winner. Repeated request locators are stable. |
| Twelve `garnet` matches | The ten-hit request respects its result bound and reports an incomplete result page. |
| Public companion document shares a question term | A request scoped to the core binding does not return the companion locator. |
| No generation and privacy-reclassified graph | Projection absence remains unavailable. A private graph is refused before the connection factory is reached. |
| Empty terms, non-ASCII question, invalid bounds and shape mismatches | The planner refuses without echoing the query. |

## Future protocol proposal

Before comparing evaluated retrieval, register the comparator and exact
code, tool and runtime digests with the approved corpus identity, ordered
questions, predeclared input shapes, backend and scope bindings. Freeze the
competence checks and complete observation schedule before exposure.
Declare the prior exposure to the original calibration. Any comparison on
that cohort is secondary evidence and cannot replace the primary ADR 0206
observation.
Record admission, projection absence, privacy refusal, candidate absence,
pagination and actual rankings separately. Use the same publication pairing,
file identity and interpretation across compared arms. Prespecify scoring,
family floors, absence and ambiguity controls and failure disposition before
running any evaluated query. Operator review owns that protocol, its
eligible data classes and scientific decision criteria. The source phase
authorizes disposable developer proof only.
