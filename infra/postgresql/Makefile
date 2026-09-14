.PHONY: up down pg-up pg-down pg-psql pg-logs

# Everything
up: pg-up

down: pg-down

# ---- PostgreSQL ----
pg-up:
	docker run -d --name ltp-postgresql \
		--env-file .env \
		-p 5432:5432 \
		-v /mntsys/postgresql/data:/data \
		--restart always \
		$$(docker build -q .)

pg-down:
	docker stop ltp-postgresql && docker rm ltp-postgresql

pg-psql:
	docker exec -it ltp-postgresql psql -U root -d incidara

pg-logs:
	docker logs -f ltp-postgresql
