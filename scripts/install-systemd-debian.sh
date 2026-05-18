#!/usr/bin/env sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this script as root." >&2
  exit 1
fi

apt-get update
apt-get install -y python3 python3-venv python3-pip

if ! id nats-sink >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/nats-sink --create-home --shell /usr/sbin/nologin nats-sink
fi

install -d -o nats-sink -g nats-sink /var/lib/nats-sink
install -d /etc/nats-sinks
install -d /opt/nats-sinks

python3 -m venv /opt/nats-sinks/venv
/opt/nats-sinks/venv/bin/python -m pip install --upgrade pip
/opt/nats-sinks/venv/bin/python -m pip install nats-sinks

if [ ! -f /etc/nats-sinks/config.json ]; then
  install -m 0640 -o root -g nats-sink examples/file-basic/config.json /etc/nats-sinks/config.json
fi

if [ ! -f /etc/nats-sinks/nats-sink.env ]; then
  install -m 0640 -o root -g nats-sink examples/systemd/nats-sink.env /etc/nats-sinks/nats-sink.env
fi

install -m 0644 examples/systemd/nats-sink.service /etc/systemd/system/nats-sink.service
systemctl daemon-reload
systemctl enable nats-sink

echo "Installed nats-sink service. Edit /etc/nats-sinks/config.json and /etc/nats-sinks/nats-sink.env, then run: systemctl start nats-sink"
