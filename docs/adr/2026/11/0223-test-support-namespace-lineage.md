# ADR 0223: Preserve ownership through the CLOE test-support namespace move

## Status

Proposed under ADR 0222. The source cutover is approved. This record
defines how to verify the support namespace move before adoption.

## Context

The legacy support package owns 464 maintained Python paths across
existing cohorts. Thirty-four living files also appear as historical
decomposition origins. Thirty-three active dynamic declarations have changed source witnesses or
namespace references, including thirteen with the old spelling in the ledger.
Two more Rust guest declarations share a witness that must load the verified
lineage before isolating its synthetic declarations. These records serve
static enforcement and qualification. Rewriting the
old records obscures which source and checks were actually accepted.

## Decision

Use a single verified forward projection for the test-support package
from repomap_test_support to common_lore_index_test_support. Bind its
predecessor revision and census to Git. Require precisely one successor
at the same relative path within the new package for every predecessor
member. Keep existing cohort IDs, owners, profiles, admitted status, and
historical records. Apply the mapping only to the current identity of
a living owner. Existing decomposition edges keep their historical
meaning for earlier revisions.

For affected dynamic declarations, append superseding records whose
old/new owner and test paths are verified by the exact mapping or
stay byte-bound at their original path. The candidate validator must
independently derive the same operator, symbols and target set. Verify
recipe parameters after the closed path and digest projection. Ordinary
dynamic changes continue under the existing validation rules.

Use canonical imports for maintained test and qualification callers.
Keep test-case filenames and collection selectors stable. Maintain
closed receipts and frozen scientific evidence at their published
digests. Align the active AMD Rust qualification manifest with its new
tracked source closure at the next qualified checkpoint.

### Closed evidence transition

The verifier and its scoped tests may land while dormant. The package move,
receipt, collection and manifest evidence follow in separate checkpoints.

At the migration checkpoint, introduce a separate ADR 0223 projection artifact. It binds the tracked
predecessor census and source revision, the exact new package census,
and the expected current membership. The retired root must be absent in
the candidate. The artifact has its own authority identifier and cannot
share source keys with the ADR 0123 product projection. Accept its single Git introduction
and its direct parent before using the committed candidate
for qualification. Worktree
verification is provisional and verifies the same current bytes. Preserve
that introduction and its parent when publishing, with full Git history.
The verifier refuses a missing or rewritten receipt. A deliberate reversal
requires a new forward disposition.

Keep the dynamic record schema and existing immutable prefix. Add one
projection-specific supersession rule, allowed only for the thirty-five
identified active records under the verified move. Ordinarily a
supersession still requires both evidence sources to change at the same
paths. In the projection branch the paths must exactly match the sealed
move, or stay at the identical path when the file is outside the moved
namespace. Equal digests are allowed only where verified bytes really
match. Derive each new operation fingerprint from the actual source and
check it against the same operation mapped through the permitted import
rewrite. Verify owner and test bytes against both source revisions. The
only non-namespace witness change is the exact Rust guest reader and
synthetic history preparation in the shared guest test. The guarded
resolver's literal target domain is sorted again after the namespace
projection because the module prefix changes its lexical position.
Compare the exact targets and typed recipe parameters after only the
separately specified path fields have been mapped and each
digest verified. Never carry authority in arbitrary parameters.
Independently resolve the complete resulting declarations and
require that all predecessors are retired only by their unique verified
successors.

Path history and cohort transition read the same map. Both check
successors separately from historical decompositions so a previously
accepted split cannot lose any child. Carry every old member to a live
member of its original cohort with the same enforcement. Existing
cohort roots are logical profile names and no root lies below the moved
package. Confirm that fact from the actual predecessor. Test config
keys and any applicable path-based limits migrate exactly, with no
lower protection or unmatched strict override.

The tracked moved package contains only Python files. Verify the entire
tree, including non-Python entries, against that statement. Preserve
sealed evidence, fixtures and held-out material at their current paths
and bytes. No working test fixture or private payload is in the moved
scope. Record deliberate historical exceptions by catalog or blob
identity. Compare pytest node IDs for both normal and abrupt
subprocess selections.

Keep the existing Rust qualification manifest as a historical artifact.
A successor requires a versioned manifest introduced after the renamed
source checkpoint. Qualify only committed candidate blobs under the
same limit fields and independently validated closure. The existing
binding refuses the incompatible closure until the new manifest is
ready. The active workflow switch and any native qualification retain
their existing approval and campaign boundaries.

## Alternatives

Keeping the old import or an alias would introduce a second active
package identity. A path-only rename with no ownership transfer would
lose retained obligations. The exact projection costs one verifier
and explicit negative proof. It preserves a single active package
and enforces every admitted obligation on its successor.

## Verification

Compare old and new tracked file censuses and measured and abrupt
test collection identities. Exercise representative imports and all
affected dynamic boundary recipes. Refuse missing, duplicate, crossing
or forged paths, digest mismatches, changed target semantics, and
cohort downgrade. Preserve all existing debt ceilings and historical
receipts. Test static and integration checks on the exact candidate
before staging, with product artifacts and study execution subject to
their existing authorizations.
