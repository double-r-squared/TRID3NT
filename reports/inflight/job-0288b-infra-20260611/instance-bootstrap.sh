#!/bin/bash
set -e
exec > /var/log/grace2-bootstrap.log 2>&1
echo "=== grace2 bootstrap $(date) ==="
dnf install -y git python3.11 python3.11-pip python3.11-devel gcc gcc-c++ tar gzip bubblewrap >/dev/null 2>&1 || dnf install -y git python3 python3-pip gcc tar gzip bubblewrap
command -v git || dnf install -y git
mkdir -p /opt/grace2 /opt/grace2/data
cd /opt/grace2
aws s3 cp s3://grace2-agent-bundle-226996537797/grace2-agent-bundle.tgz ./bundle.tgz
tar xzf bundle.tgz
PY=$(command -v python3.11 || command -v python3)
$PY -m venv venv
./venv/bin/pip install --upgrade pip wheel >/dev/null
echo "=== pip install agent + contracts ==="
./venv/bin/pip install -e packages/contracts -e services/agent
cat > /etc/systemd/system/grace2-agent.service <<'UNIT'
[Unit]
Description=GRACE-2 Bedrock agent
After=network.target
[Service]
Environment=MODEL_PROVIDER=bedrock
Environment=AWS_REGION=us-west-2
Environment=BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-6
Environment=GRACE2_DEV_PERSISTENCE=1
Environment=GRACE2_DEV_PERSISTENCE_DIR=/opt/grace2/data
Environment=GRACE2_AGENT_HOST=0.0.0.0
Environment=GRACE2_AGENT_PORT=8765
Environment=GRACE2_AGENT_HTTP_PORT=8766
ExecStart=/opt/grace2/venv/bin/grace2-agent
Restart=always
User=root
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now grace2-agent
sleep 8
systemctl is-active grace2-agent && echo "AGENT ACTIVE"
ss -ltnp | grep -E '8765|8766' || echo "ports not bound yet"
echo "=== bootstrap done ==="
