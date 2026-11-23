"""
Zero-dependency Python stdio <-> SSE bridge for MCP (Model Context Protocol).

Permite que clientes que suportam apenas transporte stdio (como Claude Desktop
via claude_desktop_config.json) se conectem a um servidor MCP remoto via HTTP/SSE
(ex: http://192.168.18.137:8443/sse) sem necessidade de Node.js ou pacotes extras.

Uso:
    python mcp_stdio_bridge.py http://192.168.18.137:8443/sse
"""

import sys
import threading
import urllib.request
import urllib.parse
import urllib.error


def main() -> int:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        sys.stderr.write("Uso: python mcp_stdio_bridge.py <SSE_URL>\n")
        return 1

    sse_url = sys.argv[1].strip()

    # Garante UTF-8 no Windows para não corromper acentos do servidor MCP
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    endpoint_ready = threading.Event()
    post_url_holder = {"url": None}
    stop_event = threading.Event()

    def sse_reader() -> None:
        try:
            req = urllib.request.Request(
                sse_url,
                headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"},
            )
            with urllib.request.urlopen(req, timeout=None) as resp:
                event_type = "message"
                data_lines = []

                while not stop_event.is_set():
                    raw_line = resp.readline()
                    if not raw_line:
                        break

                    line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")

                    if line == "":
                        # Fim do evento SSE
                        if data_lines:
                            payload = "\n".join(data_lines)
                            if event_type == "endpoint":
                                post_url_holder["url"] = urllib.parse.urljoin(sse_url, payload.strip())
                                endpoint_ready.set()
                            elif event_type == "message":
                                sys.stdout.write(payload + "\n")
                                sys.stdout.flush()
                        event_type = "message"
                        data_lines = []
                        continue

                    if line.startswith(":"):
                        # Comentário / keep-alive SSE
                        continue
                    elif line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        val = line[5:]
                        if val.startswith(" "):
                            val = val[1:]
                        data_lines.append(val)
        except Exception as exc:
            if not stop_event.is_set():
                sys.stderr.write(f"[mcp_stdio_bridge] Erro na stream SSE: {exc}\n")
                sys.stderr.flush()
        finally:
            stop_event.set()
            endpoint_ready.set()

    reader_thread = threading.Thread(target=sse_reader, daemon=True)
    reader_thread.start()

    if not endpoint_ready.wait(timeout=15.0):
        sys.stderr.write(f"[mcp_stdio_bridge] Timeout aguardando endpoint SSE em {sse_url}\n")
        return 1

    post_url = post_url_holder["url"]
    if not post_url:
        sys.stderr.write(f"[mcp_stdio_bridge] Falha ao obter endpoint de mensagens em {sse_url}\n")
        return 1

    try:
        for line in sys.stdin:
            if stop_event.is_set():
                break
            msg = line.strip()
            if not msg:
                continue

            req = urllib.request.Request(
                post_url,
                data=msg.encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=300.0) as resp:
                    resp.read()
            except urllib.error.HTTPError as exc:
                err_body = exc.read().decode("utf-8", errors="replace")
                sys.stderr.write(f"[mcp_stdio_bridge] HTTP {exc.code} ao enviar mensagem: {err_body}\n")
                sys.stderr.flush()
            except Exception as exc:
                sys.stderr.write(f"[mcp_stdio_bridge] Erro ao enviar mensagem: {exc}\n")
                sys.stderr.flush()
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()

    return 0


if __name__ == "__main__":
    sys.exit(main())
