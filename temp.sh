#!/bin/bash

# Disable bash history
unset HISTFILE

# Execution directory
cd /etc/spawn/VM/

BASE="SpiralVM"
BASE_VM_FILES=(
    "builder/basevm.sh"
    "builder/dep/sshd_config"
    "builder/dep/systemd/scripts/firewall/a.sh"
    "builder/dep/systemd/scripts/firewall/c.sh"
    "builder/dep/systemd/scripts/later.sh"
    "builder/dep/systemd/scripts/main.sh"
    "builder/dep/systemd/scripts/mount.sh"
    "builder/dep/systemd/scripts/network.sh"
    "builder/dep/systemd/scripts/prebuild.sh"
    "builder/dep/systemd/scripts/vm_manager.py"
    "builder/dep/systemd/trigger.service"
    "builder/lease-monitor.sh"
)
NEW_VM="vm$(shuf -i 100000-999999 -n 1)"
NEW_VM_FILES="later.sh"

basevm() {
    local missing_files=0 # Variable to count missing files

    # Checks if the files needed to create the base virtual machine exist
    for file in "${BASE_VM_FILES[@]}"; do # Use "${BASE_VM_FILES[@]}" to handle names with spaces
        if [[ ! -f "$file" ]]; then
            # Mensagem em Inglês US, sem o prefixo "VM"
            printf "\e[31m*\e[0m ERROR: REQUIRED FILE DOES NOT EXIST: \033[32m%s\033[0m\n" "$file"
            missing_files=$((missing_files + 1)) # Increment the counter
        fi
    done

    # If any file was missing, exit the script
    if [[ $missing_files -gt 0 ]]; then
        # Mensagem de resumo em Inglês US, sem o prefixo "VM"
        printf "\e[31m*\e[0m ERROR: %d REQUIRED FILE(S) MISSING. ABORTING.\n" "$missing_files"
        exit 1
    fi

}
