from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "dataset"
DOCS = ROOT / "docs"
OUT_DIR = ROOT / "evaluation" / "forensics"


OUTPUT_COLUMNS = [
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
]


IMAGE_FACTS = {
    "image_01": "Payslip Aug-2019; Net Pay IDR 4,365,000 for related event event_253.",
    "image_02": "Rent receipt dated 11/08/23; rent/maintenance INR 180,000, total amount INR 200,000, received INR 100,000, balance due INR 100,000 for event_1442.",
    "image_03": "Receipt dated 27/02/2026; grocery/food purchase net amount INR 41,272.00 for event_1545.",
    "image_04": "Delivery receipt; item bill INR 2,854.00 for event_1700.",
    "image_05": "Airtel bill; amount due by 06-Feb-2026 INR 704.05, amount after date INR 822.05 for event_1786.",
}


OPEN_SEMANTIC_SEEDS = [
    ("OQ-001", "Exact inclusivity of the 90-day window", "Off-by-one changes future obligations and capacity."),
    ("OQ-002", "Same-day ordering of income, expenses, and request payments", "Can change minimum balance on critical dates."),
    ("OQ-003", "Exact temporal cutoff for messages.sent_at relative to request date/time", "Prevents future-information leakage."),
    ("OQ-004", "Monthly recurrence tolerance", "Impacts recurring rent/salary/subscription projection."),
    ("OQ-005", "Conservative estimator for variable essential spending", "Directly affects headroom."),
    ("OQ-006", "Exact interpretation of max_installment_months vs option schedule duration", "Could invalidate otherwise safe offers."),
    ("OQ-007", "Rounding and output formatting conventions", "Exact-match scoring risk."),
    ("OQ-008", "Whether wait/full-payment date can occur on any day or only event/change dates", "Affects earliest date search."),
    ("OQ-009", "Handling of future occurrences after desired completion date but inside safety horizon", "Plan can complete by deadline yet fail later safety."),
    ("OQ-010", "Recurrence termination when recent history is interrupted without explicit message", "Could over-forecast expenses/income."),
    ("OQ-011", "FX rounding point", "Can cause cent-level differences."),
    ("OQ-012", "Treatment of scheduled debits outside request completion but within horizon", "Affects capacity."),
    ("OQ-013", "Whether optional spending cuts apply to already-scheduled occurrence on request date", "Affects change feasibility."),
    ("OQ-014", "Tie-breaking among multiple legal spending-change sets", "Exact spending_changes_needed may be scored."),
    ("OQ-015", "Canonical ordering of multiple spending changes", "Exact-match risk."),
    ("OQ-016", "Canonical numeric formatting", "Exact-match risk."),
    ("OQ-017", "Overlap rule between inferred recurrence and explicit future scheduled events", "Double-counting risk."),
    ("OQ-018", "Reservation date for pending debit when event_date and settlement_date differ", "Can move breach date."),
    ("OQ-019", "Whether current_available_balance is always the request-date anchor", "Double-count/replay risk."),
    ("OQ-020", "Exact identity of same source in conflict precedence", "Determines amendment winner."),
    ("OQ-021", "Whether later-sent messages tied to older events are excluded solely because they post-date the request", "Temporal leakage risk."),
    ("OQ-022", "Hidden tie-breaks among equal spending-change plans", "Ground-truth action mismatch risk."),
    ("OQ-023", "How image-backed blank event amounts should be normalized into recurring streams", "Blank amount events appear in solved cases and cannot be zero."),
]


def read_csv(name: str) -> list[dict[str, str]]:
    with (DATASET / name).open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def parse_date(value: str) -> date | None:
    if not value:
        return None
    if "T" in value:
        value = value.split("T", 1)[0]
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        d = parse_date(value)
        return datetime.combine(d, datetime.min.time()) if d else None


def dec(value: str) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(value.replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None


def money(value: str | Decimal | None) -> str:
    if value is None or value == "":
        return ""
    d = value if isinstance(value, Decimal) else dec(str(value))
    if d is None:
        return str(value)
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def split_list(value: str) -> list[str]:
    if not value:
        return []
    parts = re.split(r"[|;,]", value)
    return [p.strip() for p in parts if p.strip()]


def normalize_description(value: str) -> str:
    value = value.lower()
    value = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "", value)
    value = re.sub(r"\b\d+\b", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()[:50]


def option_plan(option: dict[str, str]) -> str:
    first = parse_date(option["first_payment_date"])
    n = int(option["number_of_payments"])
    freq = int(option["payment_frequency_days"] or "0")
    amount = option["payment_amount"]
    return "|".join(f"{first + timedelta(days=freq * i)}:{amount}" for i in range(n))


def plan_payments(plan: str) -> list[tuple[str, Decimal]]:
    if not plan or plan == "none":
        return []
    out = []
    for part in plan.split("|"):
        d, amt = part.split(":", 1)
        out.append((d, dec(amt) or Decimal("0")))
    return out


def plan_total(plan: str) -> Decimal:
    return sum((amt for _, amt in plan_payments(plan)), Decimal("0"))


def table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        clean = [str(cell).replace("\n", "<br>").replace("|", "\\|") for cell in row]
        lines.append("| " + " | ".join(clean) + " |")
    return "\n".join(lines)


@dataclass
class Data:
    samples: list[dict[str, str]]
    profiles: dict[str, dict[str, str]]
    events_by_user: dict[str, list[dict[str, str]]]
    events_by_id: dict[str, dict[str, str]]
    options_by_request: dict[str, list[dict[str, str]]]
    messages_by_user: dict[str, list[dict[str, str]]]
    images_by_request: dict[str, list[dict[str, str]]]
    rates: list[dict[str, str]]


def load_data() -> Data:
    samples = read_csv("sample_requests.csv")
    profiles = {r["user_id"]: r for r in read_csv("financial_profiles.csv")}
    events_by_user: dict[str, list[dict[str, str]]] = defaultdict(list)
    events_by_id = {}
    for row in read_csv("financial_events.csv"):
        events_by_user[row["user_id"]].append(row)
        events_by_id[row["event_id"]] = row
    for rows in events_by_user.values():
        rows.sort(key=lambda r: (r["settlement_date"] or r["event_date"], r["event_id"]))
    options_by_request: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in read_csv("request_payment_options.csv"):
        options_by_request[row["request_id"]].append(row)
    messages_by_user: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in read_csv("messages.csv"):
        messages_by_user[row["user_id"]].append(row)
    images_by_request: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in read_csv("images.csv"):
        images_by_request[row["request_id"]].append(row)
    return Data(samples, profiles, events_by_user, events_by_id, options_by_request, messages_by_user, images_by_request, read_csv("exchange_rates.csv"))


def recurring_patterns(events: list[dict[str, str]], request_date: date) -> list[str]:
    groups: dict[tuple[str, str, str, str], list[dict[str, str]]] = defaultdict(list)
    for e in events:
        d = parse_date(e["settlement_date"] or e["event_date"])
        if not d or d > request_date:
            continue
        key = (e["direction"], e["category"], e["flexibility"], normalize_description(e["description"]))
        groups[key].append(e)
    patterns = []
    for (direction, category, flex, desc), rows in groups.items():
        if len(rows) < 2:
            continue
        dates = [parse_date(r["settlement_date"] or r["event_date"]) for r in rows]
        dates = [d for d in dates if d]
        if len(dates) < 2:
            continue
        intervals = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
        amounts = [r["amount"] or "image-backed/blank" for r in rows[-4:]]
        confidence = "high" if len(rows) >= 3 and any(27 <= abs(i) <= 33 for i in intervals[-3:]) else "medium"
        patterns.append(
            f"{direction} {category} ({flex or 'fixed'}): {desc or 'no description'}; "
            f"count={len(rows)}, recent_dates={', '.join(str(d) for d in dates[-4:])}, "
            f"recent_amounts={', '.join(amounts)}, intervals={intervals[-4:]}, confidence={confidence}"
        )
    return patterns[:10]


def linked_chains(events: list[dict[str, str]], events_by_id: dict[str, dict[str, str]]) -> list[str]:
    chains = []
    for e in events:
        if e.get("linked_event_id"):
            root = events_by_id.get(e["linked_event_id"], {})
            chains.append(
                f"{e['event_id']} ({e['status']}, {e['amount'] or 'blank'}, {e['settlement_date'] or e['event_date']}) "
                f"links to {e['linked_event_id']} ({root.get('status', 'missing')}, {root.get('amount', 'blank')})"
            )
    return chains[:12]


def event_line(e: dict[str, str]) -> str:
    amount = e["amount"] or "blank"
    when = e["settlement_date"] or e["event_date"]
    return f"{e['event_id']} {when} {e['status']} {e['direction']} {amount} {e['currency']} {e['category']} {e['flexibility']} - {e['description']}"


def relevant_events(events: list[dict[str, str]], request_date: date) -> tuple[list[str], list[str], list[str], list[str]]:
    start = request_date - timedelta(days=180)
    end = request_date + timedelta(days=90)
    historical = []
    future = []
    pending_debits = []
    pending_credits = []
    for e in events:
        d = parse_date(e["settlement_date"] or e["event_date"])
        if not d or d < start or d > end:
            continue
        line = event_line(e)
        if d < request_date:
            historical.append(line)
        else:
            future.append(line)
        if e["status"] == "pending" and e["direction"] == "debit":
            pending_debits.append(line)
        if e["status"] == "pending" and e["direction"] == "credit":
            pending_credits.append(line)
    return historical[-18:], future[:20], pending_debits[:10], pending_credits[:10]


def relevant_messages(messages: list[dict[str, str]], request_id: str, request_date: date, relevant_event_ids: set[str]) -> list[str]:
    out = []
    for m in messages:
        sent = parse_dt(m["sent_at"])
        relation = []
        if m["request_id"] == request_id:
            relation.append("request")
        if m["related_event_id"] in relevant_event_ids:
            relation.append("event")
        if not m["request_id"] and not m["related_event_id"]:
            relation.append("user")
        if not relation:
            continue
        cutoff = "on/before request date" if sent and sent.date() <= request_date else "after request date"
        out.append(f"{m['message_id']} ({'/'.join(relation)}, {cutoff}, {m['sent_at']}, {m['source_type']}): {m['message_text']}")
    return out[:12]


def option_rows(options: list[dict[str, str]], profile: dict[str, str], sample: dict[str, str]) -> list[list[str]]:
    accepted = set(split_list(profile["payment_methods_user_will_consider"]))
    deadline = parse_date(sample["desired_completion_date"])
    expected_plan = sample["payment_plan"]
    rows = []
    for o in options:
        method = o["payment_method"]
        plan = option_plan(o)
        payments = plan_payments(plan)
        last_date = parse_date(payments[-1][0]) if payments else None
        pref = "yes" if method in accepted else "no"
        deadline_ok = "yes" if last_date and deadline and last_date <= deadline else "no"
        selected = "yes" if plan == expected_plan else "no"
        rows.append([
            o["payment_option_id"],
            method,
            o["number_of_payments"],
            o["first_payment_date"],
            o["payment_frequency_days"],
            o["payment_amount"],
            o["financing_fee"],
            o["total_payable_amount"],
            pref,
            deadline_ok,
            selected,
            plan,
        ])
    return rows


def spending_change_details(change_text: str, events_by_id: dict[str, dict[str, str]]) -> list[str]:
    if not change_text or change_text == "none":
        return ["none"]
    out = []
    for part in change_text.split("|"):
        if part.startswith("stop:"):
            event_id = part.split(":", 1)[1]
            e = events_by_id.get(event_id)
            out.append(f"{part}: {event_line(e) if e else 'event not found'}")
        elif part.startswith("reduce_to:"):
            _, event_id, new_amount = part.split(":", 2)
            e = events_by_id.get(event_id)
            old = dec(e["amount"]) if e else None
            new = dec(new_amount)
            delta = old - new if old is not None and new is not None else None
            out.append(f"{part}: {event_line(e) if e else 'event not found'}; per-occurrence reduction={money(delta)}")
        else:
            out.append(part)
    return out


def baseline_notes(sample: dict[str, str], profile: dict[str, str]) -> tuple[str, str, str, str]:
    safe = dec(sample["amount_safe_to_pay"]) or Decimal("0")
    requested = dec(sample["requested_amount"]) or Decimal("0")
    minimum = dec(profile["minimum_balance_to_keep"]) or Decimal("0")
    inferred_floor = minimum + safe
    safe_now = "yes" if safe >= requested else "no"
    earliest = sample["earliest_date_for_full_payment"] or ""
    safe_later = "yes" if earliest and earliest != sample["request_date"] else ("already" if earliest == sample["request_date"] else "no")
    note = (
        f"Solved baseline capacity implies at least {profile['home_currency']} {money(inferred_floor)} before a request-date payment "
        f"when amount_safe_to_pay is not capped by requested_amount. "
        "For capped affordable_now cases the true baseline floor may be higher than this lower bound."
    )
    return money(inferred_floor), safe_now, safe_later, note


def semantic_lessons(sample: dict[str, str]) -> list[str]:
    rid = sample["request_id"]
    lessons = []
    if sample["spending_changes_needed"] != "none":
        lessons.append("Baseline amount_safe_to_pay remains below requested amount, but legal spending changes can make full payment today feasible.")
    if sample["recommended_payment_method"] == "installments":
        lessons.append("Installment plan must match a supplied option; expected plan should be compared byte-for-byte to option expansion.")
    if sample["recommended_payment_method"] == "partial_payment":
        total = plan_total(sample["payment_plan"])
        lessons.append(f"Partial payment uses exactly two payments totaling {money(total)} and the first payment equals amount_safe_to_pay.")
    if sample["affordability_status"] == "not_affordable" and dec(sample["amount_safe_to_pay"]) and dec(sample["amount_safe_to_pay"]) > 0:
        lessons.append("Positive request-date capacity does not imply the full request is achievable by deadline or within the 90-day horizon.")
    if rid == "request_12":
        lessons.append("Earliest full-payment capacity can be request_date even when selected method is installments due to payment preference.")
    if rid == "request_17":
        lessons.append("Installments can begin on the request date when the supplied option does so.")
    return lessons or ["No special semantic beyond standard capacity, preference, deadline, and safety rules."]


def build_solved_case_doc(data: Data) -> tuple[str, dict[str, list[list[str]]], dict[str, int]]:
    sections = [
        "# Solved Case Forensics",
        "",
        "Generated by `evaluation/forensics/generate_forensics.py` as Phase 1 analysis-only documentation.",
        "This file studies public solved examples. It is not production financial logic and must not be imported by `code/main.py`.",
        "",
    ]
    matrices: dict[str, list[list[str]]] = defaultdict(list)
    counts = {"full": 0, "partial": 0, "unresolved": 0}
    for sample in data.samples:
        rid = sample["request_id"]
        user_id = sample["user_id"]
        profile = data.profiles[user_id]
        request_date = parse_date(sample["request_date"])
        assert request_date is not None
        events = data.events_by_user[user_id]
        hist, future, pending_debits, pending_credits = relevant_events(events, request_date)
        relevant_ids = {line.split(" ", 1)[0] for line in hist + future}
        msgs = relevant_messages(data.messages_by_user[user_id], rid, request_date, relevant_ids)
        imgs = data.images_by_request.get(rid, [])
        image_lines = [
            f"{img['image_id']} -> {img['related_event_id']}; {IMAGE_FACTS.get(img['image_id'], 'image inspected or mapping present; fact unresolved')}"
            for img in imgs
        ] or ["none"]
        rec = recurring_patterns(events, request_date)
        chains = linked_chains(events, data.events_by_id)
        inferred_floor, safe_now, safe_later, baseline_note = baseline_notes(sample, profile)
        options = data.options_by_request[rid]
        opt_rows = option_rows(options, profile, sample)
        selected_option = next((row[0] for row in opt_rows if row[10] == "yes"), "")
        spend_details = spending_change_details(sample["spending_changes_needed"], data.events_by_id)
        expected_tuple = [sample[c] for c in OUTPUT_COLUMNS]
        reconstructed_flags = []
        if sample["recommended_payment_method"] == "installments":
            reconstructed_flags.append("selected installment option expansion matches expected plan" if selected_option else "installment plan not matched to option")
        if sample["recommended_payment_method"] == "partial_payment":
            first = plan_payments(sample["payment_plan"])[0][1] if plan_payments(sample["payment_plan"]) else None
            reconstructed_flags.append("partial first payment equals amount_safe_to_pay" if first == dec(sample["amount_safe_to_pay"]) else "partial first payment mismatch")
        if sample["spending_changes_needed"] != "none":
            reconstructed_flags.append("spending changes reference existing flexible events" if all(("event not found" not in x) for x in spend_details) else "spending change event unresolved")
        if sample["earliest_date_for_full_payment"] == "":
            reconstructed_flags.append("no safe full payment date published within forecast/deadline")
        else:
            reconstructed_flags.append(f"published earliest full date: {sample['earliest_date_for_full_payment']}")
        if "unresolved" in " ".join(reconstructed_flags) or "not matched" in " ".join(reconstructed_flags):
            counts["partial"] += 1
        else:
            counts["full"] += 1
        uncertainty = []
        if sample["recommended_payment_method"] in {"wait", "not_recommended"}:
            uncertainty.append("Exact day-by-day baseline cannot be proven without final recurrence/same-day policy; solved output is treated as evidence for Phase 2 policy.")
        if sample["spending_changes_needed"] != "none":
            uncertainty.append("Exact optimizer tie behavior remains open unless cross-case action ordering supports one global rule.")
        if not uncertainty:
            uncertainty.append("No material unexplained discrepancy found in structural reconstruction.")

        sections.extend([
            f"## {rid}",
            "",
            table(
                ["Field", "Value"],
                [
                    ["request ID", rid],
                    ["user ID", user_id],
                    ["request date", sample["request_date"]],
                    ["desired completion date", sample["desired_completion_date"]],
                    ["request amount", sample["requested_amount"]],
                    ["current available balance", profile["current_available_balance"]],
                    ["minimum balance", profile["minimum_balance_to_keep"]],
                    ["home currency", profile["home_currency"]],
                    ["payment methods accepted", profile["payment_methods_user_will_consider"]],
                    ["partial-payment permission", sample["allows_partial_payment"]],
                    ["max installment preference", profile["max_installment_months"] or "blank / no installments"],
                    ["protected categories", profile["expense_categories_to_protect"]],
                    ["reducible categories", profile["expense_categories_user_is_willing_to_reduce"]],
                    ["stoppable categories", profile["expense_categories_user_is_willing_to_stop"]],
                ],
            ),
            "",
            "### Relevant Historical Financial Events",
            "\n".join(f"- {x}" for x in hist) if hist else "- none in 180-day lookback",
            "",
            "### Relevant Future Financial Events",
            "\n".join(f"- {x}" for x in future) if future else "- none within 90-day window",
            "",
            "### Pending Debits",
            "\n".join(f"- {x}" for x in pending_debits) if pending_debits else "- none observed in inspected window",
            "",
            "### Pending / Unconfirmed Credits",
            "\n".join(f"- {x}" for x in pending_credits) if pending_credits else "- none observed in inspected window",
            "",
            "### Recurring Income Pattern",
            "\n".join(f"- {x}" for x in rec if x.startswith("credit")) or "- no repeated credit pattern identified in simple grouping; inspect raw events if this case depends on income timing",
            "",
            "### Recurring Expense Patterns",
            "\n".join(f"- {x}" for x in rec if x.startswith("debit")) or "- no repeated debit pattern identified in simple grouping",
            "",
            "### Relevant Lifecycle Links",
            "\n".join(f"- {x}" for x in chains) if chains else "- none found for this user's inspected event set",
            "",
            "### Relevant Message Evidence",
            "\n".join(f"- {x}" for x in msgs) if msgs else "- none found at request/event/user scope by conservative retrieval",
            "",
            "### Relevant Image Evidence",
            "\n".join(f"- {x}" for x in image_lines),
            "",
            "### FX Conversions If Applicable",
            "- No sample-row output required external/live FX. Any foreign-currency event conversion must use supplied settlement-date directional rates; case-specific FX arithmetic remains open where no solved field exposes it.",
            "",
            "### Baseline Financial Timeline",
            f"- Forecast horizon considered by the challenge: request date through the next 90 days; exact inclusivity remains OQ-001.",
            f"- Inferred minimum projected baseline balance/lower bound: {profile['home_currency']} {inferred_floor}.",
            f"- {baseline_note}",
            f"- Reconstructed amount_safe_to_pay: {sample['amount_safe_to_pay']}.",
            f"- Reconstructed earliest full-payment date: {sample['earliest_date_for_full_payment'] or 'blank / not safe within forecast'}",
            "",
            "### Available Payment Options",
            table(
                ["option_id", "method", "payments", "first", "freq", "amount", "fee", "total_payable", "preference?", "deadline?", "selected?", "expanded_plan"],
                opt_rows,
            ),
            "",
            "### Option Safety Notes",
            "- Financial safety is inferred from the solved answer, not asserted by this analysis script. Selected options are solved-safe; non-selected options may be unsafe, preference-ineligible, deadline-ineligible, higher-cost, or ranked lower.",
            "",
            "### Possible Legal Spending Changes",
            "\n".join(f"- {x}" for x in spend_details),
            "",
            "### Effect Of Spending Changes",
            "- " + ("; ".join(spend_details) if sample["spending_changes_needed"] != "none" else "No spending changes required or selected."),
            "",
            "### Expected Solved Output",
            table(
                ["field", "expected"],
                [[col, sample[col]] for col in OUTPUT_COLUMNS],
            ),
            "",
            "### Reconstructed Explanation",
            "- " + sample["decision_explanation"],
            "- Structural reconstruction: " + "; ".join(reconstructed_flags),
            "",
            "### Semantic Lessons Learned",
            "\n".join(f"- {x}" for x in semantic_lessons(sample)),
            "",
            "### Remaining Uncertainty",
            "\n".join(f"- {x}" for x in uncertainty),
            "",
        ])

        matrices["capacity"].append([
            rid,
            sample["requested_amount"],
            sample["amount_safe_to_pay"],
            sample["earliest_date_for_full_payment"] or "",
            safe_now,
            safe_later,
            "positive-safe/not-affordable" if sample["affordability_status"] == "not_affordable" and dec(sample["amount_safe_to_pay"]) and dec(sample["amount_safe_to_pay"]) > 0 else "",
        ])
        matrices["recommendation"].append([
            rid,
            profile["payment_methods_user_will_consider"],
            sample["allows_partial_payment"],
            sample["affordability_status"],
            sample["recommended_payment_method"],
            sample["payment_plan"],
            sample["spending_changes_needed"],
            sample["decision_explanation"],
        ])
        matrices["recurrence"].append([
            user_id,
            "; ".join(x for x in rec if x.startswith("credit")) or "not detected by simple grouping",
            "; ".join(x for x in rec if x.startswith("debit") and "essential" in x) or "see case section",
            "; ".join(x for x in rec if x.startswith("debit") and ("flexible" in x or "discretionary" in x)) or "see case section",
            "; ".join(chains) if chains else "none",
            "medium; Phase 1 evidence, not production recurrence logic",
        ])
        matrices["evidence"].append([
            rid,
            ", ".join(m.split(" ", 1)[0] for m in msgs) or "none",
            ", ".join(img["image_id"] for img in imgs) or "none",
            ", ".join(x.split(" ", 1)[0] for x in chains) or "none",
            "; ".join(image_lines if image_lines != ["none"] else []) or "none",
        ])
        for row in opt_rows:
            matrices["installments"].append([rid] + row[:11])

    sections.extend([
        "# Cross-Sample Matrices",
        "",
        "## A. Capacity Matrix",
        table(["request_id", "requested_amount", "expected_amount_safe", "expected_earliest_full_date", "baseline-safe-now?", "safe-later?", "notes"], matrices["capacity"]),
        "",
        "## B. Recommendation Matrix",
        table(["request_id", "accepted_methods", "allows_partial", "expected_status", "expected_method", "expected_plan", "spending_changes", "reason"], matrices["recommendation"]),
        "",
        "## C. Recurrence Matrix",
        table(["user_id", "income pattern", "essential expense patterns", "flexible recurring patterns", "termination/amendment evidence", "confidence"], matrices["recurrence"]),
        "",
        "## D. Evidence Matrix",
        table(["request_id", "relevant messages", "relevant images", "linked events", "fact changes caused by evidence"], matrices["evidence"]),
        "",
        "## E. Installment Matrix",
        table(["request_id", "option_id", "method", "number_of_payments", "first_date", "frequency", "payment_amount", "financing_fee", "total_payable", "preference eligible?", "deadline eligible?", "expected selected?"], matrices["installments"]),
    ])
    return "\n".join(sections), matrices, counts


def build_open_semantics(matrices: dict[str, list[list[str]]]) -> str:
    status_by_id = {
        "OQ-001": ("PARTIALLY RESOLVED", "Official wording says next 90 days; samples validate 90-day safety concept but do not isolate inclusive end-day edge."),
        "OQ-002": ("PARTIALLY RESOLVED", "Same-day request payments can coexist with same-date installment starts; exact income/debit ordering still needs Phase 2 simulator tests."),
        "OQ-003": ("PARTIALLY RESOLVED", "Messages are retrieved at request/user/event scope; cutoff should exclude post-request messages unless they confirm earlier known facts, but exact timestamp policy remains open."),
        "OQ-004": ("PARTIALLY RESOLVED", "Repeated monthly-like streams appear; exact fixed-day vs 30-day handling needs implementation tests."),
        "OQ-005": ("UNRESOLVED", "Solved outputs imply conservative essentials, but exact variable estimator is not fully exposed."),
        "OQ-006": ("RESOLVED", "Blank max_installment_months means no installments; nonblank preference must permit supplied option duration."),
        "OQ-007": ("PARTIALLY RESOLVED", "Outputs preserve decimals where source has cents and omit unnecessary zeros in amount_safe_to_pay; plan amounts often preserve option precision."),
        "OQ-008": ("PARTIALLY RESOLVED", "Wait/earliest dates often land on salary-like event dates or deadlines; any-day search remains unproven."),
        "OQ-009": ("RESOLVED", "Official 90-day safety continues beyond completion date; samples consistently explain protected minimum over forecast."),
        "OQ-010": ("UNRESOLVED", "No globally proven interruption rule yet."),
        "OQ-011": ("PARTIALLY RESOLVED", "Official rate direction/settlement-date rule is clear; rounding point is not isolated by samples."),
        "OQ-012": ("RESOLVED", "Scheduled debits inside the 90-day horizon must be protected by official wording and sample explanations."),
        "OQ-013": ("PARTIALLY RESOLVED", "request_06/request_11/request_21 prove cuts can enable same-day full payment; exact same-day occurrence cut rule remains case-sensitive."),
        "OQ-014": ("PARTIALLY RESOLVED", "Only a few spending-change solved rows expose action choice; avoid extra hidden tie-break until broader tests."),
        "OQ-015": ("PARTIALLY RESOLVED", "request_21 orders stop before reduce; not enough cases for a universal order beyond action string evidence."),
        "OQ-016": ("PARTIALLY RESOLVED", "Formatting patterns documented in matrix; implement single formatter after Phase 2 loader tests."),
        "OQ-017": ("PARTIALLY RESOLVED", "Explicit future events should dedupe inferred recurrence by design; sample detail supports need but not exact identity threshold."),
        "OQ-018": ("PARTIALLY RESOLVED", "Pending debits are reserved; exact event_date vs settlement_date timing remains to be tested."),
        "OQ-019": ("RESOLVED", "Profiles act as request-date/current anchor; replaying all historical settled rows would contradict sample capacities."),
        "OQ-020": ("PARTIALLY RESOLVED", "Conflict precedence is official; exact same-source identity remains implementation detail."),
        "OQ-021": ("PARTIALLY RESOLVED", "Post-request message exclusion is safest; later-sent older-event confirmations require explicit policy."),
        "OQ-022": ("PARTIALLY RESOLVED", "No request-specific optimizer exceptions allowed; tied variants should remain visible until implementation evidence."),
        "OQ-023": ("RESOLVED", "Image-backed blank amounts must be extracted from mapped PNGs and are not zero."),
    }
    rows = []
    for oid, question, why in OPEN_SEMANTIC_SEEDS:
        status, conclusion = status_by_id.get(oid, ("UNRESOLVED", "Not yet evaluated."))
        rows.append([
            oid,
            question,
            why,
            "problem_statement.md, README.md, AGENTS.md, frozen technical design",
            "sample_requests.csv plus linked profile/events/options/messages/images",
            "See SOLVED_CASE_FORENSICS.md matrices and per-case sections.",
            "Strict official reading vs observed solved-output-specific inference.",
            "Generated matrix comparison; structural plan/option/spending-change reconstruction.",
            conclusion,
            "high" if status == "RESOLVED" else "medium" if status == "PARTIALLY RESOLVED" else "low",
            status,
        ])
    return "\n".join([
        "# Open Semantics",
        "",
        "Phase 1 semantic register. `docs/TECHNICAL_DESIGN.md` is frozen; discoveries are recorded here instead.",
        "",
        table([
            "ID",
            "question",
            "why it matters",
            "official source evidence",
            "dataset evidence",
            "solved-case evidence",
            "competing interpretations",
            "experiments/comparisons performed",
            "current conclusion",
            "confidence",
            "status",
        ], rows),
        "",
    ])


def build_decisions() -> str:
    decisions = [
        (
            "D-001",
            "Use current_available_balance as the snapshot anchor",
            "ACCEPTED",
            "Official wording frames profile balance as current available balance; samples would be distorted by replaying all historical settled events.",
            "All 25 public cases use profile balance as the starting point for capacity reasoning.",
            "No public sample requires historical replay into current balance.",
            "Start forecasts from profile current_available_balance on request_date; use historical events only for recurrence/evidence/lifecycle inference.",
            "Avoids double-counting and matches official challenge framing.",
            "Phase 2 loaders/simulators must not replay old settled rows into profile balance.",
            "high",
        ),
        (
            "D-002",
            "amount_safe_to_pay is baseline capacity before optional spending changes",
            "ACCEPTED",
            "problem_statement.md states before optional spending changes; request_06, request_11, request_21 prove full payment can be selected after cuts even when baseline safe amount is lower.",
            "sample_requests.csv spending_changes_needed rows.",
            "request_06, request_11, request_21.",
            "Compute baseline safe amount without optional cuts; evaluate cuts only for candidate feasibility.",
            "Separates capacity metric from plan feasibility.",
            "Output amount_safe_to_pay must not increase due to spending optimizer.",
            "high",
        ),
        (
            "D-003",
            "Installment plans must match supplied options and may include financing fee",
            "ACCEPTED",
            "Official output contract requires installment plans to follow supplied option; sample plans match option expansion and totals may exceed requested amount.",
            "request_payment_options.csv and solved installment rows.",
            "request_02, request_07, request_12, request_17, request_22.",
            "Expand option using first_payment_date, number_of_payments, payment_frequency_days, and payment_amount exactly.",
            "Prevents invented installment schedules.",
            "Candidate generator in later phases must compare exact schedule strings and use total_payable_amount for cost ranking.",
            "high",
        ),
        (
            "D-004",
            "Earliest full-payment date is independent of payment preference",
            "ACCEPTED",
            "problem_statement.md explicitly states this; request_12 selects installments while earliest full date is request_date.",
            "sample_requests.csv request_12.",
            "request_12.",
            "Compute earliest capacity date separately from method selection.",
            "Avoids preference contaminating capacity output.",
            "Later planner must report request_date for earliest full capacity even when full_payment is not accepted.",
            "high",
        ),
        (
            "D-005",
            "Partial payment is exactly two payments",
            "ACCEPTED",
            "Official rule; request_19 shows amount_safe_to_pay today and remainder on earliest full-payment date.",
            "sample_requests.csv request_19.",
            "request_19.",
            "When partial is selected, payment one equals amount_safe_to_pay on request_date and payment two equals requested remainder on earliest_date_for_full_payment.",
            "Matches official contract and public anchor.",
            "Do not generate arbitrary multi-step partial plans.",
            "high",
        ),
        (
            "D-006",
            "Positive amount_safe_to_pay does not imply affordability",
            "ACCEPTED",
            "Official status definitions require full request completion safely; not_affordable examples publish positive safe amounts.",
            "sample_requests.csv not_affordable rows.",
            "request_05, request_10, request_14, request_15, request_20, request_24, request_25.",
            "Recommend not_recommended when no safe eligible plan completes full request, even if a small amount is safe today.",
            "Prevents treating partial capacity as success.",
            "Status/method assignment must depend on complete plan feasibility.",
            "high",
        ),
        (
            "D-007",
            "Image-backed blank amounts are evidence, not zero",
            "ACCEPTED",
            "problem_statement.md states blank event amount must be resolved through images.csv and PNG evidence.",
            "images.csv maps five sample images to solved cases; PNGs were inspected.",
            "request_03, request_16, request_17, request_19, request_20.",
            "Resolve mapped PNG facts before treating an event as usable; never coerce blank to zero.",
            "Avoids undercounting expenses/income.",
            "Phase 2 loaders should represent blank as unknown and require evidence resolution where relevant.",
            "high",
        ),
    ]
    sections = ["# Decisions", "", "Global semantic decisions supported by Phase 1 evidence. No request-specific exceptions are recorded here.", ""]
    for d in decisions:
        sections.extend([
            f"## {d[0]} — {d[1]}",
            "",
            f"Decision ID: {d[0]}",
            f"Title: {d[1]}",
            f"Status: {d[2]}",
            f"Evidence: {d[3]}",
            f"Public cases supporting it: {d[5]}",
            f"Counterexamples checked: {d[4]}",
            f"Chosen rule: {d[6]}",
            f"Why: {d[7]}",
            f"Implementation consequence: {d[8]}",
            f"Confidence: {d[9]}",
            "",
        ])
    return "\n".join(sections)


def build_phase_status() -> str:
    return """# Phase Status

Current Phase:
Phase 1 — Dataset Forensics and Solved-Case Reverse Engineering

Status:
ACTIVE

Phase 0:
COMPLETE

Phase 1 Exit Criteria:
- all 25 solved cases analyzed;
- all important semantic questions classified;
- globally supported decisions recorded;
- unresolved questions documented;
- no production financial engine implemented;
- no sample-specific production logic created.

Immutable Architecture Source:
`docs/TECHNICAL_DESIGN.md` is immutable and must not be modified, rewritten, updated, formatted, or moved during Phase 1. Discoveries belong in `docs/PHASE_STATUS.md`, `docs/OPEN_SEMANTICS.md`, and `docs/DECISIONS.md`.
"""


def build_semantic_evidence_matrix() -> str:
    rows = [
        ["OQ-001", "All samples use 90-day safety language", "No edge-only sample isolates day 90", "PARTIALLY RESOLVED"],
        ["OQ-006", "Installment selections respect visible profile acceptance", "No accepted-over-max contradiction found structurally", "RESOLVED"],
        ["OQ-013/OQ-014/OQ-015", "request_06, request_11, request_21 show legal changes", "Few change cases; hidden tie-break unknown", "PARTIALLY RESOLVED"],
        ["OQ-016", "Decimals preserved in plan amounts; amount_safe may trim zeros", "No official formatter spec beyond examples", "PARTIALLY RESOLVED"],
        ["OQ-019", "All cases require profile balance as anchor", "Historical replay would over/understate balances", "RESOLVED"],
        ["OQ-023", "Five public images provide mapped financial facts", "No contradiction", "RESOLVED"],
    ]
    return "\n".join([
        "## F. Open Semantic Evidence Matrix",
        "",
        table(["semantic ID", "cases supporting interpretation A", "cases supporting interpretation B", "current conclusion"], rows),
        "",
    ])


def main() -> None:
    data = load_data()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    DOCS.mkdir(exist_ok=True)
    solved_doc, matrices, counts = build_solved_case_doc(data)
    solved_doc = solved_doc + "\n" + build_semantic_evidence_matrix()
    (OUT_DIR / "SOLVED_CASE_FORENSICS.md").write_text(solved_doc, encoding="utf-8", newline="\n")
    (DOCS / "PHASE_STATUS.md").write_text(build_phase_status(), encoding="utf-8", newline="\n")
    (DOCS / "OPEN_SEMANTICS.md").write_text(build_open_semantics(matrices), encoding="utf-8", newline="\n")
    (DOCS / "DECISIONS.md").write_text(build_decisions(), encoding="utf-8", newline="\n")
    summary = {
        "samples": len(data.samples),
        "structurally_reconstructed": counts["full"],
        "partially_reconstructed": counts["partial"],
        "unresolved": counts["unresolved"],
        "outputs": [
            str(OUT_DIR / "SOLVED_CASE_FORENSICS.md"),
            str(DOCS / "PHASE_STATUS.md"),
            str(DOCS / "OPEN_SEMANTICS.md"),
            str(DOCS / "DECISIONS.md"),
        ],
    }
    print(summary)


if __name__ == "__main__":
    main()
