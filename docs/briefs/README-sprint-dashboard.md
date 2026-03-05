# Sprint Dashboard - Shared Workflow

## Recommended setup

Use a static shared dashboard hosted on GitHub Pages and keep data in versioned files:

- Dashboard UI: `docs/briefs/annual-sprint-objective-dashboard.html`
- Raw Project export: `docs/briefs/mana-soft-project-1-items.csv`
- Baseline ledger: `docs/briefs/data/sprint-baseline.csv`
- Scope events ledger: `docs/briefs/data/sprint-scope-events.csv`
- Generated dataset for UI: `docs/briefs/data/sprint-dashboard-dataset.json`

This gives:

- One URL for everyone (read-only dashboard)
- Clear audit trail through Git history
- Reproducible KPI calculations
- Automatic hourly refresh through GitHub Actions

## Data rules

- Planned issue: `iteration_added_at < sprint_start_date`
- Unplanned issue: `iteration_added_at >= sprint_start_date`
- Baseline (committed scope): from `sprint-baseline.csv` if provided, otherwise auto-derived using the planned rule above.
- Scope churn: additions/removals/re-estimation from `sprint-scope-events.csv` + auto-detected unplanned scope.

## Refresh commands

```bash
GH_TOKEN=... node /Users/sivensadiyan/manatime/scripts/fetch-github-project-items.mjs mana-soft 1 /Users/sivensadiyan/manatime/docs/briefs/mana-soft-project-1-items.csv
python3 /Users/sivensadiyan/manatime/scripts/build-sprint-dashboard-dataset.py
python3 - <<'PY'
import json
src='/Users/sivensadiyan/manatime/docs/briefs/data/sprint-dashboard-dataset.json'
dst='/Users/sivensadiyan/manatime/docs/briefs/mana-soft-project-1-dashboard-seed.json'
d=json.load(open(src))
json.dump(d['rows'], open(dst,'w'), ensure_ascii=False, indent=2)
print('seed updated')
PY
```

## Team process

1. Before sprint starts, freeze commitments in `sprint-baseline.csv`.
2. During sprint, append scope changes in `sprint-scope-events.csv`.
3. Refresh dataset and commit.
4. Team views one shared dashboard URL.

## GitHub Actions

- Hourly refresh workflow: `.github/workflows/sprint-dashboard-refresh.yml`
- Pages deploy workflow: `.github/workflows/sprint-dashboard-pages.yml`

Required secret:

- `MANA_SOFT_GH_TOKEN` with access to `mana-soft` project/repositories (`read:project`, `read:org`, `repo` for private data)
