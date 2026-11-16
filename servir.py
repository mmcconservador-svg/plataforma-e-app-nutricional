"""Inicia o servidor local (waitress). Uso: python servir.py"""
import os

from app import app

host = os.environ.get("NUTRI_HOST", "127.0.0.1")  # 127.0.0.1 = somente este computador
port = int(os.environ.get("PORT", "5000"))

if __name__ == "__main__":
    print(f"Nutri Saúde Integral em http://{host}:{port}  (Ctrl+C para encerrar)")
    try:
        from waitress import serve
        serve(app, host=host, port=port, threads=8)
    except ImportError:
        app.run(host=host, port=port)
