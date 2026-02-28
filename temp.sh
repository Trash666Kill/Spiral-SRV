lxc_0() {
    local NAME="lxc_0"
    local NFS_SOURCE="172.16.10.1:/mnt/Local/Container/A/lxc"
    local MOUNT_POINT="/mnt/Remote/Servers/srv28013/lxc/0"
    local OPTIONS=""
    local MAX_ATTEMPTS=3
    local SLEEP_TIME=60
    local SILENT_FAIL=true
    local attempt=1
    local NFS_IP="${NFS_SOURCE%%:*}"

    [ -d "$MOUNT_POINT" ] || mkdir -p "$MOUNT_POINT" > /dev/null 2>&1

    if mountpoint -q "$MOUNT_POINT"; then
        printf "\e[33m*\e[0m [$NAME] Already mounted at $MOUNT_POINT\n"
        return 0
    fi

    while [ "$attempt" -le "$MAX_ATTEMPTS" ]; do
        printf "\e[32m*\e[0m [$NAME] Attempt $attempt/$MAX_ATTEMPTS — Checking ping to $NFS_IP...\n"

        if ping -c 1 -W 2 "$NFS_IP" > /dev/null 2>&1; then
            printf "\e[32m*\e[0m [$NAME] Ping OK — Trying to mount $NFS_SOURCE at $MOUNT_POINT...\n"

            if mount -t nfs "$NFS_SOURCE" "$MOUNT_POINT" ${OPTIONS:+-o "$OPTIONS"} > /dev/null 2>&1; then
                printf "\e[32m*\e[0m [$NAME] Successfully mounted on attempt $attempt/$MAX_ATTEMPTS\n"
                return 0
            else
                printf "\e[31m*\e[0m [$NAME] Mount failed\n"
            fi
        else
            printf "\e[33m*\e[0m [$NAME] Ping failed — $NFS_IP unreachable\n"
        fi

        attempt=$((attempt + 1))

        if [ "$attempt" -le "$MAX_ATTEMPTS" ]; then
            printf "\e[33m*\e[0m [$NAME] Waiting ${SLEEP_TIME}s before next attempt...\n"
            sleep "$SLEEP_TIME"
        fi
    done

    printf "\e[31m*\e[0m [$NAME] All $MAX_ATTEMPTS attempts failed\n"

    if [ "$SILENT_FAIL" = true ]; then
        return 0
    else
        return 1
    fi
}

virt_0() {
    local NAME="virt_0"
    local NFS_SOURCE="172.16.10.1:/mnt/Local/Container/A/Virt"
    local MOUNT_POINT="/mnt/Remote/Servers/srv28013/Virt/0"
    local OPTIONS=""
    local MAX_ATTEMPTS=3
    local SLEEP_TIME=60
    local SILENT_FAIL=true
    local attempt=1
    local NFS_IP="${NFS_SOURCE%%:*}"

    [ -d "$MOUNT_POINT" ] || mkdir -p "$MOUNT_POINT" > /dev/null 2>&1

    if mountpoint -q "$MOUNT_POINT"; then
        printf "\e[33m*\e[0m [$NAME] Already mounted at $MOUNT_POINT\n"
        return 0
    fi

    while [ "$attempt" -le "$MAX_ATTEMPTS" ]; do
        printf "\e[32m*\e[0m [$NAME] Attempt $attempt/$MAX_ATTEMPTS — Checking ping to $NFS_IP...\n"

        if ping -c 1 -W 2 "$NFS_IP" > /dev/null 2>&1; then
            printf "\e[32m*\e[0m [$NAME] Ping OK — Trying to mount $NFS_SOURCE at $MOUNT_POINT...\n"

            if mount -t nfs "$NFS_SOURCE" "$MOUNT_POINT" ${OPTIONS:+-o "$OPTIONS"} > /dev/null 2>&1; then
                printf "\e[32m*\e[0m [$NAME] Successfully mounted on attempt $attempt/$MAX_ATTEMPTS\n"
                return 0
            else
                printf "\e[31m*\e[0m [$NAME] Mount failed\n"
            fi
        else
            printf "\e[33m*\e[0m [$NAME] Ping failed — $NFS_IP unreachable\n"
        fi

        attempt=$((attempt + 1))

        if [ "$attempt" -le "$MAX_ATTEMPTS" ]; then
            printf "\e[33m*\e[0m [$NAME] Waiting ${SLEEP_TIME}s before next attempt...\n"
            sleep "$SLEEP_TIME"
        fi
    done

    printf "\e[31m*\e[0m [$NAME] All $MAX_ATTEMPTS attempts failed\n"

    if [ "$SILENT_FAIL" = true ]; then
        return 0
    else
        return 1
    fi
}

download_0() {
    local NAME="download_0"
    local NFS_SOURCE="172.16.10.1:/mnt/Local/Container/B/Download"
    local MOUNT_POINT="/mnt/Remote/Servers/srv28013/Download/0"
    local OPTIONS=""
    local MAX_ATTEMPTS=3
    local SLEEP_TIME=60
    local SILENT_FAIL=true
    local attempt=1
    local NFS_IP="${NFS_SOURCE%%:*}"

    [ -d "$MOUNT_POINT" ] || mkdir -p "$MOUNT_POINT" > /dev/null 2>&1

    if mountpoint -q "$MOUNT_POINT"; then
        printf "\e[33m*\e[0m [$NAME] Already mounted at $MOUNT_POINT\n"
        return 0
    fi

    while [ "$attempt" -le "$MAX_ATTEMPTS" ]; do
        printf "\e[32m*\e[0m [$NAME] Attempt $attempt/$MAX_ATTEMPTS — Checking ping to $NFS_IP...\n"

        if ping -c 1 -W 2 "$NFS_IP" > /dev/null 2>&1; then
            printf "\e[32m*\e[0m [$NAME] Ping OK — Trying to mount $NFS_SOURCE at $MOUNT_POINT...\n"

            if mount -t nfs "$NFS_SOURCE" "$MOUNT_POINT" ${OPTIONS:+-o "$OPTIONS"} > /dev/null 2>&1; then
                printf "\e[32m*\e[0m [$NAME] Successfully mounted on attempt $attempt/$MAX_ATTEMPTS\n"
                return 0
            else
                printf "\e[31m*\e[0m [$NAME] Mount failed\n"
            fi
        else
            printf "\e[33m*\e[0m [$NAME] Ping failed — $NFS_IP unreachable\n"
        fi

        attempt=$((attempt + 1))

        if [ "$attempt" -le "$MAX_ATTEMPTS" ]; then
            printf "\e[33m*\e[0m [$NAME] Waiting ${SLEEP_TIME}s before next attempt...\n"
            sleep "$SLEEP_TIME"
        fi
    done

    printf "\e[31m*\e[0m [$NAME] All $MAX_ATTEMPTS attempts failed\n"

    if [ "$SILENT_FAIL" = true ]; then
        return 0
    else
        return 1
    fi
}