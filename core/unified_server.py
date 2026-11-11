import os
import json
import yaml
import asyncio
from aiohttp import web
from typing import Dict, Any

from config.logger import setup_logging
from core.api.ota_handler import OTAHandler
from core.api.vision_handler import VisionHandler
from core.connection import ConnectionHandler
from core.auth import AuthManager, AuthenticationError
from core.utils.modules_initialize import initialize_modules
from core.chat_history import get_chat_history, record_chat_message

TAG = __name__

class AiohttpWsAdapter:
    """Adapts aiohttp.web.WebSocketResponse to match websockets.ServerConnection interface"""
    def __init__(self, ws: web.WebSocketResponse, request: web.Request):
        self._ws = ws
        self.request = type("Req", (), {
            "headers": dict(request.headers),
            "path": request.path_qs,
            "query": dict(request.query),
        })()
        self.remote_address = request.remote

    @property
    def closed(self) -> bool:
        return self._ws.closed

    @property
    def state(self):
        class State:
            name = "OPEN" if not self._ws.closed else "CLOSED"
        return State()

    async def send(self, data):
        if self._ws.closed:
            return
        if isinstance(data, str):
            await self._ws.send_str(data)
        elif isinstance(data, (bytes, bytearray)):
            await self._ws.send_bytes(data)

    async def close(self, code=1000, reason=""):
        if not self._ws.closed:
            msg = reason.encode("utf-8") if isinstance(reason, str) else (reason or b"")
            await self._ws.close(code=code, message=msg)

    def __aiter__(self):
        return self

    async def __anext__(self):
        msg = await self._ws.receive()
        if msg.type == web.WSMsgType.TEXT:
            return msg.data
        elif msg.type == web.WSMsgType.BINARY:
            return msg.data
        elif msg.type in (web.WSMsgType.CLOSE, web.WSMsgType.CLOSING, web.WSMsgType.CLOSED):
            raise StopAsyncIteration
        elif msg.type == web.WSMsgType.ERROR:
            raise StopAsyncIteration
        return msg.data


class UnifiedServer:
    def __init__(self, config: dict):
        self.config = config
        self.logger = setup_logging(config)
        self.config_lock = asyncio.Lock()

        # Initialize core AI modules
        modules = initialize_modules(
            self.logger,
            self.config,
            "VAD" in self.config.get("selected_module", {}),
            "ASR" in self.config.get("selected_module", {}),
            "LLM" in self.config.get("selected_module", {}),
            False,
            "Memory" in self.config.get("selected_module", {}),
            "Intent" in self.config.get("selected_module", {}),
        )
        self._vad = modules.get("vad")
        self._asr = modules.get("asr")
        self._llm = modules.get("llm")
        self._memory = modules.get("memory")
        self._intent = modules.get("intent")

        # Handlers
        self.ota_handler = OTAHandler(config)
        self.vision_handler = VisionHandler(config)

        # Auth
        auth_config = self.config["server"].get("auth", {})
        self.auth_enable = auth_config.get("enabled", False)
        self.allowed_devices = set(auth_config.get("allowed_devices", []))
        secret_key = self.config["server"].get("auth_key", "default-key")
        expire_seconds = auth_config.get("expire_seconds", None)
        self.auth = AuthManager(secret_key=secret_key, expire_seconds=expire_seconds)

        self.last_connected_device = "90:70:69:eb:12:60"
        self.active_sessions_count = 0

    async def handle_index(self, request: web.Request) -> web.Response:
        html_path = os.path.join(os.getcwd(), "data", "web", "index.html")
        if os.path.exists(html_path):
            with open(html_path, "r", encoding="utf-8") as f:
                content = f.read()
            return web.Response(text=content, content_type="text/html", charset="utf-8")
        return web.Response(text="<h1>XiaoZhi Control Center</h1>", content_type="text/html")

    async def handle_get_config(self, request: web.Request) -> web.Response:
        return web.json_response(self.config)

    async def handle_post_config(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            async with self.config_lock:
                # Update LLM settings
                if "model_name" in data and "SiliconFlowLLM" in self.config.get("LLM", {}):
                    self.config["LLM"]["SiliconFlowLLM"]["model_name"] = data["model_name"]
                if "temperature" in data and "SiliconFlowLLM" in self.config.get("LLM", {}):
                    self.config["LLM"]["SiliconFlowLLM"]["temperature"] = float(data["temperature"])
                if "max_tokens" in data and "SiliconFlowLLM" in self.config.get("LLM", {}):
                    self.config["LLM"]["SiliconFlowLLM"]["max_tokens"] = int(data["max_tokens"])
                if "api_key" in data and data["api_key"]:
                    if "SiliconFlowLLM" in self.config.get("LLM", {}):
                        self.config["LLM"]["SiliconFlowLLM"]["api_key"] = data["api_key"]
                    if "CosyVoiceSiliconflow" in self.config.get("TTS", {}):
                        self.config["TTS"]["CosyVoiceSiliconflow"]["access_token"] = data["api_key"]
                    if "OpenaiASR" in self.config.get("ASR", {}):
                        self.config["ASR"]["OpenaiASR"]["api_key"] = data["api_key"]

                # Update TTS voice
                if "voice" in data and "CosyVoiceSiliconflow" in self.config.get("TTS", {}):
                    self.config["TTS"]["CosyVoiceSiliconflow"]["voice"] = data["voice"]

                # Save to data/.config.yaml
                config_path = os.path.join(os.getcwd(), "data", ".config.yaml")
                with open(config_path, "w", encoding="utf-8") as f:
                    yaml.dump(self.config, f, allow_unicode=True)

                # Re-initialize modules
                modules = initialize_modules(
                    self.logger,
                    self.config,
                    False,
                    False,
                    True,
                    False,
                    False,
                    False,
                )
                if "llm" in modules:
                    self._llm = modules["llm"]

            self.logger.bind(tag=TAG).info("Config updated from Web Manager")
            return web.json_response({"success": True})
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"Error updating config: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def handle_get_prompt(self, request: web.Request) -> web.Response:
        prompt_path = os.path.join(os.getcwd(), "agent-base-prompt.txt")
        if os.path.exists(prompt_path):
            with open(prompt_path, "r", encoding="utf-8") as f:
                content = f.read()
            return web.Response(text=content, content_type="text/plain", charset="utf-8")
        return web.Response(text="You are a helpful voice assistant.", content_type="text/plain")

    async def handle_post_prompt(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            prompt = data.get("prompt", "")
            prompt_path = os.path.join(os.getcwd(), "agent-base-prompt.txt")
            with open(prompt_path, "w", encoding="utf-8") as f:
                f.write(prompt)
            self.logger.bind(tag=TAG).info("agent-base-prompt.txt updated from Web Manager")
            return web.json_response({"success": True})
        except Exception as e:
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def handle_get_mcp(self, request: web.Request) -> web.Response:
        mcp_paths = [
            os.path.join(os.getcwd(), "data", ".mcp_server_settings.json"),
            os.path.join(os.getcwd(), "mcp_server_settings.json"),
        ]
        for p in mcp_paths:
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    try:
                        return web.json_response(json.load(f))
                    except Exception:
                        pass
        return web.json_response({"mcpServers": {}})

    async def handle_post_mcp(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            mcp_path = os.path.join(os.getcwd(), "data", ".mcp_server_settings.json")
            with open(mcp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.logger.bind(tag=TAG).info("MCP settings updated from Web Manager")
            return web.json_response({"success": True})
        except Exception as e:
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def handle_get_status(self, request: web.Request) -> web.Response:
        return web.json_response({
            "online": True,
            "device_id": self.last_connected_device,
            "active_sessions": self.active_sessions_count,
            "history": get_chat_history()
        })

    async def handle_post_chat(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            message = data.get("message", "").strip()
            if not message:
                return web.json_response({"reply": "Messaggio vuoto."})

            record_chat_message("user", message)

            # Query the active LLM
            prompt_path = os.path.join(os.getcwd(), "agent-base-prompt.txt")
            system_prompt = "You are a helpful assistant."
            if os.path.exists(prompt_path):
                with open(prompt_path, "r", encoding="utf-8") as f:
                    system_prompt = f.read()

            dialogue = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message}
            ]

            full_reply = ""
            if self._llm:
                response = self._llm.response("web-session", dialogue)
                if hasattr(response, "__iter__") and not asyncio.iscoroutine(response):
                    for chunk in response:
                        if chunk:
                            full_reply += str(chunk)
                elif asyncio.iscoroutine(response):
                    full_reply = await response
                else:
                    full_reply = str(response)
            else:
                full_reply = f"Ricevuto: {message}. (LLM in inizializzazione)"

            clean_reply = full_reply.strip() or "Nessuna risposta."
            record_chat_message("assistant", clean_reply)
            return web.json_response({"reply": clean_reply})
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"Error in web chat: {e}")
            return web.json_response({"reply": f"Errore durante l'elaborazione: {e}"}, status=500)

    async def handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30.0)
        await ws.prepare(request)
        adapter = AiohttpWsAdapter(ws, request)

        device_id = request.headers.get("device-id") or request.query.get("device-id")
        if device_id:
            self.last_connected_device = device_id
            adapter.request.headers["device-id"] = device_id
        if "client-id" in request.query:
            adapter.request.headers["client-id"] = request.query["client-id"]
        if "authorization" in request.query:
            adapter.request.headers["authorization"] = request.query["authorization"]

        self.logger.bind(tag=TAG).info(f"WebSocket client connection established for device: {device_id}")
        self.active_sessions_count += 1

        handler = ConnectionHandler(
            self.config,
            self._vad,
            self._asr,
            self._llm,
            self._memory,
            self._intent,
            self,
        )

        try:
            await handler.handle_connection(adapter)
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"WebSocket connection error: {e}")
        finally:
            self.active_sessions_count = max(0, self.active_sessions_count - 1)
            await adapter.close()

        return ws

    def create_app(self) -> web.Application:
        app = web.Application()

        # Web Manager & REST APIs
        app.router.add_get("/", self.handle_index)
        app.router.add_get("/api/config", self.handle_get_config)
        app.router.add_post("/api/config", self.handle_post_config)
        app.router.add_get("/api/prompt", self.handle_get_prompt)
        app.router.add_post("/api/prompt", self.handle_post_prompt)
        app.router.add_get("/api/mcp", self.handle_get_mcp)
        app.router.add_post("/api/mcp", self.handle_post_mcp)
        app.router.add_get("/api/status", self.handle_get_status)
        app.router.add_post("/api/chat", self.handle_post_chat)

        # OTA endpoints (for ESP32 hardware handshake)
        app.router.add_get("/xiaozhi/ota/", self.ota_handler.handle_get)
        app.router.add_post("/xiaozhi/ota/", self.ota_handler.handle_post)
        app.router.add_options("/xiaozhi/ota/", self.ota_handler.handle_options)
        app.router.add_get("/xiaozhi/ota/download/{filename}", self.ota_handler.handle_download)
        app.router.add_options("/xiaozhi/ota/download/{filename}", self.ota_handler.handle_options)

        # Vision endpoints
        app.router.add_get("/mcp/vision/explain", self.vision_handler.handle_get)
        app.router.add_post("/mcp/vision/explain", self.vision_handler.handle_post)
        app.router.add_options("/mcp/vision/explain", self.vision_handler.handle_options)

        # WebSocket endpoints (Audio stream for ESP32)
        app.router.add_get("/xiaozhi/v1/", self.handle_ws)
        app.router.add_get("/ws", self.handle_ws)

        return app
