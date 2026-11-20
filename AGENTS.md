# Agent entry point

Read [agent.md](agent.md) in full, then [docs/roadmap.md](docs/roadmap.md) and the
relevant skill in [skills/README.md](skills/README.md). They define the objective,
supported scope, confidentiality rules, and development on `dev` with regression
promotion to `main`.

Before investigating, editing, building, testing, or committing anything, always
synchronize the Git repository first (`git fetch origin --tags --prune` and
`git pull --ff-only` on the active branch when the working tree is clean; if
local changes or divergence exist, stop and reconcile cleanly without discarding
work or force-pushing).
