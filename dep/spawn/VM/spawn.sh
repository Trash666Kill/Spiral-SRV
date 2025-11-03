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

VM_CONF="/root/.services/virtual-machine/vms"
BASE_VM_NAME="SpiralVM"
NEW_VM_NAME="vm$(shuf -i 100000-999999 -n 1)"
NEW_VM_IP=10.0.12.249

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
        waitobj $NEW_VM_IP 60 4 "$BASE_VM_NAME"
        # Contruindo a base
        ssh -p 22 -q root@$NEW_VM_IP "mkdir /root/builder"
        scp -P 22 -q /etc/spawn/VM/builder/basevm.sh root@$NEW_VM_IP:/root/builder
        scp -P 22 -q -r /etc/spawn/VM/systemd root@$NEW_VM_IP:/root/builder
        ssh -p 22 -q root@$NEW_VM_IP "cd /root/builder && chmod +x basevm.sh && ./basevm.sh"
        # Chamada para a função responsável pela criação no novo convidado baseado na base
        newvm

    else
        printf "\e[32m*\e[0m INFO: A VM base \033[32m%s\033[0m já existe. Pulando criação.\n" "$BASE_VM_NAME"
        echo "criando nova vm..."
        newvm
    fi
}

newvm() {
    # Inicia a criação da nova máquina vitual a partir da base
    printf "\e[32m*\e[0m CREATING VIRTUAL MACHINE FROM BASE, WAIT...\n"
    sleep 5
    eval "$VM_MANAGER" stop "$BASE_VM_NAME"

    if ! eval "$VM_MANAGER" copy "$BASE_VM_NAME" "$NEW_VM_NAME"; then
        printf "\e[31m*\e[0m ERROR COPYING VIRTUAL MACHINE \033[32m%s\033[m.\n" "$NEW_VM_NAME"
        exit 1
    fi

    MAC_ADDRESS=$(sed -n 's/^mac = *//p' "$VM_CONF/$NEW_VM_NAME.conf")

    if [ -z "$MAC_ADDRESS" ]; then
        printf "\e[31m*\e[0m ERROR: COULD NOT FIND MAC ADDRESS IN \033[32m%s\033[m.\n" "$VM_CONF/$NEW_VM_NAME.conf"
        exit 1
    fi

    reserve() {
        # Obtém o endereço IP do virtual machine a partir do DNS
        local IP_ADDRESS=$(/etc/spawn/grepip.sh)

        # Monta a string de reserva de DNS
        RESULT="$MAC_ADDRESS,$IP_ADDRESS,$NEW_VM_NAME"
        printf "\033[32m*\033[m IP ADDRESS FIXED.\n"

        # Modifica a configuração do dnsmasq para adicionar a reserva de IP
        kill -SIGHUP $(pidof dnsmasq)
        echo "$RESULT" >> /etc/dnsmasq.d/config/reservations
        kill -SIGHUP $(pidof dnsmasq)
    }

    read -p "WANT TO RESERVE THE NEXT AVAILABLE IP [y/n]? " x
    case "$x" in
        y)
            reserve  # Chama a função para coletar o próximo endereço de IP válido e fixa-o
            ;;
        n)
            printf "\033[33m*\033[m ATTENTION: A DYNAMIC IP ADDRESS WILL BE ASSIGNED TO THE VIRTUAL machine\n"
            ;;
        *)
            printf "\033[31m*\033[m ERROR: INVALID CHOICE, TYPE \033[32m'y'\033[m IF YOU WANT TO FIX AN IP ADDRESS IN THE VIRTUAL machine AND \033[32m'n'\033[m IF YOU PREFER TO LEAVE IT DYNAMIC\n"
            ;;
        esac

    # Inicia o novo virtual machine
    printf "\033[32m*\033[m STARTING...\n"
    
    if ! eval "$VM_MANAGER" run "$NEW_VM_NAME"; then
        printf "\e[31m*\e[0m ERROR STARTING VIRTUAL MACHINE \033[32m%s\033[m.\n" "$NEW_VM_NAME"
        exit 1
    fi

    # Aguardando a Máquina Virtual iniciar
    waitobj $NEW_VM_IP 60 4 "$NEW_VM_NAME"
    # Vigorando o novo hostname
    ssh -p 22 root@$NEW_VM_IP "sed -i -E \"s/(127\\.0\\.1\\.1\\s+).*/\\1$NEW_VM_NAME/\" /etc/hosts"
    ssh -p 22 root@$NEW_VM_IP "rm /etc/hostname && printf "$NEW_VM_NAME" > /etc/hostname"
    # Copia, torna o script later.sh executável e o executa na virtual machine
    scp -P 22 /etc/spawn/VM/builder/later.sh  root@$NEW_VM_IP:/root
    ssh -p 22 root@$NEW_VM_IP "chmod +x /root/later.sh && /root/later.sh"
    # Aguarda a nova máquina virtual receber parâmetros de rede via DHCP
    sed -i "s/TARGET_HOSTNAME=\"[^\"]*\"/TARGET_HOSTNAME=\"$NEW_VM_NAME\"/" /etc/spawn/VM/builder/lease-monitor.sh
    bash "/etc/spawn/VM/builder/lease-monitor.sh"
}

main() {
    basevm
}

# Execute main function
main