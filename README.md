# Embedded Docker MCP Server

A lightweight and secure **Model Context Protocol (MCP)** server for interacting with Docker environments via AI assistants and automation tools without exposing terminal or host shell access.

---

## Features

- **Safe Automation**: Full Docker lifecycle management via structured JSON-RPC tools with zero interactive shell access.
- **Compose Stacks**: Deploy, list, inspect, and remove multi-container stacks.
- **Resource Management**: Manage containers, images, persistent volumes, and networks.
- **Embedded Friendly**: Minimal resource consumption (< 70 MB RAM) running on x86_64 and ARM64 architectures.

---

## Installation

### 1. Docker Compose (Recommended)

Save the following configuration as `docker-compose.yml`:

```yaml
version: '3.8'

services:
  mcp-server:
    image: mcp-docker-manager:latest
    container_name: mcp-docker-manager
    restart: unless-stopped
    user: "0:0"
    ports:
      - "8443:8443"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    environment:
      - MCP_PORT=8443
      - PROTECTED_CONTAINERS=mcp-docker-manager
```

Run the container:

```bash
docker compose up -d
```

### 2. Building the Image Locally

```bash
docker build -t mcp-docker-manager:latest .
```

---

## Configuration

| Environment Variable | Default | Description |
| :--- | :--- | :--- |
| `MCP_PORT` | `8443` | Port used by the SSE HTTP server. |
| `DOCKER_HOST` | `http://127.0.0.1:2375` | Fallback TCP host if Unix socket is unavailable. |
| `PROTECTED_CONTAINERS` | `mcp-docker-manager` | Comma-separated list of container names immune to stop or remove actions. |

---

## Connecting to an MCP Client

Add the server to your MCP client configuration (e.g., Antigravity, Claude Desktop, or custom orchestrators):

```json
{
  "mcpServers": {
    "docker-manager": {
      "url": "http://<HOST_IP>:8443/sse"
    }
  }
}
```

---

## Available Tools

- **Stacks**: `deploy_stack`, `list_stacks`, `inspect_stack`, `remove_stack`
- **Containers**: `create_container`, `list_containers`, `inspect_container`, `start_container`, `stop_container`, `restart_container`, `pause_container`, `unpause_container`, `remove_container`, `rename_container`, `get_container_logs`, `get_container_stats`
- **Images**: `list_images`, `pull_image`, `remove_image`
- **Networks**: `list_networks`, `inspect_network`, `create_network`, `remove_network`, `connect_container_to_network`, `disconnect_container_from_network`, `get_ethernet_interfaces`
- **Volumes**: `list_volumes`, `inspect_volume`, `create_volume`, `remove_volume`, `prune_volumes`
- **System**: `get_docker_system_info`, `get_system_disk_usage`, `prune_system`

---

## License

MIT
