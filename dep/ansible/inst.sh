#!/bin/bash

ansible() {
printf "\e[32m*\e[0m SETTING UP ANSIBLE\n"

# Instala os pacotes necessários
apt -y install ansible > /dev/null 2>&1

# Adiciona os arquivos e define as permissões necessários
cp -r ansible /etc; chmod 600 /etc/ansible/inventory
}