#!/bin/bash
# SCRIPT IN CHARGE OF CHECKING THE IP ADDRESS AVAILABLE ON THE DNS SERVER AND INFORMING THE 'SPAWN' TOOL

# Desativa histórico bash
unset HISTFILE

LEASES_FILE="/var/lib/misc/dnsmasq.leases"
RESERVATIONS_FILE="/etc/dnsmasq.d/config/reservations"
IP_RANGE="10.0.10"

# Função para verificar se o IP está em uso em qualquer um dos arquivos
is_ip_in_use() {
local IP=$1
grep -q "$IP" "$LEASES_FILE" || grep -q "$IP" "$RESERVATIONS_FILE"
}

# Loop para encontrar o IP disponível mais próximo do '1'
for i in {2..254}; do
    CANDIDATE_IP="$IP_RANGE.$i"
    if ! is_ip_in_use "$CANDIDATE_IP"; then
        echo "$CANDIDATE_IP"
        exit 0
    fi
done

printf "\033[31m*\033[0m ERROR: NO AVAILABLE ADDRESSES FOUND IN RANGE: \033[32m%s\033[0m.\n" "$IP_RANGE"
exit 1