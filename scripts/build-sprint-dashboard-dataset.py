#!/usr/bin/env python3

import argparse
import csv
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional


DONE_STATUS = {"done", "complete", "completed"}
DONE_STATES = {"CLOSED", "MERGED"}

ADD_TYPES = {"add_unplanned", "add_split", "carry_in"}
REMOVE_TYPES = {"remove_scope", "carry_out", "split_parent_reduce"}
REESTIMATE_TYPES = {"reestimate", "point_adjustment"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build sprint dashboard dataset from GitHub Project export + baseline/events ledgers."
    )
    parser.add_argument(
        "--project-csv",
        default="/Users/sivensadiyan/manatime/docs/briefs/mana-soft-project-1-items.csv",
    )
    parser.add_argument(
        "--baseline-csv",
        default="/Users/sivensadiyan/manatime/docs/briefs/data/sprint-baseline.csv",
    )
    parser.add_argument(
        "--events-csv",
        default="/Users/sivensadiyan/manatime/docs/briefs/data/sprint-scope-events.csv",
    )
    parser.add_argument(
        "--output-json",
        default="/Users/sivensadiyan/manatime/docs/briefs/data/sprint-dashboard-dataset.json",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=2026,
        help="Filter sprints by iteration start year.",
    )
    return parser.parse_args()


def parse_float(value: str, fallback: float = 0.0) -> float:
    if value is None:
        return fallback
    text = str(value).strip()
    if text == "":
        return fallback
    try:
        return float(text)
    except ValueError:
        return fallback


def parse_bool(value: str) -> bool:
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "oui"}


def parse_date(value: str) -> Optional[date]:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) >= 10:
        text = text[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def quarter_for(month: int) -> str:
    if month <= 3:
        return "Q1"
    if month <= 6:
        return "Q2"
    if month <= 9:
        return "Q3"
    return "Q4"


def sprint_score(attainment_adjusted: float) -> int:
    if attainment_adjusted < 60:
        return 1
    if attainment_adjusted < 75:
        return 2
    if attainment_adjusted < 90:
        return 3
    if attainment_adjusted < 100:
        return 4
    return 5


def load_csv_rows(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def build_project_index(project_rows: List[Dict[str, str]], year: int):
    sprints = {}
    sprint_issue_keys = defaultdict(set)
    issue_index = {}

    for row in project_rows:
        if row.get("item_type") != "Issue":
            continue
        iteration = (row.get("iteration") or "").strip()
        start_text = (row.get("iteration_start_date") or "").strip()
        if not iteration or not start_text.startswith(str(year)):
            continue

        start = parse_date(start_text)
        if not start:
            continue

        duration = int(parse_float(row.get("iteration_duration"), 14.0) or 14)
        if duration <= 0:
            duration = 14
        end = start + timedelta(days=duration - 1)

        sprint_key = f"{iteration}|{start.isoformat()}"
        if sprint_key not in sprints:
            sprints[sprint_key] = {
                "iteration": iteration,
                "start_date": start,
                "end_date": end,
            }

        repository = (row.get("repository") or "").strip()
        number = (row.get("number") or "").strip()
        if not repository or not number:
            continue
        issue_key = f"{repository}#{number}"

        points = parse_float(row.get("points_value"), 1.0)
        if points <= 0:
            points = 1.0

        added_at = parse_date(row.get("iteration_added_at"))
        planned = bool(added_at and added_at < start)

        completion_date = parse_date(row.get("completion_date"))
        closed_date = parse_date(row.get("issue_closed_at"))
        done_date = completion_date or closed_date

        state = (row.get("state") or "").strip().upper()
        status = (row.get("status") or "").strip().lower()
        done = state in DONE_STATES or status in DONE_STATUS or done_date is not None

        # Conservatively keep done_date empty when unknown so it does not inflate historical completion.
        issue_index[issue_key] = {
            "points": points,
            "done": done,
            "done_date": done_date,
            "state": state,
            "status": status,
            "iteration": iteration,
            "planned": planned,
            "added_at": added_at.isoformat() if added_at else "",
        }
        sprint_issue_keys[sprint_key].add(issue_key)

    ordered_keys = sorted(sprints.keys(), key=lambda key: sprints[key]["start_date"])
    sprint_id_by_key = {}
    sprint_defs = []
    for idx, key in enumerate(ordered_keys, start=1):
        sprint_id = f"S{idx:02d}"
        sprint_id_by_key[key] = sprint_id
        sprint_defs.append(
            {
                "sprint_id": sprint_id,
                "sprint_key": key,
                "iteration": sprints[key]["iteration"],
                "start_date": sprints[key]["start_date"],
                "end_date": sprints[key]["end_date"],
                "quarter": quarter_for(sprints[key]["start_date"].month),
                "issues": sorted(sprint_issue_keys[key]),
            }
        )

    return sprint_defs, issue_index


def load_baseline(
    baseline_rows: List[Dict[str, str]],
    sprint_defs: List[Dict],
    issue_index: Dict[str, Dict],
):
    baseline_by_sprint = defaultdict(dict)
    warnings = []

    if baseline_rows:
        for row in baseline_rows:
            sprint_id = (row.get("sprint_id") or "").strip()
            issue_key = (row.get("issue_key") or "").strip()
            if not sprint_id or not issue_key:
                continue
            committed_points = parse_float(row.get("committed_points"), 0.0)
            if committed_points <= 0:
                committed_points = issue_index.get(issue_key, {}).get("points", 1.0)
            baseline_by_sprint[sprint_id][issue_key] = committed_points
        baseline_mode = "snapshot"
    else:
        baseline_mode = "derived_from_iteration_added_at"
        warnings.append(
            "Baseline CSV is empty. Baseline was derived with rule: issue added before sprint start => planned; issue added on/after start => unplanned."
        )
        for sprint in sprint_defs:
            sprint_id = sprint["sprint_id"]
            for issue_key in sprint["issues"]:
                info = issue_index.get(issue_key, {})
                if info.get("planned"):
                    baseline_by_sprint[sprint_id][issue_key] = info.get("points", 1.0)

    return baseline_by_sprint, baseline_mode, warnings


def load_events(events_rows: List[Dict[str, str]]):
    events_by_sprint = defaultdict(list)
    for row in events_rows:
        sprint_id = (row.get("sprint_id") or "").strip()
        if sprint_id:
            events_by_sprint[sprint_id].append(row)
    return events_by_sprint


def build_rows(
    sprint_defs: List[Dict],
    issue_index: Dict[str, Dict],
    baseline_by_sprint: Dict[str, Dict[str, float]],
    events_by_sprint: Dict[str, List[Dict[str, str]]],
):
    rows = []
    total_unknown_scope_points = 0.0

    for sprint in sprint_defs:
        sprint_id = sprint["sprint_id"]
        sprint_end = sprint["end_date"]
        sprint_issues = set(sprint["issues"])
        baseline_issues = baseline_by_sprint.get(sprint_id, {})
        baseline_issue_keys = set(baseline_issues.keys())

        baseline_points = sum(baseline_issues.values())
        delivered_committed_points = 0.0
        delivered_committed_count = 0

        for issue_key, committed_points in baseline_issues.items():
            info = issue_index.get(issue_key)
            if not info or not info["done"]:
                continue
            done_date = info["done_date"]
            if done_date and done_date <= sprint_end:
                delivered_committed_points += committed_points
                delivered_committed_count += 1

        scope_added = 0.0
        scope_removed = 0.0
        reestimate_delta = 0.0
        unplanned_bug_points = 0.0
        event_issue_keys = set()

        auto_unplanned_issue_keys = {
            key for key in sprint_issues if not issue_index.get(key, {}).get("planned", False)
        }
        auto_unplanned_points = sum(issue_index.get(key, {}).get("points", 1.0) for key in auto_unplanned_issue_keys)
        scope_added += auto_unplanned_points

        for event in events_by_sprint.get(sprint_id, []):
            event_type = (event.get("event_type") or "").strip().lower()
            delta = parse_float(event.get("points_delta"), 0.0)
            issue_key = (event.get("issue_key") or "").strip()
            is_bug = parse_bool(event.get("is_bug")) or "bug" in event_type
            event_issue_keys.add(issue_key)

            if event_type in ADD_TYPES:
                points = abs(delta) if delta != 0 else issue_index.get(issue_key, {}).get("points", 1.0)
                scope_added += points
                if is_bug:
                    unplanned_bug_points += points
            elif event_type in REMOVE_TYPES:
                points = abs(delta) if delta != 0 else issue_index.get(issue_key, {}).get("points", 1.0)
                scope_removed += points
            elif event_type in REESTIMATE_TYPES:
                reestimate_delta += delta

        unknown_scope_issue_keys = sprint_issues - baseline_issue_keys - event_issue_keys - auto_unplanned_issue_keys
        unknown_scope_points = sum(issue_index.get(key, {}).get("points", 1.0) for key in unknown_scope_issue_keys)
        total_unknown_scope_points += unknown_scope_points

        adjusted_commitment = max(0.0, baseline_points + scope_added - scope_removed + reestimate_delta)
        attainment_baseline = (delivered_committed_points / baseline_points * 100.0) if baseline_points > 0 else 0.0
        attainment_adjusted = (
            delivered_committed_points / adjusted_commitment * 100.0 if adjusted_commitment > 0 else 0.0
        )
        churn_points = scope_added + scope_removed + abs(reestimate_delta)
        churn_pct = (churn_points / baseline_points * 100.0) if baseline_points > 0 else 0.0

        score = sprint_score(attainment_adjusted)
        notes = (
            f"{sprint['iteration']} | planned={len(baseline_issue_keys)}, unplanned={len(auto_unplanned_issue_keys)}, "
            f"delivered_planned={delivered_committed_count}, unknown_scope_points={round(unknown_scope_points, 2)}"
        )

        rows.append(
            {
                "id": sprint_id.replace("S", ""),
                "sprint": sprint_id,
                "quarter": sprint["quarter"],
                "startDate": sprint["start_date"].isoformat(),
                "endDate": sprint["end_date"].isoformat(),
                "committed": round(baseline_points, 2),
                "delivered": round(delivered_committed_points, 2),
                "scopeAdded": round(scope_added, 2),
                "scopeRemoved": round(scope_removed, 2),
                "reestimateDelta": round(reestimate_delta, 2),
                "adjustedCommitted": round(adjusted_commitment, 2),
                "attainmentAdjusted": round(attainment_adjusted, 2),
                "churnPct": round(churn_pct, 2),
                "unplannedBugPoints": round(unplanned_bug_points, 2),
                "unplannedScopePoints": round(auto_unplanned_points, 2),
                "plannedIssueCount": len(baseline_issue_keys),
                "unplannedIssueCount": len(auto_unplanned_issue_keys),
                "unknownScopePoints": round(unknown_scope_points, 2),
                "framing": score,
                "slicing": score,
                "arbitration": score,
                "prioritization": score,
                "helping": score,
                "notes": notes,
            }
        )

    return rows, total_unknown_scope_points


def main():
    args = parse_args()
    project_csv = Path(args.project_csv)
    baseline_csv = Path(args.baseline_csv)
    events_csv = Path(args.events_csv)
    output_json = Path(args.output_json)

    project_rows = load_csv_rows(project_csv)
    baseline_rows = load_csv_rows(baseline_csv)
    events_rows = load_csv_rows(events_csv)

    sprint_defs, issue_index = build_project_index(project_rows, args.year)
    baseline_by_sprint, baseline_mode, baseline_warnings = load_baseline(baseline_rows, sprint_defs, issue_index)
    events_by_sprint = load_events(events_rows)

    rows, unknown_scope_points = build_rows(sprint_defs, issue_index, baseline_by_sprint, events_by_sprint)

    payload = {
        "meta": {
            "generatedAt": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
            "projectCsv": str(project_csv),
            "baselineCsv": str(baseline_csv),
            "eventsCsv": str(events_csv),
            "year": args.year,
            "baselineMode": baseline_mode,
            "totalSprints": len(rows),
            "unknownScopePoints": round(unknown_scope_points, 2),
            "assumptions": baseline_warnings
            + [
                "Planned rule: iteration_added_at < sprint_start_date. Added on/after start is unplanned scope.",
                "Delivered committed points count an issue only if its done date exists and is <= sprint end date.",
                "When points are missing, 1 point per issue is used.",
            ],
        },
        "rows": rows,
    }

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    print(f"Wrote {len(rows)} sprint rows to {output_json}")


if __name__ == "__main__":
    main()
