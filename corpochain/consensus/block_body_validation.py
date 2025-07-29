from __future__ import annotations

import collections
import logging
from typing import Awaitable, Callable, Dict, List, Optional, Set, Tuple, Union

from corpochain.consensus.block_record import BlockRecord
from corpochain.consensus.blockchain_interface import BlockchainInterface
from corpochain.consensus.constants import ConsensusConstants
from corpochain.consensus.find_fork_point import find_fork_point_in_chain
from corpochain.beacon.block_store import BlockStore
from corpochain.types.blockchain_format.sized_bytes import bytes32
from corpochain.types.full_block import FullBlock
from corpochain.types.unfinished_block import UnfinishedBlock
from corpochain.util.errors import Err
from corpochain.util.hash import std_hash
from corpochain.util.ints import uint32, uint64

log = logging.getLogger(__name__)


async def validate_block_body(
    execution_client: ExecutionClient,
    block: Union[FullBlock, UnfinishedBlock],
    height: uint32,
    block_record: Optional[BlockRecord],
) -> Optional[Err]:
    """
    This assumes the header block has been completely validated.
    Validates the body of the block. Returns None if everything validates correctly, or an Err if something does not validate.
    """
    if isinstance(block, FullBlock):
        assert height == block.height            
    
    if block.execution_payload is None:
        return None
    
    status = await execution_client.new_payload(block.execution_payload)
    if status == "INVALID" or status == "INVALID_BLOCK_HASH":
        return Err.PAYLOAD_INVALIDATED
    elif status == "SYNCING" or status == "ACCEPTED":
        if isinstance(block, UnfinishedBlock):
            return Err.PAYLOAD_NOT_VALIDATED
    elif status != "VALID":
        return Err.UNKNOWN
    
    if isinstance(block, FullBlock):
        assert block_record is not None
        optimistic_import = execution_client.beacon.config.get("optimistic_import", True)
        
        status = await execution_client.forkchoice_update(block_record)
        if status == "INVALID" or status == "INVALID_BLOCK_HASH":
            return Err.PAYLOAD_INVALIDATED
        elif status == "SYNCING" or status == "ACCEPTED":
            if not optimistic_import:
                return Err.PAYLOAD_NOT_VALIDATED
        elif status != "VALID":
            return Err.UNKNOWN
    
    return None
