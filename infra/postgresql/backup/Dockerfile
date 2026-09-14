FROM postgres:16

RUN apt update && apt upgrade -y

# ── pgBackRest + cron ──
RUN apt install -y --no-install-recommends curl ca-certificates gnupg && \
    install -d /usr/share/postgresql-common/pgdg && \
    curl -f https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor > /usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg && \
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" > /etc/apt/sources.list.d/pgdg.list && \
    apt update && \
    apt install -y --no-install-recommends pgbackrest jq e2fsprogs mount cron && \
    rm -rf /var/lib/apt/lists/*

# pgBackRest directories
RUN mkdir -p /etc/pgbackrest /var/lib/pgbackrest /var/log/pgbackrest /var/log/pgbackrest/incidents /tmp/pgbackrest && \
    chown -R postgres:postgres /etc/pgbackrest /var/lib/pgbackrest /var/log/pgbackrest /tmp/pgbackrest

# Scripts
COPY scripts/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY scripts/render-pgbackrest-conf.sh /usr/local/bin/render-pgbackrest-conf.sh
COPY scripts/pgbackrest-archive-guard.sh /usr/local/bin/pgbackrest-archive-guard.sh
COPY scripts/setup-wal-volume.sh /usr/local/bin/setup-wal-volume.sh
COPY scripts/restore-and-promote.sh /usr/local/bin/restore-and-promote.sh
COPY scripts/setup-pgbackrest.sh /docker-entrypoint-initdb.d/01-setup-pgbackrest.sh
COPY scripts/run-cron.sh /usr/local/bin/run-cron.sh
RUN chmod 755 /usr/local/bin/entrypoint.sh \
               /usr/local/bin/render-pgbackrest-conf.sh \
               /usr/local/bin/pgbackrest-archive-guard.sh \
               /usr/local/bin/setup-wal-volume.sh \
               /usr/local/bin/restore-and-promote.sh \
               /usr/local/bin/run-cron.sh \
               /docker-entrypoint-initdb.d/01-setup-pgbackrest.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["postgres"]
