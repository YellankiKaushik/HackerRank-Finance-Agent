# Decisions

Global semantic decisions supported by Phase 1 evidence. No request-specific exceptions are recorded here.

## D-001 — Use current_available_balance as the snapshot anchor

Decision ID: D-001
Title: Use current_available_balance as the snapshot anchor
Status: ACCEPTED
Evidence: Official wording frames profile balance as current available balance; samples would be distorted by replaying all historical settled events.
Public cases supporting it: No public sample requires historical replay into current balance.
Counterexamples checked: All 25 public cases use profile balance as the starting point for capacity reasoning.
Chosen rule: Start forecasts from profile current_available_balance on request_date; use historical events only for recurrence/evidence/lifecycle inference.
Why: Avoids double-counting and matches official challenge framing.
Implementation consequence: Phase 2 loaders/simulators must not replay old settled rows into profile balance.
Confidence: high

## D-002 — amount_safe_to_pay is baseline capacity before optional spending changes

Decision ID: D-002
Title: amount_safe_to_pay is baseline capacity before optional spending changes
Status: ACCEPTED
Evidence: problem_statement.md states before optional spending changes; request_06, request_11, request_21 prove full payment can be selected after cuts even when baseline safe amount is lower.
Public cases supporting it: request_06, request_11, request_21.
Counterexamples checked: sample_requests.csv spending_changes_needed rows.
Chosen rule: Compute baseline safe amount without optional cuts; evaluate cuts only for candidate feasibility.
Why: Separates capacity metric from plan feasibility.
Implementation consequence: Output amount_safe_to_pay must not increase due to spending optimizer.
Confidence: high

## D-003 — Installment plans must match supplied options and may include financing fee

Decision ID: D-003
Title: Installment plans must match supplied options and may include financing fee
Status: ACCEPTED
Evidence: Official output contract requires installment plans to follow supplied option; sample plans match option expansion and totals may exceed requested amount.
Public cases supporting it: request_02, request_07, request_12, request_17, request_22.
Counterexamples checked: request_payment_options.csv and solved installment rows.
Chosen rule: Expand option using first_payment_date, number_of_payments, payment_frequency_days, and payment_amount exactly.
Why: Prevents invented installment schedules.
Implementation consequence: Candidate generator in later phases must compare exact schedule strings and use total_payable_amount for cost ranking.
Confidence: high

## D-004 — Earliest full-payment date is independent of payment preference

Decision ID: D-004
Title: Earliest full-payment date is independent of payment preference
Status: ACCEPTED
Evidence: problem_statement.md explicitly states this; request_12 selects installments while earliest full date is request_date.
Public cases supporting it: request_12.
Counterexamples checked: sample_requests.csv request_12.
Chosen rule: Compute earliest capacity date separately from method selection.
Why: Avoids preference contaminating capacity output.
Implementation consequence: Later planner must report request_date for earliest full capacity even when full_payment is not accepted.
Confidence: high

## D-005 — Partial payment is exactly two payments

Decision ID: D-005
Title: Partial payment is exactly two payments
Status: ACCEPTED
Evidence: Official rule; request_19 shows amount_safe_to_pay today and remainder on earliest full-payment date.
Public cases supporting it: request_19.
Counterexamples checked: sample_requests.csv request_19.
Chosen rule: When partial is selected, payment one equals amount_safe_to_pay on request_date and payment two equals requested remainder on earliest_date_for_full_payment.
Why: Matches official contract and public anchor.
Implementation consequence: Do not generate arbitrary multi-step partial plans.
Confidence: high

## D-006 — Positive amount_safe_to_pay does not imply affordability

Decision ID: D-006
Title: Positive amount_safe_to_pay does not imply affordability
Status: ACCEPTED
Evidence: Official status definitions require full request completion safely; not_affordable examples publish positive safe amounts.
Public cases supporting it: request_05, request_10, request_14, request_15, request_20, request_24, request_25.
Counterexamples checked: sample_requests.csv not_affordable rows.
Chosen rule: Recommend not_recommended when no safe eligible plan completes full request, even if a small amount is safe today.
Why: Prevents treating partial capacity as success.
Implementation consequence: Status/method assignment must depend on complete plan feasibility.
Confidence: high

## D-007 — Image-backed blank amounts are evidence, not zero

Decision ID: D-007
Title: Image-backed blank amounts are evidence, not zero
Status: ACCEPTED
Evidence: problem_statement.md states blank event amount must be resolved through images.csv and PNG evidence.
Public cases supporting it: request_03, request_16, request_17, request_19, request_20.
Counterexamples checked: images.csv maps five sample images to solved cases; PNGs were inspected.
Chosen rule: Resolve mapped PNG facts before treating an event as usable; never coerce blank to zero.
Why: Avoids undercounting expenses/income.
Implementation consequence: Phase 2 loaders should represent blank as unknown and require evidence resolution where relevant.
Confidence: high
