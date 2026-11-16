#!/usr/bin/env bash
# Nutri Saúde Integral — inicia em Linux/macOS (dados e chaves ficam nas pastas desta unidade)
set -e
cd "$(dirname "$0")"
VENV="${HOME}/.nutri_saude_venv"

command -v python3 >/dev/null || { echo "Python 3.10+ não encontrado."; exit 1; }
if [ ! -x "$VENV/bin/python" ]; then
  echo "Preparando o ambiente neste computador (na 1ª vez precisa de internet)..."
  python3 -m venv "$VENV"
fi
if [ -d wheels ]; then
  "$VENV/bin/python" -m pip install -q --no-index --find-links wheels -r requirements.txt
else
  "$VENV/bin/python" -m pip install -q -r requirements.txt
fi

"$VENV/bin/python" -m flask --app app garantir-profissional
(command -v xdg-open >/dev/null && xdg-open http://127.0.0.1:5000 >/dev/null 2>&1) || (command -v open >/dev/null && open http://127.0.0.1:5000) || true
exec "$VENV/bin/python" servir.py
