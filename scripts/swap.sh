swap() {
    readonly DEVICE_SWAP_UUID=""

    echo "INFO: Validating script configuration..."
    if [[ -z "$DEVICE_SWAP_UUID" ]]; then
        echo "ERROR: The DEVICE_SWAP_UUID variable is not set." >&2
        exit 1
    fi

    echo "INFO: Configuring ZRAM..."

    # Carrega o módulo
    modprobe zram 2>/dev/null

    local ZRAM_SIZE
    ZRAM_SIZE="$(($(grep -Po 'MemTotal:\s*\K\d+' /proc/meminfo)/2))KiB"

    local ZRAM_DEV
    ZRAM_DEV=$(zramctl --find --algorithm zstd --size "$ZRAM_SIZE")

    if [[ -n "$ZRAM_DEV" ]]; then
        echo "INFO: ZRAM device created at $ZRAM_DEV with size $ZRAM_SIZE"

        mkswap -U clear "$ZRAM_DEV" >/dev/null
        swapon --discard --priority 100 "$ZRAM_DEV"

        if [[ $? -eq 0 ]]; then
            echo "SUCCESS: ZRAM active on $ZRAM_DEV (Priority 100)."
        else
            echo "ERROR: Failed to activate swapon on $ZRAM_DEV." >&2
        fi
    else
        echo "ERROR: Could not create/find a ZRAM device with zramctl." >&2
    fi

    echo

    echo "INFO: Activating disk swap for hibernation support..."

    swapoff -U "${DEVICE_SWAP_UUID}" 2>/dev/null

    swapon --priority -10 -U "${DEVICE_SWAP_UUID}"

    if [[ $? -eq 0 ]]; then
        echo "SUCCESS: Disk Swap activated (Priority -10)."
    else
        echo "ERROR: Failed to activate Disk Swap. Check UUID." >&2
    fi

    echo "INFO: Current swap status:"
    swapon --show
}