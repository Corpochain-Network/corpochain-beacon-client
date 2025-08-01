from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import signal
import ssl
import subprocess
import sys
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Set, TextIO, Tuple

from corpochain import __version__
from corpochain.cmds.init_funcs import check_keys, corpochain_full_version_str, corpochain_init
from corpochain.cmds.passphrase_funcs import default_passphrase, using_default_passphrase
from corpochain.daemon.keychain_server import KeychainServer, keychain_commands
from corpochain.daemon.windows_signal import kill
from corpochain.plotters.plotters import get_available_plotters
from corpochain.plotting.util import add_plot_directory
from corpochain.server.server import ssl_context_for_root, ssl_context_for_server
from corpochain.ssl.create_ssl import get_mozilla_ca_crt
from corpochain.util.beta_metrics import BetaMetricsLogger
from corpochain.util.corpochain_logging import initialize_service_logging
from corpochain.util.config import load_config
from corpochain.util.errors import KeychainCurrentPassphraseIsInvalid
from corpochain.util.json_util import dict_to_json_str
from corpochain.util.keychain import Keychain, passphrase_requirements, supports_os_passphrase_storage
from corpochain.util.lock import Lockfile, LockfileError
from corpochain.util.network import WebServer
from corpochain.util.service_groups import validate_service
from corpochain.util.setproctitle import setproctitle
from corpochain.util.ws_message import WsRpcMessage, create_payload, format_response

io_pool_exc = ThreadPoolExecutor()

try:
    from aiohttp import ClientSession, WSMsgType, web
    from aiohttp.web_ws import WebSocketResponse
except ModuleNotFoundError:
    print("Error: Make sure to run . ./activate from the project folder before starting Corpochain.")
    quit()


log = logging.getLogger(__name__)

service_plotter = "corpochain_plotter"


async def fetch(url: str):
    async with ClientSession() as session:
        try:
            mozilla_root = get_mozilla_ca_crt()
            ssl_context = ssl_context_for_root(mozilla_root, log=log)
            response = await session.get(url, ssl=ssl_context)
            if not response.ok:
                log.warning("Response not OK.")
                return None
            return await response.text()
        except Exception as e:
            log.error(f"Exception while fetching {url}, exception: {e}")
            return None


class PlotState(str, Enum):
    SUBMITTED = "SUBMITTED"
    RUNNING = "RUNNING"
    REMOVING = "REMOVING"
    FINISHED = "FINISHED"


class PlotEvent(str, Enum):
    LOG_CHANGED = "log_changed"
    STATE_CHANGED = "state_changed"


# determine if application is a script file or frozen exe
if getattr(sys, "frozen", False):
    name_map = {
        "corpochain": "corpochain",
        "corpochain_beacon": "start_beacon",
        "corpochain_harvester": "start_harvester",
        "corpochain_farmer": "start_farmer",
        "corpochain_introducer": "start_introducer",
        "corpochain_timelord": "start_timelord",
        "corpochain_timelord_launcher": "timelord_launcher",
        "corpochain_seeder": "start_seeder",
        "corpochain_crawler": "start_crawler",
    }

    def executable_for_service(service_name: str) -> str:
        application_path = os.path.dirname(sys.executable)
        if sys.platform == "win32" or sys.platform == "cygwin":
            executable = name_map[service_name]
            path = f"{application_path}/{executable}.exe"
            return path
        else:
            path = f"{application_path}/{name_map[service_name]}"
            return path

else:
    application_path = os.path.dirname(__file__)

    def executable_for_service(service_name: str) -> str:
        return service_name


async def ping() -> Dict[str, Any]:
    response = {"success": True, "value": "pong"}
    return response


class WebSocketServer:
    def __init__(
        self,
        root_path: Path,
        ca_crt_path: Path,
        ca_key_path: Path,
        crt_path: Path,
        key_path: Path,
        run_check_keys_on_unlock: bool = False,
    ):
        self.root_path = root_path
        self.log = log
        self.services: Dict[str, List[subprocess.Popen]] = dict()
        self.plots_queue: List[Dict] = []
        self.connections: Dict[str, Set[WebSocketResponse]] = dict()  # service name : {WebSocketResponse}
        self.ping_job: Optional[asyncio.Task] = None
        self.net_config = load_config(root_path, "config.yaml")
        self.self_hostname = self.net_config["self_hostname"]
        self.daemon_port = self.net_config["daemon_port"]
        self.daemon_max_message_size = self.net_config.get("daemon_max_message_size", 50 * 1000 * 1000)
        self.heartbeat = self.net_config.get("daemon_heartbeat", 300)
        self.webserver: Optional[WebServer] = None
        self.ssl_context = ssl_context_for_server(ca_crt_path, ca_key_path, crt_path, key_path, log=self.log)
        self.keychain_server = KeychainServer()
        self.run_check_keys_on_unlock = run_check_keys_on_unlock
        self.shutdown_event = asyncio.Event()

    @asynccontextmanager
    async def run(self) -> AsyncIterator[None]:
        self.log.info("Starting Daemon Server")

        # Note: the minimum_version has been already set to TLSv1_2
        # in ssl_context_for_server()
        # Daemon is internal connections, so override to TLSv1_3 only
        if ssl.HAS_TLSv1_3:
            try:
                self.ssl_context.minimum_version = ssl.TLSVersion.TLSv1_3
            except ValueError:
                # in case the attempt above confused the config, set it again (likely not needed but doesn't hurt)
                self.ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2

        if self.ssl_context.minimum_version is not ssl.TLSVersion.TLSv1_3:
            self.log.warning(
                (
                    "Deprecation Warning: Your version of SSL (%s) does not support TLS1.3. "
                    "A future version of Corpochain will require TLS1.3."
                ),
                ssl.OPENSSL_VERSION,
            )

        self.webserver = await WebServer.create(
            hostname=self.self_hostname,
            port=self.daemon_port,
            keepalive_timeout=300,
            shutdown_timeout=3,
            routes=[web.get("/", self.incoming_connection)],
            ssl_context=self.ssl_context,
            logger=self.log,
        )
        try:
            yield
        finally:
            if not self.shutdown_event.is_set():
                await self.stop()
            await self.exit()

    async def setup_process_global_state(self) -> None:
        try:
            asyncio.get_running_loop().add_signal_handler(
                signal.SIGINT,
                functools.partial(self._accept_signal, signal_number=signal.SIGINT),
            )
            asyncio.get_running_loop().add_signal_handler(
                signal.SIGTERM,
                functools.partial(self._accept_signal, signal_number=signal.SIGTERM),
            )
        except NotImplementedError:
            self.log.info("Not implemented")

    def _accept_signal(self, signal_number: int, stack_frame=None):
        asyncio.create_task(self.stop())

    def cancel_task_safe(self, task: Optional[asyncio.Task]):
        if task is not None:
            try:
                task.cancel()
            except Exception as e:
                self.log.error(f"Error while canceling task.{e} {task}")

    async def stop(self) -> Dict[str, Any]:
        self.cancel_task_safe(self.ping_job)
        service_names = list(self.services.keys())
        stop_service_jobs = [
            asyncio.create_task(kill_service(self.root_path, self.services, s_n)) for s_n in service_names
        ]
        if stop_service_jobs:
            await asyncio.wait(stop_service_jobs)
        self.services.clear()
        self.shutdown_event.set()
        log.info(f"Daemon Server stopping, Services stopped: {service_names}")
        return {"success": True, "services_stopped": service_names}

    async def incoming_connection(self, request: web.Request) -> web.StreamResponse:
        ws: WebSocketResponse = web.WebSocketResponse(
            max_msg_size=self.daemon_max_message_size, heartbeat=self.heartbeat
        )
        await ws.prepare(request)

        while True:
            msg = await ws.receive()
            self.log.debug("Received message: %s", msg)
            decoded: WsRpcMessage = {
                "command": "",
                "ack": False,
                "data": {},
                "request_id": "",
                "destination": "",
                "origin": "",
            }
            if msg.type == WSMsgType.TEXT:
                try:
                    decoded = json.loads(msg.data)
                    if "data" not in decoded:
                        decoded["data"] = {}

                    maybe_response = await self.handle_message(ws, decoded)
                    if maybe_response is None:
                        continue

                    response, connections = maybe_response

                except Exception as e:
                    tb = traceback.format_exc()
                    self.log.error(f"Error while handling message: {tb}")
                    error = {"success": False, "error": f"{e}"}
                    response = format_response(decoded, error)
                    connections = {ws}  # send error back to the sender

                await self.send_all_responses(connections, response)
            else:
                service_names = self.remove_connection(ws)

                if len(service_names) == 0:
                    service_names = ["Unknown"]

                if msg.type == WSMsgType.CLOSE:
                    self.log.info(f"ConnectionClosed. Closing websocket with {service_names}")
                elif msg.type == WSMsgType.ERROR:
                    self.log.info(f"Websocket exception. Closing websocket with {service_names}. {ws.exception()}")
                else:
                    self.log.info(f"Unexpected message type. Closing websocket with {service_names}. {msg.type}")

                await ws.close()
                break

        return ws

    async def send_all_responses(self, connections: Set[WebSocketResponse], response: str) -> None:
        for connection in connections:
            try:
                await connection.send_str(response)
            except Exception as e:
                service_names = self.remove_connection(connection)
                if len(service_names) == 0:
                    service_names = ["Unknown"]

                if isinstance(e, ConnectionResetError):
                    self.log.info(f"Peer disconnected. Closing websocket with {service_names}")
                else:
                    tb = traceback.format_exc()
                    self.log.error(f"Unexpected exception trying to send to {service_names} (websocket: {e} {tb})")
                    self.log.info(f"Closing websocket with {service_names}")

                await connection.close()

    def remove_connection(self, websocket: WebSocketResponse) -> List[str]:
        """Returns a list of service names from which the connection was removed"""
        service_names = []
        for service_name, connections in self.connections.items():
            try:
                connections.remove(websocket)
            except KeyError:
                continue
            service_names.append(service_name)
        return service_names

    async def ping_task(self) -> None:
        restart = True
        await asyncio.sleep(30)
        for service_name, connections in self.connections.items():
            if service_name == service_plotter:
                continue
            for connection in connections.copy():
                try:
                    self.log.debug(f"About to ping: {service_name}")
                    await connection.ping()
                except asyncio.CancelledError:
                    self.log.warning("Ping task received Cancel")
                    restart = False
                    break
                except Exception:
                    self.log.exception(f"Ping error to {service_name}")
                    self.log.error(f"Ping failed, connection closed to {service_name}.")
                    self.remove_connection(connection)
                    await connection.close()
        if restart is True:
            self.ping_job = asyncio.create_task(self.ping_task())

    async def handle_message(
        self, websocket: WebSocketResponse, message: WsRpcMessage
    ) -> Optional[Tuple[str, Set[WebSocketResponse]]]:
        """
        This function gets called when new message is received via websocket.
        """

        command = message["command"]
        destination = message["destination"]
        if destination != "daemon":
            if destination in self.connections:
                sockets = self.connections[destination]
                return dict_to_json_str(message), sockets

            return None

        data = message["data"]
        commands_with_data = [
            "start_service",
            "start_plotting",
            "stop_plotting",
            "stop_service",
            "is_running",
            "register_service",
        ]
        if len(data) == 0 and command in commands_with_data:
            response = {"success": False, "error": f'{command} requires "data"'}
        # Keychain commands should be handled by KeychainServer
        elif command in keychain_commands:
            response = await self.keychain_server.handle_command(command, data)
        elif command == "ping":
            response = await ping()
        elif command == "start_service":
            response = await self.start_service(data)
        elif command == "start_plotting":
            response = await self.start_plotting(data)
        elif command == "stop_plotting":
            response = await self.stop_plotting(data)
        elif command == "stop_service":
            response = await self.stop_service(data)
        elif command == "running_services":
            response = await self.running_services(data)
        elif command == "is_running":
            response = await self.is_running(data)
        elif command == "is_keyring_locked":
            response = await self.is_keyring_locked()
        elif command == "keyring_status":
            response = await self.keyring_status()
        elif command == "unlock_keyring":
            response = await self.unlock_keyring(data)
        elif command == "validate_keyring_passphrase":
            response = await self.validate_keyring_passphrase(data)
        elif command == "set_keyring_passphrase":
            response = await self.set_keyring_passphrase(data)
        elif command == "remove_keyring_passphrase":
            response = await self.remove_keyring_passphrase(data)
        elif command == "exit":
            response = await self.stop()
        elif command == "register_service":
            response = await self.register_service(websocket, data)
        elif command == "get_status":
            response = self.get_status()
        elif command == "get_version":
            response = self.get_version()
        elif command == "get_plotters":
            response = await self.get_plotters()
        else:
            self.log.error(f"UK>> {message}")
            response = {"success": False, "error": f"unknown_command {command}"}

        full_response = format_response(message, response)
        return full_response, {websocket}

    async def is_keyring_locked(self) -> Dict[str, Any]:
        locked: bool = Keychain.is_keyring_locked()
        response: Dict[str, Any] = {"success": True, "is_keyring_locked": locked}
        return response

    async def keyring_status(self) -> Dict[str, Any]:
        can_save_passphrase: bool = supports_os_passphrase_storage()
        user_passphrase_is_set: bool = Keychain.has_master_passphrase() and not using_default_passphrase()
        locked: bool = Keychain.is_keyring_locked()
        can_set_passphrase_hint: bool = True
        passphrase_hint: str = Keychain.get_master_passphrase_hint() or ""
        requirements: Dict[str, Any] = passphrase_requirements()
        response: Dict[str, Any] = {
            "success": True,
            "is_keyring_locked": locked,
            "can_save_passphrase": can_save_passphrase,
            "user_passphrase_is_set": user_passphrase_is_set,
            "can_set_passphrase_hint": can_set_passphrase_hint,
            "passphrase_hint": passphrase_hint,
            "passphrase_requirements": requirements,
        }
        # Help diagnose GUI launch issues
        self.log.debug(f"Keyring status: {response}")
        return response

    async def unlock_keyring(self, request: Dict[str, Any]) -> Dict[str, Any]:
        success: bool = False
        error: Optional[str] = None
        key: Optional[str] = request.get("key", None)
        if type(key) is not str:
            return {"success": False, "error": "missing key"}

        try:
            if Keychain.master_passphrase_is_valid(key, force_reload=True):
                Keychain.set_cached_master_passphrase(key)
                success = True
                # Inform the GUI of keyring status changes
                self.keyring_status_changed(await self.keyring_status(), "ui")
            else:
                error = "bad passphrase"
        except Exception as e:
            tb = traceback.format_exc()
            self.log.error(f"Keyring passphrase validation failed: {e} {tb}")
            error = "validation exception"

        if success and self.run_check_keys_on_unlock:
            try:
                self.log.info("Running check_keys now that the keyring is unlocked")
                check_keys(self.root_path)
                self.run_check_keys_on_unlock = False
            except Exception as e:
                tb = traceback.format_exc()
                self.log.error(f"check_keys failed after unlocking keyring: {e} {tb}")

        response: Dict[str, Any] = {"success": success, "error": error}
        return response

    async def validate_keyring_passphrase(self, request: Dict[str, Any]) -> Dict[str, Any]:
        success: bool = False
        error: Optional[str] = None
        key: Optional[str] = request.get("key", None)
        if type(key) is not str:
            return {"success": False, "error": "missing key"}

        try:
            success = Keychain.master_passphrase_is_valid(key, force_reload=True)
        except Exception as e:
            tb = traceback.format_exc()
            self.log.error(f"Keyring passphrase validation failed: {e} {tb}")
            error = "validation exception"

        response: Dict[str, Any] = {"success": success, "error": error}
        return response

    async def set_keyring_passphrase(self, request: Dict[str, Any]) -> Dict[str, Any]:
        success: bool = False
        error: Optional[str] = None
        current_passphrase: Optional[str] = None
        new_passphrase: Optional[str] = None
        passphrase_hint: Optional[str] = request.get("passphrase_hint", None)
        save_passphrase: bool = request.get("save_passphrase", False)

        if using_default_passphrase():
            current_passphrase = default_passphrase()

        if Keychain.has_master_passphrase() and not current_passphrase:
            current_passphrase = request.get("current_passphrase", None)
            if type(current_passphrase) is not str:
                return {"success": False, "error": "missing current_passphrase"}

        new_passphrase = request.get("new_passphrase", None)
        if type(new_passphrase) is not str:
            return {"success": False, "error": "missing new_passphrase"}

        if not Keychain.passphrase_meets_requirements(new_passphrase):
            return {"success": False, "error": "passphrase doesn't satisfy requirements"}

        try:
            assert new_passphrase is not None  # mypy, I love you
            Keychain.set_master_passphrase(
                current_passphrase,
                new_passphrase,
                passphrase_hint=passphrase_hint,
                save_passphrase=save_passphrase,
            )
        except KeychainCurrentPassphraseIsInvalid:
            error = "current passphrase is invalid"
        except Exception as e:
            tb = traceback.format_exc()
            self.log.error(f"Failed to set keyring passphrase: {e} {tb}")
        else:
            success = True
            # Inform the GUI of keyring status changes
            self.keyring_status_changed(await self.keyring_status(), "ui")

        response: Dict[str, Any] = {"success": success, "error": error}
        return response

    async def remove_keyring_passphrase(self, request: Dict[str, Any]) -> Dict[str, Any]:
        success: bool = False
        error: Optional[str] = None
        current_passphrase: Optional[str] = None

        if not Keychain.has_master_passphrase():
            return {"success": False, "error": "passphrase not set"}

        current_passphrase = request.get("current_passphrase", None)
        if type(current_passphrase) is not str:
            return {"success": False, "error": "missing current_passphrase"}

        try:
            Keychain.remove_master_passphrase(current_passphrase)
        except KeychainCurrentPassphraseIsInvalid:
            error = "current passphrase is invalid"
        except Exception as e:
            tb = traceback.format_exc()
            self.log.error(f"Failed to remove keyring passphrase: {e} {tb}")
        else:
            success = True
            # Inform the GUI of keyring status changes
            self.keyring_status_changed(await self.keyring_status(), "ui")

        response: Dict[str, Any] = {"success": success, "error": error}
        return response

    def get_status(self) -> Dict[str, Any]:
        response = {"success": True, "genesis_initialized": True}
        return response

    def get_version(self) -> Dict[str, Any]:
        response = {"success": True, "version": __version__}
        return response

    async def get_plotters(self) -> Dict[str, Any]:
        plotters: Dict[str, Any] = get_available_plotters(self.root_path)
        response: Dict[str, Any] = {"success": True, "plotters": plotters}
        return response

    async def _keyring_status_changed(self, keyring_status: Dict[str, Any], destination: str):
        """
        Attempt to communicate with the GUI to inform it of any keyring status changes
        (e.g. keyring becomes unlocked)
        """
        websockets = self.connections.get("ui", None)

        if websockets is None:
            return None

        if keyring_status is None:
            return None

        response = create_payload("keyring_status_changed", keyring_status, "daemon", destination)

        for websocket in websockets.copy():
            try:
                await websocket.send_str(response)
            except Exception as e:
                tb = traceback.format_exc()
                self.log.error(f"Unexpected exception trying to send to websocket: {e} {tb}")
                websockets.remove(websocket)
                await websocket.close()

    def keyring_status_changed(self, keyring_status: Dict[str, Any], destination: str):
        asyncio.create_task(self._keyring_status_changed(keyring_status, destination))

    def plot_queue_to_payload(self, plot_queue_item, send_full_log: bool) -> Dict[str, Any]:
        error = plot_queue_item.get("error")
        has_error = error is not None

        item = {
            "id": plot_queue_item["id"],
            "queue": plot_queue_item["queue"],
            "size": plot_queue_item["size"],
            "parallel": plot_queue_item["parallel"],
            "delay": plot_queue_item["delay"],
            "state": plot_queue_item["state"],
            "error": str(error) if has_error else None,
            "deleted": plot_queue_item["deleted"],
            "log_new": plot_queue_item.get("log_new"),
        }

        if send_full_log:
            item["log"] = plot_queue_item.get("log")
        return item

    def prepare_plot_state_message(self, state: PlotEvent, id):
        message = {
            "state": state,
            "queue": self.extract_plot_queue(id),
        }
        return message

    def extract_plot_queue(self, id=None) -> List[Dict]:
        send_full_log = id is None
        data = []
        for item in self.plots_queue:
            if id is None or item["id"] == id:
                data.append(self.plot_queue_to_payload(item, send_full_log))
        return data

    async def _state_changed(self, service: str, message: Dict[str, Any]):
        """If id is None, send the whole state queue"""
        if service not in self.connections:
            return None

        websockets = self.connections[service]

        if message is None:
            return None

        response = create_payload("state_changed", message, service, "ui")

        for websocket in websockets.copy():
            try:
                await websocket.send_str(response)
            except Exception as e:
                tb = traceback.format_exc()
                self.log.error(f"Unexpected exception trying to send to websocket: {e} {tb}")
                websockets.remove(websocket)
                await websocket.close()

    def state_changed(self, service: str, message: Dict[str, Any]):
        asyncio.create_task(self._state_changed(service, message))

    async def _watch_file_changes(self, config, fp: TextIO, loop: asyncio.AbstractEventLoop):
        id: str = config["id"]
        plotter: str = config["plotter"]
        final_words: List[str] = []

        if plotter == "chiapos":
            final_words = ["Renamed final file"]
        elif plotter == "bladebit":
            if "cudaplot" in config["command_args"]:
                final_words = ["Completed writing plot"]
            else:
                final_words = ["Finished plotting in"]
        elif plotter == "madmax":
            temp_dir = config["temp_dir"]
            final_dir = config["final_dir"]
            if temp_dir == final_dir:
                final_words = ["Total plot creation time was"]
            else:
                # "Renamed final plot" if moving to a final dir on the same volume
                # "Copy to <path> finished, took..." if copying to another volume
                final_words = ["Renamed final plot", "finished, took"]

        while True:
            new_data = await loop.run_in_executor(io_pool_exc, fp.readline)

            if config["state"] is not PlotState.RUNNING:
                return None

            if new_data not in (None, ""):
                config["log"] = new_data if config["log"] is None else config["log"] + new_data
                config["log_new"] = new_data
                self.state_changed(service_plotter, self.prepare_plot_state_message(PlotEvent.LOG_CHANGED, id))

            if new_data:
                for word in final_words:
                    if word in new_data:
                        return None
            else:
                time.sleep(0.5)

    async def _track_plotting_progress(self, config, loop: asyncio.AbstractEventLoop):
        file_path = config["out_file"]
        with open(file_path, "r") as fp:
            await self._watch_file_changes(config, fp, loop)

    def _common_plotting_command_args(self, request: Any, ignoreCount: bool) -> List[str]:
        n = 1 if ignoreCount else request["n"]  # Plot count
        d = request["d"]  # Final directory
        r = request["r"]  # Threads
        f = request.get("f")  # Farmer pubkey
        p = request.get("p")  # Pool pubkey

        command_args: List[str] = ["-n", str(n), "-d", d, "-r", str(r)]

        if f is not None:
            command_args.append("-f")
            command_args.append(str(f))
        if p is not None:
            command_args.append("-p")
            command_args.append(str(p))

        return command_args

    def _chiapos_plotting_command_args(self, request: Any, ignoreCount: bool) -> List[str]:
        k = request["k"]  # Plot size
        t = request["t"]  # Temp directory
        t2 = request.get("t2")  # Temp2 directory
        b = request["b"]  # Buffer size
        u = request["u"]  # Buckets
        a = request.get("a")  # Fingerprint
        e = request["e"]  # Disable bitfield
        x = request["x"]  # Exclude final directory
        override_k = request["overrideK"]  # Force plot sizes < k32

        command_args: List[str] = ["-k", str(k), "-t", t, "-b", str(b), "-u", str(u)]

        if t2 is not None:
            command_args.append("-2")
            command_args.append(str(t2))
        if a is not None:
            command_args.append("-a")
            command_args.append(str(a))
        if e is True:
            command_args.append("-e")
        if x is True:
            command_args.append("-x")
        if override_k is True:
            command_args.append("--override-k")

        return command_args

    def _bladebit_plotting_command_args(self, request: Any, ignoreCount: bool) -> List[str]:
        plot_type = request["plot_type"]
        if plot_type not in ["ramplot", "diskplot", "cudaplot"]:
            raise ValueError(f"Unknown plot_type: {plot_type}")

        command_args: List[str] = []

        # Common options among diskplot, ramplot, cudaplot
        w = request.get("w", False)  # Warm start
        m = request.get("m", False)  # Disable NUMA
        no_cpu_affinity = request.get("no_cpu_affinity", False)
        compress = request.get("compress", None)  # Compression level

        if w is True:
            command_args.append("--warmstart")
        if m is True:
            command_args.append("--nonuma")
        if no_cpu_affinity is True:
            command_args.append("--no-cpu-affinity")
        if compress is not None and str(compress).isdigit():
            command_args.append("--compress")
            command_args.append(str(compress))
        
        # ramplot don't accept any more options
        if plot_type == "ramplot":
            return command_args

        # Options only applicable for cudaplot
        if plot_type == "cudaplot":
            device_index = request.get("device", None)
            t1 = request.get("t", None)  # Temp directory
            t2 = request.get("t2", None)  # Temp2 directory
            disk_128 = request.get("disk_128", False)
            disk_16 = request.get("disk_16", False)

            if device_index is not None and str(device_index).isdigit():
                command_args.append("--device")
                command_args.append(str(device_index))
            if t1 is not None:
                command_args.append("-t")
                command_args.append(t1)
            if t2 is not None:
                command_args.append("-2")
                command_args.append(t2)
            if disk_128:
                command_args.append("--disk-128")
            if disk_16:
                command_args.append("--disk-16")
            return command_args

        # if plot_type == "diskplot"
        # memo = request["memo"]
        t1 = request["t"]  # Temp directory
        t2 = request.get("t2")  # Temp2 directory
        u = request.get("u")  # Buckets
        cache = request.get("cache")
        f1_threads = request.get("f1_threads")
        fp_threads = request.get("fp_threads")
        c_threads = request.get("c_threads")
        p2_threads = request.get("p2_threads")
        p3_threads = request.get("p3_threads")
        alternate = request.get("alternate", False)
        no_t1_direct = request.get("no_t1_direct", False)
        no_t2_direct = request.get("no_t2_direct", False)

        command_args.append("-t")
        command_args.append(t1)
        if t2 is not None:
            command_args.append("-2")
            command_args.append(t2)
        if u is not None:
            command_args.append("-u")
            command_args.append(str(u))
        if cache is not None:
            command_args.append("--cache")
            command_args.append(str(cache))
        if f1_threads is not None:
            command_args.append("--f1-threads")
            command_args.append(str(f1_threads))
        if fp_threads is not None:
            command_args.append("--fp-threads")
            command_args.append(str(fp_threads))
        if c_threads is not None:
            command_args.append("--c-threads")
            command_args.append(str(c_threads))
        if p2_threads is not None:
            command_args.append("--p2-threads")
            command_args.append(str(p2_threads))
        if p3_threads is not None:
            command_args.append("--p3-threads")
            command_args.append(str(p3_threads))
        if alternate is not None:
            command_args.append("--alternate")
        if no_t1_direct is not None:
            command_args.append("--no-t1-direct")
        if no_t2_direct is not None:
            command_args.append("--no-t2-direct")

        return command_args

    def _madmax_plotting_command_args(self, request: Any, ignoreCount: bool, index: int) -> List[str]:
        k = request["k"]  # Plot size
        t = request["t"]  # Temp directory
        t2 = request["t2"]  # Temp2 directory
        u = request["u"]  # Buckets
        v = request["v"]  # Buckets for phase 3 & 4
        K = request.get("K", 1)  # Thread multiplier for phase 2
        G = request.get("G", False)  # Alternate tmpdir/tmp2dir

        command_args: List[str] = []
        command_args.append(f"-k{k}")
        command_args.append(f"-u{u}")
        command_args.append(f"-v{v}")
        command_args.append(f"-K{K}")

        # Handle madmax's tmptoggle option ourselves when managing GUI plotting
        if G is True and t != t2 and index % 2:
            # Swap tmp and tmp2
            command_args.append(f"-t{t2}")
            command_args.append(f"-2{t}")
        else:
            command_args.append(f"-t{t}")
            command_args.append(f"-2{t2}")

        return command_args

    def _build_plotting_command_args(self, request: Any, ignoreCount: bool, index: int) -> List[str]:
        plotter: str = request.get("plotter", "chiapos")
        command_args: List[str] = ["corpochain", "plotters", plotter]

        if plotter == "bladebit":
            # plotter command must be either
            # 'corpochain plotters bladebit ramplot' or 'corpochain plotters bladebit diskplot'
            plot_type = request["plot_type"]
            assert plot_type == "diskplot" or plot_type == "ramplot" or plot_type == "cudaplot"
            command_args.append(plot_type)

        command_args.extend(self._common_plotting_command_args(request, ignoreCount))

        if plotter == "chiapos":
            command_args.extend(self._chiapos_plotting_command_args(request, ignoreCount))
        elif plotter == "madmax":
            command_args.extend(self._madmax_plotting_command_args(request, ignoreCount, index))
        elif plotter == "bladebit":
            command_args.extend(self._bladebit_plotting_command_args(request, ignoreCount))

        return command_args

    def _is_serial_plotting_running(self, queue: str = "default") -> bool:
        response = False
        for item in self.plots_queue:
            if item["queue"] == queue and item["parallel"] is False and item["state"] is PlotState.RUNNING:
                response = True
        return response

    def _get_plots_queue_item(self, id: str):
        config = next(item for item in self.plots_queue if item["id"] == id)
        return config

    def _run_next_serial_plotting(self, loop: asyncio.AbstractEventLoop, queue: str = "default"):
        next_plot_id = None

        if self._is_serial_plotting_running(queue) is True:
            return None

        for item in self.plots_queue:
            if item["queue"] == queue and item["state"] is PlotState.SUBMITTED and item["parallel"] is False:
                next_plot_id = item["id"]
                break

        if next_plot_id is not None:
            loop.create_task(self._start_plotting(next_plot_id, loop, queue))

    def _post_process_plotting_job(self, job: Dict[str, Any]):
        id: str = job["id"]
        final_dir: str = job["final_dir"]
        exclude_final_dir: bool = job["exclude_final_dir"]
        log.info(f"Post-processing plotter job with ID {id}")  # lgtm [py/clear-text-logging-sensitive-data]
        if not exclude_final_dir:
            try:
                add_plot_directory(self.root_path, final_dir)
            except ValueError as e:
                log.warning(f"_post_process_plotting_job: {e}")

    async def _start_plotting(self, id: str, loop: asyncio.AbstractEventLoop, queue: str = "default"):
        current_process = None
        try:
            log.info(f"Starting plotting with ID {id}")  # lgtm [py/clear-text-logging-sensitive-data]
            config = self._get_plots_queue_item(id)

            if config is None:
                raise Exception(f"Plot queue config with ID {id} does not exist")

            state = config["state"]
            if state is not PlotState.SUBMITTED:
                raise Exception(f"Plot with ID {id} has no state submitted")

            assert id == config["id"]
            delay = config["delay"]
            await asyncio.sleep(delay)

            if config["state"] is not PlotState.SUBMITTED:
                return None

            service_name = config["service_name"]
            command_args = config["command_args"]

            # Set the -D/--connect_to_daemon flag to signify that the child should connect
            # to the daemon to access the keychain
            command_args.append("-D")

            self.log.debug(f"command_args before launch_plotter are {command_args}")
            self.log.debug(f"self.root_path before launch_plotter is {self.root_path}")
            process, pid_path = launch_plotter(self.root_path, service_name, command_args, id)

            current_process = process

            config["state"] = PlotState.RUNNING
            config["out_file"] = plotter_log_path(self.root_path, id).absolute()
            config["process"] = process
            self.state_changed(service_plotter, self.prepare_plot_state_message(PlotEvent.STATE_CHANGED, id))

            if service_name not in self.services:
                self.services[service_name] = []

            self.services[service_name].append(process)

            await self._track_plotting_progress(config, loop)

            self.log.debug("finished tracking plotting progress. setting state to FINISHED")

            config["state"] = PlotState.FINISHED
            self.state_changed(service_plotter, self.prepare_plot_state_message(PlotEvent.STATE_CHANGED, id))

            self._post_process_plotting_job(config)

        except (subprocess.SubprocessError, IOError):
            log.exception(f"problem starting {service_name}")  # lgtm [py/clear-text-logging-sensitive-data]
            error = Exception("Start plotting failed")
            config["state"] = PlotState.FINISHED
            config["error"] = error
            self.state_changed(service_plotter, self.prepare_plot_state_message(PlotEvent.STATE_CHANGED, id))
            raise error

        finally:
            if current_process is not None:
                self.services[service_name].remove(current_process)
                current_process.wait()  # prevent zombies
            self._run_next_serial_plotting(loop, queue)

    async def start_plotting(self, request: Dict[str, Any]):
        service_name = request["service"]

        plotter = request.get("plotter", "chiapos")
        delay = int(request.get("delay", 0))
        parallel = request.get("parallel", False)
        size = request.get("k")
        temp_dir = request.get("t")
        final_dir = request.get("d")
        exclude_final_dir = request.get("x", False)
        count = int(request.get("n", 1))
        queue = request.get("queue", "default")

        ids: List[str] = []
        for k in range(count):
            id = str(uuid.uuid4())
            ids.append(id)
            config = {
                "id": id,  # lgtm [py/clear-text-logging-sensitive-data]
                "size": size,
                "queue": queue,
                "plotter": plotter,
                "service_name": service_name,
                "command_args": self._build_plotting_command_args(request, True, k),
                "parallel": parallel,
                "delay": delay * k if parallel is True else delay,
                "state": PlotState.SUBMITTED,
                "deleted": False,
                "error": None,
                "log": None,
                "process": None,
                "temp_dir": temp_dir,
                "final_dir": final_dir,
                "exclude_final_dir": exclude_final_dir,
            }

            self.plots_queue.append(config)

            # notify GUI about new plot queue item
            self.state_changed(service_plotter, self.prepare_plot_state_message(PlotEvent.STATE_CHANGED, id))

            # only the first item can start when user selected serial plotting
            can_start_serial_plotting = k == 0 and self._is_serial_plotting_running(queue) is False

            if parallel is True or can_start_serial_plotting:
                log.info(f"Plotting will start in {config['delay']} seconds")
                # TODO: loop gets passed down a lot, review for potential removal
                loop = asyncio.get_running_loop()
                loop.create_task(self._start_plotting(id, loop, queue))
            else:
                log.info("Plotting will start automatically when previous plotting finish")

        response = {
            "success": True,
            "ids": ids,
            "service_name": service_name,
        }

        return response

    async def stop_plotting(self, request: Dict[str, Any]) -> Dict[str, Any]:
        id = request["id"]
        config = self._get_plots_queue_item(id)
        if config is None:
            return {"success": False}

        id = config["id"]
        state = config["state"]
        process = config["process"]
        queue = config["queue"]

        if config["state"] is PlotState.REMOVING:
            return {"success": False}

        try:
            run_next = False
            if process is not None and state == PlotState.RUNNING:
                run_next = True
                config["state"] = PlotState.REMOVING
                self.state_changed(service_plotter, self.prepare_plot_state_message(PlotEvent.STATE_CHANGED, id))
                await kill_processes([process], self.root_path, service_plotter, id)

            config["state"] = PlotState.FINISHED
            config["deleted"] = True

            self.state_changed(service_plotter, self.prepare_plot_state_message(PlotEvent.STATE_CHANGED, id))

            self.plots_queue.remove(config)

            if run_next:
                # TODO: review to see if we can remove this
                loop = asyncio.get_running_loop()
                self._run_next_serial_plotting(loop, queue)

            return {"success": True}
        except Exception as e:
            log.error(f"Error during killing the plot process: {e}")
            config["state"] = PlotState.FINISHED
            config["error"] = str(e)
            self.state_changed(service_plotter, self.prepare_plot_state_message(PlotEvent.STATE_CHANGED, id))
            return {"success": False}

    async def start_service(self, request: Dict[str, Any]):
        service_command = request["service"]

        error = None
        success = False
        testing = False
        already_running = False
        if "testing" in request:
            testing = request["testing"]

        if not validate_service(service_command):
            error = "unknown service"

        if service_command in self.services:
            processes = self.services[service_command]
            if all(process.poll() is not None for process in processes):
                self.services.pop(service_command)
                error = None
            else:
                self.log.info(f"Service {service_command} already running")
                already_running = True
        elif len(self.connections.get(service_command, [])) > 0:
            # If the service was started manually (not launched by the daemon), we should
            # have a connection to it.
            self.log.info(f"Service {service_command} already registered")
            already_running = True

        if already_running:
            success = True
        elif error is None:
            try:
                exe_command = service_command
                if testing is True:
                    exe_command = f"{service_command} --testing=true"
                process, pid_path = launch_service(self.root_path, exe_command)
                self.services[service_command] = [process]
                success = True
            except (subprocess.SubprocessError, IOError):
                log.exception(f"problem starting {service_command}")
                error = "start failed"

        response = {"success": success, "service": service_command, "error": error}
        return response

    async def stop_service(self, request: Dict[str, Any]) -> Dict[str, Any]:
        service_name = request["service"]
        result = await kill_service(self.root_path, self.services, service_name)
        response = {"success": result, "service_name": service_name}
        return response

    def is_service_running(self, service_name: str) -> bool:
        processes: List[subprocess.Popen]
        if service_name == service_plotter:
            processes = self.services.get(service_name, [])
            is_running = len(processes) > 0
        else:
            processes = self.services.get(service_name, [])
            is_running = any(process.poll() is None for process in processes)
            if not is_running:
                # Check if we have a connection to the requested service. This might be the
                # case if the service was started manually (i.e. not started by the daemon).
                service_connections = self.connections.get(service_name)
                if service_connections is not None:
                    is_running = len(service_connections) > 0
        return is_running

    async def running_services(self, request: Dict[str, Any]) -> Dict[str, Any]:
        services = list({*self.services.keys(), *self.connections.keys()})
        running_services = [service_name for service_name in services if self.is_service_running(service_name)]

        return {"success": True, "running_services": running_services}

    async def is_running(self, request: Dict[str, Any]) -> Dict[str, Any]:
        service_name = request["service"]
        is_running = self.is_service_running(service_name)
        return {"success": True, "service_name": service_name, "is_running": is_running}

    async def exit(self) -> None:
        if self.webserver is not None:
            self.webserver.close()
            await self.webserver.await_closed()
        log.info("corpochain daemon exiting")

    async def register_service(self, websocket: WebSocketResponse, request: Dict[str, Any]) -> Dict[str, Any]:
        self.log.info(f"Register service {request}")
        service = request.get("service")
        if service is None:
            self.log.error("Service Name missing from request to 'register_service'")
            return {"success": False}
        if service not in self.connections:
            self.connections[service] = set()
        self.connections[service].add(websocket)

        response: Dict[str, Any] = {"success": True}
        if service == service_plotter:
            response = {
                "success": True,
                "service": service,
                "queue": self.extract_plot_queue(),
            }
        else:
            if self.ping_job is None:
                self.ping_job = asyncio.create_task(self.ping_task())
        self.log.info(f"registered for service {service}")
        log.info(f"{response}")
        return response


def daemon_launch_lock_path(root_path: Path) -> Path:
    """
    A path to a file that is lock when a daemon is launching but not yet started.
    This prevents multiple instances from launching.
    """
    return service_launch_lock_path(root_path, "daemon")


def service_launch_lock_path(root_path: Path, service: str) -> Path:
    """
    A path that is locked when a service is running.
    """
    service_name = service.replace(" ", "-").replace("/", "-")
    return root_path / "run" / service_name


def pid_path_for_service(root_path: Path, service: str, id: str = "") -> Path:
    """
    Generate a path for a PID file for the given service name.
    """
    pid_name = service.replace(" ", "-").replace("/", "-")
    return root_path / "run" / f"{pid_name}{id}.pid"


def plotter_log_path(root_path: Path, id: str):
    return root_path / "plotter" / f"plotter_log_{id}.txt"


def launch_plotter(root_path: Path, service_name: str, service_array: List[str], id: str):
    # we need to pass on the possibly altered CORPOCHAIN_ROOT
    os.environ["CORPOCHAIN_ROOT"] = str(root_path)
    service_executable = executable_for_service(service_array[0])

    # Swap service name with name of executable
    service_array[0] = service_executable
    startupinfo = None
    creationflags = 0
    if sys.platform == "win32" or sys.platform == "cygwin":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        # If the current process group is used, CTRL_C_EVENT will kill the parent and everyone in the group!
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

    plotter_path = plotter_log_path(root_path, id)

    if plotter_path.parent.exists():
        if plotter_path.exists():
            plotter_path.unlink()
    else:
        plotter_path.parent.mkdir(parents=True, exist_ok=True)
    outfile = open(plotter_path.resolve(), "w")
    log.info(f"Service array: {service_array}")  # lgtm [py/clear-text-logging-sensitive-data]
    process = subprocess.Popen(
        service_array,
        shell=False,
        stderr=outfile,
        stdout=outfile,
        startupinfo=startupinfo,
        creationflags=creationflags,
    )

    pid_path = pid_path_for_service(root_path, service_name, id)
    try:
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        with open(pid_path, "w") as f:
            f.write(f"{process.pid}\n")
    except Exception:
        pass
    return process, pid_path


def launch_service(root_path: Path, service_command) -> Tuple[subprocess.Popen, Path]:
    """
    Launch a child process.
    """
    # set up CORPOCHAIN_ROOT
    # invoke correct script
    # save away PID

    # we need to pass on the possibly altered CORPOCHAIN_ROOT
    os.environ["CORPOCHAIN_ROOT"] = str(root_path)

    # Insert proper e
    service_array = service_command.split()
    service_executable = executable_for_service(service_array[0])
    service_array[0] = service_executable

    startupinfo = None
    if sys.platform == "win32" or sys.platform == "cygwin":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    log.debug(f"Launching service {service_array} with CORPOCHAIN_ROOT: {os.environ['CORPOCHAIN_ROOT']}")

    # CREATE_NEW_PROCESS_GROUP allows graceful shutdown on windows, by CTRL_BREAK_EVENT signal
    if sys.platform == "win32" or sys.platform == "cygwin":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        creationflags = 0
    environ_copy = os.environ.copy()
    process = subprocess.Popen(
        service_array, shell=False, startupinfo=startupinfo, creationflags=creationflags, env=environ_copy
    )

    pid_path = pid_path_for_service(root_path, service_command)
    try:
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        with open(pid_path, "w") as f:
            f.write(f"{process.pid}\n")
    except Exception:
        pass
    return process, pid_path


async def kill_processes(
    processes: List[subprocess.Popen],
    root_path: Path,
    service_name: str,
    id: str,
    delay_before_kill: int = 15,
) -> bool:
    pid_path = pid_path_for_service(root_path, service_name, id)

    if sys.platform == "win32" or sys.platform == "cygwin":
        log.info("sending CTRL_BREAK_EVENT signal to %s", service_name)

        for process in processes:
            kill(process.pid, signal.SIGBREAK)
    else:
        log.info("sending term signal to %s", service_name)
        for process in processes:
            process.terminate()

    count: float = 0
    while count < delay_before_kill:
        if all(process.poll() is not None for process in processes):
            break
        await asyncio.sleep(0.5)
        count += 0.5
    else:
        for process in processes:
            process.kill()
        log.info("sending kill signal to %s", service_name)
    for process in processes:
        r = process.wait()
        log.info("process %s returned %d", service_name, r)

    try:
        pid_path_killed = pid_path.with_suffix(".pid-killed")
        if pid_path_killed.exists():
            pid_path_killed.unlink()
        os.rename(pid_path, pid_path_killed)
    except Exception:
        pass

    return True


async def kill_service(
    root_path: Path, services: Dict[str, List[subprocess.Popen]], service_name: str, delay_before_kill: int = 15
) -> bool:
    processes = services.get(service_name)
    if processes is None:
        return False
    del services[service_name]
    result = await kill_processes(processes, root_path, service_name, "", delay_before_kill)
    return result


def is_running(services: Dict[str, subprocess.Popen], service_name: str) -> bool:
    process = services.get(service_name)
    return process is not None and process.poll() is None


async def async_run_daemon(root_path: Path, wait_for_unlock: bool = False) -> int:
    # When wait_for_unlock is true, we want to skip the check_keys() call in corpochain_init
    # since it might be necessary to wait for the GUI to unlock the keyring first.
    corpochain_init(root_path, should_check_keys=(not wait_for_unlock))
    config = load_config(root_path, "config.yaml")
    setproctitle("corpochain_daemon")
    initialize_service_logging("daemon", config)
    crt_path = root_path / config["daemon_ssl"]["private_crt"]
    key_path = root_path / config["daemon_ssl"]["private_key"]
    ca_crt_path = root_path / config["private_ssl_ca"]["crt"]
    ca_key_path = root_path / config["private_ssl_ca"]["key"]
    sys.stdout.flush()
    json_msg = dict_to_json_str(
        {
            "message": "cert_path",
            "success": True,
            "cert": f"{crt_path}",
            "key": f"{key_path}",
            "ca_crt": f"{ca_crt_path}",
        }
    )
    sys.stdout.write("\n" + json_msg + "\n")
    sys.stdout.flush()
    try:
        with Lockfile.create(daemon_launch_lock_path(root_path), timeout=1):
            log.info(f"corpochain-beacon-client version: {corpochain_full_version_str()}")

            beta_metrics: Optional[BetaMetricsLogger] = None
            if config.get("beta", {}).get("enabled", False):
                beta_metrics = BetaMetricsLogger(root_path)
                beta_metrics.start_logging()

            ws_server = WebSocketServer(
                root_path,
                ca_crt_path,
                ca_key_path,
                crt_path,
                key_path,
                run_check_keys_on_unlock=wait_for_unlock,
            )
            await ws_server.setup_process_global_state()
            async with ws_server.run():
                await ws_server.shutdown_event.wait()

            if beta_metrics is not None:
                await beta_metrics.stop_logging()

            log.info("Daemon WebSocketServer closed")
            sys.stdout.close()
            return 0
    except LockfileError:
        print("daemon: already launching")
        return 2


def run_daemon(root_path: Path, wait_for_unlock: bool = False) -> int:
    result = asyncio.run(async_run_daemon(root_path, wait_for_unlock))
    return result


def main() -> int:
    from corpochain.util.default_root import DEFAULT_ROOT_PATH
    from corpochain.util.keychain import Keychain

    wait_for_unlock = "--wait-for-unlock" in sys.argv[1:] and Keychain.is_keyring_locked()
    return run_daemon(DEFAULT_ROOT_PATH, wait_for_unlock)


if __name__ == "__main__":
    main()
