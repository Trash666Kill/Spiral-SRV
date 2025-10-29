#!/bin/bash

# === CONFIGURAÇÕES ===
TARGET_IP="10.0.12.249"   # IP a ser monitorado
TIMEOUT_SECONDS=60        # Tempo máximo de espera em segundos
PINGS_REQUIRED=4          # Quantidade de pings consecutivos necessários
# =====================

echo "Aguardando $PINGS_REQUIRED pings consecutivos de $TARGET_IP (timeout: ${TIMEOUT_SECONDS}s)..."

timeout "$TIMEOUT_SECONDS" bash -c '
  target="'"$TARGET_IP"'"
  required='"$PINGS_REQUIRED"'
  count=0
  while [ $count -lt $required ]; do
    if ping -c 1 -W 1 "$target" &>/dev/null; then
      ((count++))
      echo "Ping OK ($count/$required)"
    else
      count=0
      echo "Ping falhou. Reiniciando contagem..."
    fi
    sleep 1
  done
  echo "$required pings consecutivos bem-sucedidos!"
'

# Verifica o resultado
if [ $? -eq 0 ]; then
  echo "Sucesso! Continuando o script..."
  # === SEU CÓDIGO AQUI ===
else
  echo "Falha: não recebeu $PINGS_REQUIRED pings em $TIMEOUT_SECONDS segundos."
  exit 1
fi