import urllib.request
import urllib.parse
import json
import time

base_url = "http://192.168.18.137:8443"
sse_url = f"{base_url}/sse"

print("1. Conectando ao SSE em 192.168.18.137:8443...")
req = urllib.request.Request(sse_url)
resp = urllib.request.urlopen(req)

session_path = None
for _ in range(10):
    line = resp.readline().decode("utf-8").strip()
    if line.startswith("data:"):
        session_path = line.replace("data:", "").strip()
        break

print(f"2. Sessao aberta: {session_path}")
msg_url = urllib.parse.urljoin(base_url, session_path)

def send_rpc(method, params=None, req_id=1):
    payload = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        payload["params"] = params
    data = json.dumps(payload).encode("utf-8")
    post_req = urllib.request.Request(msg_url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(post_req) as p_resp:
        return p_resp.status

send_rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "AntigravityClient", "version": "1.0"}}, req_id=1)
send_rpc("notifications/initialized")

# Dispara a criacao do container helloworld
print("3. Invocando create_container para 'hello-world:latest' com nome 'helloworld'...")
send_rpc("tools/call", {
    "name": "create_container",
    "arguments": {
        "image": "hello-world:latest",
        "name": "helloworld",
        "auto_start": True
    }
}, req_id=2)

print("4. Aguardando criacao do container e coleta dos logs...")
start = time.time()
while time.time() - start < 45:
    line = resp.readline().decode("utf-8").strip()
    if line.startswith("data:"):
        try:
            msg = json.loads(line[5:].strip())
            if msg.get("id") == 2:
                result = msg.get("result", {})
                content = result.get("content", [{}])[0].get("text", "")
                print("\n=== RESPOSTA DA CRIACAO DO CONTAINER ===")
                print(content)
                break
        except Exception as e:
            pass

# Coleta os logs do container helloworld
time.sleep(1)
print("\n5. Coletando os logs do container 'helloworld'...")
send_rpc("tools/call", {
    "name": "get_container_logs",
    "arguments": {
        "name_or_id": "helloworld",
        "tail": 50
    }
}, req_id=3)

start = time.time()
while time.time() - start < 10:
    line = resp.readline().decode("utf-8").strip()
    if line.startswith("data:"):
        try:
            msg = json.loads(line[5:].strip())
            if msg.get("id") == 3:
                result = msg.get("result", {})
                content = result.get("content", [{}])[0].get("text", "")
                print("\n=== LOGS IMPRESSOS PELO CONTAINER HELLOWORLD ===")
                print(content)
                break
        except Exception as e:
            pass
