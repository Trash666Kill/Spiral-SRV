#!/bin/bash

# - Description: Performs an incremental and differential backup of a remote SMB share to local directories using rsync.
# - Mounts the remote SMB share if not already mounted, using provided credentials and mount options.
# - Checks available disk space for the parent directories of the source, incremental, differential, and log directories.
# - Verifies minimum required disk space before rsync (fixed 1GB) and .zst creation (based on INCR_DIR size).
# - Creates the necessary directories if they do not exist, with user confirmation for automatic creation.
# - Excludes specified file types and directories (e.g., *.exe, DfsrPrivate/) from the backup using an exclusion file.
# - Uses rsync with bandwidth limits, permission preservation, and logging to synchronize files to the incremental directory, with deleted/changed files backed up to the differential directory.
# - Runs rsync as a specified user (default: root) and logs operations to a timestamped log file.
# - Ignores rsync error code 23 (partial transfer due to errors like permission denied) to continue execution.
# - Cleans up temporary exclusion files after execution; includes commented-out commands for optional cleanup of old differential files.
# - Unmounts the SMB share if it was mounted by the script.
# - To modify backup parameters, edit the REMOTE_SHARE, ORIG_DIR, INCR_DIR, DIFF_DIR, BW_LIMIT, or EXCLUDES_FILE content.
# - Exits on critical errors during directory creation, mounting, or rsync execution (excluding error code 23).

incr_diff() {
    DATE=$(date +%F_%H-%M-%S)
    REMOTE_SHARE="//172.30.100.22/hsugisawa"
    ORIG_DIR="/mnt/Remote/Servers/HS-DFS-01/hsugisawa/"
    INCR_DIR="/mnt/Local/Container/A/Backup/Incremental/HS-DFS-01/hsugisawa/"
    DIFF_DIR="/mnt/Local/Container/A/Backup/Differential/HS-DFS-01/hsugisawa/"
    FULL_DIR="/mnt/Local/Container/A/Backup/Full/HS-DFS-01/hsugisawa/"
    LOG_FILE="/var/log/rsync/dfs-hs-01_hsugisawa-INCR-${DATE}.log"
    EXCLUDES_FILE=$(mktemp)
    cat > "$EXCLUDES_FILE" <<EOF
*.exe
*.EXE
*.msi
*.MSI
*.bat
*.BAT
*.jnlp
*.JNLP
*.iso
*.ISO
*.db
*.DB
*.dmp
*.DMP
*.dat
*.DAT
*.bak
*.BAK
DfsrPrivate/
TASY/
EOF

    # Configuration variables
    MOUNT_OPTS="ro"
    CIFS_USER="keith.campbell"
    CIFS_PASS="123456789"
    BW_LIMIT="10240" # Bandwidth limit in KB/s
    RSYNC_USER="root" # User to execute rsync
    MIN_SPACE=1024000 # Minimum required space in KB (1GB)

    # Display available disk space for the filesystems
    printf "\e[33m*\e[0m Running: Checking available disk space\n"
    for dir in "$ORIG_DIR" "$INCR_DIR" "$DIFF_DIR" "$FULL_DIR" "$(dirname "$LOG_FILE")"; do
        parent_dir=$(dirname "$dir")
        if [ -d "$parent_dir" ]; then
            df -h "$parent_dir" | awk 'NR==2 {print "  " $6 " -> Available: " $4 " (" $5 " used)"}'
        else
            printf "\e[31m*\e[0m Error: %s -> Filesystem not found (parent directory does not exist)\n" "$parent_dir"
        fi
    done
    printf "\n"

    # Check minimum disk space for INCR_DIR and DIFF_DIR
    for dir in "$INCR_DIR" "$DIFF_DIR"; do
        avail_space=$(df --output=avail "$dir" | tail -1)
        if [ "$avail_space" -lt "$MIN_SPACE" ]; then
            printf "\e[31m*\e[0m Error: Insufficient disk space in %s. Required: %d KB, Available: %d KB\n" "$dir" "$MIN_SPACE" "$avail_space"
            rm -f "$EXCLUDES_FILE"
            return 1
        fi
    done

    # Check if directories exist
    for dir in "$ORIG_DIR" "$INCR_DIR" "$DIFF_DIR" "$FULL_DIR" "$(dirname "$LOG_FILE")"; do
        if [ ! -d "$dir" ]; then
            printf "\e[33m*\e[0m Running: The directory %s does not exist.\n" "$dir"
            read -p "Do you want to create the directories automatically? (y/n): " answer
            if [[ "$answer" =~ ^[Yy]$ ]]; then
                printf "\e[33m*\e[0m Running: Creating directory %s\n" "$dir"
                mkdir -pv "$dir" || { printf "\e[31m*\e[0m Error: Failed to create %s\n" "$dir"; rm -f "$EXCLUDES_FILE"; return 1; }
                printf "\e[32m*\e[0m Successfully! Directory %s created\n" "$dir"
            else
                printf "\e[31m*\e[0m Error: Operation canceled by the user\n"
                rm -f "$EXCLUDES_FILE"
                return 1
            fi
        fi
    done

    # Check if the directory is already mounted
    local mounted=0
    if ! mountpoint -q "$ORIG_DIR"; then
        printf "\e[33m*\e[0m Running: Mounting %s on %s\n" "$REMOTE_SHARE" "$ORIG_DIR"
        mount -t cifs "$REMOTE_SHARE" "$ORIG_DIR" -o "$MOUNT_OPTS",username="$CIFS_USER",password="$CIFS_PASS"
        if [ $? -ne 0 ]; then
            printf "\e[31m*\e[0m Error: Failed to mount %s\n" "$REMOTE_SHARE"
            rm -f "$EXCLUDES_FILE"
            return 1
        fi
        printf "\e[32m*\e[0m Successfully! %s mounted\n" "$REMOTE_SHARE"
        mounted=1
    else
        printf "\e[32m*\e[0m Successfully! The directory %s is already mounted\n" "$ORIG_DIR"
    fi

    # Rsync command
    RSYNC_CMD=( rsync --bwlimit="$BW_LIMIT" -ahx --acls --xattrs --numeric-ids --chmod=ugo+r --ignore-errors --force --exclude-from="$EXCLUDES_FILE" \
                --delete --backup --backup-dir="$DIFF_DIR" --info=del,name,stats2 --log-file="$LOG_FILE" \
                "$ORIG_DIR" "$INCR_DIR" )
    printf "\e[33m*\e[0m Running: %s\n" "${RSYNC_CMD[@]}"

    # Execute rsync with the specified user
    su - "$RSYNC_USER" -c "$(printf '%q ' "${RSYNC_CMD[@]}")"
    rsync_exit_code=$?
    if [ $rsync_exit_code -ne 0 ] && [ $rsync_exit_code -ne 23 ]; then
        printf "\e[31m*\e[0m Error: Rsync exited with critical error (code %d)\n" "$rsync_exit_code"
        rm -f "$EXCLUDES_FILE"
        if [ "$mounted" -eq 1 ]; then
            umount "$ORIG_DIR" && printf "\e[32m*\e[0m Successfully! %s unmounted\n" "$ORIG_DIR" || printf "\e[31m*\e[0m Error: Failed to unmount %s\n" "$ORIG_DIR"
        fi
        return 1
    elif [ $rsync_exit_code -eq 23 ]; then
        printf "\e[33m*\e[0m Warning: Rsync encountered partial transfer errors (e.g., permission denied), continuing\n"
    fi
    printf "\e[32m*\e[0m Successfully! Rsync completed\n"

    # Cleanup of old files and directories
    printf "\e[33m*\e[0m Running: Cleaning up temporary exclusion file\n"
    rm -f "$EXCLUDES_FILE"
    printf "\e[32m*\e[0m Successfully! Temporary file removed\n"
    find "$DIFF_DIR" -type f -mtime +240 -delete
    find "$DIFF_DIR" -type d -empty -delete
    find /var/log/rsync -type f -name "*.log" -printf "%T@ %p\n" | sort -nr | tail -n +32 | awk '{print $2}' | xargs -r rm -v
}

full() {
    local MAX_AGE_DAYS=30
    local ZST_FILE="$FULL_DIR/Full_${DATE}.tar.zst"
    local IONICE_CLASS="3"
    local NICE_PRIORITY="19"
    local TRANSFER_RATE="10m"
    local ZSTD_THREADS="2"
    local LOG_FILE="/var/log/rsync/dfs-hs-01_hsugisawa-FULL-${DATE}.log"

    # Ensure log directory exists
    mkdir -p /var/log/rsync/ || {
        printf "\e[31m*\e[0m Error: Failed to create log directory /var/log/rsync/.\n" >&2
        exit 1
    }

    # Helper function to log messages
    log_message() {
        local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
        printf "[%s] %s\n" "$timestamp" "$*" >> "$LOG_FILE"
    }

    # Initialize log file
    log_message "Starting backup process for INCR_DIR: $INCR_DIR to FULL_DIR: $FULL_DIR"

    # Check if INCR_DIR exists
    if [ ! -d "$INCR_DIR" ]; then
        log_message "ERROR: Directory $INCR_DIR does not exist."
        printf "\e[31m*\e[0m Error: Directory %s does not exist.\n" "$INCR_DIR" >&2
        exit 1
    fi

    # Delete .zst files older than MAX_AGE_DAYS to free up space
    log_message "INFO: Checking for old .zst files in $FULL_DIR older than $MAX_AGE_DAYS days"
    printf "\e[33m*\e[0m Running: Checking for old .zst files in %s older than %s days\n" "$FULL_DIR" "$MAX_AGE_DAYS"
    find "$FULL_DIR" -type f -name 'Full_*.tar.zst' -mtime +"$MAX_AGE_DAYS" -delete 2>/dev/null
    if [ $? -eq 0 ]; then
        log_message "INFO: Old backup files in $FULL_DIR older than $MAX_AGE_DAYS days were deleted."
        printf "\e[33m*\e[0m Running: Old backup files in %s older than %s days were deleted.\n" "$FULL_DIR" "$MAX_AGE_DAYS"
    fi

    # Calculate required space based on INCR_DIR size plus 10% margin
    local INCR_SIZE=$(du -s --block-size=1K "$INCR_DIR" | awk '{print $1}')
    local MIN_SPACE=$((INCR_SIZE + INCR_SIZE / 10)) # INCR_SIZE + 10% margin
    log_message "INCR_DIR size: $INCR_SIZE KB, Required space (with 10% margin): $MIN_SPACE KB"

    # Check minimum disk space for FULL_DIR
    avail_space=$(df --output=avail "$FULL_DIR" | tail -1)
    log_message "Available space in FULL_DIR: $avail_space KB"
    if [ "$avail_space" -lt "$MIN_SPACE" ]; then
        log_message "ERROR: Insufficient disk space in $FULL_DIR. Required: $MIN_SPACE KB, Available: $avail_space KB"
        printf "\e[31m*\e[0m Error: Insufficient disk space in %s. Required: %d KB, Available: %d KB\n" "$FULL_DIR" "$MIN_SPACE" "$avail_space"
        exit 1
    fi

    # Check for existing .zst files in FULL_DIR
    recent_zst=$(find "$FULL_DIR" -type f -name 'Full_*.tar.zst' -mtime -"$MAX_AGE_DAYS" -print -quit)
    if [ -n "$recent_zst" ]; then
        log_message "INFO: Recent backup $recent_zst exists and is less than $MAX_AGE_DAYS days old, skipping creation"
        printf "\e[32m*\e[0m Info: Recent backup %s exists and is less than %s days old, skipping creation\n" "$recent_zst" "$MAX_AGE_DAYS"
        exit 0
    fi

    # Delete existing FULL_DIR/Full if it exists and create a new copy of INCR_DIR
    log_message "INFO: Deleting existing $FULL_DIR/Full (if any) and copying $INCR_DIR to $FULL_DIR/Full"
    printf "\e[33m*\e[0m Running: Copying %s to %s/Full...\n" "$INCR_DIR" "$FULL_DIR"
    rm -rf "$FULL_DIR/Full" 2>/dev/null
    cp -a --reflink=always "$INCR_DIR" "$FULL_DIR/Full" || {
        log_message "ERROR: Failed to copy $INCR_DIR to $FULL_DIR/Full"
        printf "\e[31m*\e[0m Error: Failed to copy %s to %s/Full.\n" "$INCR_DIR" "$FULL_DIR" >&2
        exit 1
    }

    # Create the .zst file
    log_message "INFO: Creating $ZST_FILE"
    printf "\e[33m*\e[0m Running: Creating %s...\n" "$ZST_FILE"
    set -o pipefail
    ionice -c "$IONICE_CLASS" nice -n "$NICE_PRIORITY" tar -cvf - "$INCR_DIR" 2>/dev/null | pv -q -L "$TRANSFER_RATE" | zstd --threads="$ZSTD_THREADS" > "$ZST_FILE" || {
        log_message "ERROR: Failed to create $ZST_FILE"
        printf "\e[31m*\e[0m Error: Failed to create %s.\n" "$ZST_FILE" >&2
        rm -rf "$FULL_DIR/Full" 2>/dev/null
        exit 1
    }

    # Log final .zst file size
    local ZST_SIZE=$(du -s --block-size=1K "$ZST_FILE" | awk '{print $1}')
    log_message "SUCCESS: File $ZST_FILE created, size: $ZST_SIZE KB, $FULL_DIR/Full retained"
    printf "\e[32m*\e[0m Successfully! File %s created, $FULL_DIR/Full retained.\n" "$ZST_FILE"
}

# Main function to orchestrate the setup
main() {
    incr_diff || {
        printf "\e[31m*\e[0m Error: incr_diff failed, aborting full backup\n"
        exit 1
    }
    full
}

# Execute main function
main