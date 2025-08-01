from __future__ import annotations

import asyncio
import dataclasses
import logging
import multiprocessing
import traceback
from concurrent.futures import Executor
from concurrent.futures.process import ProcessPoolExecutor
from enum import Enum
from multiprocessing.context import BaseContext
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from corpochain.consensus.block_body_validation import validate_block_body
from corpochain.consensus.block_header_validation import validate_unfinished_header_block
from corpochain.consensus.block_record import BlockRecord
from corpochain.consensus.blockchain_interface import BlockchainInterface
from corpochain.consensus.constants import ConsensusConstants
from corpochain.consensus.difficulty_adjustment import get_next_sub_slot_iters_and_difficulty
from corpochain.consensus.find_fork_point import find_fork_point_in_chain
from corpochain.consensus.full_block_to_block_record import block_to_block_record
from corpochain.consensus.multiprocess_validation import (
    PreValidationResult,
    pre_validate_blocks_multiprocessing,
)
from corpochain.beacon.block_height_map import BlockHeightMap
from corpochain.beacon.block_store import BlockStore
from corpochain.types.blockchain_format.sized_bytes import bytes32
from corpochain.types.blockchain_format.sub_epoch_summary import SubEpochSummary
from corpochain.types.blockchain_format.vdf import VDFInfo
from corpochain.types.end_of_slot_bundle import EndOfSubSlotBundle
from corpochain.types.full_block import FullBlock
from corpochain.types.header_block import HeaderBlock
from corpochain.types.unfinished_block import UnfinishedBlock
from corpochain.types.unfinished_header_block import UnfinishedHeaderBlock
from corpochain.types.weight_proof import SubEpochChallengeSegment
from corpochain.util.errors import ConsensusError, Err
from corpochain.util.generator_tools import get_block_header
from corpochain.util.hash import std_hash
from corpochain.util.inline_executor import InlineExecutor
from corpochain.util.ints import uint16, uint32, uint64, uint128
from corpochain.util.setproctitle import getproctitle, setproctitle

log = logging.getLogger(__name__)


class ReceiveBlockResult(Enum):
    """
    When Blockchain.receive_block(b) is called, one of these results is returned,
    showing whether the block was added to the chain (extending the peak),
    and if not, why it was not added.
    """

    NEW_PEAK = 1  # Added to the peak of the blockchain
    ADDED_AS_ORPHAN = 2  # Added as an orphan/stale block (not a new peak of the chain)
    INVALID_BLOCK = 3  # Block was not added because it was invalid
    ALREADY_HAVE_BLOCK = 4  # Block is already present in this blockchain
    DISCONNECTED_BLOCK = 5  # Block's parent (previous pointer) is not in this blockchain


@dataclasses.dataclass
class StateChangeSummary:
    peak: BlockRecord
    fork_height: uint32


class Blockchain(BlockchainInterface):
    constants: ConsensusConstants
    execution_client: ExecutionClient

    # peak of the blockchain
    _peak_height: Optional[uint32]
    # All blocks in peak path are guaranteed to be included, can include orphan blocks
    __block_records: Dict[bytes32, BlockRecord]
    # all hashes of blocks in block_record by height, used for garbage collection
    __heights_in_cache: Dict[uint32, Set[bytes32]]
    # maps block height (of the current heaviest chain) to block hash and sub
    # epoch summaries
    __height_map: BlockHeightMap
    # Store
    block_store: BlockStore
    # Used to verify blocks in parallel
    pool: Executor
    # Set holding seen compact proofs, in order to avoid duplicates.
    _seen_compact_proofs: Set[Tuple[VDFInfo, uint32]]

    # Whether blockchain is shut down or not
    _shut_down: bool

    # Lock to prevent simultaneous reads and writes
    lock: asyncio.Lock
    compact_proof_lock: asyncio.Lock

    @staticmethod
    async def create(
        block_store: BlockStore,
        consensus_constants: ConsensusConstants,
        execution_client: ExecutionClient,
        blockchain_dir: Path,
        reserved_cores: int,
        multiprocessing_context: Optional[BaseContext] = None,
        *,
        single_threaded: bool = False,
    ) -> "Blockchain":
        """
        Initializes a blockchain with the BlockRecords from disk, assuming they have all been
        validated. Uses the genesis block given in override_constants, or as a fallback,
        in the consensus constants config.
        """
        self = Blockchain()
        self.lock = asyncio.Lock()  # External lock handled by beacon client
        self.compact_proof_lock = asyncio.Lock()
        if single_threaded:
            self.pool = InlineExecutor()
        else:
            cpu_count = multiprocessing.cpu_count()
            if cpu_count > 61:
                cpu_count = 61  # Windows Server 2016 has an issue https://bugs.python.org/issue26903
            num_workers = max(cpu_count - reserved_cores, 1)
            self.pool = ProcessPoolExecutor(
                max_workers=num_workers,
                mp_context=multiprocessing_context,
                initializer=setproctitle,
                initargs=(f"{getproctitle()}_worker",),
            )
            log.info(f"Started {num_workers} processes for block validation")

        self.constants = consensus_constants
        self.block_store = block_store
        self.execution_client = execution_client
        self._shut_down = False
        await self._load_chain_from_store(blockchain_dir)
        self._seen_compact_proofs = set()
        return self

    def shut_down(self) -> None:
        self._shut_down = True
        self.pool.shutdown(wait=True)

    async def _load_chain_from_store(self, blockchain_dir: Path) -> None:
        """
        Initializes the state of the Blockchain class from the database.
        """
        self.__height_map = await BlockHeightMap.create(blockchain_dir, self.block_store.db_wrapper)
        self.__block_records = {}
        self.__heights_in_cache = {}
        block_records, peak = await self.block_store.get_block_records_close_to_peak(self.constants.BLOCKS_CACHE_SIZE)
        for block in block_records.values():
            self.add_block_record(block)

        if len(block_records) == 0:
            assert peak is None
            self._peak_height = None
            return

        assert peak is not None
        self._peak_height = self.block_record(peak).height
        assert self.__height_map.contains_height(self._peak_height)
        assert not self.__height_map.contains_height(uint32(self._peak_height + 1))

    def get_peak(self) -> Optional[BlockRecord]:
        """
        Return the peak of the blockchain
        """
        if self._peak_height is None:
            return None
        return self.height_to_block_record(self._peak_height)

    async def get_full_peak(self) -> Optional[FullBlock]:
        if self._peak_height is None:
            return None
        """ Return list of FullBlocks that are peaks"""
        peak_hash: Optional[bytes32] = self.height_to_hash(self._peak_height)
        assert peak_hash is not None  # Since we must have the peak block
        block = await self.block_store.get_full_block(peak_hash)
        assert block is not None
        return block

    async def get_full_block(self, header_hash: bytes32) -> Optional[FullBlock]:
        return await self.block_store.get_full_block(header_hash)

    async def receive_block(
        self,
        block: FullBlock,
        pre_validation_result: PreValidationResult,
        fork_point_with_peak: Optional[uint32] = None,
    ) -> Tuple[ReceiveBlockResult, Optional[Err], Optional[StateChangeSummary]]:
        """
        This method must be called under the blockchain lock
        Adds a new block into the blockchain, if it's valid and connected to the current
        blockchain, regardless of whether it is the child of a head, or another block.
        Returns a header if block is added to head. Returns an error if the block is
        invalid. Also returns the fork height, in the case of a new peak.

        Args:
            block: The FullBlock to be validated.
            pre_validation_result: A result of successful pre validation
            fork_point_with_peak: The fork point, for efficiency reasons, if None, it will be recomputed

        Returns:
            The result of adding the block to the blockchain (NEW_PEAK, ADDED_AS_ORPHAN, INVALID_BLOCK,
                DISCONNECTED_BLOCK, ALREDY_HAVE_BLOCK)
            An optional error if the result is not NEW_PEAK or ADDED_AS_ORPHAN
            A StateChangeSumamry iff NEW_PEAK, with:
                - A fork point if the result is NEW_PEAK
        """

        genesis: bool = block.height == 0
        if self.contains_block(block.header_hash):
            return ReceiveBlockResult.ALREADY_HAVE_BLOCK, None, None

        if not self.contains_block(block.prev_header_hash) and not genesis:
            return ReceiveBlockResult.DISCONNECTED_BLOCK, Err.INVALID_PREV_BLOCK_HASH, None

        if not genesis and (self.block_record(block.prev_header_hash).height + 1) != block.height:
            return ReceiveBlockResult.INVALID_BLOCK, Err.INVALID_HEIGHT, None

        required_iters = pre_validation_result.required_iters
        if pre_validation_result.error is not None:
            return ReceiveBlockResult.INVALID_BLOCK, Err(pre_validation_result.error), None
        assert required_iters is not None
        
        block_record = block_to_block_record(
            self.constants,
            self,
            required_iters,
            block,
            None,
        )

        error_code = await validate_block_body(
            self.execution_client,
            block,
            block.height,
            block_record,
        )
        if error_code is not None:
            return ReceiveBlockResult.INVALID_BLOCK, error_code, None

        # Always add the block to the database
        async with self.block_store.db_wrapper.writer():
            try:
                header_hash: bytes32 = block.header_hash
                # Perform the DB operations to update the state, and rollback if something goes wrong
                await self.block_store.add_full_block(header_hash, block, block_record)
                records, state_change_summary = await self._reconsider_peak(
                    block_record, genesis, fork_point_with_peak
                )

                # Then update the memory cache. It is important that this is not cancelled and does not throw
                # This is done after all async/DB operations, so there is a decreased chance of failure.
                self.add_block_record(block_record)
                if state_change_summary is not None:
                    self.__height_map.rollback(state_change_summary.fork_height)
                for fetched_block_record in records:
                    self.__height_map.update_height(
                        fetched_block_record.height,
                        fetched_block_record.header_hash,
                        fetched_block_record.sub_epoch_summary_included,
                    )
            except BaseException as e:
                self.block_store.rollback_cache_block(header_hash)
                log.error(
                    f"Error while adding block {block.header_hash} height {block.height},"
                    f" rolling back: {traceback.format_exc()} {e}"
                )
                raise

        # make sure to update _peak_height after the transaction is committed,
        # otherwise other tasks may go look for this block before it's available
        if state_change_summary is not None:
            self._peak_height = block_record.height

        # This is done outside the try-except in case it fails, since we do not want to revert anything if it does
        await self.__height_map.maybe_flush()

        if state_change_summary is not None:
            return ReceiveBlockResult.NEW_PEAK, None, state_change_summary
        else:
            return ReceiveBlockResult.ADDED_AS_ORPHAN, None, None

    async def _reconsider_peak(
        self,
        block_record: BlockRecord,
        genesis: bool,
        fork_point_with_peak: Optional[uint32],
    ) -> Tuple[List[BlockRecord], Optional[StateChangeSummary]]:
        """
        When a new block is added, this is called, to check if the new block is the new peak of the chain.
        This also handles reorgs by reverting blocks which are not in the heaviest chain.
        It returns the summary of the applied changes, including the height of the fork between the previous chain
        and the new chain, or returns None if there was no update to the heaviest chain.
        """

        peak = self.get_peak()

        if genesis:
            if peak is None:
                block: Optional[FullBlock] = await self.block_store.get_full_block(block_record.header_hash)
                assert block is not None

                await self.block_store.set_in_chain([(block_record.header_hash,)])
                await self.block_store.set_peak(block_record.header_hash)
                return [block_record], StateChangeSummary(
                    block_record, uint32(0)
                )
            return [], None

        assert peak is not None
        if block_record.weight <= peak.weight:
            # This is not a heavier block than the heaviest we have seen
            return [], None

        # Finds the fork. if the block is just being appended, it will return the peak
        # If no blocks in common, returns -1, and reverts all blocks
        if block_record.prev_hash == peak.header_hash:
            fork_height: int = peak.height
        elif fork_point_with_peak is not None:
            fork_height = fork_point_with_peak
        else:
            fork_height = find_fork_point_in_chain(self, block_record, peak)

        # Collects all blocks from fork point to new peak
        blocks_to_add: List[Tuple[FullBlock, BlockRecord]] = []
        curr = block_record.header_hash

        # Backtracks up to the fork point, pulling all the required blocks from DB (that will soon be in the chain)
        while fork_height < 0 or curr != self.height_to_hash(uint32(fork_height)):
            fetched_full_block: Optional[FullBlock] = await self.block_store.get_full_block(curr)
            fetched_block_record: Optional[BlockRecord] = await self.block_store.get_block_record(curr)
            assert fetched_full_block is not None
            assert fetched_block_record is not None
            blocks_to_add.append((fetched_full_block, fetched_block_record))
            if fetched_full_block.height == 0:
                # Doing a full reorg, starting at height 0
                break
            curr = fetched_block_record.prev_hash

        records_to_add: List[BlockRecord] = []
        for fetched_full_block, fetched_block_record in reversed(blocks_to_add):
            records_to_add.append(fetched_block_record)

        # we made it to the end successfully
        # Rollback sub_epoch_summaries
        await self.block_store.rollback(fork_height)
        await self.block_store.set_in_chain([(br.header_hash,) for br in records_to_add])

        # Changes the peak to be the new peak
        await self.block_store.set_peak(block_record.header_hash)

        return records_to_add, StateChangeSummary(
            block_record, uint32(max(fork_height, 0))
        )

    def get_next_difficulty(self, header_hash: bytes32, new_slot: bool) -> uint64:
        assert self.contains_block(header_hash)
        curr = self.block_record(header_hash)
        if curr.height <= 2:
            return self.constants.DIFFICULTY_STARTING

        return get_next_sub_slot_iters_and_difficulty(self.constants, new_slot, curr, self)[1]

    def get_next_slot_iters(self, header_hash: bytes32, new_slot: bool) -> uint64:
        assert self.contains_block(header_hash)
        curr = self.block_record(header_hash)
        if curr.height <= 2:
            return self.constants.SUB_SLOT_ITERS_STARTING
        return get_next_sub_slot_iters_and_difficulty(self.constants, new_slot, curr, self)[0]

    async def get_sp_and_ip_sub_slots(
        self, header_hash: bytes32
    ) -> Optional[Tuple[Optional[EndOfSubSlotBundle], Optional[EndOfSubSlotBundle]]]:
        block: Optional[FullBlock] = await self.block_store.get_full_block(header_hash)
        if block is None:
            return None
        curr_br: BlockRecord = self.block_record(block.header_hash)
        is_overflow = curr_br.overflow

        curr: Optional[FullBlock] = block
        assert curr is not None
        while True:
            if curr_br.first_in_sub_slot:
                curr = await self.block_store.get_full_block(curr_br.header_hash)
                assert curr is not None
                break
            if curr_br.height == 0:
                break
            curr_br = self.block_record(curr_br.prev_hash)

        if len(curr.finished_sub_slots) == 0:
            # This means we got to genesis and still no sub-slots
            return None, None

        ip_sub_slot = curr.finished_sub_slots[-1]

        if not is_overflow:
            # Pos sub-slot is the same as infusion sub slot
            return None, ip_sub_slot

        if len(curr.finished_sub_slots) > 1:
            # Have both sub-slots
            return curr.finished_sub_slots[-2], ip_sub_slot

        prev_curr: Optional[FullBlock] = await self.block_store.get_full_block(curr.prev_header_hash)
        if prev_curr is None:
            assert curr.height == 0
            prev_curr = curr
            prev_curr_br = self.block_record(curr.header_hash)
        else:
            prev_curr_br = self.block_record(curr.prev_header_hash)
        assert prev_curr_br is not None
        while prev_curr_br.height > 0:
            if prev_curr_br.first_in_sub_slot:
                prev_curr = await self.block_store.get_full_block(prev_curr_br.header_hash)
                assert prev_curr is not None
                break
            prev_curr_br = self.block_record(prev_curr_br.prev_hash)

        if len(prev_curr.finished_sub_slots) == 0:
            return None, ip_sub_slot
        return prev_curr.finished_sub_slots[-1], ip_sub_slot

    def get_recent_reward_challenges(self) -> List[Tuple[bytes32, uint128]]:
        peak = self.get_peak()
        if peak is None:
            return []
        recent_rc: List[Tuple[bytes32, uint128]] = []
        curr: Optional[BlockRecord] = peak
        while curr is not None and len(recent_rc) < 2 * self.constants.MAX_SUB_SLOT_BLOCKS:
            if curr != peak:
                recent_rc.append((curr.reward_infusion_new_challenge, curr.total_iters))
            if curr.first_in_sub_slot:
                assert curr.finished_reward_slot_hashes is not None
                sub_slot_total_iters = curr.ip_sub_slot_total_iters(self.constants)
                # Start from the most recent
                for rc in reversed(curr.finished_reward_slot_hashes):
                    if sub_slot_total_iters < curr.sub_slot_iters:
                        break
                    recent_rc.append((rc, sub_slot_total_iters))
                    sub_slot_total_iters = uint128(sub_slot_total_iters - curr.sub_slot_iters)
            curr = self.try_block_record(curr.prev_hash)
        return list(reversed(recent_rc))

    async def validate_unfinished_block_header(
        self, block: UnfinishedBlock, skip_overflow_ss_validation: bool = True
    ) -> Tuple[Optional[uint64], Optional[Err]]:
        if (
            not self.contains_block(block.prev_header_hash)
            and block.prev_header_hash != self.constants.GENESIS_CHALLENGE
        ):
            return None, Err.INVALID_PREV_BLOCK_HASH

        unfinished_header_block = UnfinishedHeaderBlock(
            block.finished_sub_slots,
            block.reward_chain_block,
            block.challenge_chain_sp_proof,
            block.reward_chain_sp_proof,
            block.foliage,
            block.foliage_transaction_block,
            block.execution_payload,
        )
        prev_b = self.try_block_record(unfinished_header_block.prev_header_hash)
        sub_slot_iters, difficulty = get_next_sub_slot_iters_and_difficulty(
            self.constants, len(unfinished_header_block.finished_sub_slots) > 0, prev_b, self
        )
        required_iters, error = validate_unfinished_header_block(
            self.constants,
            self,
            unfinished_header_block,
            difficulty,
            sub_slot_iters,
            skip_overflow_ss_validation,
        )
        if error is not None:
            return required_iters, error.code
        return required_iters, None

    async def validate_unfinished_block(
        self, block: UnfinishedBlock, skip_overflow_ss_validation: bool = True
    ) -> PreValidationResult:
        required_iters, error = await self.validate_unfinished_block_header(block, skip_overflow_ss_validation)

        if error is not None:
            return PreValidationResult(uint16(error.value), None)

        prev_height = (
            -1
            if block.prev_header_hash == self.constants.GENESIS_CHALLENGE
            else self.block_record(block.prev_header_hash).height
        )

        error_code = await validate_block_body(
            self.execution_client,
            block,
            uint32(prev_height + 1),
            None,
        )

        if error_code is not None:
            return PreValidationResult(uint16(error_code.value), None)

        return PreValidationResult(None, required_iters)

    async def pre_validate_blocks_multiprocessing(
        self,
        blocks: List[FullBlock],
        batch_size: int = 4,
        wp_summaries: Optional[List[SubEpochSummary]] = None,
    ) -> List[PreValidationResult]:
        return await pre_validate_blocks_multiprocessing(
            self.constants,
            self,
            blocks,
            self.pool,
            batch_size,
            wp_summaries,
        )

    def contains_block(self, header_hash: bytes32) -> bool:
        """
        True if we have already added this block to the chain. This may return false for orphan blocks
        that we have added but no longer keep in memory.
        """
        return header_hash in self.__block_records

    def block_record(self, header_hash: bytes32) -> BlockRecord:
        return self.__block_records[header_hash]

    def height_to_block_record(self, height: uint32) -> BlockRecord:
        # Precondition: height is in the blockchain
        header_hash: Optional[bytes32] = self.height_to_hash(height)
        if header_hash is None:
            raise ValueError(f"Height is not in blockchain: {height}")
        return self.block_record(header_hash)

    def get_ses_heights(self) -> List[uint32]:
        return self.__height_map.get_ses_heights()

    def get_ses(self, height: uint32) -> SubEpochSummary:
        return self.__height_map.get_ses(height)

    def height_to_hash(self, height: uint32) -> Optional[bytes32]:
        if not self.__height_map.contains_height(height):
            return None
        return self.__height_map.get_hash(height)

    def contains_height(self, height: uint32) -> bool:
        return self.__height_map.contains_height(height)

    def get_peak_height(self) -> Optional[uint32]:
        return self._peak_height

    async def warmup(self, fork_point: uint32) -> None:
        """
        Loads blocks into the cache. The blocks loaded include all blocks from
        fork point - BLOCKS_CACHE_SIZE up to and including the fork_point.

        Args:
            fork_point: the last block height to load in the cache

        """
        if self._peak_height is None:
            return None
        block_records = await self.block_store.get_block_records_in_range(
            max(fork_point - self.constants.BLOCKS_CACHE_SIZE, uint32(0)), fork_point
        )
        for block_record in block_records.values():
            self.add_block_record(block_record)

    def clean_block_record(self, height: int) -> None:
        """
        Clears all block records in the cache which have block_record < height.
        Args:
            height: Minimum height that we need to keep in the cache
        """
        if height < 0:
            return None
        blocks_to_remove = self.__heights_in_cache.get(uint32(height), None)
        while blocks_to_remove is not None and height >= 0:
            for header_hash in blocks_to_remove:
                del self.__block_records[header_hash]  # remove from blocks
            del self.__heights_in_cache[uint32(height)]  # remove height from heights in cache

            if height == 0:
                break
            height = height - 1
            blocks_to_remove = self.__heights_in_cache.get(uint32(height), None)

    def clean_block_records(self) -> None:
        """
        Cleans the cache so that we only maintain relevant blocks. This removes
        block records that have height < peak - BLOCKS_CACHE_SIZE.
        These blocks are necessary for calculating future difficulty adjustments.
        """

        if len(self.__block_records) < self.constants.BLOCKS_CACHE_SIZE:
            return None

        assert self._peak_height is not None
        if self._peak_height - self.constants.BLOCKS_CACHE_SIZE < 0:
            return None
        self.clean_block_record(self._peak_height - self.constants.BLOCKS_CACHE_SIZE)

    async def get_block_records_in_range(self, start: int, stop: int) -> Dict[bytes32, BlockRecord]:
        return await self.block_store.get_block_records_in_range(start, stop)

    async def get_header_blocks_in_range(
        self, start: int, stop: int
    ) -> Dict[bytes32, HeaderBlock]:
        hashes = []
        for height in range(start, stop + 1):
            header_hash: Optional[bytes32] = self.height_to_hash(uint32(height))
            if header_hash is not None:
                hashes.append(header_hash)

        blocks: List[FullBlock] = []
        for hash in hashes.copy():
            block = self.block_store.block_cache.get(hash)
            if block is not None:
                blocks.append(block)
                hashes.remove(hash)
        blocks_on_disk: List[FullBlock] = await self.block_store.get_blocks_by_hash(hashes)
        blocks.extend(blocks_on_disk)
        header_blocks: Dict[bytes32, HeaderBlock] = {}

        for block in blocks:
            if self.height_to_hash(block.height) != block.header_hash:
                raise ValueError(f"Block at {block.header_hash} is no longer in the blockchain (it's in a fork)")
            header = get_block_header(block)
            header_blocks[header.header_hash] = header

        return header_blocks

    async def get_header_block_by_height(
        self, height: int, header_hash: bytes32
    ) -> Optional[HeaderBlock]:
        header_dict: Dict[bytes32, HeaderBlock] = await self.get_header_blocks_in_range(height, height)
        if len(header_dict) == 0:
            return None
        if header_hash not in header_dict:
            return None
        return header_dict[header_hash]

    async def get_block_records_at(self, heights: List[uint32], batch_size: int = 900) -> List[BlockRecord]:
        """
        gets block records by height (only blocks that are part of the chain)
        """
        records: List[BlockRecord] = []
        hashes: List[bytes32] = []
        assert batch_size < 999  # sqlite in python 3.7 has a limit on 999 variables in queries
        for height in heights:
            header_hash: Optional[bytes32] = self.height_to_hash(height)
            if header_hash is None:
                raise ValueError(f"Do not have block at height {height}")
            hashes.append(header_hash)
            if len(hashes) > batch_size:
                res = await self.block_store.get_block_records_by_hash(hashes)
                records.extend(res)
                hashes = []

        if len(hashes) > 0:
            res = await self.block_store.get_block_records_by_hash(hashes)
            records.extend(res)
        return records

    async def get_block_record_from_db(self, header_hash: bytes32) -> Optional[BlockRecord]:
        if header_hash in self.__block_records:
            return self.__block_records[header_hash]
        return await self.block_store.get_block_record(header_hash)

    def remove_block_record(self, header_hash: bytes32) -> None:
        sbr = self.block_record(header_hash)
        del self.__block_records[header_hash]
        self.__heights_in_cache[sbr.height].remove(header_hash)

    def add_block_record(self, block_record: BlockRecord) -> None:
        """
        Adds a block record to the cache.
        """

        self.__block_records[block_record.header_hash] = block_record
        if block_record.height not in self.__heights_in_cache.keys():
            self.__heights_in_cache[block_record.height] = set()
        self.__heights_in_cache[block_record.height].add(block_record.header_hash)

    async def persist_sub_epoch_challenge_segments(
        self, ses_block_hash: bytes32, segments: List[SubEpochChallengeSegment]
    ) -> None:
        await self.block_store.persist_sub_epoch_challenge_segments(ses_block_hash, segments)

    async def get_sub_epoch_challenge_segments(
        self,
        ses_block_hash: bytes32,
    ) -> Optional[List[SubEpochChallengeSegment]]:
        segments: Optional[List[SubEpochChallengeSegment]] = await self.block_store.get_sub_epoch_challenge_segments(
            ses_block_hash
        )
        if segments is None:
            return None
        return segments

    # Returns 'True' if the info is already in the set, otherwise returns 'False' and stores it.
    def seen_compact_proofs(self, vdf_info: VDFInfo, height: uint32) -> bool:
        pot_tuple = (vdf_info, height)
        if pot_tuple in self._seen_compact_proofs:
            return True
        # Periodically cleanup to keep size small. TODO: make this smarter, like FIFO.
        if len(self._seen_compact_proofs) > 10000:
            self._seen_compact_proofs.clear()
        self._seen_compact_proofs.add(pot_tuple)
        return False
