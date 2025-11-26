#!/bin/bash

HOSTNAME_ARG="$1"

if [ -z "$HOSTNAME_ARG" ]; then
    echo "Erro: Hostname não fornecido." >&2
    exit 1
fi

HOSTNAME_LOWER="${HOSTNAME_ARG,,}"

# Configurações
LEASES_FILE="/var/lib/misc/dnsmasq.leases"
RESERVATIONS_FILE="/etc/dnsmasq.d/config/reservations"
IP_RANGE_PREFIX="10.0.10"
MAX_LIMIT=248  # Ajustado para bater com seu dhcp-range

is_ip_in_use() {
    local IP=$1
    grep -Fw -q "$IP" "$LEASES_FILE" || grep -Fw -q "$IP" "$RESERVATIONS_FILE"
}

if [[ "$HOSTNAME_LOWER" == ct* ]]; then
    START=3
    STEP=2
elif [[ "$HOSTNAME_LOWER" == vm* ]]; then
    START=2
    STEP=2
else
    START=2
    STEP=1
fi

# O loop agora vai somente até a variável MAX_LIMIT (248)
for ((i=START; i<=MAX_LIMIT; i+=STEP)); do
    CANDIDATE_IP="$IP_RANGE_PREFIX.$i"
    
    if ! is_ip_in_use "$CANDIDATE_IP"; then
        echo "$CANDIDATE_IP"
        exit 0
    fi
done

printf "\033[31m*\033[0m ERROR: NO AVAILABLE ADDRESSES FOUND FOR \033[33m%s\033[0m IN RANGE: \033[32m%s.2-%s\033[0m.\n" "$HOSTNAME_ARG" "$IP_RANGE_PREFIX" "$MAX_LIMIT" >&2
exit 1