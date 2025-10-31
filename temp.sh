#!/bin/bash

newvm() {
    # Inicia a criação da nova máquina vitual a partir da base
    printf "\e[32m*\e[0m CREATING VIRTUAL MACHINE FROM BASE, WAIT...\n"
    
    # 1. COPIE A VM PRIMEIRO E VERIFIQUE SE DEU CERTO
    if ! eval "$VM_MANAGER" copy "$BASE_VM_NAME" "$NEW_VM_NAME"; then
        printf "\e[31m*\e[0m ERROR COPYING VIRTUAL MACHINE \033[32m%s\033[m.\n" "$NEW_VM_NAME"
        exit 1
    fi

    # 2. AGORA QUE O ARQUIVO .conf EXISTE, CAPTURE O MAC
    MAC_ADDRESS=$(sed -n 's/^mac = *//p' "$VM_CONF/$NEW_VM_NAME.conf")

    # 3. VERIFIQUE SE O MAC FOI ENCONTRADO
    if [ -z "$MAC_ADDRESS" ]; then
        printf "\e[31m*\e[0m ERROR: COULD NOT FIND MAC ADDRESS IN \033[32m%s\033[m.\n" "$VM_CONF/$NEW_VM_NAME.conf"
        exit 1
    fi

    reserve() {
        # Obtém o endereço IP do virtual machine a partir do DNS
        local IP_ADDRESS=$(/etc/spawn/grepip.sh)

        # Monta a string de reserva de DNS
        # A variável MAC_ADDRESS agora está disponível
        RESULT="$MAC_ADDRESS,$IP_ADDRESS,$NEW_VM_NAME"
        printf "\033[32m*\033[m IP ADDRESS FIXED.\n"

        # Modifica a configuração do dnsmasq para adicionar a reserva de IP
        kill -SIGHUP $(pidof dnsmasq)
        echo "$RESULT" >> /etc/dnsmasq.d/config/reservations
        kill -SIGHUP $(pidof dnsmasq)
    }

    # 4. AGORA PERGUNTE SOBRE A RESERVA
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
    
    # 5. INICIE A VM E VERIFIQUE SE DEU CERTO
    if ! eval "$VM_MANAGER" run "$NEW_VM_NAME"; then
        printf "\e[31m*\e[0m ERROR STARTING VIRTUAL MACHINE \033[32m%s\033[m.\n" "$NEW_VM_NAME"
        exit 1
    fi
    
    # Aguardando a Máquina Virtual iniciar
    waitobj 10.0.12.249 60 4 "$NEW_VM_NAME"
    # Vigorando o novo hostname
    ssh -p 22 root@10.0.12.249 "sed -i -E \"s/(127\\.0\\.1\\.1\\s+).*/\\1$NEW_VM_NAME/\" /etc/hosts"
    ssh -p 22 root@10.0.12.249 "rm /etc/hostname && printf "$NEW_VM_NAME" > /etc/hostname"
    # Copia, torna o script later.sh executável e o executa na virtual machine
    scp -P 22 /etc/spawn/VM/builder/later.sh  root@10.0.12.249:/root
    ssh -p 22 root@10.0.12.249 "chmod +x /root/later.sh && /root/later.sh"
    # Aguardar a nova máquina virtual receber parâmetros de rede via DHCP
    sed -i "s/TARGET_HOSTNAME=\"[^\"]*\"/TARGET_HOSTNAME=\"$NEW_VM_NAME\"/" /etc/spawn/VM/builder/lease-monitor.sh
    bash "/etc/spawn/VM/builder/lease-monitor.sh"
}

#
# ... (função basevm() fica aqui) ...
#

# PONTO DE ENTRADA DO SCRIPT
# Esta linha inicia todo o processo.
basevm