# Retained supplement analysis

- Origin: `native` (native observation bound: `True`)
- Attempt identity SHA-256: `sha256:f73928db4075a96b9f0bbf797c29e70ec1bf7826c863259e8f7488b48b75347d`; input: `sha256:3c9b89c50f20b9f0fd5c85ae00a34efedde7ac7a50a8a32c8ff7623de7df26b3`
- Input pin: `sha256:c401ff63624c3ed3c0a3b28987cc3b99cacd82907f8a576d51fe56278db98b62`; evaluator closure: `sha256:970636aeb4ad332b2d30476e7383dc63d79aac45df30966995384e320412a977`
- Worker closure: `sha256:3ef59123fd9281409d42a8e0f4b02471ec3088c54429033d62cbd6b51af01e23`; analyzer source: `sha256:52fbe5ea446215b11dd3f485c5eb4b53b2f056a4ffd58849880154a53823a2b7`
- Counted worker tokens co-verified: `True`
- Grid: tasks=24 (positives=16), arms=6, repetitions=3, slots=432
- Slot states: `{'completed': 375, 'failed': 0, 'not_run': 0, 'refused': 36, 'unavailable': 21}`
- Metric namespaces: `file=available`, `span=unavailable (span_namespace_not_recorded_in_bundle)`

## Family breakdown

| Positive family | Comparable untruncated positives | Floor (>=2) |
|---|---:|---|
| `exact_symbol_and_partial_name` | 4 | met |
| `behavior_without_exact_symbol` | 4 | met |
| `code_plus_design_or_message_context` | 4 | met |
| `relevant_tests_for_behavior` | 4 | met |
| **Total comparable** | **16 / 16** | **met (>=8)** |

## Task-weighted headline metrics (common comparable positives)

| Metric | native_tools_baseline | current_graph_baseline:postgresql | postgres_native_fts:postgresql | exact_vector:canonical-node-v1 | exact_vector:canonical-node-public-text-v2 | hybrid_rrf:canonical-node-public-text-v2 |
|---|---|---|---|---|---|---|
| `file_recall@5` | 1.000000 (n=16/16) | 1.000000 (n=16/16) | 0.875000 (n=16/16) | 1.000000 (n=16/16) | 1.000000 (n=16/16) | 1.000000 (n=16/16) |
| `file_mrr@5` | 1.000000 (n=16/16) | 1.000000 (n=16/16) | 0.937500 (n=16/16) | 1.000000 (n=16/16) | 1.000000 (n=16/16) | 1.000000 (n=16/16) |
| `file_ndcg@5` | 1.000000 (n=16/16) | 1.000000 (n=16/16) | 0.889143 (n=16/16) | 1.000000 (n=16/16) | 1.000000 (n=16/16) | 1.000000 (n=16/16) |
| `file_recall@10` | 1.000000 (n=16/16) | 1.000000 (n=16/16) | 0.875000 (n=16/16) | 1.000000 (n=16/16) | 1.000000 (n=16/16) | 1.000000 (n=16/16) |
| `file_mrr@10` | 1.000000 (n=16/16) | 1.000000 (n=16/16) | 0.937500 (n=16/16) | 1.000000 (n=16/16) | 1.000000 (n=16/16) | 1.000000 (n=16/16) |
| `file_ndcg@10` | 1.000000 (n=16/16) | 1.000000 (n=16/16) | 0.889143 (n=16/16) | 1.000000 (n=16/16) | 1.000000 (n=16/16) | 1.000000 (n=16/16) |

## Task-weighted headline metrics (all observed positives per arm)

| Metric | native_tools_baseline | current_graph_baseline:postgresql | postgres_native_fts:postgresql | exact_vector:canonical-node-v1 | exact_vector:canonical-node-public-text-v2 | hybrid_rrf:canonical-node-public-text-v2 |
|---|---|---|---|---|---|---|
| `file_recall@5` | 1.000000 (n=16/16) | 1.000000 (n=16/16) | 0.875000 (n=16/16) | 1.000000 (n=16/16) | 1.000000 (n=16/16) | 1.000000 (n=16/16) |
| `file_mrr@5` | 1.000000 (n=16/16) | 1.000000 (n=16/16) | 0.937500 (n=16/16) | 1.000000 (n=16/16) | 1.000000 (n=16/16) | 1.000000 (n=16/16) |
| `file_ndcg@5` | 1.000000 (n=16/16) | 1.000000 (n=16/16) | 0.889143 (n=16/16) | 1.000000 (n=16/16) | 1.000000 (n=16/16) | 1.000000 (n=16/16) |
| `file_recall@10` | 1.000000 (n=16/16) | 1.000000 (n=16/16) | 0.875000 (n=16/16) | 1.000000 (n=16/16) | 1.000000 (n=16/16) | 1.000000 (n=16/16) |
| `file_mrr@10` | 1.000000 (n=16/16) | 1.000000 (n=16/16) | 0.937500 (n=16/16) | 1.000000 (n=16/16) | 1.000000 (n=16/16) | 1.000000 (n=16/16) |
| `file_ndcg@10` | 1.000000 (n=16/16) | 1.000000 (n=16/16) | 0.889143 (n=16/16) | 1.000000 (n=16/16) | 1.000000 (n=16/16) | 1.000000 (n=16/16) |

## Pairwise arm comparisons (`file_ndcg@10`, common comparable positives)

| Left arm | Right arm | Paired N / Positives | Excluded | Mean diff | W / T / L | Status |
|---|---|---:|---:|---:|---|---|
| `native_tools_baseline` | `current_graph_baseline:postgresql` | 16 / 16 | 0 | 0.000000 | 0 / 16 / 0 | available |
| `native_tools_baseline` | `postgres_native_fts:postgresql` | 16 / 16 | 0 | 0.110857 | 3 / 13 / 0 | available |
| `native_tools_baseline` | `exact_vector:canonical-node-v1` | 16 / 16 | 0 | 0.000000 | 0 / 16 / 0 | available |
| `native_tools_baseline` | `exact_vector:canonical-node-public-text-v2` | 16 / 16 | 0 | 0.000000 | 0 / 16 / 0 | available |
| `native_tools_baseline` | `hybrid_rrf:canonical-node-public-text-v2` | 16 / 16 | 0 | 0.000000 | 0 / 16 / 0 | available |
| `current_graph_baseline:postgresql` | `postgres_native_fts:postgresql` | 16 / 16 | 0 | 0.110857 | 3 / 13 / 0 | available |
| `current_graph_baseline:postgresql` | `exact_vector:canonical-node-v1` | 16 / 16 | 0 | 0.000000 | 0 / 16 / 0 | available |
| `current_graph_baseline:postgresql` | `exact_vector:canonical-node-public-text-v2` | 16 / 16 | 0 | 0.000000 | 0 / 16 / 0 | available |
| `current_graph_baseline:postgresql` | `hybrid_rrf:canonical-node-public-text-v2` | 16 / 16 | 0 | 0.000000 | 0 / 16 / 0 | available |
| `postgres_native_fts:postgresql` | `exact_vector:canonical-node-v1` | 16 / 16 | 0 | -0.110857 | 0 / 13 / 3 | available |
| `postgres_native_fts:postgresql` | `exact_vector:canonical-node-public-text-v2` | 16 / 16 | 0 | -0.110857 | 0 / 13 / 3 | available |
| `postgres_native_fts:postgresql` | `hybrid_rrf:canonical-node-public-text-v2` | 16 / 16 | 0 | -0.110857 | 0 / 13 / 3 | available |
| `exact_vector:canonical-node-v1` | `exact_vector:canonical-node-public-text-v2` | 16 / 16 | 0 | 0.000000 | 0 / 16 / 0 | available |
| `exact_vector:canonical-node-v1` | `hybrid_rrf:canonical-node-public-text-v2` | 16 / 16 | 0 | 0.000000 | 0 / 16 / 0 | available |
| `exact_vector:canonical-node-public-text-v2` | `hybrid_rrf:canonical-node-public-text-v2` | 16 / 16 | 0 | 0.000000 | 0 / 16 / 0 | available |

## Controls

| Control kind | Tasks | Slots | States | Safety refusal (T/F/U) | Absent answer (T/F/U) | Capability match (T/F) |
|---|---:|---:|---|---|---|---|
| `absent` | 2 | 36 | `{'completed': 36}` | 0/0/36 | 18/18/0 | 36/0 |
| `ambiguous` | 2 | 36 | `{'completed': 36}` | 0/0/36 | 0/0/36 | 36/0 |
| `privacy` | 2 | 36 | `{'refused': 36}` | 36/0/0 | 0/0/36 | 36/0 |
| `unsupported` | 2 | 36 | `{'completed': 15, 'unavailable': 21}` | 0/0/36 | 0/0/36 | 36/0 |

## Layered costs

Phase records: 432 (shared unallocated: 0); phases: `['query']`; unknown elapsed: 0; unknown field samples: 0.
Shared costs are retained once, never allocated to arms; layers are not additive.

| Arm | Query status | Elapsed N / Obs | Unknown | Min ns | Mean ns | Max ns |
|---|---|---:|---:|---:|---:|---:|
| `native_tools_baseline` | available | 72 / 72 | 0 | 108557836 | 117877298.111111 | 160917332 |
| `current_graph_baseline:postgresql` | available | 72 / 72 | 0 | 111319466 | 121434017.569444 | 161007001 |
| `postgres_native_fts:postgresql` | available | 72 / 72 | 0 | 110602249 | 120737819.097222 | 141185442 |
| `exact_vector:canonical-node-v1` | available | 72 / 72 | 0 | 55493877 | 300475623.833333 | 574982859 |
| `exact_vector:canonical-node-public-text-v2` | available | 72 / 72 | 0 | 55417667 | 347362872.083333 | 630021938 |
| `hybrid_rrf:canonical-node-public-text-v2` | available | 72 / 72 | 0 | 55501294 | 200916564.791667 | 253438661 |

## Limitations

- `repetitions_share_embeddings_not_independent_samples`
- `descriptive_paired_observation_only_no_significance_claim`
- `unknown_values_are_never_zero`
- `shared_costs_not_allocated_and_layers_not_additive`
- `physical_postgresql_and_rss_costs_unknown`
- `small_calibration_set_no_generalization_claim`

## Decision boundary

Operator criteria decision: pending. No threshold or preferred arm is selected.
Scientific, held-out, full-W5, production and native-platform acceptance: false.
Predecessor failure identities retained, never pooled: `['sha256:89e6d755fe74eacb890f2bdffd186e969fb081697e2b4e63b9222f6747d80651']`.
