# Positive-control validation artifact (temporary — NOT for merge)

This file exists only to exercise the repository's governance controls with a harmless,
documentation-only change. It touches no runtime code, no tests, and no CI workflow.

Expected behaviour: the full CI matrix (`test (3.10)` … `test (3.13)`) and the fail-closed
aggregate `test` check all pass, and the pull request becomes mergeable through the normal
path with no administrator override.

This pull request is closed after evidence capture and is never merged.
