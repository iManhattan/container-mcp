import unittest
import asyncio
import json
import os
import sys

# Adiciona o diretório do script ao path para importação
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from server import (
    _demux_docker_stream,
    _is_protected,
    mcp,
    PROTECTED_CONTAINERS
)
from mcp.client.sse import sse_client
from mcp.client.session import ClientSession


class TestServerUnit(unittest.TestCase):
    """Testes unitários rigorosos das funções internas do server.py"""

    def test_demux_empty(self):
        self.assertEqual(_demux_docker_stream(b""), "")

    def test_demux_plain_text(self):
        text = "Mensagem simples sem multiplexação\n"
        self.assertEqual(_demux_docker_stream(text.encode("utf-8")), text)

    def test_demux_stdout_frame(self):
        msg = "2026-11-18 Iniciando servico CLP Nexto\n"
        payload = msg.encode("utf-8")
        header = bytes([1, 0, 0, 0]) + len(payload).to_bytes(4, byteorder="big")
        raw = header + payload
        result = _demux_docker_stream(raw)
        self.assertEqual(result, msg)
        self.assertNotIn("\x00", result)
        self.assertNotIn("\x01", result)

    def test_demux_stderr_frame(self):
        msg = "ERROR: Falha de comunicacao Modbus\n"
        payload = msg.encode("utf-8")
        header = bytes([2, 0, 0, 0]) + len(payload).to_bytes(4, byteorder="big")
        raw = header + payload
        result = _demux_docker_stream(raw)
        self.assertEqual(result, msg)

    def test_demux_mixed_multiple_frames(self):
        msg1 = "Linha 1 do stdout\n"
        msg2 = "Linha 2 de alerta stderr\n"
        msg3 = "Linha 3 final com caracteres acentuados: CPU Operando OK (Atenção)\n"
        
        f1 = bytes([1, 0, 0, 0]) + len(msg1.encode("utf-8")).to_bytes(4, "big") + msg1.encode("utf-8")
        f2 = bytes([2, 0, 0, 0]) + len(msg2.encode("utf-8")).to_bytes(4, "big") + msg2.encode("utf-8")
        f3 = bytes([1, 0, 0, 0]) + len(msg3.encode("utf-8")).to_bytes(4, "big") + msg3.encode("utf-8")
        
        result = _demux_docker_stream(f1 + f2 + f3)
        expected = msg1 + msg2 + msg3
        self.assertEqual(result, expected)

    def test_is_protected(self):
        self.assertTrue(_is_protected("mcp-docker-manager"))
        self.assertTrue(_is_protected("mcp-portainer-alternative"))
        self.assertTrue(_is_protected("/mcp-docker-manager"))
        self.assertFalse(_is_protected("meu-container-usuario"))
        self.assertFalse(_is_protected("mosquitto-broker"))
        self.assertFalse(_is_protected("redis-cache"))

    def test_registered_tools_count(self):
        # O servidor FastMCP deve ter todas as 38 ferramentas registradas
        tools = mcp._tool_manager.list_tools()
        self.assertEqual(len(tools), 38, f"Esperado 38 ferramentas registradas, obtido: {len(tools)}")
        tool_names = [t.name for t in tools]
        
        # Novas ferramentas essenciais
        self.assertIn("update_container_resources", tool_names)
        self.assertIn("get_container_processes", tool_names)
        self.assertIn("rename_container", tool_names)
        self.assertIn("load_image_from_file", tool_names)
        self.assertIn("save_image_to_file", tool_names)
        self.assertIn("tag_image", tool_names)
        self.assertIn("get_host_diagnostics", tool_names)

    def test_security_tar_path_traversal(self):
        from server import load_image_from_file, save_image_to_file
        # Caminhos fora de /codesys/NextoUser, /tmp ou /var devem ser bloqueados
        res1 = load_image_from_file("/etc/shadow")
        self.assertIn("error", res1)
        self.assertIn("Ação Negada por Segurança", res1["error"])

        res2 = save_image_to_file("alpine", "/root/bad.tar")
        self.assertIn("error", res2)
        self.assertIn("Ação Negada por Segurança", res2["error"])

    def test_rename_protected_container_blocked(self):
        from server import rename_container
        res = rename_container("mcp-docker-manager", "new-name")
        self.assertIn("error", res)
        self.assertIn("Ação Negada", res["error"])

    def test_update_container_resources_empty_payload(self):
        from server import update_container_resources
        res = update_container_resources("some-cnt")
        self.assertIn("error", res)
        self.assertIn("Nenhum parâmetro de recurso fornecido", res["error"])


class TestRemoteIntegration(unittest.TestCase):
    """Testes de integração rigorosos no servidor MCP rodando no CLP 192.168.18.137"""

    @classmethod
    def setUpClass(cls):
        cls.url = "http://192.168.18.137:8443/sse"

    def run_async(self, coro):
        return asyncio.run(coro)

    def test_01_connection_and_system_info(self):
        async def _test():
            async with sse_client(self.url) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    res = await session.call_tool("get_docker_system_info", {})
                    data = json.loads(res.content[0].text)
                    self.assertIn("operating_system", data)
                    self.assertEqual(data.get("architecture"), "aarch64")
                    self.assertGreater(data.get("total_memory_mb", 0), 300)
                    print(f"\n[OK] SO detectado: {data.get('operating_system')} ({data.get('architecture')}), RAM: {data.get('total_memory_mb')} MB")
        self.run_async(_test())

    def test_02_security_rejection_of_protected_container(self):
        async def _test():
            async with sse_client(self.url) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    res = await session.call_tool("remove_container", {"name_or_id": "mcp-docker-manager", "force": True})
                    output = res.content[0].text
                    self.assertIn("Ação Negada", output)
                    print(f"\n[OK] Protecao de autodestruicao validada: {output.strip()}")
        self.run_async(_test())

    def test_03_security_rejection_forbidden_volume_binds(self):
        async def _test():
            async with sse_client(self.url) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    # Tenta criar container montando docker.sock no host
                    res = await session.call_tool("create_container", {
                        "image": "alpine:latest",
                        "name": "hacker-test",
                        "volumes": {"/var/run/docker.sock": "/host/docker.sock"}
                    })
                    data = json.loads(res.content[0].text)
                    self.assertIn("error", data)
                    self.assertIn("Ação Negada por Segurança", data["error"])
                    print(f"\n[OK] Bloqueio de montagem indevida validado: {data['error']}")
        self.run_async(_test())

    def test_04_storage_and_network_lifecycle(self):
        async def _test():
            async with sse_client(self.url) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    
                    # Cria volume de teste
                    v_create = await session.call_tool("create_volume", {"name": "val_rigorous_vol"})
                    v_data = json.loads(v_create.content[0].text)
                    self.assertEqual(v_data.get("status"), "success")
                    
                    # Inspeciona volume
                    v_insp = await session.call_tool("inspect_volume", {"name": "val_rigorous_vol"})
                    v_insp_data = json.loads(v_insp.content[0].text)
                    self.assertEqual(v_insp_data.get("name"), "val_rigorous_vol")
                    
                    # Remove volume
                    v_rem = await session.call_tool("remove_volume", {"name": "val_rigorous_vol", "force": True})
                    self.assertIn("removido com sucesso", v_rem.content[0].text)

                    # Cria rede de teste
                    n_create = await session.call_tool("create_network", {"name": "val_rigorous_net", "subnet": "172.29.0.0/24"})
                    n_data = json.loads(n_create.content[0].text)
                    self.assertEqual(n_data.get("status"), "success")

                    # Remove rede
                    n_rem = await session.call_tool("remove_network", {"network_name_or_id": "val_rigorous_net"})
                    self.assertIn("removida com sucesso", n_rem.content[0].text)
                    print("\n[OK] Ciclo de vida completo de Volumes e Redes validado.")
        self.run_async(_test())


if __name__ == "__main__":
    unittest.main(verbosity=2)
