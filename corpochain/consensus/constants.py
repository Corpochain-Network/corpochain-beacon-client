from __future__ import annotations

import dataclasses
import logging
from typing import Any

from corpochain.types.blockchain_format.sized_bytes import bytes20, bytes32
from corpochain.util.byte_types import hexstr_to_bytes
from corpochain.util.ints import uint8, uint32, uint64, uint128

log = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class ConsensusConstants:
    SLOT_BLOCKS_TARGET: uint32  # How many blocks to target per sub-slot
    MIN_BLOCKS_PER_CHALLENGE_BLOCK: uint8  # How many blocks must be created per slot (to make challenge sb)
    # Max number of blocks that can be infused into a sub-slot.
    # Note: this must be less than SUB_EPOCH_BLOCKS/2, and > SLOT_BLOCKS_TARGET
    MAX_SUB_SLOT_BLOCKS: uint32
    NUM_SPS_SUB_SLOT: uint32  # The number of signage points per sub-slot (including the 0th sp at the sub-slot start)

    SUB_SLOT_ITERS_STARTING: uint64  # The sub_slot_iters for the first epoch
    SUB_SLOT_ITERS_HARD_MIN: uint64  # Hard minimum for anti-stall protection
    DIFFICULTY_CONSTANT_FACTOR: uint128  # Multiplied by the difficulty to get iterations
    DIFFICULTY_STARTING: uint64  # The difficulty for the first epoch
    # The maximum factor by which difficulty and sub_slot_iters can change per epoch
    DIFFICULTY_CHANGE_MAX_FACTOR: uint32
    SUB_EPOCH_BLOCKS: uint32  # The number of blocks per sub-epoch
    EPOCH_BLOCKS: uint32  # The number of blocks per sub-epoch, must be a multiple of SUB_EPOCH_BLOCKS

    SIGNIFICANT_BITS: int  # The number of bits to look at in difficulty and min iters. The rest are zeroed
    DISCRIMINANT_SIZE_BITS: int  # Max is 1024 (based on ClassGroupElement int size)
    NUMBER_ZERO_BITS_PLOT_FILTER: int  # H(plot id + challenge hash + signage point) must start with these many zeroes
    MIN_PLOT_SIZE: int
    MAX_PLOT_SIZE: int
    SUB_SLOT_TIME_TARGET: int  # The target number of seconds per sub-slot
    NUM_SP_INTERVALS_EXTRA: int  # The difference between signage point and infusion point (plus required_iters)
    MAX_FUTURE_TIME: int  # The next block can have a timestamp of at most these many seconds more
    NUMBER_OF_TIMESTAMPS: int  # Than the average of the last NUMBER_OF_TIMESTAMPS blocks
    # Used as the initial cc rc challenges, as well as first block back pointers, and first SES back pointer
    # We override this value based on the chain being run (testnet, mainnet, etc)
    GENESIS_CHALLENGE: bytes32
    MAX_VDF_WITNESS_SIZE: int  # The maximum number of classgroup elements within an n-wesolowski proof

    WEIGHT_PROOF_THRESHOLD: uint8
    WEIGHT_PROOF_RECENT_BLOCKS: uint32
    MAX_BLOCK_COUNT_PER_REQUESTS: uint32
    BLOCKS_CACHE_SIZE: uint32
    
    GENESIS_EXECUTION_BLOCK_HASH: bytes32
    PREFARM_ADDRESS: bytes20
    PREFARM_AMOUNT: uint64

    def replace(self, **changes: object) -> "ConsensusConstants":
        return dataclasses.replace(self, **changes)

    def replace_str_to_bytes(self, **changes: Any) -> "ConsensusConstants":
        """
        Overrides str (hex) values with bytes.
        """

        filtered_changes = {}
        for k, v in changes.items():
            if not hasattr(self, k):
                # NETWORK_TYPE used to be present in default config, but has been removed
                if k not in ["NETWORK_TYPE"]:
                    log.warning(f'invalid key in network configuration (config.yaml) "{k}". Ignoring')
                continue
            if isinstance(v, str):
                filtered_changes[k] = hexstr_to_bytes(v)
            else:
                filtered_changes[k] = v

        return dataclasses.replace(self, **filtered_changes)
