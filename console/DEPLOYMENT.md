# Deployment Workflow

## Rule: Local code is always the source of truth

### Mandatory steps (in order, every time):

1. **Edit code locally** — make all changes in the local repo
2. **Verify local is latest** — `npm run build` (or equivalent) to confirm no errors
3. **Sync to remote** — tar + scp the whole project dir (exclude node_modules, .git, dist, db-data)
4. **Docker build on remote** — `sudo docker build -t <image> .`
5. **Make stop + make run** — `sudo make stop && sudo make run`

### Per-project specifics:

#### incidara-console
- **Local repo**: `/Users/yutingjiang/Documents/1-workspace/incidara-console/`
- **Remote dir**: `/home/operator/yutji/incidara-console/` on `operator@192.0.2.10`
- **Image**: `incidara-console`
- **Container**: `incidara-console`
- **Build**: `npm run build` (local, for verification only — Dockerfile does its own build)
- **Sync command**:
  ```bash
  cd /Users/yutingjiang/Documents/1-workspace/incidara-console
  tar czf /tmp/incidara-console.tar.gz \
    --exclude='node_modules' --exclude='.git' --exclude='dist' \
    --exclude='tsconfig.tsbuildinfo' --exclude='.pnpm-store' \
    --exclude='db-data' --exclude='.DS_Store' .
  SSH_AUTH_SOCK=/tmp/ssh-agent.socket scp -o StrictHostKeyChecking=no \
    /tmp/incidara-console.tar.gz operator@192.0.2.10:/tmp/incidara-console.tar.gz
  ```
- **Remote deploy**:
  ```bash
  cd /home/operator/yutji/incidara-console
  # Preserve .env and config (secrets + runtime config)
  cp .env /tmp/incidara-console.env.bak
  cp -r config /tmp/incidara-console-config.bak
  # Extract (overwrite source only, not node_modules owned by root)
  tar xzf /tmp/incidara-console.tar.gz
  # Restore preserved files
  cp /tmp/incidara-console.env.bak .env
  # Build and restart
  sudo docker build -t incidara-console .
  sudo make stop && sudo make run
  ```

#### incidara (MCP servers + agent containers)
- **Local repo**: `./incidara/`
- **MCP server code**: `./incidara/incidara_agents/mcp-servers/node-operations/` and `./incidara/mcp-servers/agent-evidence/`
- **Agent Dockerfiles**: `./incidara/incidara_agents/agents/<agent-name>/Dockerfile`
- **Agent Makefiles**: `./incidara/incidara_agents/agents/<agent-name>/Makefile`
- **Deploy**: Dockerfile builds image with MCP packages baked in → Makefile runs container

### NEVER do these:
- ❌ `docker cp` to hot-patch files into running containers (lost on restart)
- ❌ `apt-get install` inside containers (lost on restart)
- ❌ Edit files directly on remote (local must be source of truth)
- ❌ Build locally and scp dist/ to container (Dockerfile must be the build path)
- ❌ Skip the build step (unverified code may break at runtime)
