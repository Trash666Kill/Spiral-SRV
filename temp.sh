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

BASE_VM_FILES=(
    "basect.sh"
    "systemd/scripts/main.sh"
    "systemd/scripts/network.sh"
)
NEW_VM="vm$(shuf -i 100000-999999 -n 1)"
NEW_VM_FILES="later.sh"

basect() {
    # Checks if the files needed to create the base virtual machine exist
    for file in $BASE_VM_FILES; do
        if [[ ! -f "$file" ]]; then
            printf "VM[31m*\e[0m ERROR: FILES REQUIRED TO BUILD THE BASE VIRTUAL MACHINE \033[32m%s\033[0m DO NOT EXIST.\n" "$file"
            exit 1
        fi
    done

    # Verifica se a virtual machine base já existe
    if ! lxc-ls --filter "^${BASE}$" | grep -q "${BASE}"; then
        printf "\e[33m*\e[0m ATTENTION: THE BASE VIRTUAL MACHINE \033[32m%s\033[0m DOES NOT EXIST, WAIT...\n" "$BASE"

        # Cria o virtual machine base se não existir
        lxc-create --name "${BASE}" --template download -- --dist debian --release "${RELEASE}" --arch "${ARCH}" > /dev/null

        # Copia o script de configuração para o diretório do virtual machine
        cp basect.sh /var/lib/lxc/"${BASE}"/rootfs/root/

        # Verifica se a cópia foi bem-sucedida
        if [ $? -ne 0 ]; then
            printf "\e[31m*\e[0m ERROR: FAILED TO CREATE BASE VIRTUAL MACHINE \033[32m%s\033[0m.\n" "$BASE"
            exit 1
        fi

        # Tenta iniciar o virtual machine
        if ! lxc-start --name "${BASE}"; then
            printf "\e[31m*\e[0m ERROR: VIRTUAL MACHINE \033[32m%s\033[0m FAILED TO START.\n" "$BASE"
            exit 1
        fi

        # Tenta conectar o virtual machine à internet
        printf "\e[32m*\e[0m TRYING TO CONNECT TO THE INTERNET, WAIT...\n"
        if ! lxc-attach --name "${BASE}" -- dhclient eth0; then
            printf "\e[31m*\e[0m ERROR: VIRTUAL MACHINE \033[32m%s\033[0m WAS UNABLE TO CONNECT TO THE INTERNET.\n" "$BASE"
            lxc-stop --name "${BASE}"
            exit 1
        fi

        # Realiza as operações de construção e configuração no virtual machine
        printf "\e[32m*\e[0m BUILDING BASE, WAIT...\n"
        lxc-attach --name "${BASE}" -- chmod +x /root/basect.sh
        lxc-attach --name "${BASE}" -- /root/basect.sh
        cp systemd/trigger.service /var/lib/lxc/"${BASE}"/rootfs/etc/systemd/system
        cp systemd/scripts/{main.sh,network.sh} /var/lib/lxc/"${BASE}"/rootfs/root/.services
        lxc-attach --name "${BASE}" -- chmod 700 /root/.services/{main.sh,network.sh}
        lxc-attach --name "${BASE}" -- systemctl daemon-reload && lxc-attach --name "${BASE}" -- systemctl enable trigger --quiet

        # Verifica se a atualização ou instalação dos pacotes falhou
        if [ $? -ne 0 ]; then
            printf "\e[31m*\e[0m ERROR: COULD NOT UPDATE OR INSTALL PACKAGES IN VIRTUAL MACHINE \033[32m%s\033[0m.\n" "$BASE"
            lxc-stop --name "${BASE}"
            exit 1
        fi

        # Para o virtual machine após a conclusão
        lxc-stop --name "${BASE}"
        sleep 5

        printf "\e[32m*\e[0m VIRTUAL MACHINE \033[32m%s\033[0m SUCCESSFULLY CREATED AND CONFIGURED.\n" "$BASE"
    else
        printf "\e[32m*\e[0m BASE VIRTUAL MACHINE ALREADY EXISTS.\n"
    fi
}

newct() {
# Verifica se os arquivos necessários para criar o novo virtual machine existem
for file in $NEW_VM_FILES; do
    if [[ ! -f "$file" ]]; then
        printf "\e[31m*\e[0m ERROR: FILES REQUIRED TO BUILD THE NEW VIRTUAL MACHINE \033[32m%s\033[0m DO NOT EXIST.\n" "$file"
        exit 1
    fi
done

# Inicia a criação do novo virtual machine a partir do virtual machine base
printf "\e[32m*\e[0m CREATING VIRTUAL MACHINE FROM BASE, WAIT...\n"
lxc-copy --name "${BASE}" --newname "${NEW_VM}"

# Verifica se a cópia do virtual machine foi bem-sucedida
if [ $? -eq 0 ]; then
    printf "\033[32m*\033[0m VIRTUAL MACHINE CREATED SUCCESSFULLY\n"

    # Caminho do arquivo de configuração do virtual machine
    local lxc_config_path="/var/lib/lxc/$NEW_VM/config"

    # Verifica se o arquivo de configuração do virtual machine existe
    if [ ! -f "$lxc_config_path" ]; then
        printf "\e[31m*\e[0m ERROR: VIRTUAL MACHINE CONFIGURATION FILE \033[32m%s\033[0m NOT FOUND\n" "$NEW_VM"
        exit 1
    fi

    # Gera um UUID e cria um endereço MAC único
    local uuid=$(uuidgen | tr -d '-' | cut -c 1-12)
    local MAC_ADDRESS="00:16:3e:${uuid:0:2}:${uuid:2:2}:${uuid:4:2}"

    # Atualiza ou adiciona a configuração de endereço MAC no arquivo de configuração do virtual machine
    sed -i '/lxc.net.0.hwaddr/d' "$lxc_config_path"
    echo "lxc.net.0.hwaddr = $MAC_ADDRESS" >> "$lxc_config_path"

    reserve() {
    # Obtém o endereço IP do virtual machine a partir do DNS
    local IP_ADDRESS=$(/etc/spawn/CT/grepip.sh)

    # Monta a string de reserva de DNS
    RESULT="$MAC_ADDRESS,$IP_ADDRESS,$NEW_VM"
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
            printf "\033[33m*\033[0m ATTENTION: A DYNAMIC IP ADDRESS WILL BE ASSIGNED TO THE VIRTUAL MACHINE\n"
            ;;
        *)
            printf "\033[31m*\033[0m ERROR: INVALID CHOICE, TYPE \033[32m'y'\033[0m IF YOU WANT TO FIX AN IP ADDRESS IN THE VIRTUAL MACHINE AND \033[32m'n'\033[0m IF YOU PREFER TO LEAVE IT DYNAMIC\n"
            ;;
        esac

    # Inicia o novo virtual machine
    printf "\033[32m*\033[0m STARTING...\n"
    cp later.sh /var/lib/lxc/"${NEW_VM}"/rootfs/root/
    lxc-start --name "${NEW_VM}"

    # Torna o script later.sh executável e o executa dentro do virtual machine
    lxc-attach --name "${NEW_VM}" -- chmod +x /root/later.sh
    lxc-attach --name "${NEW_VM}" -- /root/later.sh
else
    printf "\e[31m*\e[0m ERROR CREATING VIRTUAL MACHINE \033[32m%s\033[0m.\n" "$NEW_VM"
    exit 1
fi
}

main() {
    basect
    newct
}

# Execute main function
main