#!/bin/bash

# --- CONFIGURAÇÃO ---
# Coloque o hostname que você está esperando aqui
TARGET_HOSTNAME="debian"
LEASE_FILE="/var/lib/misc/dnsmasq.leases"
# --------------------

echo "Monitorando $LEASE_FILE pelo host '$TARGET_HOSTNAME'..."
echo "Aguardando o host aparecer no arquivo de leases..."

# Loop infinito
while true; do
    # Use 'awk' para verificar o arquivo
    # 1. '-v host="$TARGET_HOSTNAME"': Passa a variável do bash para o awk
    # 2. '$4 == host': Verifica se a 4ª coluna é igual ao nosso hostname
    # 3. Se for, imprime os dados formatados e...
    # 4. 'exit': ...diz ao awk para parar de processar o arquivo
    dados_encontrados=$(awk -v host="$TARGET_HOSTNAME" '
        $4 == host {
            printf "MAC: %s\nHostname: %s\nIP: %s\n", $2, $4, $3
            exit
        }
    ' "$LEASE_FILE")

    # Verifica se o comando awk encontrou alguma coisa
    if [ -n "$dados_encontrados" ]; then
        echo "---"
        echo "Host encontrado!"
        echo "$dados_encontrados"
        echo "---"
        break # Sai do loop 'while'
    fi
    
    # Se não encontrou, espera 2 segundos e tenta de novo
    sleep 2
done

echo "Monitoramento concluído."