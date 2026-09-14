# Ticket Check

Poll vendor ticket API for `ua` nodes. Complete RMA when repair is done, escalate anomalies.

## When to Use

- Scheduled run (cron: every 30 min during business hours)
- Manual: "check tickets" or "ticket check"

---

## Workflow

### Step 1: Check all ua tickets (single call)

Call `check_completed_tickets()` → polls vendor API for ALL ua nodes in one call.

Returns ONLY actionable nodes:
- **completed**: ticket is terminal (completed/repaired) → go to Step 2a
- **anomalous**: ticket is abnormal (rejected/cancelled/on-hold) → go to Step 2b
- **no_ticket**: node is ua but has no RMA record → go to Step 2c

In-progress tickets are NOT returned (will be checked next run).

### Step 2a: Complete RMA (terminal ticket)

For each node in the `completed` list:

Call `complete_rma(hostname, ticket_id)` → fetches vendor ticket data, inserts `CompleteRMA` with full vendor response, moves to `ready_ua`.

### Step 2b: Escalate anomaly

For each node in the `anomalous` list:

Call `move_node_status(hostname, node_id, "ua", "triaged_unknown", "ticket_{status}", "Vendor ticket {ticket_id}: {status}")` → moves to `triaged_unknown` for triage agent to re-diagnose.

### Step 2c: No ticket found

For each node in the `no_ticket` list:

Call `move_node_status(hostname, node_id, "ua", "triaged_unknown", "no_rma_ticket", "Node in ua status but no RMA ticket found")`.

### Step 3: Report

Write results to `/app/workspace/reports/ticket_check_<YYYY-MM-DD>.md`:

```markdown
# Ticket Check Report — <date>

## Summary
- Total ua nodes: N
- In-progress (skipped): N
- RMA completed (→ ready_ua): N
- Anomalous (→ triaged_unknown): N
- No ticket (→ triaged_unknown): N
- API errors: N

## Completed (→ ready_ua)
| Hostname | Ticket ID | Repair Status |
|----------|-----------|---------------|

## Anomalous (→ triaged_unknown)
| Hostname | Ticket ID | Status | Reason |
|----------|-----------|--------|--------|

## No Ticket (→ triaged_unknown)
| Hostname | Reason |
|----------|--------|
```

Also flag any anomalies or no-ticket nodes for operator attention.
