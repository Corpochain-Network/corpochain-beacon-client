from __future__ import annotations

import pathlib
import sys
from typing import Any, Dict, Optional

from corpochain.consensus.constants import ConsensusConstants
from corpochain.consensus.default_constants import DEFAULT_CONSTANTS
from corpochain.farmer.farmer import Farmer
from corpochain.farmer.farmer_api import FarmerAPI
from corpochain.rpc.farmer_rpc_api import FarmerRpcApi
from corpochain.server.outbound_message import NodeType
from corpochain.server.start_service import RpcInfo, Service, async_run
from corpochain.types.peer_info import PeerInfo
from corpochain.util.corpochain_logging import initialize_service_logging
from corpochain.util.config import load_config, load_config_cli
from corpochain.util.default_root import DEFAULT_ROOT_PATH
from corpochain.util.keychain import Keychain
from corpochain.util.network import get_host_addr

# See: https://bugs.python.org/issue29288
"".encode("idna")

SERVICE_NAME = "farmer"


def create_farmer_service(
    root_path: pathlib.Path,
    config: Dict[str, Any],
    consensus_constants: ConsensusConstants,
    keychain: Optional[Keychain] = None,
    connect_to_daemon: bool = True,
) -> Service[Farmer]:
    service_config = config[SERVICE_NAME]

    connect_peers = []
    fnp = service_config.get("beacon_peer")
    if fnp is not None:
        connect_peers.append(
            PeerInfo(str(get_host_addr(fnp["host"], prefer_ipv6=config.get("prefer_ipv6", False))), fnp["port"])
        )

    overrides = service_config["network_overrides"]["constants"][service_config["selected_network"]]
    updated_constants = consensus_constants.replace_str_to_bytes(**overrides)

    farmer = Farmer(
        root_path, service_config, consensus_constants=updated_constants, local_keychain=keychain
    )
    peer_api = FarmerAPI(farmer)
    network_id = service_config["selected_network"]
    rpc_info: Optional[RpcInfo] = None
    if service_config["start_rpc_server"]:
        rpc_info = (FarmerRpcApi, service_config["rpc_port"])
    return Service(
        root_path=root_path,
        config=config,
        node=farmer,
        peer_api=peer_api,
        node_type=NodeType.FARMER,
        advertised_port=service_config["port"],
        service_name=SERVICE_NAME,
        server_listen_ports=[service_config["port"]],
        connect_peers=connect_peers,
        on_connect_callback=farmer.on_connect,
        network_id=network_id,
        rpc_info=rpc_info,
        connect_to_daemon=connect_to_daemon,
    )


async def async_main() -> int:
    # TODO: refactor to avoid the double load
    config = load_config(DEFAULT_ROOT_PATH, "config.yaml")
    service_config = load_config_cli(DEFAULT_ROOT_PATH, "config.yaml", SERVICE_NAME)
    config[SERVICE_NAME] = service_config
    initialize_service_logging(service_name=SERVICE_NAME, config=config)
    service = create_farmer_service(DEFAULT_ROOT_PATH, config, DEFAULT_CONSTANTS)
    await service.setup_process_global_state()
    await service.run()

    return 0


def main() -> int:
    return async_run(async_main())


if __name__ == "__main__":
    sys.exit(main())
