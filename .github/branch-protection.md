# Branch Protection Setup

Configure the following on the `main` branch in **Settings → Branches → Branch protection rules**:

| Setting | Value |
|---------|-------|
| Require status checks to pass before merging | ✅ |
| Required status checks | `Lint (ruff)` and `Tests pass` |
| Require branches to be up to date before merging | ✅ |
| Do not allow bypassing the above settings | ✅ (recommended) |

> **Why `Tests pass` and not the individual matrix jobs?**
> The `test` job uses a Python version matrix, which creates separate check entries
> per version. The `tests-pass` job depends on all of them and presents a single
> required check — easier to manage and automatically covers new versions added to
> the matrix later.
