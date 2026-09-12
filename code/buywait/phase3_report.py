from __future__ import annotations

from pathlib import Path
from collections import Counter

from .lifecycle import resolve_lifecycles
from .loaders import load_dataset, repo_root_from_here
from .snapshot import validate_snapshots_for_all_requests


def build_report() -> str:
    dataset = load_dataset()
    snapshots = validate_snapshots_for_all_requests(dataset)
    result = resolve_lifecycles(dataset.events, messages=dataset.messages)
    stats = result.statistics()
    raw_status_counts = Counter(event.status.value for event in dataset.events)
    snapshot_classes: Counter[str] = Counter()
    snapshot_unresolved_linked = 0
    snapshot_internal_transfer_neutral = 0
    for snapshot in snapshots:
        snapshot_result = resolve_lifecycles(
            snapshot.user_events,
            request_date=snapshot.request.request_date,
            messages=tuple(item.message for item in snapshot.messages),
        )
        for effective in snapshot_result.effective_events:
            snapshot_classes[effective.event_class.value] += 1
            if effective.event_class.value == "internal_transfer_neutral":
                snapshot_internal_transfer_neutral += 1
        snapshot_unresolved_linked += sum(1 for component in snapshot_result.unresolved_components if component.has_link)

    lines = [
        "# Phase 3 Lifecycle Report",
        "",
        "This report is structural only. It does not perform recurrence inference, forecasting, headroom, planning, or output generation.",
        "",
        "## Snapshot Validation",
        "",
        f"- evaluation request snapshots built: {len(snapshots)}",
        f"- requests with payment options: {sum(1 for snapshot in snapshots if snapshot.payment_options)}",
        f"- snapshots retaining at least one message: {sum(1 for snapshot in snapshots if snapshot.messages)}",
        f"- snapshots retaining at least one image mapping: {sum(1 for snapshot in snapshots if snapshot.images)}",
        "",
        "## Snapshot Effective Classification Totals",
        "",
    ]
    for key, value in sorted(snapshot_classes.items()):
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            f"- unresolved linked components across request snapshots: {snapshot_unresolved_linked}",
            f"- internal transfer neutralizations across request snapshots: {snapshot_internal_transfer_neutral}",
            "",
        ]
    )
    lines.extend(
        [
            "## Lifecycle Statistics",
            "",
            "Raw event status counts:",
            "",
        ]
    )
    for key, value in sorted(raw_status_counts.items()):
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "Resolved lifecycle counts:",
            "",
        ]
    )
    for key, value in stats.items():
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## Unresolved Linked Components",
            "",
        ]
    )
    unresolved = [component for component in result.unresolved_components if component.has_link]
    if not unresolved:
        lines.append("- none")
    else:
        for component in unresolved[:50]:
            lines.append(f"- {'|'.join(component.source_event_ids)}")
        if len(unresolved) > 50:
            lines.append(f"- ... {len(unresolved) - 50} additional unresolved linked components omitted")
    return "\n".join(lines) + "\n"


def write_report(path: Path | None = None) -> Path:
    target = path or (repo_root_from_here() / "evaluation" / "phase3_lifecycle_report.md")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_report(), encoding="utf-8", newline="\n")
    return target


def main() -> None:
    path = write_report()
    print(path)


if __name__ == "__main__":
    main()
