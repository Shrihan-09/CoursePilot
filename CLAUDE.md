# CoursePilot - standing instructions

## Program requirement sources
- Before encoding any program or curriculum version, find its official department/school requirements page in addition to its official catalog requirements. Archive both and any delegated requirement documents with URL, UTC retrieval time, exact saved-byte SHA-256, representation and archive path; label extraction-only captures explicitly.
- Preserve and show source disagreements with both claims, provenance and applicability. Never silently select a source or correct an apparent typo. Record unresolved material conflicts for review before publication.
- Model curriculum applicability explicitly, including Industrial Engineering's distinct classes 2026–2029 and class 2030 curricula. Keep governing curriculum class year separate from a mutable graduation target; do not extrapolate to unlisted classes.
- Phase A program expansion is research/documentation only until Phase B is authorized. See docs/investigations/phase-08a-department-source-conflicts.md and its paired-source/conflict manifests.

## Git workflow
- After every commit, push the current branch to origin immediately (set upstream on first push).
- Never leave commits unpushed at the end of a task.
- Never push private data: data/private/, anything from C:\coursepilot-private, *.har, *state*.json, or real student documents. The pre-commit guard must pass.
- Never force-push to main.
- When a task is finished, report the branch name and the pushed commit hash.

## Pull requests
- Do all work on a feature branch, never directly on main.
- When a task is finished (or at each STOP point in a prompt), push the branch and open a pull request into main with `gh pr create`. Give it a clear title and a body covering: what changed, why, tests run and results, migrations, anything I need to decide or check, and any data/backfill steps.
- NEVER merge PRs yourself, never merge into main locally, and never enable auto-merge. I review and merge every PR manually on GitHub.
- If a PR for the branch is already open, push new commits to it and add a comment summarizing them instead of opening a new one.
- Start the next task's branch from the latest main after I've merged. If my merge hasn't happened yet, ask me before branching off an unmerged branch.
- Report the PR URL at the end of every task.
