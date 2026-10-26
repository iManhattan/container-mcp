# Estágio de compilação/instalação de dependências
FROM python:3.11-alpine AS builder

WORKDIR /build
COPY requirements.txt .

RUN apk add --no-cache gcc musl-dev libffi-dev && \
    pip install --no-cache-dir --prefix=/install -r requirements.txt

# Imagem final de execução mínima e segura
FROM python:3.11-alpine

# Criação de usuário sem privilégios (non-root)
RUN addgroup -g 10001 mcpuser && \
    adduser -u 10001 -G mcpuser -D -h /home/mcpuser -s /sbin/nologin mcpuser

WORKDIR /app

# Copia pacotes compilados
COPY --from=builder /install /usr/local
COPY server.py /app/server.py

# Mudança de permissões
RUN chown -R mcpuser:mcpuser /app

# Execução estrita como usuário não-root
USER 10001

ENV PYTHONUNBUFFERED=1
ENV MCP_PORT=8443
EXPOSE 8443

# Inicializa o servidor FastMCP em modo SSE
CMD ["python", "server.py"]
