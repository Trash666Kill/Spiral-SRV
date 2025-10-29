#!/bin/bash

# Disable bash history
unset HISTFILE

# Execution directory
cd /etc/spawn/VM/

BASE_BUILDER_FILES=(
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

BASE_VM_FILES=(
    /var/lib/libvirt/images/SpiralVM-Pre.qcow2.bak
    /var/lib/libvirt/images/SpiralVM-Base.qcow2
)
BASE_VM="${BASE_VM_FILES[1]}"
BASE_NAME="SpiralVM"

NEW_VM_FILES="later.sh"
NEW_VM="vm$(shuf -i 100000-999999 -n 1)"

basevm() {
    local missing_files=0 # Variable to count missing files

    # Checks if the files needed to create the base virtual machine exist
    for file in "${BASE_BUILDER_VM_FILES[@]}"; do # Use "${BASE_BUILDER_VM_FILES[@]}" to handle names with spaces
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

    # Checks if the base virtual machine file already exists
    if [[ ! -f "$BASE_VM" ]]; then
        printf "\e[33m*\e[0m ATTENTION: THE BASE VIRTUAL MACHINE FILE \033[32m%s\033[0m DOES NOT EXIST, WAIT...\n" "$BASE_VM"
        
        local PRE_BASE_VM="${BASE_VM_FILES[0]}" # Agora aponta para ...Pre.qcow2.bak

        # Now, check if the "Pre" file exists, which is needed to create the "Base" file
        if [[ ! -f "$PRE_BASE_VM" ]]; then
            printf "\e[31m*\e[0m ERROR: CANNOT CREATE BASE VM. THE PRE-REQUISITE FILE \033[32m%s\033[0m ALSO DOES NOT EXIST.\n" "$PRE_BASE_VM"
            exit 1
        else
            printf "\e[32m*\e[0m INFO: Found \033[32m%s\033[0m. Proceeding to create Base VM...\n" "$PRE_BASE_VM"
            #
            vm_manager.py copy SpiralVM-Pre SpiralVM-Base
            
            # qemu-img create ...
        fi

    fi 
}


basevm