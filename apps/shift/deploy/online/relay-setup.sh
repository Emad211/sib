#!/usr/bin/env bash
# Relay VPS setup (O05), Ubuntu 22.04/24.04, run as root once. It stores no clinic data and no TLS key.
#   sudo PUBLIC_NAME=shift.clinic.example RATHOLE_BIN=/root/rathole bash relay-setup.sh
# RATHOLE_BIN is the rathole binary you copied to the VPS yourself (nothing is downloaded by this script).
set -euo pipefail
: "${PUBLIC_NAME:?set PUBLIC_NAME}"
: "${RATHOLE_BIN:?set RATHOLE_BIN to the rathole binary you copied}"
here="$(cd "$(dirname "$0")" && pwd)"

apt-get update -y
apt-get install -y haproxy ufw

install -m 0755 "$RATHOLE_BIN" /usr/local/bin/rathole
install -d -m 0700 /etc/rathole
if [ ! -f /etc/rathole/server.toml ]; then
    shared_hex="$(openssl rand -hex 32)"
    keys="$(/usr/local/bin/rathole --genkey)"
    private="$(printf '%s\n' "$keys" | awk '/Private Key/{getline; print}')"
    public="$(printf '%s\n' "$keys" | awk '/Public Key/{getline; print}')"
    sed -e "s/REPLACE_WITH_THE_SHARED_TOKEN/$shared_hex/" -e "s#REPLACE_WITH_THE_SERVER_PRIVATE_KEY#$private#" \
        "$here/rathole-server.toml.example" > /etc/rathole/server.toml
    chmod 0600 /etc/rathole/server.toml
    umask 077
    printf 'token=%s\nserver_public_key=%s\n' "$shared_hex" "$public" > /root/clinic-tunnel-secrets.txt
    echo "Tunnel secrets for the clinic PC: /root/clinic-tunnel-secrets.txt (copy them over, then delete)."
fi

cat > /etc/systemd/system/rathole.service <<'UNIT'
[Unit]
Description=Rathole tunnel server for the clinic shift app
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/local/bin/rathole --server /etc/rathole/server.toml
Restart=always
RestartSec=3
User=rathole
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes

[Install]
WantedBy=multi-user.target
UNIT
id rathole >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin rathole
chown -R rathole:rathole /etc/rathole   # only the tunnel's own user reads the token and the key

sed "s/shift.clinic.example/$PUBLIC_NAME/" "$here/haproxy.cfg" > /etc/haproxy/haproxy.cfg
haproxy -c -f /etc/haproxy/haproxy.cfg

ufw allow OpenSSH
ufw allow 443/tcp
ufw allow 2333/tcp
ufw --force enable

systemctl daemon-reload
systemctl enable --now rathole
systemctl enable haproxy
systemctl restart haproxy
echo "Relay ready: TLS for $PUBLIC_NAME passes through to the clinic PC once its tunnel connects."
