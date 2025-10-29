#!/bin/bash

# Disable bash history
unset HISTFILE

# Execution directory
cd /etc/spawn/VM/

BASE_VM_FILES=(
    "builder/basevm.sh"
    "systemd/scripts/main.sh"
    "systemd/scripts/network.sh"
    "systemd/scripts/firewall/a.sh"
    "systemd/scripts/firewall/c.sh"
    "systemd/scripts/mount.sh"
    "systemd/trigger.service"
)

NEW_VM_FILES=(
    "builder/later.sh"
    "builder/lease-monitor.sh"
    "builder/vm_manager.py"
)
VM_MANAGER="${BASE_VM_FILES[2]}"

BASE_VM_NAME="SpiralVM"
NEW_VM_NAME="vm$(shuf -i 100000-999999 -n 1)"



basevm() {
    # Checks if the files needed to create the base virtual machine exist 
    local missing_files=0 # Variable to count missing files

    for file in "${BASE_VM_FILES[@]}"; do
        if [[ ! -f "$file" ]]; then
            printf "\e[31m*\e[0m ERROR: REQUIRED FILE DOES NOT EXIST: \033[32m%s\033[0m\n" "$file"
            missing_files=$((missing_files + 1)) # Increment the counter
        fi
    done

    # If any file was missing, exit the script
    if [[ $missing_files -gt 0 ]]; then
        printf "\e[31m*\e[0m ERROR: %d REQUIRED FILE(S) MISSING. ABORTING.\n" "$missing_files"
        exit 1
    fi

    # Check if base virtual machine already exists
    output=$(python3 vm_manager.py list)

    if ! echo "$output" | grep -q "$BASE_VM_NAME"; then
        printf "\e[33m*\e[0m ATTENTION: THE BASE VIRTUAL MACHINE \033[32m%s\033[0m DOES NOT EXIST, WAIT...\n" "$BASE_VM_NAME"
    fi

}


basevm