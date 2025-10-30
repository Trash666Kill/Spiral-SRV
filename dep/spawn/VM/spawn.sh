#!/bin/bash

# Disable bash history
unset HISTFILE

# Execution directory
cd /etc/spawn/VM/

PRE_BASE_VM="SpiralVM-Pre"

BASE_VM_FILES=(
    "builder/basevm.sh"
    "systemd/scripts/main.sh"
    "systemd/scripts/network.sh"
    "systemd/scripts/firewall/a.sh"
    "systemd/scripts/firewall/c.sh"
    "systemd/scripts/mount.sh"
    "systemd/trigger.service"
    "builder/vm_manager.py"
)

NEW_VM_FILES=(
    "builder/later.sh"
    "builder/lease-monitor.sh"
    "builder/vm_manager.py"
)
VM_MANAGER="python3 ${NEW_VM_FILES[2]}"

BASE_VM_NAME="SpiralVM"
NEW_VM_NAME="vm$(shuf -i 100000-999999 -n 1)"

waitobj() {
    local TARGET_IP="$1"
    local TIMEOUT_SECONDS="$2"
    local PINGS_REQUIRED="$3"
    local OBJECT="$4"

    local MAX_ATTEMPTS="$TIMEOUT_SECONDS"

    printf "\e[33m*\e[0m INFO: AWAITING RESPONSE FROM $OBJECT.\n"

    timeout "$(( TIMEOUT_SECONDS + 10 ))" bash -c '
        target="'"$TARGET_IP"'"
        required='"$PINGS_REQUIRED"'
        max_attempts='"$MAX_ATTEMPTS"'
        count=0
        fail_count=0
        attempt=0

        while [ $count -lt $required ] && [ $attempt -lt $max_attempts ]; do
            ((attempt++))

            if ping -c 1 -W 1 "$target" &>/dev/null; then
                ((count++))
                fail_count=0
                echo -e "\e[32m*\e[0m ATTEMPT OK ($count/$required)"
            else
                ((fail_count++))
                count=0
                echo -e "\e[33m*\e[0m AWAITING RESPONS ($fail_count/$max_attempts)"
            fi

            sleep 1
        done

        if [ $count -lt $required ]; then
            exit 1
        fi
    '

    if [ $? -eq 0 ]; then
        printf "\e[32m*\e[0m THE OBJECT $OBJECT RESPONDED.\n"
    else
        printf "\e[31m*\e[0m ERROR: THE OBJECT $OBJECT DID NOT RESPOND.\n"
        exit 1
    fi
}

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
    output=$(eval "$VM_MANAGER" list)

    if ! echo "$output" | grep -qE "${BASE_VM_NAME}[[:space:]]"; then
        printf "\e[33m*\e[0m ATTENTION: THE BASE VIRTUAL MACHINE \033[32m%s\033[0m DOES NOT EXIST, WAIT...\n" "$BASE_VM_NAME"

        # Create SpiralVM-Base
        eval "$VM_MANAGER" copy "$PRE_BASE_VM" "$BASE_VM_NAME"
        sleep 5
        eval "$VM_MANAGER" run "$BASE_VM_NAME"
        # Aguardando a Máquina Virtual iniciar
        waitobj 10.0.12.249 60 4 "$BASE_VM_NAME"
        # Contruindo a base
        ssh -p 22 -q root@10.0.12.249 "mkdir /root/builder"
        scp -P 22 -q /etc/spawn/VM/builder/basevm.sh root@10.0.12.249:/root/builder
        scp -P 22 -q -r /etc/spawn/VM/systemd root@10.0.12.249:/root/builder
        ssh -p 22 -q root@10.0.12.249 "cd /root/builder && chmod +x basevm.sh && ./basevm.sh"

    else
        printf "\e[32m*\e[0m INFO: A VM base \033[32m%s\033[0m já existe. Pulando criação.\n" "$BASE_VM_NAME"
        echo "criando nova vm..."
    fi
}

newvm() {
    # Checks if the files needed to create the new virtual machine exist 
    local missing_files=0 # Variable to count missing files

    for file in "${NEW_VM_FILES[@]}"; do
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

}