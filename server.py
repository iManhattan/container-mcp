"""
Complete Safe Docker MCP Server - Substituto integral do Portainer para Sistemas Embarcados (Nexto / CODESYS).

Fornece um conjunto completo de ferramentas (Tools) para gerenciar o ciclo de vida completo do Docker:
- Stacks (deploy de docker-compose, listar, inspecionar e remover stacks)
- Contêineres (criar, listar, inspecionar, iniciar, parar, pausar, despausar, reiniciar, logs, métricas, remover, renomear)
- Imagens (listar, puxar/pull, inspecionar, remover)
- Manipulação Avançada de Ethernet e Redes (listar, inspecionar, criar com subnet/gateway, conectar com IP estático, desconectar, remover, interfaces ethernet do host)
- Manipulação Avançada de Volumes (listar, inspecionar, criar, remover, prune de volumes não utilizados)
- Limpeza e Armazenamento (disk usage / df, prune de recursos órfãos)

Sem disponibilizar shell/terminal do sistema por diretriz de segurança.
"""

import os
import socket
import logging
import base64
import json
from typing import List, Dict, Any, Optional
try:
    from mcp.server.fastmcp import FastMCP
except (ImportError, ModuleNotFoundError):
    from mcp.server.mcpserver import MCPServer as FastMCP
try:
    import httpx
except ImportError:
    import httpx2 as httpx
import yaml

# Configuração de logs
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mcp-docker-manager")

# Configurações de ambiente
PROTECTED_CONTAINERS_RAW = os.getenv("PROTECTED_CONTAINERS", "mcp-portainer-alternative,mcp-docker-manager")
PROTECTED_CONTAINERS = [name.strip() for name in PROTECTED_CONTAINERS_RAW.split(",") if name.strip()]
MCP_PORT = int(os.getenv("MCP_PORT", "8443"))

# Inicializa o FastMCP escutando em 0.0.0.0 (compatível com FastMCP v1 e MCPServer v2)
try:
    mcp = FastMCP("Docker-Portainer-SafeMCP", host="0.0.0.0", port=MCP_PORT)
except TypeError:
    mcp = FastMCP("Docker-Portainer-SafeMCP")

# Conexão direta com o Docker Engine via Unix Socket ou TCP
DOCKER_SOCKET = "/var/run/docker.sock"
if os.path.exists(DOCKER_SOCKET):
    logger.info(f"Conectando ao Docker Engine via Unix Socket direto: {DOCKER_SOCKET}")
    transport = httpx.HTTPTransport(uds=DOCKER_SOCKET)
    client = httpx.Client(transport=transport, base_url="http://docker", timeout=120.0)
else:
    docker_host = os.getenv("DOCKER_HOST", "http://127.0.0.1:2375")
    logger.info(f"Conectando ao Docker Engine via TCP: {docker_host}")
    client = httpx.Client(base_url=docker_host, timeout=120.0)


def _is_protected(name_or_id: str) -> bool:
    """Verifica se o contêiner está na lista de protegidos contra parada/remoção."""
    for protected in PROTECTED_CONTAINERS:
        if protected in name_or_id:
            return True
    return False


def _demux_docker_stream(raw: bytes) -> str:
    """
    Decodifica a stream multiplexada do Docker (stdout e stderr).
    Cada frame possui um cabeçalho binário de 8 bytes:
    [STREAM_TYPE (1 byte), 0, 0, 0, SIZE (4 bytes big-endian)].
    Remove os bytes de controle e retorna texto limpo em UTF-8.
    """
    if not raw:
        return ""

    out = []
    i = 0
    n = len(raw)
    is_multiplexed = False

    while i + 8 <= n:
        stream_type = raw[i]
        # Checa cabeçalho padrão Docker (1 = stdout, 2 = stderr, 0 = stdin)
        if stream_type in (0, 1, 2) and raw[i + 1 : i + 4] == b"\x00\x00\x00":
            is_multiplexed = True
            frame_size = int.from_bytes(raw[i + 4 : i + 8], byteorder="big")
            i += 8
            payload = raw[i : i + frame_size]
            out.append(payload.decode("utf-8", errors="replace"))
            i += frame_size
        else:
            break

    if is_multiplexed and i == n:
        return "".join(out)

    return raw.decode("utf-8", errors="replace")


# ============================================================================
# 1. GESTÃO DE STACKS (DOCKER COMPOSE)
# ============================================================================

@mcp.tool()
def deploy_stack(stack_name: str, compose_yaml: str) -> Dict[str, Any]:
    """
    Realiza o deploy de uma Stack completa a partir de um conteúdo docker-compose YAML.
    Cria a rede da stack, baixa imagens se necessário e inicia os serviços compatíveis com Portainer.
    
    Args:
        stack_name: Nome identificador da stack (ex: 'mqtt-stack', 'telemetria').
        compose_yaml: String contendo o arquivo docker-compose.yml completo.
    """
    try:
        data = yaml.safe_load(compose_yaml)
        if not data or not isinstance(data, dict):
            return {"error": "YAML inválido ou vazio."}

        services = data.get("services", {})
        if not services:
            return {"error": "Nenhum serviço definido na chave 'services'."}

        network_name = f"{stack_name}_default"
        net_resp = client.get(f"/networks/{network_name}")
        if net_resp.status_code == 404:
            client.post("/networks/create", json={"Name": network_name, "Driver": "bridge"})

        results = []

        for s_name, s_cfg in services.items():
            image = s_cfg.get("image")
            if not image:
                results.append({"service": s_name, "status": "skipped", "error": "Imagem não especificada"})
                continue

            c_name = s_cfg.get("container_name") or f"{stack_name}_{s_name}"

            if _is_protected(c_name):
                return {"error": f"Ação Negada por Segurança: O serviço '{s_name}' tenta sobrescrever o contêiner protegido '{c_name}'."}

            old_c = client.get(f"/containers/{c_name}/json")
            if old_c.status_code == 200:
                client.post(f"/containers/{c_name}/stop")
                client.delete(f"/containers/{c_name}", params={"v": True, "force": True})

            exposed_ports = {}
            port_bindings = {}
            raw_ports = s_cfg.get("ports", [])
            for p in raw_ports:
                p_str = str(p)
                if ":" in p_str:
                    host_p, cont_p = p_str.split(":")[:2]
                    cont_p_key = cont_p if "/" in cont_p else f"{cont_p}/tcp"
                    exposed_ports[cont_p_key] = {}
                    port_bindings[cont_p_key] = [{"HostPort": host_p}]

            raw_env = s_cfg.get("environment", {})
            env_list = []
            if isinstance(raw_env, dict):
                env_list = [f"{k}={v}" for k, v in raw_env.items()]
            elif isinstance(raw_env, list):
                env_list = raw_env

            binds = []
            for v in s_cfg.get("volumes", []):
                v_str = str(v)
                if ":" in v_str:
                    h_path = v_str.split(":")[0]
                    if h_path in ("/", "/var/run/docker.sock", "/codesys/NextoUser/Files/docker.sock") or "docker.sock" in h_path:
                        return {"error": f"Ação Negada por Segurança: Montagem do caminho '{h_path}' proibida."}
                    binds.append(v_str)

            labels = {
                "com.docker.compose.project": stack_name,
                "com.docker.compose.service": s_name,
                "com.docker.compose.version": "custom-mcp"
            }

            cmd = s_cfg.get("command")
            if isinstance(cmd, str):
                cmd = ["sh", "-c", cmd]

            # Limites de memória e CPU
            host_config = {
                "PortBindings": port_bindings,
                "Binds": binds,
                "NetworkMode": network_name,
                "RestartPolicy": {"Name": s_cfg.get("restart", "unless-stopped")},
                "Privileged": False
            }

            mem_limit_raw = s_cfg.get("mem_limit")
            if mem_limit_raw:
                if isinstance(mem_limit_raw, int):
                    host_config["Memory"] = min(mem_limit_raw, 380 * 1024 * 1024)
                elif isinstance(mem_limit_raw, str):
                    s_lower = mem_limit_raw.lower().strip()
                    if s_lower.endswith("m"):
                        host_config["Memory"] = min(int(s_lower[:-1]), 380) * 1024 * 1024
                    elif s_lower.endswith("g"):
                        host_config["Memory"] = 380 * 1024 * 1024
                    elif s_lower.endswith("k"):
                        host_config["Memory"] = int(s_lower[:-1]) * 1024

            cpu_shares_raw = s_cfg.get("cpu_shares")
            if cpu_shares_raw and isinstance(cpu_shares_raw, int):
                host_config["CpuShares"] = cpu_shares_raw

            payload = {
                "Image": image,
                "Cmd": cmd,
                "Env": env_list,
                "Labels": labels,
                "ExposedPorts": exposed_ports,
                "HostConfig": host_config
            }

            check_img = client.get(f"/images/{image}/json")
            if check_img.status_code == 404:
                client.post("/images/create", params={"fromImage": image}, timeout=300.0)

            c_resp = client.post("/containers/create", params={"name": c_name}, json=payload)
            c_resp.raise_for_status()
            cid = c_resp.json()["Id"][:12]

            client.post(f"/containers/{cid}/start").raise_for_status()
            results.append({"service": s_name, "container_name": c_name, "id": cid, "status": "running"})

        return {
            "status": "success",
            "stack_name": stack_name,
            "services_deployed": results,
            "message": f"Stack '{stack_name}' implantada com sucesso e visível no Portainer!"
        }
    except Exception as e:
        logger.error(f"Erro no deploy da stack: {e}")
        return {"error": f"Falha ao realizar deploy da stack: {str(e)}"}


@mcp.tool()
def list_stacks() -> List[Dict[str, Any]]:
    """Lista todas as Stacks Docker implantadas no equipamento."""
    try:
        resp = client.get("/containers/json", params={"all": True})
        resp.raise_for_status()
        containers = resp.json()

        stacks: Dict[str, List[Dict[str, Any]]] = {}
        for c in containers:
            labels = c.get("Labels") or {}
            project = labels.get("com.docker.compose.project")
            if project:
                if project not in stacks:
                    stacks[project] = []
                stacks[project].append({
                    "id": c["Id"][:12],
                    "name": [n.lstrip("/") for n in c.get("Names", [])],
                    "service": labels.get("com.docker.compose.service"),
                    "state": c.get("State"),
                    "status": c.get("Status")
                })

        return [
            {
                "stack_name": name,
                "total_services": len(services),
                "services": services
            }
            for name, services in stacks.items()
        ]
    except httpx.HTTPError as e:
        return [{"error": f"Falha ao listar stacks: {str(e)}"}]


@mcp.tool()
def inspect_stack(stack_name: str) -> Dict[str, Any]:
    """Inspeciona todos os serviços e contêineres de uma Stack específica."""
    try:
        resp = client.get("/containers/json", params={"all": True})
        resp.raise_for_status()
        containers = resp.json()

        matching = []
        for c in containers:
            labels = c.get("Labels") or {}
            if labels.get("com.docker.compose.project") == stack_name:
                matching.append({
                    "id": c["Id"][:12],
                    "names": [n.lstrip("/") for n in c.get("Names", [])],
                    "service": labels.get("com.docker.compose.service"),
                    "state": c.get("State"),
                    "status": c.get("Status"),
                    "image": c.get("Image")
                })

        if not matching:
            return {"error": f"Nenhuma stack encontrada com o nome '{stack_name}'."}

        return {
            "stack_name": stack_name,
            "services_count": len(matching),
            "containers": matching
        }
    except httpx.HTTPError as e:
        return {"error": f"Falha ao inspecionar stack: {str(e)}"}


@mcp.tool()
def remove_stack(stack_name: str) -> Dict[str, Any]:
    """Para e remove todos os contêineres e a rede de uma Stack inteira."""
    try:
        resp = client.get("/containers/json", params={"all": True})
        resp.raise_for_status()
        containers = resp.json()

        removed = []
        for c in containers:
            labels = c.get("Labels") or {}
            if labels.get("com.docker.compose.project") == stack_name:
                cid = c["Id"]
                c_name = c.get("Names", [""])[0].lstrip("/")
                if _is_protected(c_name):
                    continue
                client.post(f"/containers/{cid}/stop")
                client.delete(f"/containers/{cid}", params={"v": True, "force": True})
                removed.append(c_name or cid[:12])

        client.delete(f"/networks/{stack_name}_default")

        return {
            "status": "success",
            "stack_name": stack_name,
            "containers_removed": removed,
            "message": f"Stack '{stack_name}' e seus recursos foram removidos com sucesso."
        }
    except httpx.HTTPError as e:
        return {"error": f"Falha ao remover stack: {str(e)}"}


# ============================================================================
# 2. MANIPULAÇÃO AVANÇADA DE ETHERNET E REDES (NETWORKING)
# ============================================================================

@mcp.tool()
def list_networks() -> List[Dict[str, Any]]:
    """Lista todas as redes Docker (bridge, host, overlay, macvlan) no equipamento."""
    try:
        resp = client.get("/networks")
        resp.raise_for_status()
        return [
            {
                "id": n["Id"][:12],
                "name": n.get("Name"),
                "driver": n.get("Driver"),
                "scope": n.get("Scope"),
                "containers_count": len(n.get("Containers") or {}),
            }
            for n in resp.json()
        ]
    except httpx.HTTPError as e:
        return [{"error": f"Falha ao listar redes: {str(e)}"}]


@mcp.tool()
def inspect_network(network_name_or_id: str) -> Dict[str, Any]:
    """
    Inspeciona uma rede Docker em detalhes (sub-redes, gateways, contêineres conectados, IPs e MAC addresses).
    
    Args:
        network_name_or_id: Nome ou ID da rede.
    """
    try:
        resp = client.get(f"/networks/{network_name_or_id}")
        if resp.status_code == 404:
            return {"error": f"Rede '{network_name_or_id}' não encontrada."}
        resp.raise_for_status()
        n = resp.json()

        # Extrai contêineres conectados
        containers_connected = []
        for cid, cinfo in (n.get("Containers") or {}).items():
            containers_connected.append({
                "container_id": cid[:12],
                "name": cinfo.get("Name"),
                "ipv4_address": cinfo.get("IPv4Address"),
                "mac_address": cinfo.get("MacAddress"),
            })

        # Configurações IPAM (Subnet e Gateway)
        ipam_configs = n.get("IPAM", {}).get("Config") or []

        return {
            "id": n.get("Id")[:12],
            "name": n.get("Name"),
            "driver": n.get("Driver"),
            "scope": n.get("Scope"),
            "ipam_configs": ipam_configs,
            "containers": containers_connected,
        }
    except httpx.HTTPError as e:
        return {"error": f"Falha ao inspecionar rede: {str(e)}"}


@mcp.tool()
def create_network(
    name: str,
    driver: str = "bridge",
    subnet: Optional[str] = None,
    gateway: Optional[str] = None
) -> Dict[str, Any]:
    """
    Cria uma nova rede Ethernet/Docker com opções avançadas de endereçamento IP.
    
    Args:
        name: Nome da rede (ex: 'rede-sensores', 'rede-clp').
        driver: Driver de rede ('bridge', 'host', 'macvlan'). Padrão 'bridge'.
        subnet: Sub-rede opcional em formato CIDR (ex: '192.168.100.0/24').
        gateway: Gateway opcional para a sub-rede (ex: '192.168.100.1').
    """
    payload: Dict[str, Any] = {"Name": name, "Driver": driver}

    if subnet or gateway:
        ipam_config = {}
        if subnet:
            ipam_config["Subnet"] = subnet
        if gateway:
            ipam_config["Gateway"] = gateway
        payload["IPAM"] = {"Config": [ipam_config]}

    try:
        resp = client.post("/networks/create", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return {
            "status": "success",
            "id": data.get("Id")[:12],
            "name": name,
            "driver": driver,
            "message": f"Rede '{name}' criada com sucesso."
        }
    except httpx.HTTPError as e:
        return {"error": f"Falha ao criar rede: {str(e)}"}


@mcp.tool()
def remove_network(network_name_or_id: str) -> str:
    """Remove uma rede Docker."""
    try:
        resp = client.delete(f"/networks/{network_name_or_id}")
        if resp.status_code == 204:
            return f"Rede '{network_name_or_id}' removida com sucesso."
        if resp.status_code == 404:
            return f"Rede '{network_name_or_id}' não encontrada."
        return f"Status retornado: {resp.status_code}"
    except httpx.HTTPError as e:
        return f"Falha ao remover rede: {str(e)}"


@mcp.tool()
def connect_container_to_network(
    container: str,
    network: str,
    ip_address: Optional[str] = None
) -> str:
    """
    Conecta um contêiner a uma rede Docker, permitindo atribuir um IP estático específico.
    
    Args:
        container: Nome ou ID do contêiner.
        network: Nome ou ID da rede.
        ip_address: Endereço IPv4 estático opcional (ex: '192.168.100.50').
    """
    payload: Dict[str, Any] = {"Container": container}
    if ip_address:
        payload["EndpointConfig"] = {"IPAMConfig": {"IPv4Address": ip_address}}

    try:
        resp = client.post(f"/networks/{network}/connect", json=payload)
        if resp.status_code in (200, 204):
            ip_msg = f" com IP estático {ip_address}" if ip_address else ""
            return f"Contêiner '{container}' conectado à rede '{network}'{ip_msg} com sucesso."
        return f"Status retornado: {resp.status_code}"
    except httpx.HTTPError as e:
        return f"Falha ao conectar contêiner à rede: {str(e)}"


@mcp.tool()
def disconnect_container_from_network(
    container: str,
    network: str,
    force: bool = False
) -> str:
    """Desconecta um contêiner de uma rede Docker."""
    try:
        resp = client.post(f"/networks/{network}/disconnect", json={"Container": container, "Force": force})
        if resp.status_code in (200, 204):
            return f"Contêiner '{container}' desconectado da rede '{network}' com sucesso."
        return f"Status retornado: {resp.status_code}"
    except httpx.HTTPError as e:
        return f"Falha ao desconectar contêiner da rede: {str(e)}"


@mcp.tool()
def get_ethernet_interfaces() -> List[Dict[str, Any]]:
    """
    Lista as interfaces de rede Ethernet e virtuais ativas no equipamento (ex: eth0, eth1, docker0)
    com estatísticas de tráfego (bytes recebidos/transmitidos).
    """
    interfaces = []
    try:
        if os.path.exists("/proc/net/dev"):
            with open("/proc/net/dev", "r") as f:
                lines = f.readlines()
            for line in lines[2:]:
                parts = line.strip().split(":")
                if len(parts) == 2:
                    iface_name = parts[0].strip()
                    stats = parts[1].split()
                    rx_bytes = int(stats[0]) / (1024 * 1024)
                    tx_bytes = int(stats[8]) / (1024 * 1024)
                    interfaces.append({
                        "interface": iface_name,
                        "rx_mb": round(rx_bytes, 2),
                        "tx_mb": round(tx_bytes, 2),
                    })
    except Exception as e:
        logger.error(f"Erro ao ler interfaces ethernet: {e}")

    return interfaces


# ============================================================================
# 3. MANIPULAÇÃO AVANÇADA DE VOLUMES (STORAGE)
# ============================================================================

@mcp.tool()
def list_volumes() -> List[Dict[str, Any]]:
    """Lista todos os volumes de dados persistentes criados no equipamento."""
    try:
        resp = client.get("/volumes")
        resp.raise_for_status()
        volumes = resp.json().get("Volumes") or []
        return [
            {
                "name": v.get("Name"),
                "driver": v.get("Driver"),
                "mountpoint": v.get("Mountpoint"),
                "created_at": v.get("CreatedAt"),
                "scope": v.get("Scope"),
            }
            for v in volumes
        ]
    except httpx.HTTPError as e:
        return [{"error": f"Falha ao listar volumes: {str(e)}"}]


@mcp.tool()
def inspect_volume(name: str) -> Dict[str, Any]:
    """
    Inspeciona detalhes completos de um volume Docker (ponto de montagem no disco, labels, driver).
    
    Args:
        name: Nome do volume.
    """
    try:
        resp = client.get(f"/volumes/{name}")
        if resp.status_code == 404:
            return {"error": f"Volume '{name}' não encontrado."}
        resp.raise_for_status()
        v = resp.json()
        return {
            "name": v.get("Name"),
            "driver": v.get("Driver"),
            "mountpoint": v.get("Mountpoint"),
            "created_at": v.get("CreatedAt"),
            "labels": v.get("Labels") or {},
            "options": v.get("Options") or {},
            "scope": v.get("Scope"),
        }
    except httpx.HTTPError as e:
        return {"error": f"Falha ao inspecionar volume: {str(e)}"}


@mcp.tool()
def create_volume(
    name: str,
    driver: str = "local",
    labels: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """
    Cria um novo volume de dados Docker para armazenamento persistente.
    
    Args:
        name: Nome do volume (ex: 'dados-clp', 'telemetria-db').
        driver: Driver do volume (padrão 'local').
        labels: Dicionário opcional de metadados/tags.
    """
    payload: Dict[str, Any] = {"Name": name, "Driver": driver}
    if labels:
        payload["Labels"] = labels

    try:
        resp = client.post("/volumes/create", json=payload)
        resp.raise_for_status()
        v = resp.json()
        return {
            "status": "success",
            "name": v.get("Name"),
            "mountpoint": v.get("Mountpoint"),
            "message": f"Volume '{name}' criado com sucesso."
        }
    except httpx.HTTPError as e:
        return {"error": f"Falha ao criar volume: {str(e)}"}


@mcp.tool()
def remove_volume(name: str, force: bool = False) -> str:
    """
    Remove um volume de dados Docker.
    
    Args:
        name: Nome do volume.
        force: Força a remoção mesmo que o volume tenha sido usado recentemente.
    """
    try:
        resp = client.delete(f"/volumes/{name}", params={"force": force})
        if resp.status_code == 204:
            return f"Volume '{name}' removido com sucesso."
        if resp.status_code == 404:
            return f"Volume '{name}' não encontrado."
        if resp.status_code == 409:
            return f"Conflito: Volume '{name}' está em uso por outro contêiner e não pode ser removido."
        return f"Status retornado: {resp.status_code}"
    except httpx.HTTPError as e:
        return f"Falha ao remover volume: {str(e)}"


@mcp.tool()
def prune_volumes() -> Dict[str, Any]:
    """
    Remove todos os volumes não utilizados por nenhum contêiner para recuperar espaço em disco/flash.
    """
    try:
        resp = client.post("/volumes/prune")
        resp.raise_for_status()
        data = resp.json()
        reclaimed_mb = data.get("SpaceReclaimed", 0) / (1024 * 1024)
        return {
            "volumes_deleted": data.get("VolumesDeleted") or [],
            "space_reclaimed_mb": round(reclaimed_mb, 2),
            "status": "Limpeza de volumes concluída com sucesso."
        }
    except httpx.HTTPError as e:
        return {"error": f"Falha ao executar prune de volumes: {str(e)}"}


# ============================================================================
# 4. GESTÃO DE CONTÊINERES (CONTAINERS)
# ============================================================================

@mcp.tool()
def list_containers(all: bool = False) -> List[Dict[str, Any]]:
    """Lista contêineres Docker no dispositivo embarcado."""
    try:
        resp = client.get("/containers/json", params={"all": all})
        resp.raise_for_status()
        containers = resp.json()
        return [
            {
                "id": c["Id"][:12],
                "names": [n.lstrip("/") for n in c.get("Names", [])],
                "image": c.get("Image"),
                "state": c.get("State"),
                "status": c.get("Status"),
                "ports": [
                    f"{p.get('PublicPort')}->{p.get('PrivatePort')}/{p.get('Type')}"
                    for p in c.get("Ports", [])
                    if "PublicPort" in p
                ],
            }
            for c in containers
        ]
    except httpx.HTTPError as e:
        return [{"error": f"Falha na comunicação com Docker: {str(e)}"}]


@mcp.tool()
def inspect_container(name_or_id: str) -> Dict[str, Any]:
    """Inspeciona detalhes completos de um contêiner."""
    try:
        resp = client.get(f"/containers/{name_or_id}/json")
        if resp.status_code == 404:
            return {"error": f"Contêiner '{name_or_id}' não encontrado."}
        resp.raise_for_status()
        data = resp.json()
        
        state = data.get("State", {})
        net = data.get("NetworkSettings", {})
        config = data.get("Config", {})
        
        return {
            "id": data.get("Id", "")[:12],
            "name": data.get("Name", "").lstrip("/"),
            "image": config.get("Image"),
            "status": state.get("Status"),
            "running": state.get("Running"),
            "started_at": state.get("StartedAt"),
            "restart_count": data.get("RestartCount", 0),
            "ip_address": net.get("IPAddress") or net.get("Networks", {}).get("bridge", {}).get("IPAddress"),
            "ports": data.get("HostConfig", {}).get("PortBindings", {}),
            "mounts": [
                {"source": m.get("Source"), "destination": m.get("Destination"), "rw": m.get("RW")}
                for m in data.get("Mounts", [])
            ],
            "env": config.get("Env", []),
            "is_protected": _is_protected(name_or_id)
        }
    except httpx.HTTPError as e:
        return {"error": f"Falha ao inspecionar contêiner: {str(e)}"}


@mcp.tool()
def create_container(
    image: str,
    name: Optional[str] = None,
    ports: Optional[Dict[str, int]] = None,
    env: Optional[Dict[str, str]] = None,
    volumes: Optional[Dict[str, str]] = None,
    cmd: Optional[List[str]] = None,
    restart_policy: str = "unless-stopped",
    memory_limit_mb: Optional[int] = None,
    cpu_shares: Optional[int] = None,
    auto_start: bool = True
) -> Dict[str, Any]:
    """
    Cria e opcionalmente inicia um novo contêiner Docker com regras de segurança embutidas.
    
    Args:
        image: Nome da imagem (ex: 'eclipse-mosquitto:latest', 'alpine').
        name: Nome opcional para o contêiner.
        ports: Mapeamento de portas {porta_container: porta_host} (ex: {'1883/tcp': 1883}).
        env: Variáveis de ambiente {'CHAVE': 'VALOR'}.
        volumes: Mapeamento de volumes {caminho_host: caminho_container}.
        cmd: Comando executado dentro do contêiner como lista.
        restart_policy: Política de reinicialização ('always', 'unless-stopped', 'on-failure', 'no').
        memory_limit_mb: Limite máximo de memória RAM em megabytes (salvaguarda para o CLP).
        cpu_shares: Peso relativo de CPU (ex: 512, 1024).
        auto_start: Se True, inicia o contêiner imediatamente.
    """
    binds = []
    if volumes:
        for host_path, container_path in volumes.items():
            if host_path in ("/", "/var/run/docker.sock", "/codesys/NextoUser/Files/docker.sock") or "docker.sock" in host_path:
                return {"error": f"Ação Negada por Segurança: Montagem do caminho '{host_path}' não é permitida."}
            binds.append(f"{host_path}:{container_path}")

    port_bindings = {}
    exposed_ports = {}
    if ports:
        for container_port, host_port in ports.items():
            c_port = container_port if "/" in container_port else f"{container_port}/tcp"
            exposed_ports[c_port] = {}
            port_bindings[c_port] = [{"HostPort": str(host_port)}]

    env_list = [f"{k}={v}" for k, v in env.items()] if env else []

    host_config = {
        "PortBindings": port_bindings,
        "Binds": binds,
        "RestartPolicy": {"Name": restart_policy},
        "Privileged": False,
    }
    if memory_limit_mb and memory_limit_mb > 0:
        mem_clamped = min(memory_limit_mb, 380)
        host_config["Memory"] = mem_clamped * 1024 * 1024
    if cpu_shares and cpu_shares > 0:
        host_config["CpuShares"] = cpu_shares

    payload = {
        "Image": image,
        "Cmd": cmd,
        "Env": env_list,
        "ExposedPorts": exposed_ports,
        "HostConfig": host_config
    }

    try:
        create_params = {"name": name} if name else {}
        resp = client.post("/containers/create", params=create_params, json=payload)
        
        if resp.status_code == 404:
            logger.info(f"Imagem {image} não encontrada localmente. Tentando baixar...")
            pull_resp = client.post("/images/create", params={"fromImage": image}, timeout=300.0)
            if pull_resp.status_code != 200:
                return {"error": f"Imagem '{image}' não encontrada e falha ao baixar."}
            resp = client.post("/containers/create", params=create_params, json=payload)

        resp.raise_for_status()
        res_data = resp.json()
        cid = res_data["Id"][:12]

        if auto_start:
            client.post(f"/containers/{cid}/start").raise_for_status()
            return {"status": "success", "id": cid, "name": name, "started": True, "message": f"Contêiner '{name or cid}' criado e iniciado com sucesso."}

        return {"status": "success", "id": cid, "name": name, "started": False, "message": f"Contêiner '{name or cid}' criado com sucesso."}
    except httpx.HTTPError as e:
        return {"error": f"Falha ao criar contêiner: {str(e)}"}


@mcp.tool()
def get_container_logs(name_or_id: str, tail: int = 100) -> str:
    """Recupera os últimos logs limpos (stdout/stderr sem caracteres de controle) de um contêiner."""
    try:
        resp = client.get(
            f"/containers/{name_or_id}/logs",
            params={"stdout": 1, "stderr": 1, "tail": min(tail, 500), "timestamps": 1}
        )
        if resp.status_code == 404:
            return f"Contêiner '{name_or_id}' não encontrado."
        resp.raise_for_status()
        return _demux_docker_stream(resp.content)
    except httpx.HTTPError as e:
        return f"Falha ao recuperar logs: {str(e)}"


@mcp.tool()
def get_container_stats(name_or_id: str) -> Dict[str, Any]:
    """Obtém métricas em tempo real de consumo de CPU e Memória RAM de um contêiner."""
    try:
        resp = client.get(f"/containers/{name_or_id}/stats", params={"stream": False})
        if resp.status_code == 404:
            return {"error": f"Contêiner '{name_or_id}' não encontrado."}
        resp.raise_for_status()
        stats = resp.json()

        mem_usage = stats.get("memory_stats", {}).get("usage", 0) / (1024 * 1024)
        mem_limit = stats.get("memory_stats", {}).get("limit", 1) / (1024 * 1024)
        mem_percent = (mem_usage / mem_limit) * 100 if mem_limit > 0 else 0

        cpu_delta = (
            stats.get("cpu_stats", {}).get("cpu_usage", {}).get("total_usage", 0)
            - stats.get("precpu_stats", {}).get("cpu_usage", {}).get("total_usage", 0)
        )
        system_delta = (
            stats.get("cpu_stats", {}).get("system_cpu_usage", 0)
            - stats.get("precpu_stats", {}).get("system_cpu_usage", 0)
        )
        online_cpus = stats.get("cpu_stats", {}).get("online_cpus", 1)
        cpu_percent = 0.0
        if system_delta > 0 and cpu_delta > 0:
            cpu_percent = (cpu_delta / system_delta) * online_cpus * 100.0

        return {
            "container": name_or_id,
            "memory_usage_mb": round(mem_usage, 2),
            "memory_limit_mb": round(mem_limit, 2),
            "memory_percent": round(mem_percent, 2),
            "cpu_percent": round(cpu_percent, 2),
        }
    except httpx.HTTPError as e:
        return {"error": f"Falha ao obter métricas: {str(e)}"}


@mcp.tool()
def restart_container(name_or_id: str) -> str:
    """Reinicia um contêiner com segurança."""
    if _is_protected(name_or_id):
        return f"Ação Negada: O contêiner '{name_or_id}' é protegido pelo sistema."
    try:
        resp = client.post(f"/containers/{name_or_id}/restart")
        if resp.status_code == 404:
            return f"Contêiner '{name_or_id}' não encontrado."
        if resp.status_code == 204:
            return f"Contêiner '{name_or_id}' reiniciado com sucesso."
        return f"Status retornado: {resp.status_code}"
    except httpx.HTTPError as e:
        return f"Falha ao reiniciar contêiner: {str(e)}"


@mcp.tool()
def start_container(name_or_id: str) -> str:
    """Inicia um contêiner que está parado."""
    try:
        resp = client.post(f"/containers/{name_or_id}/start")
        if resp.status_code == 404:
            return f"Contêiner '{name_or_id}' não encontrado."
        if resp.status_code in (204, 304):
            return f"Contêiner '{name_or_id}' iniciado com sucesso."
        return f"Status retornado: {resp.status_code}"
    except httpx.HTTPError as e:
        return f"Falha ao iniciar contêiner: {str(e)}"


@mcp.tool()
def stop_container(name_or_id: str) -> str:
    """Para um contêiner de forma graciosa."""
    if _is_protected(name_or_id):
        return f"Ação Negada: O contêiner '{name_or_id}' é protegido pelo sistema."
    try:
        resp = client.post(f"/containers/{name_or_id}/stop")
        if resp.status_code == 404:
            return f"Contêiner '{name_or_id}' não encontrado."
        if resp.status_code in (204, 304):
            return f"Contêiner '{name_or_id}' parado com sucesso."
        return f"Status retornado: {resp.status_code}"
    except httpx.HTTPError as e:
        return f"Falha ao parar contêiner: {str(e)}"


@mcp.tool()
def remove_container(name_or_id: str, force: bool = False) -> str:
    """Remove um contêiner do sistema."""
    if _is_protected(name_or_id):
        return f"Ação Negada: O contêiner '{name_or_id}' é protegido pelo sistema."
    try:
        resp = client.delete(f"/containers/{name_or_id}", params={"force": force, "v": True})
        if resp.status_code == 204:
            return f"Contêiner '{name_or_id}' removido com sucesso."
        if resp.status_code == 404:
            return f"Contêiner '{name_or_id}' não encontrado."
        return f"Status retornado: {resp.status_code}"
    except httpx.HTTPError as e:
        return f"Falha ao remover contêiner: {str(e)}"


@mcp.tool()
def update_container_resources(
    name_or_id: str,
    memory_limit_mb: Optional[int] = None,
    cpu_shares: Optional[int] = None
) -> Dict[str, Any]:
    """
    Atualiza dinamicamente os limites de memória RAM e CPU de um contêiner em execução (sem recriá-lo).
    
    Args:
        name_or_id: Nome ou ID do contêiner.
        memory_limit_mb: Novo limite de memória RAM em megabytes (ex: 64, 128, máx 380 para proteger o CLP).
        cpu_shares: Peso relativo de compartilhamento de CPU (ex: 512, 1024).
    """
    payload: Dict[str, Any] = {}
    if memory_limit_mb is not None:
        mem_clamped = min(memory_limit_mb, 380)
        payload["Memory"] = mem_clamped * 1024 * 1024
    if cpu_shares is not None:
        payload["CpuShares"] = cpu_shares

    if not payload:
        return {"error": "Nenhum parâmetro de recurso fornecido (informe memory_limit_mb ou cpu_shares)."}

    try:
        resp = client.post(f"/containers/{name_or_id}/update", json=payload)
        if resp.status_code == 404:
            return {"error": f"Contêiner '{name_or_id}' não encontrado."}
        resp.raise_for_status()
        return {
            "status": "success",
            "container": name_or_id,
            "message": f"Recursos do contêiner '{name_or_id}' atualizados com sucesso.",
            "updated_resources": payload
        }
    except httpx.HTTPError as e:
        return {"error": f"Falha ao atualizar recursos do contêiner: {str(e)}"}


@mcp.tool()
def get_container_processes(name_or_id: str, ps_args: str = "-ef") -> Dict[str, Any]:
    """
    Lista os processos em execução dentro de um contêiner (equivalente à aba Top/Processes do Portainer).
    
    Args:
        name_or_id: Nome ou ID do contêiner.
        ps_args: Argumentos passados para o utilitário ps (padrão: '-ef').
    """
    try:
        resp = client.get(f"/containers/{name_or_id}/top", params={"ps_args": ps_args})
        if resp.status_code == 404:
            return {"error": f"Contêiner '{name_or_id}' não encontrado."}
        resp.raise_for_status()
        data = resp.json()
        titles = data.get("Titles", [])
        processes = data.get("Processes", [])
        return {
            "container": name_or_id,
            "titles": titles,
            "processes_count": len(processes),
            "processes": [dict(zip(titles, proc)) for proc in processes]
        }
    except httpx.HTTPError as e:
        return {"error": f"Falha ao listar processos do contêiner: {str(e)}"}


@mcp.tool()
def rename_container(name_or_id: str, new_name: str) -> Dict[str, Any]:
    """
    Renomeia um contêiner existente.
    
    Args:
        name_or_id: Nome ou ID atual do contêiner.
        new_name: Novo nome desejado para o contêiner.
    """
    if _is_protected(name_or_id):
        return {"error": f"Ação Negada: O contêiner '{name_or_id}' é protegido pelo sistema e não pode ser renomeado."}
    try:
        resp = client.post(f"/containers/{name_or_id}/rename", params={"name": new_name})
        if resp.status_code == 404:
            return {"error": f"Contêiner '{name_or_id}' não encontrado."}
        resp.raise_for_status()
        return {
            "status": "success",
            "old_name": name_or_id,
            "new_name": new_name,
            "message": f"Contêiner renomeado de '{name_or_id}' para '{new_name}' com sucesso."
        }
    except httpx.HTTPError as e:
        return {"error": f"Falha ao renomear contêiner: {str(e)}"}


# ============================================================================
# 5. GESTÃO DE IMAGENS (IMAGES)
# ============================================================================

@mcp.tool()
def list_images() -> List[Dict[str, Any]]:
    """Lista todas as imagens Docker locais no dispositivo embarcado."""
    try:
        resp = client.get("/images/json")
        resp.raise_for_status()
        images = resp.json()
        return [
            {
                "id": img["Id"][:19],
                "repo_tags": img.get("RepoTags") or ["<none>"],
                "size_mb": round(img.get("Size", 0) / (1024 * 1024), 2),
                "created": img.get("Created"),
            }
            for img in images
        ]
    except httpx.HTTPError as e:
        return [{"error": f"Falha ao listar imagens: {str(e)}"}]


@mcp.tool()
def pull_image(
    image_name: str,
    username: Optional[str] = None,
    password: Optional[str] = None
) -> str:
    """
    Baixa uma imagem do Docker Hub ou de um registry privado (com autenticação opcional).
    
    Args:
        image_name: Nome completo da imagem (ex: 'eclipse-mosquitto:latest', 'ghcr.io/org/repo:tag').
        username: Nome de usuário do registry (opcional para repositórios privados).
        password: Senha ou token de acesso pessoal (opcional).
    """
    headers = {}
    if username and password:
        auth_data = {"username": username, "password": password}
        auth_b64 = base64.b64encode(json.dumps(auth_data).encode("utf-8")).decode("utf-8")
        headers["X-Registry-Auth"] = auth_b64

    try:
        resp = client.post(
            "/images/create",
            params={"fromImage": image_name},
            headers=headers,
            timeout=300.0
        )
        if resp.status_code == 404:
            return f"Imagem '{image_name}' não encontrada no registry."
        resp.raise_for_status()
        return f"Download da imagem '{image_name}' concluído com sucesso."
    except httpx.HTTPError as e:
        return f"Falha ao baixar imagem: {str(e)}"


@mcp.tool()
def tag_image(source_image: str, target_repo: str, target_tag: str = "latest") -> str:
    """
    Cria uma nova tag/alias para uma imagem Docker existente.
    
    Args:
        source_image: Nome ou ID da imagem de origem.
        target_repo: Novo repositório/nome para a tag (ex: 'meu-clp/aplicacao').
        target_tag: Tag da imagem (padrão: 'latest').
    """
    try:
        resp = client.post(
            f"/images/{source_image}/tag",
            params={"repo": target_repo, "tag": target_tag}
        )
        if resp.status_code == 404:
            return f"Imagem '{source_image}' não encontrada."
        resp.raise_for_status()
        return f"Imagem '{source_image}' etiquetada com sucesso como '{target_repo}:{target_tag}'."
    except httpx.HTTPError as e:
        return f"Falha ao etiquetar imagem: {str(e)}"


@mcp.tool()
def load_image_from_file(tar_path: str) -> Dict[str, Any]:
    """
    Carrega uma imagem Docker a partir de um arquivo .tar existente no disco do equipamento.
    Indispensável para ambientes industriais offline (sem acesso à internet).
    
    Args:
        tar_path: Caminho completo para o arquivo tar (ex: '/codesys/NextoUser/Files/minha_imagem.tar').
    """
    if not (tar_path.startswith("/codesys/NextoUser") or tar_path.startswith("/tmp") or tar_path.startswith("/var")):
        return {"error": f"Ação Negada por Segurança: Caminho '{tar_path}' fora das pastas autorizadas."}

    if not os.path.exists(tar_path):
        return {"error": f"Arquivo '{tar_path}' não encontrado no sistema de arquivos do CLP."}

    try:
        with open(tar_path, "rb") as f:
            tar_data = f.read()

        resp = client.post(
            "/images/load",
            content=tar_data,
            headers={"Content-Type": "application/x-tar"},
            timeout=300.0
        )
        resp.raise_for_status()
        return {
            "status": "success",
            "file": tar_path,
            "output": resp.text.strip(),
            "message": f"Imagem(ns) carregada(s) com sucesso a partir de '{tar_path}'."
        }
    except Exception as e:
        return {"error": f"Falha ao carregar imagem do arquivo tar: {str(e)}"}


@mcp.tool()
def save_image_to_file(image_name: str, target_tar_path: str) -> Dict[str, Any]:
    """
    Exporta uma imagem Docker para um arquivo tar no disco do equipamento (backup offline).
    
    Args:
        image_name: Nome ou tag da imagem a exportar.
        target_tar_path: Caminho de destino para salvar o .tar (ex: '/codesys/NextoUser/Files/backup_img.tar').
    """
    if not (target_tar_path.startswith("/codesys/NextoUser") or target_tar_path.startswith("/tmp") or target_tar_path.startswith("/var")):
        return {"error": f"Ação Negada por Segurança: Caminho '{target_tar_path}' fora das pastas autorizadas."}

    try:
        resp = client.get(f"/images/{image_name}/get", timeout=300.0)
        if resp.status_code == 404:
            return {"error": f"Imagem '{image_name}' não encontrada."}
        resp.raise_for_status()

        os.makedirs(os.path.dirname(os.path.abspath(target_tar_path)), exist_ok=True)
        with open(target_tar_path, "wb") as f:
            f.write(resp.content)

        size_mb = os.path.getsize(target_tar_path) / (1024 * 1024)
        return {
            "status": "success",
            "image": image_name,
            "file": target_tar_path,
            "size_mb": round(size_mb, 2),
            "message": f"Imagem '{image_name}' exportada com sucesso para '{target_tar_path}'."
        }
    except Exception as e:
        return {"error": f"Falha ao salvar imagem para arquivo tar: {str(e)}"}


@mcp.tool()
def remove_image(image_name_or_id: str, force: bool = False) -> str:
    """Remove uma imagem Docker local para liberar memória flash."""
    try:
        resp = client.delete(f"/images/{image_name_or_id}", params={"force": force})
        if resp.status_code == 200:
            return f"Imagem '{image_name_or_id}' removida com sucesso."
        return f"Status retornado: {resp.status_code}"
    except httpx.HTTPError as e:
        return f"Falha ao remover imagem: {str(e)}"


# ============================================================================
# 6. DIAGNÓSTICO, ARMAZENAMENTO E LIMPEZA
# ============================================================================

@mcp.tool()
def get_host_diagnostics() -> Dict[str, Any]:
    """
    Retorna métricas de integridade de hardware do CLP (uptime, carga, memória livre da CPU e partições).
    """
    diag: Dict[str, Any] = {}
    try:
        if os.path.exists("/proc/uptime"):
            with open("/proc/uptime", "r") as f:
                uptime_sec = float(f.read().split()[0])
                diag["uptime_hours"] = round(uptime_sec / 3600, 2)

        if os.path.exists("/proc/loadavg"):
            with open("/proc/loadavg", "r") as f:
                load = f.read().split()[:3]
                diag["load_average"] = [float(x) for x in load]

        if os.path.exists("/proc/meminfo"):
            mem: Dict[str, float] = {}
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        key = parts[0].strip()
                        val = parts[1].strip().split()[0]
                        if key in ("MemTotal", "MemFree", "MemAvailable", "Buffers", "Cached"):
                            mem[key] = round(int(val) / 1024, 2)
            diag["host_memory_mb"] = mem

        check_path = "/codesys/NextoUser" if os.path.exists("/codesys/NextoUser") else "/"
        if hasattr(os, "statvfs"):
            stat = os.statvfs(check_path)
            total_mb = (stat.f_blocks * stat.f_frsize) / (1024 * 1024)
            free_mb = (stat.f_bavail * stat.f_frsize) / (1024 * 1024)
            diag["disk_storage"] = {
                "path": check_path,
                "total_mb": round(total_mb, 2),
                "free_mb": round(free_mb, 2),
                "used_percent": round(((total_mb - free_mb) / total_mb) * 100, 1) if total_mb > 0 else 0
            }
    except Exception as e:
        diag["error"] = f"Falha parcial ao ler diagnósticos do host: {str(e)}"

    return diag

@mcp.tool()
def get_docker_system_info() -> Dict[str, Any]:
    """Retorna o status geral do ambiente Docker (total de contêineres, versão do SO, arquitetura)."""
    try:
        resp = client.get("/info")
        resp.raise_for_status()
        info = resp.json()
        return {
            "containers_total": info.get("Containers", 0),
            "containers_running": info.get("ContainersRunning", 0),
            "containers_paused": info.get("ContainersPaused", 0),
            "containers_stopped": info.get("ContainersStopped", 0),
            "images_count": info.get("Images", 0),
            "operating_system": info.get("OperatingSystem"),
            "architecture": info.get("Architecture"),
            "cpus": info.get("NCPU"),
            "total_memory_mb": round(info.get("MemTotal", 0) / (1024 * 1024), 2),
            "server_version": info.get("ServerVersion"),
        }
    except httpx.HTTPError as e:
        return {"error": f"Falha ao obter informações do sistema Docker: {str(e)}"}


@mcp.tool()
def get_system_disk_usage() -> Dict[str, Any]:
    """Retorna o uso detalhado de espaço em disco no CLP por imagens, contêineres e volumes."""
    try:
        resp = client.get("/system/df")
        resp.raise_for_status()
        data = resp.json()

        layers_size = data.get("LayersSize", 0) / (1024 * 1024)
        images = data.get("Images") or []
        containers = data.get("Containers") or []
        volumes = data.get("Volumes") or []

        images_size = sum(img.get("Size", 0) for img in images) / (1024 * 1024)

        return {
            "images_count": len(images),
            "images_total_mb": round(images_size, 2),
            "containers_count": len(containers),
            "volumes_count": len(volumes),
            "total_layers_size_mb": round(layers_size, 2),
        }
    except httpx.HTTPError as e:
        return {"error": f"Falha ao calcular uso de disco: {str(e)}"}


@mcp.tool()
def prune_system(all: bool = False) -> Dict[str, Any]:
    """Executa limpeza de recursos órfãos (containers parados, redes vazias, imagens dangling)."""
    try:
        c_resp = client.post("/containers/prune").json()
        n_resp = client.post("/networks/prune").json()
        i_resp = client.post("/images/prune", params={"all": all}).json()

        space_reclaimed_mb = (
            (c_resp.get("SpaceReclaimed", 0) + i_resp.get("SpaceReclaimed", 0)) / (1024 * 1024)
        )

        return {
            "containers_deleted": c_resp.get("ContainersDeleted") or [],
            "images_deleted": len(i_resp.get("ImagesDeleted") or []),
            "space_reclaimed_mb": round(space_reclaimed_mb, 2),
            "status": "Limpeza concluída com sucesso."
        }
    except httpx.HTTPError as e:
        return {"error": f"Falha ao executar limpeza do sistema: {str(e)}"}


if __name__ == "__main__":
    logger.info(f"Iniciando Complete MCP Docker Manager em SSE na porta {MCP_PORT} (0.0.0.0)...")
    if hasattr(mcp, "settings") and hasattr(mcp.settings, "host"):
        mcp.settings.host = "0.0.0.0"
        mcp.settings.port = MCP_PORT
    try:
        mcp.run(transport="sse")
    except TypeError:
        mcp.run(transport="sse", host="0.0.0.0", port=MCP_PORT)
