# capability-scan

Scan gateway session events for tool failures, MCP errors, auth issues, and waiting-input sessions.

The scanner compresses raw gateway events into grouped signals. It finds WHAT is failing. You judge whether each failure is real, noise, or attention-worthy.

## Procedure

1. Query behavior-scan for waiting_input session IDs:
   ```bash
   psql "$CHAT_UI_DB_URL" -c "SELECT s.gateway_session_id FROM tasks t JOIN sessions s ON t.session_id = s.id WHERE t.status = 'waiting_input' AND t.created_at > NOW() - INTERVAL '24 hours'"
   ```

2. Run the scanner with those session IDs:
   ```bash
   cd ${CLAUDE_SKILL_DIR} && python3 -m scripts.attention_queue.cli --waiting-sessions <session_ids>
   ```

3. Read the generated Markdown output.

4. **Classify waiting-input signals.** The scanner passes ALL waiting sessions to you. For each one:
   - Read the evidence text (last assistant message).
   - **Operational**: the agent is asking "what should I classify this as?", "is this hardware or platform?", "how should I route this?" — keep.
   - **Routine**: the agent is reporting status and waiting for approval ("ready to proceed", "evidence complete, approve to continue") — skip.
   - If the text is just a status report with no question, skip.

5. **Filter noise.** For each tool failure group:
   - Is this one-off or recurring? Skip single-occurrence failures more than 24h old.
   - Is this a known transient pattern? Skip if same error appeared only in recycled/recovered sessions.
   - Is this affecting production work? Skip if all affected sessions were agent self-tests or idle loops.

6. **Assess impact.** For each kept signal:
   - Auth/token failures blocking alert submission → highest priority.
   - MCP server unavailable for multiple agents → high.
   - Tool errors on single agent, single session → low.

7. Return the filtered list. Pass to the parent skill for merging with behavior-scan output.