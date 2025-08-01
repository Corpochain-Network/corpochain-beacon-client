from __future__ import annotations

import asyncio
import logging
import traceback
from concurrent.futures import Executor
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from blspy import AugSchemeMPL, G1Element

from corpochain.consensus.block_header_validation import validate_finished_header_block
from corpochain.consensus.block_record import BlockRecord
from corpochain.consensus.blockchain_interface import BlockchainInterface
from corpochain.consensus.constants import ConsensusConstants
from corpochain.consensus.difficulty_adjustment import get_next_sub_slot_iters_and_difficulty
from corpochain.consensus.full_block_to_block_record import block_to_block_record
from corpochain.consensus.get_block_challenge import get_block_challenge
from corpochain.consensus.pot_iterations import calculate_iterations_quality, is_overflow_block
from corpochain.types.blockchain_format.proof_of_space import verify_and_get_quality_string
from corpochain.types.blockchain_format.sized_bytes import bytes32
from corpochain.types.blockchain_format.sub_epoch_summary import SubEpochSummary
from corpochain.types.full_block import FullBlock
from corpochain.types.header_block import HeaderBlock
from corpochain.types.unfinished_block import UnfinishedBlock
from corpochain.util.block_cache import BlockCache
from corpochain.util.generator_tools import get_block_header
from corpochain.util.errors import Err, ValidationError
from corpochain.util.ints import uint16, uint32, uint64
from corpochain.util.streamable import Streamable, streamable

log = logging.getLogger(__name__)


@streamable
@dataclass(frozen=True)
class PreValidationResult(Streamable):
    error: Optional[uint16]
    required_iters: Optional[uint64]  # Iff error is None


def batch_pre_validate_blocks(
    constants: ConsensusConstants,
    blocks_pickled: Dict[bytes, bytes],
    full_blocks_pickled: Optional[List[bytes]],
    header_blocks_pickled: Optional[List[bytes]],
    expected_difficulty: List[uint64],
    expected_sub_slot_iters: List[uint64],
) -> List[bytes]:
    blocks: Dict[bytes32, BlockRecord] = {}
    for k, v in blocks_pickled.items():
        blocks[bytes32(k)] = BlockRecord.from_bytes(v)
    results: List[PreValidationResult] = []
    if full_blocks_pickled is not None and header_blocks_pickled is not None:
        assert ValueError("Only one should be passed here")

    # In this case, we are validating full blocks, not headers
    if full_blocks_pickled is not None:
        for i in range(len(full_blocks_pickled)):
            try:
                block: FullBlock = FullBlock.from_bytes(full_blocks_pickled[i])
                header_block = get_block_header(block)
                required_iters, error = validate_finished_header_block(
                    constants,
                    BlockCache(blocks),
                    header_block,
                    expected_difficulty[i],
                    expected_sub_slot_iters[i],
                )
                error_int: Optional[uint16] = None
                if error is not None:
                    error_int = uint16(error.code.value)

                results.append(
                    PreValidationResult(error_int, required_iters)
                )
            except Exception:
                error_stack = traceback.format_exc()
                log.error(f"Exception: {error_stack}")
                results.append(PreValidationResult(uint16(Err.UNKNOWN.value), None))
    # In this case, we are validating header blocks
    elif header_blocks_pickled is not None:
        for i in range(len(header_blocks_pickled)):
            try:
                header_block = HeaderBlock.from_bytes(header_blocks_pickled[i])
                required_iters, error = validate_finished_header_block(
                    constants,
                    BlockCache(blocks),
                    header_block,
                    expected_difficulty[i],
                    expected_sub_slot_iters[i],
                )
                error_int = None
                if error is not None:
                    error_int = uint16(error.code.value)
                results.append(PreValidationResult(error_int, required_iters))
            except Exception:
                error_stack = traceback.format_exc()
                log.error(f"Exception: {error_stack}")
                results.append(PreValidationResult(uint16(Err.UNKNOWN.value), None))
    return [bytes(r) for r in results]


async def pre_validate_blocks_multiprocessing(
    constants: ConsensusConstants,
    block_records: BlockchainInterface,
    blocks: Sequence[FullBlock],
    pool: Executor,
    batch_size: int,
    wp_summaries: Optional[List[SubEpochSummary]] = None,
) -> List[PreValidationResult]:
    """
    This method must be called under the blockchain lock
    If all the full blocks pass pre-validation, (only validates header), returns the list of required iters.
    if any validation issue occurs, returns False.

    Args:
        constants:
        pool:
        constants:
        block_records:
        blocks: list of full blocks to validate (must be connected to current chain)
    """
    prev_b: Optional[BlockRecord] = None
    # Collects all the recent blocks (up to the previous sub-epoch)
    recent_blocks: Dict[bytes32, BlockRecord] = {}
    recent_blocks_compressed: Dict[bytes32, BlockRecord] = {}
    num_sub_slots_found = 0
    num_blocks_seen = 0
    if blocks[0].height > 0:
        if not block_records.contains_block(blocks[0].prev_header_hash):
            return [PreValidationResult(uint16(Err.INVALID_PREV_BLOCK_HASH.value), None)]
        curr = block_records.block_record(blocks[0].prev_header_hash)
        num_sub_slots_to_look_for = 3 if curr.overflow else 2
        while (
            curr.sub_epoch_summary_included is None
            or num_blocks_seen < constants.NUMBER_OF_TIMESTAMPS
            or num_sub_slots_found < num_sub_slots_to_look_for
        ) and curr.height > 0:
            if num_blocks_seen < constants.NUMBER_OF_TIMESTAMPS or num_sub_slots_found < num_sub_slots_to_look_for:
                recent_blocks_compressed[curr.header_hash] = curr

            if curr.first_in_sub_slot:
                assert curr.finished_challenge_slot_hashes is not None
                num_sub_slots_found += len(curr.finished_challenge_slot_hashes)
            recent_blocks[curr.header_hash] = curr
            if curr.is_transaction_block:
                num_blocks_seen += 1
            curr = block_records.block_record(curr.prev_hash)
        recent_blocks[curr.header_hash] = curr
        recent_blocks_compressed[curr.header_hash] = curr
    block_record_was_present = []
    for block in blocks:
        block_record_was_present.append(block_records.contains_block(block.header_hash))

    diff_ssis: List[Tuple[uint64, uint64]] = []
    for block in blocks:
        if block.height != 0:
            assert block_records.contains_block(block.prev_header_hash)
            if prev_b is None:
                prev_b = block_records.block_record(block.prev_header_hash)

        sub_slot_iters, difficulty = get_next_sub_slot_iters_and_difficulty(
            constants, len(block.finished_sub_slots) > 0, prev_b, block_records
        )

        overflow = is_overflow_block(constants, block.reward_chain_block.signage_point_index)
        challenge = get_block_challenge(constants, block, BlockCache(recent_blocks), prev_b is None, overflow, False)
        if block.reward_chain_block.challenge_chain_sp_vdf is None:
            cc_sp_hash: bytes32 = challenge
        else:
            cc_sp_hash = block.reward_chain_block.challenge_chain_sp_vdf.output.get_hash()
        q_str: Optional[bytes32] = verify_and_get_quality_string(
            block.reward_chain_block.proof_of_space, constants, challenge, cc_sp_hash
        )
        if q_str is None:
            for i, block_i in enumerate(blocks):
                if not block_record_was_present[i] and block_records.contains_block(block_i.header_hash):
                    block_records.remove_block_record(block_i.header_hash)
            return [PreValidationResult(uint16(Err.INVALID_POSPACE.value), None)]

        required_iters: uint64 = calculate_iterations_quality(
            constants.DIFFICULTY_CONSTANT_FACTOR,
            q_str,
            block.reward_chain_block.proof_of_space.size,
            difficulty,
            cc_sp_hash,
        )

        try:
            block_rec = block_to_block_record(
                constants,
                block_records,
                required_iters,
                block,
                None,
                sub_slot_iters,
            )
        except ValueError:
            return [PreValidationResult(uint16(Err.INVALID_SUB_EPOCH_SUMMARY.value), None)]

        if block_rec.sub_epoch_summary_included is not None and wp_summaries is not None:
            idx = int(block.height / constants.SUB_EPOCH_BLOCKS) - 1
            next_ses = wp_summaries[idx]
            if not block_rec.sub_epoch_summary_included.get_hash() == next_ses.get_hash():
                log.error("sub_epoch_summary does not match wp sub_epoch_summary list")
                return [PreValidationResult(uint16(Err.INVALID_SUB_EPOCH_SUMMARY.value), None)]
        # Makes sure to not override the valid blocks already in block_records
        if not block_records.contains_block(block_rec.header_hash):
            block_records.add_block_record(block_rec)  # Temporarily add block to dict
            recent_blocks[block_rec.header_hash] = block_rec
            recent_blocks_compressed[block_rec.header_hash] = block_rec
        else:
            recent_blocks[block_rec.header_hash] = block_records.block_record(block_rec.header_hash)
            recent_blocks_compressed[block_rec.header_hash] = block_records.block_record(block_rec.header_hash)
        prev_b = block_rec
        diff_ssis.append((difficulty, sub_slot_iters))

    block_dict: Dict[bytes32, FullBlock] = {}
    for i, block in enumerate(blocks):
        block_dict[block.header_hash] = block
        if not block_record_was_present[i]:
            block_records.remove_block_record(block.header_hash)

    recent_sb_compressed_pickled = {bytes(k): bytes(v) for k, v in recent_blocks_compressed.items()}
    futures = []
    # Pool of workers to validate blocks concurrently
    for i in range(0, len(blocks), batch_size):
        end_i = min(i + batch_size, len(blocks))
        blocks_to_validate = blocks[i:end_i]
        if any([len(block.finished_sub_slots) > 0 for block in blocks_to_validate]):
            final_pickled = {bytes(k): bytes(v) for k, v in recent_blocks.items()}
        else:
            final_pickled = recent_sb_compressed_pickled
        b_pickled: Optional[List[bytes]] = None
        hb_pickled: Optional[List[bytes]] = None
        for block in blocks_to_validate:
            # We ONLY add blocks which are in the past, based on header hashes (which are validated later) to the
            # prev blocks dict. This is important since these blocks are assumed to be valid
            prev_blocks_dict: Dict[bytes32, FullBlock] = {}
            curr_b: FullBlock = block

            while curr_b.prev_header_hash in block_dict:
                curr_b = block_dict[curr_b.prev_header_hash]
                prev_blocks_dict[curr_b.header_hash] = curr_b

            if isinstance(block, FullBlock):
                if b_pickled is None:
                    b_pickled = []
                b_pickled.append(bytes(block))
            else:
                if hb_pickled is None:
                    hb_pickled = []
                hb_pickled.append(bytes(block))

        futures.append(
            asyncio.get_running_loop().run_in_executor(
                pool,
                batch_pre_validate_blocks,
                constants,
                final_pickled,
                b_pickled,
                hb_pickled,
                [diff_ssis[j][0] for j in range(i, end_i)],
                [diff_ssis[j][1] for j in range(i, end_i)],
            )
        )
    # Collect all results into one flat list
    return [
        PreValidationResult.from_bytes(result)
        for batch_result in (await asyncio.gather(*futures))
        for result in batch_result
    ]
