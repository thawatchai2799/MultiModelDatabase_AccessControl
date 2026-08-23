#!/bin/bash
# Custom entrypoint for the streaming-replication replica (Scenario B2:
# read-replica lag). Wraps the standard postgres image entrypoint: if the
# data directory is empty, clone the primary via pg_basebackup (which writes
# standby.signal and primary_conninfo for us via -R) before handing off to
# the normal postgres startup.
set -euo pipefail

PGDATA="${PGDATA:-/var/lib/postgresql/data}"

if [ -z "$(ls -A "$PGDATA" 2>/dev/null)" ]; then
    echo "[replica-entrypoint] empty PGDATA -- cloning primary via pg_basebackup"
    until pg_isready -h postgres-primary -U replicator -d postgres 2>/dev/null; do
        echo "[replica-entrypoint] waiting for primary to accept connections..."
        sleep 2
    done
    PGPASSWORD="replicator_dev_only_change_me" pg_basebackup \
        -h postgres-primary -D "$PGDATA" -U replicator -Fp -Xs -P -R \
        --checkpoint=fast
    chmod 0700 "$PGDATA"
    echo "[replica-entrypoint] base backup complete; standby.signal written by -R"
else
    echo "[replica-entrypoint] PGDATA already populated -- skipping pg_basebackup"
fi

# Pass through whatever compose's `command:` supplies (postgres -c hba_file=...),
# defaulting to plain `postgres` if nothing was given.
if [ "$#" -eq 0 ]; then set -- postgres; fi
exec docker-entrypoint.sh "$@"
