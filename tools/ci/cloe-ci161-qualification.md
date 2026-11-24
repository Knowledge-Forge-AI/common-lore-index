# CI161 Split Staging and Installed Qualification

[CI161 attempt 1](https://github.com/Knowledge-Forge-AI/common-lore-index/actions/runs/44650267036)
qualified public staging `d056f6fbb4b18ee1a12c0da51b1207cb9659fc69` over
public main `39cb2b3452bfd32b05d38f842c0f891614cbf58d`. GitHub ran merge
`a06b7b026ac21a12b069f5ccc401fa683b7a8437` with tree
`f56802af8dff420926ec291ede6cdfd06a404c33`. All 14 jobs passed.
The retained staging, native AMD, Rust and system archive digests matched
GitHub metadata and their records bound the same attempt and tree.

The three measured shards and one abrupt shard overlapped after smoke.
The longest shard took 97 minutes and 45 seconds. The stable aggregate
accepted 2,655 measured and five abrupt selected cases with terminal
cleanup evidence. The coverage results were:

| Gate | Lines or statements | Branches |
| --- | ---: | ---: |
| Python unit | 72,756/78,399 (92.80%) | 22,076/25,360 (87.05%) |
| Python integration | 69,950/78,399 (89.22%) | 20,327/25,362 (80.15%) |
| Go integration | 574/653 (87.90%) | — |

The unit job reported 20,776 passes and 40 skips. Integration passed its hard
coverage floor and remained 470 covered branches below the 82% target.

All five retained installed bundles passed the existing offline checker at
the exact GitHub merge source census. They cover the environment, Local,
MCP, configuration migration and provisioned Server scenarios. This retains
the ADR0181 installed diagnostic scope. The public release stays v0.0.4.

CI161 consumed one application start, one Rust qualifier entry, two pinned
wheel transfers and one native component execution. The cumulative spent
totals are 68, 40, 78 and 38. A changed candidate needs its own binding.
