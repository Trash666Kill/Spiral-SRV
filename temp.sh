newvm() {
# Inicia a criação da nova máquina vitual a partir da base
    printf "\e[32m*\e[0m CREATING VIRTUAL MACHINE FROM BASE, WAIT...\n"
    eval "$VM_MANAGER" copy "$BASE_VM_NAME" "$NEW_VM_NAME"

    reserve() {
    # Obtém o endereço IP do virtual machine a partir do DNS
    local IP_ADDRESS=$(/etc/spawn/grepip.sh)

    # Monta a string de reserva de DNS
    RESULT="$MAC_ADDRESS,$IP_ADDRESS,$NEW_VM_NAME"
    printf "\033[32m*\033[0m IP ADDRESS FIXED.\n"

    # Modifica a configuração do dnsmasq para adicionar a reserva de IP
    kill -SIGHUP $(pidof dnsmasq)
    echo "$RESULT" >> /etc/dnsmasq.d/config/reservations
    kill -SIGHUP $(pidof dnsmasq)
    }

    # Pergunta ao usuário se o endereço de IP deve ser reservado
    read -p "WANT TO RESERVE THE NEXT AVAILABLE IP [y/n]? " x
    case "$x" in
        y)
            reserve  # Chama a função para coletar o próximo endereço de IP válido e fixa-o
            ;;
        n)
            printf "\033[33m*\033[0m ATTENTION: A DYNAMIC IP ADDRESS WILL BE ASSIGNED TO THE VIRTUAL machine\n"
            ;;
        *)
            printf "\033[31m*\033[0m ERROR: INVALID CHOICE, TYPE \033[32m'y'\033[0m IF YOU WANT TO FIX AN IP ADDRESS IN THE VIRTUAL machine AND \033[32m'n'\033[0m IF YOU PREFER TO LEAVE IT DYNAMIC\n"
            ;;
        esac

    # Inicia o novo virtual machine
    printf "\033[32m*\033[0m STARTING...\n"
    eval "$VM_MANAGER" run "$NEW_VM_NAME"
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
else
    printf "\e[31m*\e[0m ERROR CREATING VIRTUAL MACHINE \033[32m%s\033[0m.\n" "$NEW_VM_NAME"
    exit 1
fi
}