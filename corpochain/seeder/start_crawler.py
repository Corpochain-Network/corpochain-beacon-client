from __future__ import annotations

import logging
import pathlib
import sys
from multiprocessing import freeze_support
from typing import Dict, Optional

from corpochain.consensus.constants import ConsensusConstants
from corpochain.consensus.default_constants import DEFAULT_CONSTANTS
from corpochain.rpc.crawler_rpc_api import CrawlerRpcApi
from corpochain.seeder.crawler import Crawler
from corpochain.seeder.crawler_api import CrawlerAPI
from corpochain.server.outbound_message import NodeType
from corpochain.server.start_service import RpcInfo, Service, async_run
from corpochain.util.corpochain_logging import initialize_service_logging
from corpochain.util.config import load_config, load_config_cli
from corpochain.util.default_root import DEFAULT_ROOT_PATH

# See: https://bugs.python.org/issue29288
"".encode("idna")

SERVICE_NAME = "seeder"
log = logging.getLogger(__name__)


def create_beacon_crawler_service(
    root_path: pathlib.Path,
    config: Dict,
    consensus_constants: ConsensusConstants,
    connect_to_daemon: bool = True,
) -> Service[Crawler]:
    service_config = config[SERVICE_NAME]

    crawler = Crawler(
        service_config,
        root_path=root_path,
        consensus_constants=consensus_constants,
    )
    api = CrawlerAPI(crawler)

    network_id = service_config["selected_network"]

    rpc_info: Optional[RpcInfo] = None
    if service_config.get("start_rpc_server", True):
        rpc_info = (CrawlerRpcApi, service_config.get("rpc_port", 9206))

    return Service(
        root_path=root_path,
        config=config,
        node=api.crawler,
        peer_api=api,
        node_type=NodeType.BEACON,
        advertised_port=service_config["port"],
        service_name="beacon",
        upnp_ports=[],
        server_listen_ports=[service_config["port"]],
        on_connect_callback=crawler.on_connect,
        network_id=network_id,
        rpc_info=rpc_info,
        connect_to_daemon=connect_to_daemon,
    )


async def async_main() -> int:
    # TODO: refactor to avoid the double load
    config = load_config(DEFAULT_ROOT_PATH, "config.yaml")
    service_config = load_config_cli(DEFAULT_ROOT_PATH, "config.yaml", SERVICE_NAME)
    config[SERVICE_NAME] = service_config
    overrides = service_config["network_overrides"]["constants"][service_config["selected_network"]]
    updated_constants = DEFAULT_CONSTANTS.replace_str_to_bytes(**overrides)
    initialize_service_logging(service_name=SERVICE_NAME, config=config)
    service = create_beacon_crawler_service(DEFAULT_ROOT_PATH, config, updated_constants)
    await service.setup_process_global_state()
    await service.run()

    return 0


def main() -> int:
    freeze_support()
    return async_run(async_main())


if __name__ == "__main__":
    sys.exit(main())
