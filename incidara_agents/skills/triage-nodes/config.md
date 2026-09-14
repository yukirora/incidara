# Configuration

## SSH / Cluster Access

SSH agent socket is mounted at `/tmp/ssh-agent.sock` with key pre-loaded.
SSH directly to cluster nodes (no jump host needed).

```
export SSH_AUTH_SOCK=/tmp/ssh-agent.sock
ssh -A -o StrictHostKeyChecking=no operator@<node_ip>
```

## LTP Job API

Access job logs and container output via the LTP REST API.

| Variable | Source |
|----------|--------|
| `LTP_HOST` | Set in container env (e.g. `http://192.0.2.10`) |
| `LTP_TOKEN` | Set in container env or read from `~/.ltp_tokens/<host>_token` |

If no token is available, `ltp.sh logs` commands will fail — skip those steps and note as incomplete.

## Defaults

| Item | Value |
|------|-------|
| Working file | `/app/workspace/triage_nodes_working.md` |
| Report file | `/app/workspace/reports/triage_nodes_<YYYY-MM-DD>.md` |

## Node Filtering

- Nodes with `ctrl` in the hostname (e.g. `lg-cmc-demo-r01u01-ctrl-000001`) are control-plane nodes — **skip** during triage.
- Use `--exclude node1,node2` argument to skip specific nodes.

## Pod Namespaces

- `default` / `kube-system`

## Chat UI Delegation

Used to delegate confirmed hardware nodes to the repair agent.

| Variable | Source |
|----------|--------|
| `CHAT_UI_URL` | Set in container env (e.g. `http://127.0.0.1:3001`) |
| `CHAT_UI_USER` | Service account email for API login |
| `CHAT_UI_PASSWORD` | Service account password |
