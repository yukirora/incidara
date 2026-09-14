#!/bin/bash
# setup-wal-volume.sh — Create/mount loop file for WAL with hard size limit
# Runs as privileged init container before PostgreSQL starts
#
# Smart behavior:
#   - If loop image already exists → mount it (regardless of BACKUP_ENABLED)
#     (needed because pg_wal symlink may already point there)
#   - If loop image doesn't exist + BACKUP_ENABLED=true → create and mount
#   - If loop image doesn't exist + BACKUP_ENABLED=false → skip, exit cleanly
#     (pg_wal will stay in PGDATA)
#
# Host layout:
#   {hostPath}/pgwal/pgwal.img       (sparse ext4 loop image)
#   {hostPath}/pgwal/mount/           (mount point for loop image)
#
# Container layout:
#   /var/lib/postgresql/wal/mount/    → mount point for loop image
#   /var/lib/postgresql/wal/mount/pg_wal/ → PG WAL directory (entrypoint creates symlink)

WAL_BASE="/var/lib/postgresql/wal"
WAL_IMG="${WAL_BASE}/pgwal.img"
WAL_MOUNT="${WAL_BASE}/mount"
WAL_SIZE_MB="${WAL_VOLUME_MAX_MB:-2048}"
BACKUP_ENABLED="${BACKUP_ENABLED:-true}"

log() { echo ">>> setup-wal-volume: $1"; }

# Create base directory
mkdir -p "$WAL_BASE" "$WAL_MOUNT"

# Check if already mounted
if mountpoint -q "$WAL_MOUNT" 2>/dev/null; then
    log "Already mounted at $WAL_MOUNT"
    mkdir -p "$WAL_MOUNT/pg_wal"
    exit 0
fi

# If no loop image exists and backup is disabled → nothing to do
if [ ! -f "$WAL_IMG" ] && [ "$BACKUP_ENABLED" != "true" ]; then
    log "No existing loop image and backup disabled — skipping WAL volume setup"
    exit 0
fi

# Create loop image if not exists (only reaches here if BACKUP_ENABLED=true or image exists)
if [ ! -f "$WAL_IMG" ]; then
    log "Creating ${WAL_SIZE_MB}MB sparse loop image"
    truncate -s "${WAL_SIZE_MB}M" "$WAL_IMG"
    mkfs.ext4 -F "$WAL_IMG" >/dev/null 2>&1
    log "Loop image created"
fi

# Try to mount
log "Mounting loop image"
if mount -o loop "$WAL_IMG" "$WAL_MOUNT" 2>/dev/null; then
    log "Mounted successfully"
else
    # Mount failed — try filesystem repair
    log "Mount failed, attempting filesystem repair"
    e2fsck -fy "$WAL_IMG" >/dev/null 2>&1 || true
    if mount -o loop "$WAL_IMG" "$WAL_MOUNT" 2>/dev/null; then
        log "Repaired and mounted"
    else
        # Still failed — recreate loop image (data lost but PG can recover)
        log "Repair failed, recreating loop image (WAL data lost)"
        umount "$WAL_MOUNT" 2>/dev/null || true
        rm -f "$WAL_IMG"

        if [ "$BACKUP_ENABLED" != "true" ]; then
            log "Existing loop image was corrupt and backup disabled — not recreating"
            exit 0
        fi

        truncate -s "${WAL_SIZE_MB}M" "$WAL_IMG"
        mkfs.ext4 -F "$WAL_IMG" >/dev/null 2>&1
        if mount -o loop "$WAL_IMG" "$WAL_MOUNT" 2>/dev/null; then
            log "Recreated and mounted"
        else
            log "FATAL: Cannot mount WAL volume"
            exit 1
        fi
    fi
fi

# Create pg_wal directory inside mount (but don't create archive_status — 
# initdb --waldir requires empty dir for fresh DBs; migration handles existing DBs)
mkdir -p "$WAL_MOUNT/pg_wal"
chown 999:999 "$WAL_MOUNT/pg_wal" 2>/dev/null || true

log "WAL volume ready"
