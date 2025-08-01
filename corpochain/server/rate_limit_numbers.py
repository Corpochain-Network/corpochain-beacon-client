# All of these rate limits scale with the number of transactions so the aggregate amounts are higher
from __future__ import annotations

import copy
import dataclasses
import functools
from typing import Any, Dict, List, Optional

from corpochain.protocols.protocol_message_types import ProtocolMessageTypes
from corpochain.protocols.shared_protocol import Capability


@dataclasses.dataclass(frozen=True)
class RLSettings:
    frequency: int  # Max request per time period (ie 1 min)
    max_size: int  # Max size of each request
    max_total_size: Optional[int] = None  # Max cumulative size of all requests in that period


def get_rate_limits_to_use(our_capabilities: List[Capability], peer_capabilities: List[Capability]) -> Dict[str, Any]:
    return rate_limits[1]


rate_limits = {
    1: {
        "default_settings": RLSettings(100, 1024 * 1024, 100 * 1024 * 1024),
        "non_tx_freq": 1000,  # There is also a freq limit for many requests
        "non_tx_max_total_size": 100 * 1024 * 1024,  # There is also a size limit for many requests
        "rate_limits_other": {
            ProtocolMessageTypes.handshake: RLSettings(5, 10 * 1024, 5 * 10 * 1024),
            ProtocolMessageTypes.harvester_handshake: RLSettings(5, 1024 * 1024),
            ProtocolMessageTypes.new_signage_point_harvester: RLSettings(100, 1024),
            ProtocolMessageTypes.new_proof_of_space: RLSettings(100, 2048),
            ProtocolMessageTypes.request_signatures: RLSettings(100, 2048),
            ProtocolMessageTypes.respond_signatures: RLSettings(100, 2048),
            ProtocolMessageTypes.new_signage_point: RLSettings(200, 2048),
            ProtocolMessageTypes.declare_proof_of_space: RLSettings(100, 10 * 1024),
            ProtocolMessageTypes.request_signed_values: RLSettings(100, 512),
            ProtocolMessageTypes.farming_info: RLSettings(100, 1024),
            ProtocolMessageTypes.signed_values: RLSettings(100, 1024),
            ProtocolMessageTypes.new_peak_timelord: RLSettings(100, 20 * 1024),
            ProtocolMessageTypes.new_unfinished_block_timelord: RLSettings(100, 10 * 1024),
            ProtocolMessageTypes.new_signage_point_vdf: RLSettings(100, 100 * 1024),
            ProtocolMessageTypes.new_infusion_point_vdf: RLSettings(100, 100 * 1024),
            ProtocolMessageTypes.new_end_of_sub_slot_vdf: RLSettings(100, 100 * 1024),
            ProtocolMessageTypes.request_compact_proof_of_time: RLSettings(100, 10 * 1024),
            ProtocolMessageTypes.respond_compact_proof_of_time: RLSettings(100, 100 * 1024),
            ProtocolMessageTypes.new_peak: RLSettings(200, 512),
            ProtocolMessageTypes.request_proof_of_weight: RLSettings(5, 100),
            ProtocolMessageTypes.respond_proof_of_weight: RLSettings(5, 50 * 1024 * 1024, 100 * 1024 * 1024),
            ProtocolMessageTypes.request_block: RLSettings(200, 100),
            ProtocolMessageTypes.reject_block: RLSettings(200, 100),
            ProtocolMessageTypes.request_blocks: RLSettings(500, 100),
            ProtocolMessageTypes.respond_blocks: RLSettings(100, 50 * 1024 * 1024, 5 * 50 * 1024 * 1024),
            ProtocolMessageTypes.reject_blocks: RLSettings(100, 100),
            ProtocolMessageTypes.respond_block: RLSettings(200, 2 * 1024 * 1024, 10 * 2 * 1024 * 1024),
            ProtocolMessageTypes.new_unfinished_block: RLSettings(200, 100),
            ProtocolMessageTypes.request_unfinished_block: RLSettings(200, 100),
            ProtocolMessageTypes.respond_unfinished_block: RLSettings(200, 2 * 1024 * 1024, 10 * 2 * 1024 * 1024),
            ProtocolMessageTypes.new_signage_point_or_end_of_sub_slot: RLSettings(200, 200),
            ProtocolMessageTypes.request_signage_point_or_end_of_sub_slot: RLSettings(200, 200),
            ProtocolMessageTypes.respond_signage_point: RLSettings(200, 50 * 1024),
            ProtocolMessageTypes.respond_end_of_sub_slot: RLSettings(100, 50 * 1024),
            ProtocolMessageTypes.request_compact_vdf: RLSettings(200, 1024),
            ProtocolMessageTypes.respond_compact_vdf: RLSettings(200, 100 * 1024),
            ProtocolMessageTypes.new_compact_vdf: RLSettings(100, 1024),
            ProtocolMessageTypes.request_peers: RLSettings(10, 100),
            ProtocolMessageTypes.respond_peers: RLSettings(10, 1 * 1024 * 1024),
            ProtocolMessageTypes.request_peers_introducer: RLSettings(100, 100),
            ProtocolMessageTypes.respond_peers_introducer: RLSettings(100, 1024 * 1024),
            ProtocolMessageTypes.request_plots: RLSettings(10, 10 * 1024 * 1024),
            ProtocolMessageTypes.respond_plots: RLSettings(10, 100 * 1024 * 1024),
            ProtocolMessageTypes.plot_sync_start: RLSettings(1000, 100 * 1024 * 1024),
            ProtocolMessageTypes.plot_sync_loaded: RLSettings(1000, 100 * 1024 * 1024),
            ProtocolMessageTypes.plot_sync_removed: RLSettings(1000, 100 * 1024 * 1024),
            ProtocolMessageTypes.plot_sync_invalid: RLSettings(1000, 100 * 1024 * 1024),
            ProtocolMessageTypes.plot_sync_keys_missing: RLSettings(1000, 100 * 1024 * 1024),
            ProtocolMessageTypes.plot_sync_duplicates: RLSettings(1000, 100 * 1024 * 1024),
            ProtocolMessageTypes.plot_sync_done: RLSettings(1000, 100 * 1024 * 1024),
            ProtocolMessageTypes.plot_sync_response: RLSettings(3000, 100 * 1024 * 1024),
        },
    },
}
