de() {
printf "\e[32m*\e[0m SETTING UP DESKTOP ENVIRONMENT AND VNC SERVER\n"

TARGET_USER=$(grep 1001 /etc/passwd | cut -f 1 -d ":")

# Instala os pacotes necessários para o ambiente desktop
apt -y install xorg dbus-x11 lightdm openbox obconf hsetroot terminator lxpanel \
lxtask lxsession-logout lxappearance numlockx progress arc-theme ffmpegthumbnailer \
gpicview galculator l3afpad compton pcmanfm firefox-esr engrampa \
tigervnc-standalone-server tigervnc-common novnc > /dev/null 2>&1

# Configura o LightDM com o arquivo de greeter personalizado
rm /etc/lightdm/lightdm-gtk-greeter.conf
printf '[greeter]
background = #2e3436
default-user-image = #avatar-default-symbolic
indicators = ~host;~spacer;~spacer;~power' > /etc/lightdm/lightdm-gtk-greeter.conf

# Cria o grupo e atribui o usuário alvo necessário
groupadd -r autologin
gpasswd -a $TARGET_USER autologin > /dev/null 2>&1

# Configura a inicialização automatica no modo gráfico
rm /etc/lightdm/lightdm.conf
printf '[Seat:*]
autologin-user=%s
autologin-guest=false
autologin-user-timeout=0' "$TARGET_USER" > /etc/lightdm/lightdm.conf

# Instala e configura background, temas e ícones para o ambiente desktop
tar -xvf de/01-Qogir.tar.xz -C /usr/share/icons > /dev/null 2>&1
tar -xvf de/Arc-Dark.tar.xz -C /usr/share/themes > /dev/null 2>&1
cp de/debian-swirl.png /usr/share/icons/default
su - "$TARGET_USER" -c "rm -r /home/$TARGET_USER/.config" > /dev/null 2>&1
cp -r de/config /home/$TARGET_USER/.config; chown "$TARGET_USER":"$TARGET_USER" -R /home/$TARGET_USER/.config
cp de/gtkrc-2.0 /home/$TARGET_USER/.gtkrc-2.0; chown "$TARGET_USER":"$TARGET_USER" /home/$TARGET_USER/.gtkrc-2.0

# Criação do diretório de configuração do VNC para o usuário alvo
su - "$TARGET_USER" -c "mkdir -p /home/$TARGET_USER/.vnc"

# Criação do script de inicialização do VNC
su - "$TARGET_USER" -c "printf '#!/bin/sh
unset SESSION_MANAGER
unset DBUS_SESSION_BUS_ADDRESS
exec /bin/sh /etc/X11/xinit/xinitrc' > /home/$TARGET_USER/.vnc/xstartup; chmod +x /home/$TARGET_USER/.vnc/xstartup"

# Configuração da senha para o VNC
su - "$TARGET_USER" -c "echo -n "$PASSWORD_TARGET" | vncpasswd -f > /home/$TARGET_USER/.vnc/passwd; chmod 600 /home/$TARGET_USER/.vnc/passwd"

# Criação do serviço systemd para iniciar o VNC Server e o proxy noVNC
printf '[Unit]
Description=Start VNC Server and noVNC Proxy
After=network.target

[Service]
Type=simple
User=%s
Group=%s
ExecStartPre=/usr/bin/vncserver -verbose -geometry 1024x768 :1
ExecStart=/usr/share/novnc/utils/novnc_proxy --vnc localhost:5901
ExecStop=/usr/bin/vncserver -kill :1
Environment=DISPLAY=:1

[Install]
WantedBy=multi-user.target' "$TARGET_USER" "$TARGET_USER" > /etc/systemd/system/novnc.service

# Recarrega os arquivos de configuração do systemd para registrar o novo serviço e ativa a inicialização automática
systemctl daemon-reload --quiet; systemctl enable novnc --quiet

# Define a inicialização padrão para o modo CLI
systemctl set-default multi-user.target --quiet
}

grub() {
printf "\e[32m*\e[0m SETTING UP GRUB\n"

# Remove o arquivo de configuração atual do GRUB (se existir)
rm -f /etc/default/grub

# Cria um novo arquivo de configuração do GRUB com parâmetros personalizados
printf 'GRUB_DEFAULT=0
GRUB_TIMEOUT=0
GRUB_DISTRIBUTOR=`lsb_release -i -s 2> /dev/null || echo Debian`
GRUB_CMDLINE_LINUX_DEFAULT="console=tty0 console=ttyS0,115200n8"
GRUB_CMDLINE_LINUX=""' > /etc/default/grub; chmod 644 /etc/default/grub

# Atualiza a configuração do GRUB
update-grub

# Mensagem de conclusão
if [ $? -eq 0 ]; then
    printf "\e[32m*\e[0m GRUB CONFIGURATION UPDATED SUCCESSFULLY\n"
else
    printf "\e[31m*\e[0m ERROR: FAILED TO UPDATE GRUB CONFIGURATION\n"
fi
}