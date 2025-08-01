from __future__ import annotations

import logging
from typing import Dict, List, Optional

from corpochain.consensus.block_record import BlockRecord
from corpochain.consensus.blockchain_interface import BlockchainInterface
from corpochain.types.blockchain_format.sized_bytes import bytes32
from corpochain.types.blockchain_format.sub_epoch_summary import SubEpochSummary
from corpochain.types.header_block import HeaderBlock
from corpochain.types.weight_proof import SubEpochChallengeSegment, SubEpochSegments
from corpochain.util.ints import uint32


class BlockCache(BlockchainInterface):
    def __init__(
        self,
        blocks: Dict[bytes32, BlockRecord],
        headers: Dict[bytes32, HeaderBlock] = None,
        height_to_hash: Dict[uint32, bytes32] = None,
        sub_epoch_summaries: Dict[uint32, SubEpochSummary] = None,
    ):
        if sub_epoch_summaries is None:
            sub_epoch_summaries = {}
        if height_to_hash is None:
            height_to_hash = {}
        if headers is None:
            headers = {}
        self._block_records = blocks
        self._headers = headers
        self._height_to_hash = height_to_hash
        self._sub_epoch_summaries = sub_epoch_summaries
        self._sub_epoch_segments: Dict[bytes32, SubEpochSegments] = {}
        self.log = logging.getLogger(__name__)

    def block_record(self, header_hash: bytes32) -> BlockRecord:
        return self._block_records[header_hash]

    def height_to_block_record(self, height: uint32, check_db: bool = False) -> BlockRecord:
        # Precondition: height is < peak height

        header_hash: Optional[bytes32] = self.height_to_hash(height)
        assert header_hash is not None

        return self.block_record(header_hash)

    def get_ses_heights(self) -> List[uint32]:
        return sorted(self._sub_epoch_summaries.keys())

    def get_ses(self, height: uint32) -> SubEpochSummary:
        return self._sub_epoch_summaries[height]

    def height_to_hash(self, height: uint32) -> Optional[bytes32]:
        if height not in self._height_to_hash:
            self.log.warning(f"could not find height in cache {height}")
            return None
        return self._height_to_hash[height]

    def contains_block(self, header_hash: bytes32) -> bool:
        return header_hash in self._block_records

    def contains_height(self, height: uint32) -> bool:
        return height in self._height_to_hash

    async def get_block_records_in_range(self, start: int, stop: int) -> Dict[bytes32, BlockRecord]:
        return self._block_records

    async def get_block_records_at(self, heights: List[uint32]) -> List[BlockRecord]:
        block_records: List[BlockRecord] = []
        for height in heights:
            block_records.append(self.height_to_block_record(height))
        return block_records

    async def get_block_record_from_db(self, header_hash: bytes32) -> Optional[BlockRecord]:
        return self._block_records[header_hash]

    def remove_block_record(self, header_hash: bytes32):
        del self._block_records[header_hash]

    def add_block_record(self, block: BlockRecord):
        self._block_records[block.header_hash] = block

    async def persist_sub_epoch_challenge_segments(
        self, sub_epoch_summary_hash: bytes32, segments: List[SubEpochChallengeSegment]
    ):
        self._sub_epoch_segments[sub_epoch_summary_hash] = SubEpochSegments(segments)

    async def get_sub_epoch_challenge_segments(
        self,
        sub_epoch_summary_hash: bytes32,
    ) -> Optional[List[SubEpochChallengeSegment]]:
        segments = self._sub_epoch_segments.get(sub_epoch_summary_hash)
        if segments is None:
            return None
        return segments.challenge_segments
