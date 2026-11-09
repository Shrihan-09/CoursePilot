# CoursePilot - standing instructions

## Archive and cleanup safety
- Never delete anything under `data/raw/` or `C:\coursepilot-private\`. This includes deletion through a parent folder, test teardown, cleanup scripts, `git clean`, or pruning.
- Temp cleanup may touch only the exact folders created by the current task. Verify their resolved absolute paths before cleanup; never traverse junctions or symlinks into protected paths. If a folder's ownership or target is unclear, stop and report it.
- Create new test and scratch output in a unique system-temp folder outside OneDrive. Never point pytest `--basetemp` or another cleanup-capable tool at an existing data or archive folder.
- Gitignored archives are not backed up by Git. Preserve their local and OneDrive copies; never treat ignored files as disposable.

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
