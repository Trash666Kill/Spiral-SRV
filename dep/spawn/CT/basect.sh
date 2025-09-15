#!/bin/bash
#SCRIPT FOR CREATING THE BASE LXC CONTAINER

# Desativa histórico bash
unset HISTFILE

global() {
# Definição de variáveis de rede
SUBNET=10.0.10          # Sub-rede
MASK=24                 # Máscara de rede
GW=${SUBNET}.254        # Endereço do gateway
INTERFACE=eth0          # Interface de rede utilizada
LDNSS=${GW}             # Servidor DNS local
}

connectiontest() {
# Testa a conectividade com os repositórios do Debian
if ! ping -4 -c 4 debian.org &>/dev/null; then
    printf "ERROR: UNABLE TO CONNECT TO \e[32mDEBIAN REPOSITORIES\e[0m\n"
    exit 1
fi
}

target_user() {
# Instala o sudo, caso não esteja presente
apt -y install sudo > /dev/null 2>&1

# Configura o perfil para desabilitar o histórico de comandos do usuário
sed -i '$ a unset HISTFILE\nexport HISTSIZE=0\nexport HISTFILESIZE=0\nexport HISTCONTROL=ignoreboth' /etc/profile

printf "\e[32m*\e[0m CREATING USER \e[32mSysOp\e[0m\n"

# Cria o grupo 'sysop' com o GID 1001
groupadd -g 1001 sysop

# Cria o usuário 'sysop' com UID 1001, associando-o ao grupo 'sysop' e configurando o shell como bash
useradd -m -u 1001 -g 1001 -c "SysOp" -s /bin/bash sysop

# Armazena o nome do usuário 'sysop' na variável TARGET_USER
TARGET_USER=$(grep 1001 /etc/passwd | cut -f 1 -d ":")
}

packages() {
printf "\e[32m*\e[0m INSTALLING PACKAGE CATEGORIES: TEXT EDITOR\n"
EDITOR="vim"
apt -y install $EDITOR > /dev/null 2>&1

printf "\e[32m*\e[0m NETWORK TOOLS\n"
NETWORK="nfs-common net-tools"
apt -y install $NETWORK > /dev/null 2>&1

printf "\e[32m*\e[0m SCRIPTING AND AUTOMATION SUPPORT\n"
SCRIPTING="sshpass python3-apt"
apt -y install $SCRIPTING > /dev/null 2>&1

printf "\e[32m*\e[0m SYSTEM MONITORING AND DIAGNOSTICS\n"
MONITORING="screen"
apt -y install $MONITORING > /dev/null 2>&1

printf "\e[32m*\e[0m ADDITIONAL UTILITIES.\n"
EXTRA_UTILS="uuid-runtime pwgen"
apt -y install $EXTRA_UTILS > /dev/null 2>&1
}

directories() {
printf "\e[32m*\e[0m CREATING DIRECTORIES\n"

# Cria diretórios para serviços e dados temporários
mkdir -p /mnt/{Temp,Services}; chown "$TARGET_USER":"$TARGET_USER" -R /mnt/*
mkdir -p /root/{Temp,.services/scheduled,.crypt}; chmod 600 /root/.crypt

# Cria diretórios específicos para o usuário alvo
su - "$TARGET_USER" -c "mkdir -p /home/$TARGET_USER/{Temp,.services/scheduled,.crypt}"
}

trigger() {
printf '[Unit]
Description=The beginning

[Service]
ExecStartPre=/bin/sleep 10
Type=oneshot
ExecStart=/root/.services/trigger.sh
RemainAfterExit=true
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target' > /etc/systemd/system/trigger.service; systemctl enable trigger --quiet

printf '#!/bin/bash

# Network
#%s
systemctl restart systemd-resolved
dhclient -r %s
dhclient %s

# Shots
sleep 15
if ping -4 -c 4 %s &> /dev/null; then
    #/home/sysop/.services/service.sh
exit 0
fi
exit 1' "$INTERFACE" "$INTERFACE" "$INTERFACE" "$LDNSS" > /root/.services/trigger.sh; chmod 700 /root/.services/trigger.sh
}

ssh() {
printf "\e[32m*\e[0m SETTING UP SSH\n"

# Instala os pacotes necessários para o SSH
apt -y install openssh-server sshfs autossh > /dev/null 2>&1

# Remove o arquivo de configuração do SSH antigo
rm /etc/ssh/sshd_config

# Cria um novo arquivo de configuração para o SSH com as configurações desejadas
printf 'Include /etc/ssh/sshd_config.d/*.conf

Port 22
AllowTcpForwarding no
GatewayPorts no

PubkeyAuthentication yes
PermitRootLogin no

ChallengeResponseAuthentication no

UsePAM yes

X11Forwarding yes
PrintMotd no
PrintLastLog no

AcceptEnv LANG LC_*

Subsystem       sftp    /usr/lib/openssh/sftp-server' > /etc/ssh/sshd_config; chmod 644 /etc/ssh/sshd_config

# Remove o arquivo motd e cria um novo arquivo vazio
rm /etc/motd; touch /etc/motd

# Cria diretórios e arquivos necessários para a configuração SSH do usuário alvo
su - "$TARGET_USER" -c "mkdir /home/$TARGET_USER/.ssh"
chmod 700 /home/"$TARGET_USER"/.ssh

# Cria o arquivo authorized_keys e define permissões
su - "$TARGET_USER" -c "echo | touch /home/$TARGET_USER/.ssh/authorized_keys"
chmod 600 /home/"$TARGET_USER"/.ssh/authorized_keys

# Gera uma chave SSH para o usuário
su - "$TARGET_USER" -c "echo | ssh-keygen -t rsa -b 4096 -N '' <<<$'\n'" > /dev/null 2>&1

# Define permissões adequadas para o diretório .ssh do root e cria o arquivo authorized_keys
chmod 600 /root/.ssh
touch /root/.ssh/authorized_keys; chmod 600 /root/.ssh/authorized_keys

# Gera uma chave SSH para o root
ssh-keygen -t rsa -b 4096 -N '' <<<$'\n' > /dev/null 2>&1
}

later() {
printf "\e[32m*\e[0m PERFORMING SUBSEQUENT PROCEDURES\n"

# Instala o systemd-resolved
apt -y install systemd-resolved > /dev/null 2>&1

# Desabilita o serviço systemd-networkd
systemctl disable --now systemd-networkd --quiet

# Desabilita o socket do systemd-networkd
systemctl disable --now systemd-networkd.socket --quiet

# Desabilita o serviço systemd-resolved
systemctl disable --now systemd-resolved --quiet
}

finish() {
# Remove pacotes desnecessários
apt -y autoremove > /dev/null 2>&1

# Remove o script que está sendo executado (o próprio arquivo do script)
rm -- "$0"
}

# Sequence
global; connectiontest; target_user; packages;
directories; trigger; ssh; later;
finish