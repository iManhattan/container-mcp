# Embedded Safe Docker MCP Server

A robust, high-performance, and secure **Model Context Protocol (MCP)** server tailored for embedded Linux systems and industrial controllers (PLCs, IoT Edge, Codesys/Nexto, and Buildroot environments) running Docker.

It serves as a full-featured, headless, and agent-ready replacement for **Portainer**, allowing AI agents (such as Antigravity, Claude, and custom autonomous workflows) to perform automated container lifecycle operations, stack deployments, network diagnostics, and storage management **without granting shell or terminal access** to the host.

---

## 🔒 Security & Hardening Principles

Embedded systems and industrial devices must remain isolated from unauthorized terminal or shell escapes (`/bin/sh`, `/bin/bash`):

1. **No Interactive Shell / Exec**: Unlike Portainer, this server exposes strictly typed JSON-RPC tools. It contains **no execution mechanisms** for arbitrary host or container shell commands.
2. **Defensive Volume Restrictions**: The server explicitly forbids mounting host root (`/`) or Docker sockets into child containers created by agents.
3. **Protected Containers**: Critical system containers (such as the MCP server itself) are whitelisted and immune to remote stop, restart, or removal operations.
4. **Lightweight Footprint**: Optimized multi-stage Alpine build consuming **< 70 MB of RAM** and negligible CPU overhead, perfect for memory-constrained embedded hardware.

---

## 🛠️ Complete MCP Tool Catalog

### 1. Stack Management (Docker Compose)
- `deploy_stack(stack_name: str, compose_yaml: str)`: Deploys a full multi-container application stack from a Docker Compose YAML string. Automatically provisions networks, pulls images, and injects official Portainer-compatible compose labels.
- `list_stacks()`: Lists all deployed stacks with service counts and container states.
- `inspect_stack(stack_name: str)`: Details all containers, images, ports, and states associated with a specific stack.
- `remove_stack(stack_name: str)`: Gracefully stops and tears down all containers and networks belonging to a stack.

### 2. Container Lifecycle & Operations
- `create_container(image, name, ports, env, volumes, cmd, restart_policy, auto_start)`: Provisions and optionally starts a new container with built-in parameter validation.
- `list_containers(all: bool)`: Enumerates running or all containers with IDs, names, images, states, and port mappings.
- `inspect_container(name_or_id: str)`: Retrieves container metadata (IP addresses, port bindings, environment variables, restart counts).
- `start_container(name_or_id: str)`: Starts a stopped container.
- `stop_container(name_or_id: str)`: Gracefully stops an active container.
- `restart_container(name_or_id: str)`: Safely restarts a container.
- `pause_container(name_or_id: str)` / `unpause_container(name_or_id: str)`: Freezes and unfreezes container execution without restarts.
- `remove_container(name_or_id: str, force: bool)`: Deletes a container from the system.
- `rename_container(name_or_id: str, new_name: str)`: Renames an existing container.
- `get_container_logs(name_or_id: str, tail: int)`: Collects stdout/stderr output for remote diagnostics.
- `get_container_stats(name_or_id: str)`: Real-time telemetry reporting memory usage (MB / %), memory limits, and CPU load.

### 3. Image Management
- `list_images()`: Lists all locally cached Docker images, tags, and sizes in MB.
- `pull_image(image_name: str)`: Pulls a new image from Docker Hub or a private registry.
- `remove_image(image_name_or_id: str, force: bool)`: Deletes an image to reclaim embedded flash storage.

### 4. Networking & Ethernet Interfaces
- `get_ethernet_interfaces()`: Reads physical and virtual host interfaces (e.g., `eth0`, `eth1`, `docker0`) and live network throughput (RX/TX in MB).
- `list_networks()`: Lists Docker networks (bridge, host, macvlan) and attached container counts.
- `inspect_network(network_name_or_id: str)`: Details IPAM subnets, gateways, and attached container IPs and MAC addresses.
- `create_network(name, driver, subnet, gateway)`: Creates custom subnets with custom CIDR ranges (e.g., `192.168.100.0/24`).
- `connect_container_to_network(container, network, ip_address)`: Connects a container to a network, with optional static IP assignment.
- `disconnect_container_from_network(container, network, force)`: Detaches a container from a network.
- `remove_network(network_name_or_id: str)`: Deletes an unused network.

### 5. Volume & Storage Management
- `list_volumes()`: Lists all persistent data volumes and driver metadata.
- `inspect_volume(name: str)`: Inspects volume details, host mountpoints, and labels.
- `create_volume(name: str, driver: str, labels: dict)`: Provisions a named persistent storage volume.
- `remove_volume(name: str, force: bool)`: Safely deletes a volume (protected if in active use).
- `prune_volumes()`: Removes orphaned volumes to reclaim embedded flash space.

### 6. System Diagnostics & Maintenance
- `get_docker_system_info()`: Overview of host OS (Buildroot/Alpine/Ubuntu), CPU architecture (`aarch64`, `x86_64`), total RAM, and Docker version.
- `get_system_disk_usage()`: Comprehensive storage report (`docker system df`).
- `prune_system(all: bool)`: Automatic cleanup of stopped containers, dangling images, and unused networks.

---

## 🚀 Deployment Guide (via Portainer)

Deploying to an embedded controller without host shell access:

### Step 1: Build the Image in Portainer
1. Upload the provided build bundle:
   - `mcp-server-build.tar.gz` (or `mcp-server-build.zip`)
2. In Portainer, navigate to **Images** ➔ **+ Build a new image**.
3. Set **Name** to: `mcp-portainer-alternative:latest`.
4. Choose **Upload**, select the archive, and click **Build the image**.

### Step 2: Deploy the Stack
1. In Portainer, go to **Stacks** ➔ **+ Add stack**.
2. Name the stack (e.g., `mcp-manager`).
3. Paste the following Compose configuration:

```yaml
version: '3.8'

services:
  mcp-server:
    image: mcp-portainer-alternative:latest
    container_name: mcp-portainer-alternative
    restart: unless-stopped
    user: "0:0"
    ports:
      - "8443:8443"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    environment:
      - MCP_PORT=8443
      - PROTECTED_CONTAINERS=mcp-portainer-alternative
```

4. Click **Deploy the stack**.

The MCP Server is immediately reachable on your controller's IP:
```
http://<DEVICE_IP>:8443/sse
```

---

## 🔌 Connecting to MCP Clients

### Antigravity / Claude Desktop / Custom Client
Add the server entry to your MCP client configuration:

```json
{
  "mcpServers": {
    "embedded-docker-manager": {
      "url": "http://192.168.18.137:8443/sse"
    }
  }
}
```

---

## 📜 License
MIT License. Created for safe, automated interaction with embedded and edge software products.
