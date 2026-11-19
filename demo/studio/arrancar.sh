#!/usr/bin/env bash
# Arranca el Estudio de agentes v2 en segundo plano: http://<ip-del-servidor>:8790
cd "$(dirname "$0")"
PORT=$(python3 -c "import json;print(json.load(open('settings.json')).get('port',8790))" 2>/dev/null || echo 8790)
if ss -ltn | grep -q ":$PORT "; then echo "Ya está en marcha en el puerto $PORT"; else
  export LANGSMITH_TRACING=false
  setsid nohup .venv-studio/bin/python server.py > server.log 2>&1 < /dev/null &
  for i in $(seq 1 30); do ss -ltn | grep -q ":$PORT " && break; sleep 0.5; done
fi
TOKEN=$(python3 -c "import json;print(json.load(open('settings.json'))['token'])")
echo "Estudio en http://$(hostname -I | awk '{print $1}'):$PORT"
echo "Token: $TOKEN"
