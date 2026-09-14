.PHONY: build run stop logs

build:
	docker build -t incidara-console .

run:
	docker run -d --name incidara-console --env-file .env --network host \
		-v $(CURDIR)/config:/app/config:ro \
		incidara-console

stop:
	docker stop incidara-console && docker rm incidara-console

logs:
	docker logs -f incidara-console
