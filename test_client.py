"""
Script de teste rápido para validar a conexão e ferramentas do MCP Docker Manager.
Pode ser executado diretamente em um ambiente com Python e httpx instalados.
"""

import httpx
import json

SERVER_URL = "http://localhost:8443"

def test_health():
    print(f"Testando conexão com o MCP Server em {SERVER_URL}...")
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{SERVER_URL}/sse")
            print(f"Resposta do endpoint SSE: status {resp.status_code}")
    except Exception as e:
        print(f"Nota: Certifique-se de que o container está ativo. Detalhe: {e}")

if __name__ == "__main__":
    test_health()
