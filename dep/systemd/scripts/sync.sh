#!/bin/bash

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")

VM123456() {
DOMAIN="VM284254"
BACKUP_DIR="/mnt/Local/Container-B/Backup/SRV78292/Virt/${DOMAIN}"
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

music() {
SOURCE_DIR="/mnt/Local/Pool/A/Music/"
DEST_DIR="/mnt/Local/USB/A/Backup/SRV93237/Music/"
LOG_FILE="/var/log/rsync/music-${TIMESTAMP}.log"

rsync -aHAX --delete --numeric-ids --bwlimit=20480 --info=del,name,stats2 --log-file="$LOG_FILE" "$SOURCE_DIR" "$DEST_DIR"
}

hybrid() {
DATE=$(date +%F_%H-%M-%S)
REMOTE_SHARE="//172.30.100.22/hsugisawa"
ORIG_DIR="/mnt/Remote/Servers/HS-DFS-01/hsugisawa/"
INCR_DIR="/mnt/Local/Pool/A/Backup/Incremental/HS-DFS-01/hsugisawa/"
DIFF_DIR="/mnt/Local/Pool/A/Backup/Differential/HS-DFS-01/hsugisawa/"
LOG_FILE="/var/log/rsync/dfs-hs-01-${DATE}.log"
EXCLUDES_FILE=$(mktemp)
cat > "$EXCLUDES_FILE" <<EOF
*.exe
*.msi
*.dll
*.ini
*.inf
*.cab
*.bat
*.jnlp
*.iso
*.db
*.dat
*.bak
DfsrPrivate/
EOF

# Variáveis de configuração
MOUNT_OPTS="ro"
CIFS_USER="keith.campbell"
CIFS_PASS="e%XEtoU4BJ8yTP"
BW_LIMIT="5120" # Limite de banda em KB/s
RSYNC_USER="root" # Usuário para executar o rsync

# Verifica se o diretório já está montado
if ! mountpoint -q "$ORIG_DIR"; then
    echo "Montando $REMOTE_SHARE em $ORIG_DIR..."
    mount -t cifs "$REMOTE_SHARE" "$ORIG_DIR" -o "$MOUNT_OPTS",username="$CIFS_USER",password="$CIFS_PASS"
    if [ $? -ne 0 ]; then
        echo "Erro ao montar $REMOTE_SHARE"
        return 1
    fi
else
    echo "O diretório $ORIG_DIR já está montado."
fi

# Cria os diretórios caso não existam
for dir in "$ORIG_DIR" "$INCR_DIR" "$DIFF_DIR" "$(dirname "$LOG_FILE")"; do
    if [ ! -d "$dir" ]; then
        echo "Criando diretório: $dir"
        mkdir -p "$dir" || { echo "Erro ao criar $dir"; return 1; }
    fi
done

# Comando rsync
RSYNC_CMD=( rsync --bwlimit="$BW_LIMIT" -ahx --chmod=ugo+r --ignore-errors --exclude-from="$EXCLUDES_FILE" \
            --delete --backup --backup-dir="$DIFF_DIR" --info=del,name,stats2 --log-file="$LOG_FILE" \
            "$ORIG_DIR" "$INCR_DIR" )

# Exibe as variáveis interpretadas, ative apenas em caso de debug
printf "\e[32m%s\e[0m\n" "${RSYNC_CMD[@]}"

# Executa o rsync com usuário específico
su - "$RSYNC_USER" -c "$(printf '%q ' "${RSYNC_CMD[@]}")"
if [ $? -ne 0 ]; then
    echo "Existem alguns erros"
    return 1
fi

# Limpeza de arquivos e diretórios antigos
rm -f "$EXCLUDES_FILE"
find "$DIFF_DIR" -type f -mtime +30 -delete
find "$DIFF_DIR" -type d -empty -delete
}

# Sequence
VM284254; hybrid; music


#!/bin/bash

# Diretórios de referência
DATE=$(date +%F_%H-%M-%S)
INCR_DIR="Incremental"
FULL_DIR="Full"

echo "Verificação: Início do script."

# Verificar se o diretório FULL_DIR existe
if [ -d "$FULL_DIR" ]; then
    echo "Verificação: Diretório $FULL_DIR existe. Apagando..."
    rm -rf "$FULL_DIR"  # Remove o diretório existente
    if [ $? -eq 0 ]; then
        echo "Verificação: $FULL_DIR apagado com sucesso."
    else
        echo "Erro: Falha ao apagar o diretório $FULL_DIR."
        exit 1  # Sai do script em caso de erro ao remover o diretório
    fi
fi

# Criar o diretório FULL_DIR
echo "Criando o diretório $FULL_DIR..."
cp -a --reflink=always "$INCR_DIR" "$FULL_DIR"
if [ $? -eq 0 ]; then
    echo "Verificação: $FULL_DIR criado com sucesso a partir de $INCR_DIR."
else
    echo "Erro: Falha ao criar $FULL_DIR."
    exit 1  # Sai do script em caso de falha na criação
fi


# Obtendo tamanho dos diretórios em bytes
echo "Verificação: Calculando tamanhos dos diretórios."
SIZE_INCR=$(du -sb "$INCR_DIR" 2>/dev/null | awk '{print $1}')
SIZE_FULL=$(du -sb "$FULL_DIR" 2>/dev/null | awk '{print $1}')

# Diferença máxima permitida (2 GiB)
DIFF_MAX=$((2 * 1024 * 1024 * 1024))

echo "Verificação: Calculando diferença entre diretórios."
# Calcula a diferença absoluta
DIFF=$((SIZE_INCR - SIZE_FULL))
DIFF=${DIFF#-}  # Remove sinal negativo

# Se a diferença for maior que 2 GiB, sai do script
if [ "$DIFF" -gt "$DIFF_MAX" ]; then
    echo "Erro: A diferença entre os diretórios é maior que 2 GiB. Abortando."
    exit 1
fi

# Verificar se já existe um backup Full-*.tar.zst
echo "Verificação: Procurando por backups antigos."
OLD_BACKUP=$(find . -maxdepth 1 -name "Full-*.tar.zst")

if [ -n "$OLD_BACKUP" ]; then
    echo "Verificação: Backup encontrado - $OLD_BACKUP"
    # Verificar se o backup tem 30 dias ou mais
    OLD_BACKUP_AGE=$(find . -maxdepth 1 -name "Full-*.tar.zst" -mtime +29)

    if [ -n "$OLD_BACKUP_AGE" ]; then
        echo "Encontrado backup antigo: $OLD_BACKUP"
        echo "Removendo backup antigo..."
        rm -f "$OLD_BACKUP"
        if [ $? -eq 0 ]; then
            echo "Verificação: Backup antigo removido com sucesso."
        else
            echo "Erro: Falha ao remover o backup antigo."
        fi
    else
        echo "Backup existente ainda é recente. Mantendo arquivo: $OLD_BACKUP"
    fi
fi

# Se o diretório FULL_DIR tem 30 dias ou mais, prosseguir com a compactação
if [ -n "$AGE_FULL" ]; then
    # Nome do novo backup
    BACKUP_FILE="Full-${DATE}.tar.zst"

    echo "Compactando $FULL_DIR em $BACKUP_FILE..."
    # Compactar o diretório FULL_DIR
    tar -I 'zstd --threads=0' -cf "$BACKUP_FILE" "$FULL_DIR"

    # Verificar se a compactação foi bem-sucedida
    if [ $? -eq 0 ]; then
        echo "Arquivo compactado com sucesso: $BACKUP_FILE"

        # Testar integridade do arquivo compactado
        echo "Testando integridade do backup..."
        zstd --test "$BACKUP_FILE"

        if [ $? -eq 0 ]; then
            echo "Teste bem-sucedido. Removendo $FULL_DIR..."
            rm -rf "$FULL_DIR"
            if [ $? -eq 0 ]; then
                echo "Verificação: $FULL_DIR removido com sucesso."
            else
                echo "Erro: Falha ao remover $FULL_DIR."
            fi

            # Criar nova cópia do INCR_DIR
            cp -a --reflink=always "$INCR_DIR" "$FULL_DIR"
            if [ $? -eq 0 ]; then
                echo "Verificação: Nova cópia de $INCR_DIR criada em $FULL_DIR."
            else
                echo "Erro: Falha ao criar nova cópia de $INCR_DIR."
            fi
        else
            echo "Erro: Falha no teste de integridade do arquivo compactado! O diretório $FULL_DIR não será excluído."
        fi
    else
        echo "Erro: Falha ao compactar $FULL_DIR! O diretório não será excluído."
    fi
else
    echo "Verificação: O diretório $FULL_DIR ainda não tem 30 dias, nenhuma ação de compressão será realizada."
fi

echo "Verificação: Fim do script."



#TESTE
#!/bin/bash

# Definir o timestamp atual e outros diretórios/nomes de arquivos
TIMESTAMP=$(date +%F_%H-%M-%S)
INCR_DIR="Incremental"   # Diretório de origem
FULL_COMPRESSED="Full-${TIMESTAMP}.tar.zst"
ERROR_LOG="${FULL_COMPRESSED%.tar.zst}_error.log"

# Verificar se o diretório de origem existe
if [ ! -e "$INCR_DIR" ]; then
    echo "Erro: O caminho '$INCR_DIR' não existe!"
    exit 1
fi

# Verificar permissões do diretório
if [ -r "$INCR_DIR" ] && [ -w "$INCR_DIR" ]; then
    echo "O diretório '$INCR_DIR' tem permissões de LEITURA e GRAVAÇÃO."
elif [ -r "$INCR_DIR" ]; then
    echo "O diretório '$INCR_DIR' tem apenas permissão de LEITURA."
elif [ -w "$INCR_DIR" ]; then
    echo "O diretório '$INCR_DIR' tem apenas permissão de GRAVAÇÃO."
else
    echo "Erro: O diretório '$INCR_DIR' não tem permissões de leitura nem gravação!"
    exit 1
fi

# Criar backup e comprimir com zstd
tar -cf - "$INCR_DIR" | zstd --threads=0 -o "$FULL_COMPRESSED" 2> "$ERROR_LOG"

# Verificar a integridade do arquivo comprimido com zstd
zstd --test "$FULL_COMPRESSED"
if [ $? -eq 0 ]; then
    echo "O arquivo comprimido está íntegro (teste zstd)."
else
    echo "Erro: O arquivo comprimido está corrompido (falha no teste zstd)!"
    exit 1
fi

# Verificar o tamanho do arquivo e dividir se for maior que 50 GiB
FILE_SIZE=$(du -BG "$FULL_COMPRESSED" | cut -f1 | tr -d 'G')

if [ "$FILE_SIZE" -gt 50 ]; then
    echo "O arquivo excede 50 GiB. Dividindo em partes de 15 GiB..."
    split -b 15G "$FULL_COMPRESSED" "${FULL_COMPRESSED}.part-"
    rm -f "$FULL_COMPRESSED"
    echo "Arquivo dividido em partes de 15 GiB como ${FULL_COMPRESSED}.part-*"
fi

# Mensagens de status
echo "Backup completo realizado com sucesso!"
echo "Erros (se houver) salvos em: $ERROR_LOG"

# Comando para descompactação
if [ "$FILE_SIZE" -gt 50 ]; then
    echo "Para descompactar o backup, use o seguinte comando:"
    echo "cat ${FULL_COMPRESSED}.part-* | zstd -d | tar -xf -"
else
    echo "Para descompactar o backup, use o seguinte comando:"
    echo "zstd -d $FULL_COMPRESSED | tar -xf -"
fi
