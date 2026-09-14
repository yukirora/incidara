# Transcript Mining

Extract a structured investigation trace from Claude SDK session transcripts.

## When to Use

- Case post-mortem: reconstruct what the agent actually did during triage/repair
- NFF analysis: determine if the agent investigated at all, or just auto-triaged
- Quality audit: check if the agent followed the skill procedure

## Step 0: Find session IDs from case_memory

Before you can mine transcripts, you need the session IDs. Look them up from the case record:

```
SELECT claude_session_id FROM case_memory WHERE id = <case_id>
```

The `claude_session_id` field is a JSONB array containing objects like:
```json
[{"agent": "repair", "session_id": "sess_bc032a8b"}, {"agent": "triage", "session_id": "sess_ad80feb7"}]
```

Each entry tells you which agent handled the case and its gateway session ID.

**Agent name → mount path mapping:**
- `triage` or `triage-unknown` → mount path `triage`
- `repair` or `repair-draft` → mount path `repair`

**If claude_session_id is empty** — no agent session recorded (auto-triage with zero investigation).

## Step 1: Run the mining script

Use `mine_transcript.py` to extract the trace automatically:

```bash
# By case ID (looks up sessions from DB)
python3 ${CLAUDE_SKILL_DIR}/sub-skills/case-diagnosis/sub-skills/transcript-mining/mine_transcript.py \
  --case-id <case_id>

# By hostname (searches all transcripts)
python3 ${CLAUDE_SKILL_DIR}/sub-skills/case-diagnosis/sub-skills/transcript-mining/mine_transcript.py \
  --hostname <hostname>

# By session file directly
python3 ${CLAUDE_SKILL_DIR}/sub-skills/case-diagnosis/sub-skills/transcript-mining/mine_transcript.py \
  --session-file /mnt/transcripts/triage/-app-workspace/<uuid>.jsonl
```

The script outputs:
- **Trace table** with Who column (agent vs user), tool calls, inputs, results
- **Summary** — total tool calls, diagnostic depth, user input detection

If the script fails (missing DB, no transcripts mounted), note: "Transcript mining not available — analysis based on summary fields only."

## Step 2: Review the trace and annotate

The script produces the raw trace. Review it and annotate:

1. **Mark user guidance** — did the agent pivot after a user message without new tool calls? That means the agent adopted the user's finding instead of discovering it.

2. **Mark investigation gaps** — did the agent stop investigating a subsystem after one check? Did it skip whole subsystems (IB, BMC, disk, dmesg)?

3. **Mark classification decision** — what evidence did the agent cite for its final classification? Was it from its own tool calls or from a user message?

**How to detect user contributions in the trace:**
- `💬 user` rows with technical content (not "continue" or "yes") → user provided information or direction
- Agent pivoting after a user message without new tool calls → agent adopted the user's finding
- Agent citing evidence that doesn't appear in any tool result → likely came from a user message

## Step 3: Summarize Investigation Completeness

After the trace, summarize:
- **Total checks run** vs **expected checks** for this fault type
- **Subsystems checked** (GPU, IB, BMC, disk, network, power) vs **subsystems skipped**
- **Investigation depth**: deep (multiple checks per subsystem) | shallow (one check per subsystem) | none (auto-triage, JSON-only reason)
- **Dead ends**: where the agent couldn't get data (SSH failed, BMC timeout, etc.)

## Output

The trace table and completeness summary.
