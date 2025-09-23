apt-get install -y fluxbox x11vnc websockify novnc xvfb
vim /usr/local/bin/start-vnc.sh
{
#!/bin/bash

# Inicia o servidor X11 (fluxbox)
Xvfb :99 -screen 0 1024x768x24 &

# Inicia o servidor VNC
x11vnc -display :99 -nohttpd -noxdamage -forever -shared -bg -passwd mypassword &

# Inicia o noVNC
websockify --web /usr/share/novnc 8080 localhost:5900
}
chmod +x /usr/local/bin/start-vnc.sh