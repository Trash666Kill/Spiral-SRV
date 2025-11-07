#!/bin/bash

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")

VM543178() {
    DOMAIN="VM543178"
    BACKUP_DIR="/mnt/Local/USB/A/Backup/Virt/${DOMAIN}"
    ORIGINAL_FILE=$(sudo virsh domblklist "$DOMAIN" | awk '/vda/ {print $2}')
    BACKUP_FILE="${BACKUP_DIR}/${DOMAIN}-${TIMESTAMP}.qcow2.bak"

    # Verificar se as variáveis obrigatórias estão definidas
    if [ -z "$ORIGINAL_FILE" ] || [ -z "$BACKUP_DIR" ]; then
        echo "ERROR: ORIGINAL_FILE or BACKUP_DIR not set."
        exit 1
    fi

    # Verificar se o arquivo original existe
    if [ ! -f "$ORIGINAL_FILE" ]; then
        echo "ERROR: ORIGINAL FILE $ORIGINAL_FILE NOT FOUND."
        exit 1
    fi

    # Calcular o tamanho do arquivo original
    if ! ORIGINAL_SIZE_BYTES=$(du -b "$ORIGINAL_FILE" 2>/dev/null | awk '{print $1}'); then
        echo "ERROR: Failed to get the size of $ORIGINAL_FILE."
        exit 1
    fi
    ORIGINAL_SIZE_MB=$(awk "BEGIN {printf \"%.2f\", $ORIGINAL_SIZE_BYTES / 1024 / 1024}")

    # Verificar se o diretório de backup é acessível
    if ! AVAILABLE_SPACE_BYTES=$(df --output=avail "$BACKUP_DIR" 2>/dev/null | tail -n 1); then
        echo "ERROR: Failed to get available space in $BACKUP_DIR."
        exit 1
    fi
    AVAILABLE_SPACE_MB=$(awk "BEGIN {printf \"%.2f\", $AVAILABLE_SPACE_BYTES / 1024}")

    # Exibir informações calculadas
    echo "ORIGINAL FILE SIZE: ${ORIGINAL_SIZE_MB} MB"
    echo "AVAILABLE SPACE IN BACKUP DIRECTORY: ${AVAILABLE_SPACE_MB} MB"

    # Adicionar margem de segurança de 10% ao espaço necessário
    SAFETY_MARGIN_MB=$(awk "BEGIN {printf \"%.2f\", $ORIGINAL_SIZE_MB * 0.1}")
    REQUIRED_SPACE_MB=$(awk "BEGIN {printf \"%.2f\", $ORIGINAL_SIZE_MB + $SAFETY_MARGIN_MB}")

    # Exibir requisitos ajustados
    echo "REQUIRED SPACE (WITH 10% SAFETY MARGIN): ${REQUIRED_SPACE_MB} MB"

    # Comparar espaço disponível com o espaço necessário
    if (( $(echo "$AVAILABLE_SPACE_MB < $REQUIRED_SPACE_MB" | bc -l) )); then
        echo "ERROR: NOT ENOUGH SPACE IN $BACKUP_DIR."
        echo "REQUIRED: ${REQUIRED_SPACE_MB} MB, AVAILABLE: ${AVAILABLE_SPACE_MB} MB"
        exit 1
    else
        echo "SUFFICIENT SPACE AVAILABLE. PROCEEDING WITH THE BACKUP..."
    fi

    # Listando os arquivos com extensão .bak em ordem decrescente de tempo
    RECENT_FILES=$(find "$BACKUP_DIR" -type f -name "*.bak" -printf "%T+ %p\n" | sort -r | cut -d' ' -f2)

    # Verificando se há arquivos com menos de 7 dias
    FILES_LESS_THAN_7_DAYS_OLD=$(find "$BACKUP_DIR" -type f -name "*.bak" -mtime -7)

    # Caso não existam arquivos com menos de 7 dias
    if [ -z "$FILES_LESS_THAN_7_DAYS_OLD" ]; then
        echo "NO RECENT FILES FOUND"

        # Encontrar o arquivo antigo mais recente
        MOST_RECENT_OLD_FILE=$(echo "$RECENT_FILES" | tail -n 1)

        if [ -n "$MOST_RECENT_OLD_FILE" ]; then
            echo "KEEPING THE MOST RECENT OLD FILE: $MOST_RECENT_OLD_FILE"
            FILES_TO_KEEP="$MOST_RECENT_OLD_FILE"
        else
            FILES_TO_KEEP=""
        fi
    else
        # Manter apenas os 7 arquivos mais recentes
        FILES_TO_KEEP=$(echo "$RECENT_FILES" | head -n 7)
    fi

    # Listar arquivos para exclusão
    FILES_TO_DELETE=""
    for FILE in $RECENT_FILES; do
        if ! echo "$FILES_TO_KEEP" | grep -q "$FILE"; then
            FILES_TO_DELETE+="$FILE"$'\n'
        fi
    done

    # Exibir e excluir arquivos, se houver
    if [ -n "$FILES_TO_DELETE" ]; then
        echo "DELETING OLD FILES:"
        echo "$FILES_TO_DELETE" | while read -r FILE; do
            if [ -n "$FILE" ]; then
                echo "DELETING: $FILE"
                rm -v "$FILE" >/dev/null 2>&1
            fi
        done
    fi

    # Exibir os arquivos que foram mantidos
    if [ -n "$FILES_TO_KEEP" ]; then
        echo "FILES KEPT:"
        echo "$FILES_TO_KEEP"
    fi

    # Iniciar processo de backup com o virsh
    XML_CONFIG="<domainbackup>
  <disks>
    <disk name='vda' type='file'>
      <target file='${BACKUP_FILE}'/>
      <driver type='qcow2'/>
    </disk>
  </disks>
</domainbackup>"
    echo "ORIGINAL FILE:"
    echo "$ORIGINAL_FILE"
    virsh backup-begin --domain "$DOMAIN" --backupxml <(echo "$XML_CONFIG")

    # Aguardar a conclusão do backup
    while true; do
        # Capturar informações do job
        JOB_INFO=$(virsh domjobinfo "$DOMAIN" --completed 2>/dev/null)
        JOB_STATUS=$(echo "$JOB_INFO" | grep "Job type" | awk '{print $3}')
        if [[ "$JOB_STATUS" == "Completed" ]]; then
            TIME_ELAPSED_MS=$(echo "$JOB_INFO" | grep "Time elapsed" | awk '{print $3}')
            TIME_ELAPSED_MIN=$(awk "BEGIN {printf \"%.2f\", $TIME_ELAPSED_MS / 60000}")
            echo "Backup completed in $TIME_ELAPSED_MIN minutes"
            echo "Absolute path: $BACKUP_FILE"
            break
        fi
        sleep 10
    done
}

# Sequence
VM543178