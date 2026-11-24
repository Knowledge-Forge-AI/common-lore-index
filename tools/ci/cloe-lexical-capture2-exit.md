# CLOE-LEXICAL-CAPTURE2 Bounded Capture and Alias Exit

Direct filesystem capture enumerates candidates in relative-path order.
At each level it sorts sibling names, closes the directory handle and opens
a subtree when traversal reaches it. Binding selection and pre-open file
checks apply before any body read. A checked descriptor reads up to the
selected file cap plus one overflow-detection byte, including across file
growth after the initial size check.

The 2,051-entry fixture kept its full candidate-order and admission digest.
The first candidate required 17 directory scans before
the change and 1 afterward. Peak traced allocation up
to that candidate fell from 1,610,865
to 10,644 bytes. PostgreSQL
capture with a tight aggregate limit left the later subtree unopened.

Alias extraction recognizes acronym boundaries with local character checks
and processes repeated raw terms once. All 16,807 generated
cases preserved alias order and values. On the synthetic 8,191-byte long
acronym input, the median of five calls went from 345.6 ms to
0.44 ms. A retained-publication ranked query found the expected
split terms from the new fixture.

The combined frozen source passed 33 scoped unit and 32 scoped containerized integration cases. The retained Python, file-length, compilation, and Git diff checks passed. The integration snapshot shares exact product and integration inputs with the final candidate. The final unit and static runs include the reviewed unit-test refinements.
