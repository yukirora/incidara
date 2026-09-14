.PHONY: up down logs psql backup restore reset

up:
	docker compose up -d
	@echo "Postgres up on 127.0.0.1:$${HOST_PORT:-5434}"

down:
	docker compose down

logs:
	docker compose logs -f db

cron-logs:
	docker compose logs -f cron

psql:
	docker exec -it incidara-agent-db psql -U $$(grep POSTGRES_USER .env | cut -d= -f2) -d $$(grep POSTGRES_DB .env | cut -d= -f2)

# Run a one-off full backup
backup:
	docker exec incidara-agent-db gosu postgres pgbackrest --stanza=app --type=full backup

# Restore from OSS (stops DB, runs restore, then starts DB)
restore:
	docker compose down
	docker compose --profile restore run --rm restore
	docker compose up -d

# Wipes everything including backup data. Use with care.
reset:
	docker compose down
	rm -rf data pgwal pgbackrest
