HTYPE=$(hostnamectl chassis)





#!/bin/bash

# Atualiza e instala o cloud kernel sem prompts
apt-get update
apt-get install -y linux-image-cloud-amd64 linux-headers-cloud-amd64

# Cria a lista de pacotes de kernel a serem removidos
# - Ignora qualquer kernel com "cloud" no nome
# - Ignora o kernel atualmente em execução (segurança)
TO_REMOVE=$(dpkg-query -W -f='${Package}\n' 'linux-image-[0-9]*' | grep -v "cloud" | grep -v "$(uname -r)")

# Adiciona o metapacote genérico à lista de remoção para evitar futuras instalações
if dpkg-query -W -f='${Status}' linux-image-amd64 2>/dev/null | grep -q "ok installed"; then
    TO_REMOVE="$TO_REMOVE linux-image-amd64"
fi

# Se a lista não estiver vazia, remove os pacotes e limpa o sistema
if [ -n "$TO_REMOVE" ]; then
    apt-get purge -y $TO_REMOVE
    apt-get autoremove --purge -y
fi

echo "Cloud kernel instalado e kernels antigos removidos."
echo "É necessário reiniciar o sistema. Execute: sudo reboot"