# Phase 3 Lifecycle Report

This report is structural only. It does not perform recurrence inference, forecasting, headroom, planning, or output generation.

## Snapshot Validation

- evaluation request snapshots built: 250
- requests with payment options: 250
- snapshots retaining at least one message: 198
- snapshots retaining at least one image mapping: 11

## Snapshot Effective Classification Totals

- cancelled_ignored: 20
- failed_ignored: 12
- future_scheduled_credit: 42
- future_scheduled_debit: 13
- historical_evidence: 22859
- pending_debit: 49
- unresolved: 7
- unresolved linked components across request snapshots: 7
- internal transfer neutralizations across request snapshots: 0

## Lifecycle Statistics

Raw event status counts:

- cancelled: 22
- failed: 21
- pending: 71
- scheduled: 70
- settled: 25148
- unrealized: 10

Resolved lifecycle counts:

- standalone_events: 25226
- linked_event_chains: 58
- chain_length_distribution: {2: 58}
- cancelled_terminal_chains: 22
- failed_terminal_chains: 14
- settled_terminal_chains: 25121
- pending_terminal_chains: 57
- unrealized_effective_records: 0
- unresolved_chains: 7
- cyclic_linked_event_chains: 0
- broken_linked_event_chains: 0
- effective_event_classes: {'cancelled_ignored': 22, 'failed_ignored': 14, 'future_confirmed_credit': 1668, 'future_confirmed_debit': 23453, 'future_scheduled_credit': 47, 'future_scheduled_debit': 16, 'pending_debit': 57, 'unresolved': 7}

## Unresolved Linked Components

- event_5168|event_5169
- event_8575|event_8576
- event_12808|event_12809
- event_16690|event_16691
- event_21101|event_21102
- event_23306|event_23307
- event_23855|event_23856
