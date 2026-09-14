FROM postgres:16.8

COPY init.sql /docker-entrypoint-initdb.d/
COPY pricing.sql /docker-entrypoint-initdb.d/

CMD ["-N", "1000"]
