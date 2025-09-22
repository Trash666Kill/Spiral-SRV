sudo apt update
sudo apt install -y tigervnc-standalone-server tigervnc-common xvfb x11-apps firefox-esr openbox x11-xserver-utils
su - sysop
touch ~/.Xresources
vncserver :1 *execute apenas para definir senha de acesso
vncserver -kill :1 *mata a instância em execução
vim ~/.vnc/xstartup
{
#!/bin/bash
# Inicia Xvfb com suporte a XRandr
Xvfb :1 -screen 0 1920x1080x24 +extension RANDR &
export DISPLAY=:1
# Carrega configurações XRandr (opcional)
xrdb $HOME/.Xresources
# Inicia o gerenciador de janelas openbox
openbox &
# Inicia o Firefox
firefox-esr &
# Mantém o script ativo
sleep infinity
}
chmod +x ~/.vnc/xstartup
vim ~/.services/vnc.sh
{
#!/bin/bash
vncserver -kill :1 2>/dev/null || true
vncserver :1 -SecurityTypes VncAuth -AcceptSetDesktopSize
/usr/share/novnc/utils/novnc_proxy --vnc localhost:5901 --listen 0.0.0.0:6080 --web /usr/share/novnc 2>/dev/null
vncserver -kill :1
}
chmod u+x ~/.services/vnc.sh
