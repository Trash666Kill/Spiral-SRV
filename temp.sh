smb_mount_unit() {
    local NAME="smb_mount_unit"
    local SMB_SOURCE="//172.30.100.22/hsugisawa"
    local MOUNT_POINT="/mnt/hsugisawa_smb"
    local OPTIONS=""
    local MAX_ATTEMPTS=3
    local SLEEP_TIME=60
    local SILENT_FAIL=true
    local attempt=1
    local SMB_IP="${SMB_SOURCE##//}"
    local SMB_IP="${SMB_IP%%/*}"

    [ -d "$MOUNT_POINT" ] || mkdir -p "$MOUNT_POINT" > /dev/null 2>&1

    if mountpoint -q "$MOUNT_POINT"; then
        printf "\e[33m*\e[0m [$NAME] Already mounted at $MOUNT_POINT\n"
        return 0
    fi

    while [ "$attempt" -le "$MAX_ATTEMPTS" ]; do
        printf "\e[32m*\e[0m [$NAME] Attempt $attempt/$MAX_ATTEMPTS — Checking ping to $SMB_IP...\n"

        if ping -c 1 -W 2 "$SMB_IP" > /dev/null 2>&1; then
            printf "\e[32m*\e[0m [$NAME] Ping OK — Trying to mount $SMB_SOURCE at $MOUNT_POINT...\n"

            if mount -t cifs "$SMB_SOURCE" "$MOUNT_POINT" ${OPTIONS:+-o "$OPTIONS"} > /dev/null 2>&1; then
                printf "\e[32m*\e[0m [$NAME] Successfully mounted on attempt $attempt/$MAX_ATTEMPTS\n"
                return 0
            else
                printf "\e[31m*\e[0m [$NAME] Mount failed\n"
            fi
        else
            printf "\e[33m*\e[0m [$NAME] Ping failed — $SMB_IP unreachable\n"
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