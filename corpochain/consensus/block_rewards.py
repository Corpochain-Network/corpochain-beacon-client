from __future__ import annotations

from typing import List

from corpochain.util.ints import uint64
from corpochain.types.blockchain_format.execution_payload import WithdrawalV1
from corpochain.consensus.block_record import BlockRecord
from corpochain.consensus.blockchain_interface import BlockchainInterface
from corpochain.consensus.constants import ConsensusConstants

_corpochain_to_gwei = 1000000000
_blocks_per_year = 4608 * 2 * 365

def create_withdrawals(
    constants: ConsensusConstants,
    prev_tx_block: BlockRecord,
    blocks: BlockchainInterface,
) -> List[WithdrawalV1]:
    withdrawals: List[WithdrawalV1] = []
    
    next_wd_index: uint64
    if prev_tx_block.last_withdrawal_index is None:
        next_wd_index = 0
    else:
        next_wd_index = prev_tx_block.last_withdrawal_index + 1
    
    if prev_tx_block.height == 0:
        # Add prefarm withdrawal
        withdrawals.append(
            WithdrawalV1(
                next_wd_index,
                uint64(0),
                constants.PREFARM_ADDRESS,
                constants.PREFARM_AMOUNT * _corpochain_to_gwei,
            )
        )
        next_wd_index += 1
    
    # Add block rewards
    curr: BlockRecord = prev_tx_block
    while True:
        withdrawals.append(
            WithdrawalV1(
                next_wd_index,
                uint64(1),
                curr.coinbase,
                _calculate_block_reward(curr.height),
            )
        )
        next_wd_index += 1
        
        if curr.prev_hash == constants.GENESIS_CHALLENGE:
            break
        curr = blocks.block_record(curr.prev_hash)
        if curr.is_transaction_block:
            break
    
    return withdrawals

def _calculate_block_reward(
    height: uint64
) -> uint64:
    if height < 3 * _blocks_per_year:
        return uint64(2 * _corpochain_to_gwei)
    elif height < 6 * _blocks_per_year:
        return uint64(1 * _corpochain_to_gwei)
    elif height < 9 * _blocks_per_year:
        return uint64(0.5 * _corpochain_to_gwei)
    elif height < 12 * _blocks_per_year:
        return uint64(0.25 * _corpochain_to_gwei)
    elif height < 15 * _blocks_per_year:
        return uint64(0.125 * _corpochain_to_gwei)
    else:
        return uint64(0)
