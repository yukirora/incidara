# incidara-console DB

Dedicated Postgres 16 container for the chat UI. Deployed on the dev host
alongside the claude-agent gateway; not run locally.

## Deploy

Sync this directory to the dev host (e.g. via rsync), then on that host:

```bash
cp .env.example .env         # edit POSTGRES_PASSWORD
sudo make up                 # starts on 127.0.0.1:${DB_HOST_PORT:-5433}
sudo make psql               # interactive shell
sudo make logs               # tail logs
sudo make down               # stop (keeps data/)
sudo make reset              # stop + wipe data/
```

## Connection string

From the chat UI server (same host, `--network host`):

```
postgresql://chat_ui_user:<password>@127.0.0.1:5433/chat_ui
```

The server reads this from `CHAT_UI_DATABASE_URL` in its own `.env`.

## Schema

`init/001_init.sql` runs automatically on first boot (empty `data/` volume).
Later schema changes go in `server/src/db/migrations/` and are applied by
the server's migration runner on startup.
