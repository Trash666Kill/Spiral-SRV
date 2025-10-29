#!/bin/bash

# --- CONFIGURAÇÃO ---
# Defina o hostname que você está procurando
TARGET_HOSTNAME=""

# Defina o tempo máximo de espera (em segundos)
TIMEOUT_SECONDS=60

# Caminho para o arquivo de leases
LEASE_FILE="/var/lib/misc/dnsmasq.leases"

# Intervalo de verificação (em segundos)
POLL_INTERVAL_SECONDS=2
# --------------------


# --- Validação ---
if [ ! -f "$LEASE_FILE" ]; then
    # Envia mensagens de erro para o stderr (>&2)
    echo "Erro: Arquivo de leases não encontrado em $LEASE_FILE" >&2
    exit 2 # Sai com código de erro 2 (Arquivo não encontrado)
fi
# ------------------


echo "Monitorando $LEASE_FILE..."
echo "Procurando por Host: '$TARGET_HOSTNAME'"
echo "Timeout:            $TIMEOUT_SECONDS segundos"
echo "---"

# Grava o tempo de início (em segundos desde 1970)
start_time=$(date +%s)

# Calcula o tempo final exato
end_time=$((start_time + TIMEOUT_SECONDS))

# Loop principal
# Continua enquanto o tempo atual for MENOR que o tempo final
while [ $(date +%s) -lt $end_time ]; do

    # Usa 'awk' para verificar o arquivo
    # -v host="$TARGET_HOSTNAME"': Passa a variável do bash para o awk
    # '$4 == host': Verifica se a 4ª coluna é igual ao nosso hostname
    dados_encontrados=$(awk -v host="$TARGET_HOSTNAME" '
        $4 == host {
            printf "MAC: %s\nHostname: %s\nIP: %s\n", $2, $4, $3
            exit
        }
    ' "$LEASE_FILE")

    # Verifica se o comando awk encontrou alguma coisa
    if [ -n "$dados_encontrados" ]; then
        echo "Host encontrado!"
        echo "$dados_encontrados"
        exit 0 # Sucesso!
    fi
    
    # Se não encontrou, espera e tenta de novo
    sleep $POLL_INTERVAL_SECONDS
done

# --- Timeout Atingido ---
# Se o script chegou até aqui, o loop 'while' terminou sem sucesso.
echo "Erro: Timeout de $TIMEOUT_SECONDS segundos atingido." >&2
echo "Host '$TARGET_HOSTNAME' não foi encontrado." >&2
exit 1 # Sai com código de erro 1 (Timeout)