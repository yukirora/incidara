.PHONY: up down logs psql reset

up:
	docker compose up -d
	@echo "Postgres up on localhost:$${DB_HOST_PORT:-5433}"

down:
	docker compose down

logs:
	docker compose logs -f postgres

psql:
	docker exec -it incidara-console-db psql -U $$(grep POSTGRES_USER .env | cut -d= -f2) -d $$(grep POSTGRES_DB .env | cut -d= -f2)

# Wipes the data volume. Use with care.
reset:
	docker compose down -v
	rm -rf data
