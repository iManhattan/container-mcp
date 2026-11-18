# Safe Docker MCP Server - Suíte Industrial Completa (38 Tools)

Servidor MCP (**Model Context Protocol**) de alto desempenho e segurança para produtos de software embarcado com Docker (Altus Nexto / CODESYS / Linux Industrial / Buildroot / Ubuntu). Substitui integralmente o Portainer para automações inteligentes e agentes de IA sem qualquer risco de acesso indevido ao terminal (shell) do sistema operacional.

---

## 🛠️ Catálogo Completo de Ferramentas (38 Tools)

### 1. Gestão de Stacks (Docker Compose)
- `deploy_stack(stack_name, compose_yaml)`: Realiza deploy atômico de Stacks a partir de YAML, suportando mapeamento de portas, variáveis, volumes, redes e limites de memória/CPU (`mem_limit`, `cpu_shares`).
- `list_stacks()`: Lista todas as Stacks implantadas com contagem de serviços e status.
- `inspect_stack(stack_name)`: Detalha serviços, imagens e contêineres de uma Stack.
- `remove_stack(stack_name)`: Para e remove com segurança todos os recursos da Stack.

### 2. Gestão de Contêineres (Containers)
- `create_container(image, name, ports, env, volumes, cmd, restart_policy, memory_limit_mb, cpu_shares, auto_start)`: Cria contêineres com controle rígido de quotas de RAM/CPU e proteção contra montagens perigosas de volumes.
- `list_containers(all)`: Inventário detalhado de contêineres em execução ou parados.
- `inspect_container(name_or_id)`: Inspeção técnica completa (IPs, portas, mounts, estado de execução).
- `start_container(name_or_id)`: Inicia um contêiner parado.
- `stop_container(name_or_id)`: Para um contêiner de forma graciosa (com proteção de autodestruição).
- `restart_container(name_or_id)`: Reinicia um contêiner.
- `remove_container(name_or_id, force)`: Deleta um contêiner (com bloqueio para protegidos).
- `rename_container(name_or_id, new_name)`: Renomeia um contêiner existente.
- `update_container_resources(name_or_id, memory_limit_mb, cpu_shares)`: Ajusta dinamicamente os limites de RAM e CPU em tempo de execução.
- `get_container_processes(name_or_id, ps_args)`: Lista os processos e comandos ativos dentro do contêiner (equivalente à aba Processes do Portainer).
- `get_container_logs(name_or_id, tail)`: Recupera logs filtrados e decodificados com demuxer padrão Docker (sem caracteres corrompidos).
- `get_container_stats(name_or_id)`: Métricas em tempo real de consumo de CPU (%) e RAM (MB e %).

### 3. Gestão de Imagens e Ambientes Offline (Air-Gapped)
- `list_images()`: Lista imagens locais com IDs, tags e tamanho em MB.
- `pull_image(image_name, username, password)`: Baixa imagens públicas ou privadas com autenticação (Docker Hub, GHCR, Harbor).
- `tag_image(source_image, target_repo, target_tag)`: Cria novos aliases/tags locais.
- `load_image_from_file(tar_path)`: Carrega imagens a partir de arquivos `.tar` (essencial para CLPs sem internet).
- `save_image_to_file(image_name, target_tar_path)`: Exporta imagens para arquivos `.tar` para backup offline.
- `remove_image(image_name_or_id, force)`: Deleta imagens locais para liberar memória flash.

### 4. Redes e Ethernet Industrial (Networking)
- `list_networks()`: Lista redes ativas e contagem de contêineres conectados.
- `inspect_network(network_name_or_id)`: Inspeciona sub-redes, gateways e contêineres associados.
- `create_network(name, driver, subnet, gateway)`: Cria redes customizadas com endereçamento CIDR estático.
- `remove_network(network_name_or_id)`: Deleta uma rede existente.
- `connect_container_to_network(container, network, ip_address)`: Vincula contêiner à rede com IP fixo opcional.
- `disconnect_container_from_network(container, network, force)`: Desconecta contêiner de uma rede.
- `get_ethernet_interfaces()`: Métricas de tráfego (RX/TX em MB) das interfaces Ethernet ativas.

### 5. Volumes e Armazenamento Persistente (Storage)
- `list_volumes()`: Lista volumes de dados locais.
- `inspect_volume(name)`: Detalha pontos de montagem e escopo no filesystem do host.
- `create_volume(name, driver, labels)`: Cria volumes persistentes para aplicações industriais.
- `remove_volume(name, force)`: Remove volumes não utilizados.
- `prune_volumes()`: Limpeza em lote de volumes órfãos.

### 6. Diagnóstico de Hardware e Limpeza Geral
- `get_host_diagnostics()`: Monitoramento de hardware (uptime, load average, RAM total/livre do host e disco de usuário do Nexto).
- `get_docker_system_info()`: Visão global do Docker (versão, SO, arquitetura, CPUs).
- `get_system_disk_usage()`: Detalhamento do uso de disco por camadas, contêineres e imagens.
- `prune_system(all)`: Limpeza profunda de recursos órfãos para recuperação de memória flash.

---

## 🛡️ Diretrizes de Segurança Industrial
1. **Sem Acesso ao Terminal**: Nenhuma ferramenta expõe comandos arbitrários de shell (`sh`/`bash`).
2. **Whitelist de Montagens**: Caminhos críticos do host como `/`, `/var/run/docker.sock` e `/codesys` são bloqueados contra bind mounts arbitrários.
3. **Contêineres Protegidos**: `mcp-docker-manager` e contêineres essenciais são imunizados contra paradas ou remoções acidentais.
4. **Proteção Anti-OOM**: Criações e atualizações contam com limites superiores de memória RAM para proteger o runtime do CLP.
