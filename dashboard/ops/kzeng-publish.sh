#!/bin/bash
# Publish the HF310 dashboard at https://hf310.mitoto.cn
# Run ON kzeng (47.97.193.62) as root.
set -euo pipefail

TS=$(date +%Y%m%d-%H%M%S)
PUBLIC_PORT=8083
DOMAIN=hf310.mitoto.cn

echo "== 1/5 frps: allow remote port $PUBLIC_PORT =="
cp /etc/frp/frps.toml /etc/frp/frps.toml.bak-hf310dash-$TS
python3 - <<PY
import re
p = "/etc/frp/frps.toml"
s = open(p).read()
desired = "allowPorts = [{ start = 8080, end = 8083 }, { start = 26222, end = 26222 }]"
s = re.sub(r"allowPorts\s*=.*", desired, s)
open(p, "w").write(s)
PY
grep -n allowPorts /etc/frp/frps.toml
frps verify -c /etc/frp/frps.toml
systemctl restart frps && sleep 2 && systemctl is-active frps

echo "== 2/5 nginx: http vhost for ACME =="
cat > /etc/nginx/sites-available/$DOMAIN <<NGX
server {
    listen 80;
    server_name $DOMAIN;
    location /.well-known/acme-challenge/ { root /var/www/html; }
    location / { return 301 https://\$host\$request_uri; }
}
NGX
ln -sf /etc/nginx/sites-available/$DOMAIN /etc/nginx/sites-enabled/$DOMAIN
mkdir -p /var/www/html
nginx -t && systemctl reload nginx

echo "== 3/5 certificate =="
if [ ! -f /etc/letsencrypt/live/$DOMAIN/fullchain.pem ]; then
  PYTHONPATH=/usr/lib/python3/dist-packages certbot certonly --webroot -w /var/www/html \
    -d $DOMAIN --non-interactive --agree-tos --register-unsafely-without-email || true
fi
ls -l /etc/letsencrypt/live/$DOMAIN/ 2>/dev/null || echo "NO CERT"

echo "== 4/5 nginx: https vhost -> frp $PUBLIC_PORT =="
cat > /etc/nginx/sites-available/$DOMAIN <<NGX
server {
    listen 80;
    server_name $DOMAIN;
    location /.well-known/acme-challenge/ { root /var/www/html; }
    location / { return 301 https://\$host\$request_uri; }
}
server {
    listen 443 ssl http2;
    server_name $DOMAIN;
    ssl_certificate     /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;
    client_max_body_size 2m;
    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Content-Type-Options nosniff always;
    location = /healthz { proxy_pass http://127.0.0.1:$PUBLIC_PORT; access_log off; }
    location / {
        proxy_pass http://127.0.0.1:$PUBLIC_PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 120s;
        proxy_connect_timeout 10s;
    }
}
NGX
nginx -t && systemctl reload nginx

echo "== 5/5 verify =="
sleep 2
ss -lntp | grep -E ":$PUBLIC_PORT|:443"
curl -s -o /dev/null -w "loopback healthz: %{http_code}\n" http://127.0.0.1:$PUBLIC_PORT/healthz
curl -s -o /dev/null -w "public https: %{http_code}\n" https://$DOMAIN/healthz
echo "done"
