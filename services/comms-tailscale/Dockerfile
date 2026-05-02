FROM python:3.11-slim
RUN pip install --no-cache-dir "infisical-python>=2.1" \
 && apt-get update && apt-get install -y --no-install-recommends curl \
 && TSVER=$(curl -s https://pkgs.tailscale.com/stable/ | grep -oP 'tailscale_\K[0-9.]+(?=_amd64\.tgz)' | head -1) \
 && curl -fsSL "https://pkgs.tailscale.com/stable/tailscale_${TSVER}_amd64.tgz" -o /tmp/ts.tgz \
 && tar -C /usr/local/bin -xzf /tmp/ts.tgz --strip-components=1 --wildcards '*/tailscale' '*/tailscaled' \
 && rm /tmp/ts.tgz \
 && apt-get remove -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
COPY fetch_secret.py /fetch_secret.py
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
