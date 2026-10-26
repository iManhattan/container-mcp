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
from typing import List, Dict, Any, Optional
from mcp.server.fastmcp import FastMCP
import httpx
import yaml

# Configuração de logs
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mcp-docker-manager")

# Configurações de ambiente
PROTECTED_CONTAINERS_RAW = os.getenv("PROTECTED_CONTAINERS", "mcp-portainer-alternative")
PROTECTED_CONTAINERS = [name.strip() for name in PROTECTED_CONTAINERS_RAW.split(",") if name.strip()]
MCP_PORT = int(os.getenv("MCP_PORT", "8443"))

# Inicializa o FastMCP escutando em 0.0.0.0
mcp = FastMCP("Docker-Portainer-SafeMCP", host="0.0.0.0", port=MCP_PORT)

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

            payload = {
                "Image": image,
                "Cmd": cmd,
                "Env": env_list,
                "Labels": labels,
                "ExposedPorts": exposed_ports,
                "HostConfig": {
                    "PortBindings": port_bindings,
                    "Binds": binds,
                    "NetworkMode": network_name,
                    "RestartPolicy": {"Name": s_cfg.get("restart", "unless-stopped")},
                    "Privileged": False
                }
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
    auto_start: bool = True
) -> Dict[str, Any]:
    """Cria e opcionalmente inicia um novo contêiner Docker com regras de segurança embutidas."""
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

    payload = {
        "Image": image,
        "Cmd": cmd,
        "Env": env_list,
        "ExposedPorts": exposed_ports,
        "HostConfig": {
            "PortBindings": port_bindings,
            "Binds": binds,
            "RestartPolicy": {"Name": restart_policy},
            "Privileged": False,
        }
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
    """Recupera os últimos logs (stdout/stderr) de um contêiner."""
    try:
        resp = client.get(
            f"/containers/{name_or_id}/logs",
            params={"stdout": 1, "stderr": 1, "tail": min(tail, 500), "timestamps": 1}
        )
        if resp.status_code == 404:
            return f"Contêiner '{name_or_id}' não encontrado."
        resp.raise_for_status()
        return resp.text
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
def pull_image(image_name: str) -> str:
    """Baixa uma imagem do Docker Hub ou de um registry público."""
    try:
        resp = client.post("/images/create", params={"fromImage": image_name}, timeout=300.0)
        resp.raise_for_status()
        return f"Download da imagem '{image_name}' concluído com sucesso."
    except httpx.HTTPError as e:
        return f"Falha ao baixar imagem: {str(e)}"


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
    mcp.settings.host = "0.0.0.0"
    mcp.settings.port = MCP_PORT
    mcp.run(transport="sse")
