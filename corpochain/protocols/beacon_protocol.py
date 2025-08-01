from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from corpochain.types.blockchain_format.sized_bytes import bytes32
from corpochain.types.blockchain_format.vdf import VDFInfo, VDFProof
from corpochain.types.end_of_slot_bundle import EndOfSubSlotBundle
from corpochain.types.full_block import FullBlock
from corpochain.types.peer_info import TimestampedPeerInfo
from corpochain.types.unfinished_block import UnfinishedBlock
from corpochain.types.weight_proof import WeightProof
from corpochain.util.ints import uint8, uint32, uint64, uint128
from corpochain.util.streamable import Streamable, streamable

"""
Protocol between beacon clients.
Note: When changing this file, also change protocol_message_types.py, and the protocol version in shared_protocol.py
"""


@streamable
@dataclass(frozen=True)
class NewPeak(Streamable):
    header_hash: bytes32
    height: uint32
    weight: uint128
    fork_point_with_previous_peak: uint32
    unfinished_reward_block_hash: bytes32


@streamable
@dataclass(frozen=True)
class RequestProofOfWeight(Streamable):
    total_number_of_blocks: uint32
    tip: bytes32


@streamable
@dataclass(frozen=True)
class RespondProofOfWeight(Streamable):
    wp: WeightProof
    tip: bytes32


@streamable
@dataclass(frozen=True)
class RequestBlock(Streamable):
    height: uint32


@streamable
@dataclass(frozen=True)
class RejectBlock(Streamable):
    height: uint32


@streamable
@dataclass(frozen=True)
class RequestBlocks(Streamable):
    start_height: uint32
    end_height: uint32


@streamable
@dataclass(frozen=True)
class RespondBlocks(Streamable):
    start_height: uint32
    end_height: uint32
    blocks: List[FullBlock]


@streamable
@dataclass(frozen=True)
class RejectBlocks(Streamable):
    start_height: uint32
    end_height: uint32


@streamable
@dataclass(frozen=True)
class RespondBlock(Streamable):
    block: FullBlock


@streamable
@dataclass(frozen=True)
class NewUnfinishedBlock(Streamable):
    unfinished_reward_hash: bytes32


@streamable
@dataclass(frozen=True)
class RequestUnfinishedBlock(Streamable):
    unfinished_reward_hash: bytes32


@streamable
@dataclass(frozen=True)
class RespondUnfinishedBlock(Streamable):
    unfinished_block: UnfinishedBlock


@streamable
@dataclass(frozen=True)
class NewSignagePointOrEndOfSubSlot(Streamable):
    prev_challenge_hash: Optional[bytes32]
    challenge_hash: bytes32
    index_from_challenge: uint8
    last_rc_infusion: bytes32


@streamable
@dataclass(frozen=True)
class RequestSignagePointOrEndOfSubSlot(Streamable):
    challenge_hash: bytes32
    index_from_challenge: uint8
    last_rc_infusion: bytes32


@streamable
@dataclass(frozen=True)
class RespondSignagePoint(Streamable):
    index_from_challenge: uint8
    challenge_chain_vdf: VDFInfo
    challenge_chain_proof: VDFProof
    reward_chain_vdf: VDFInfo
    reward_chain_proof: VDFProof


@streamable
@dataclass(frozen=True)
class RespondEndOfSubSlot(Streamable):
    end_of_slot_bundle: EndOfSubSlotBundle


@streamable
@dataclass(frozen=True)
class NewCompactVDF(Streamable):
    height: uint32
    header_hash: bytes32
    field_vdf: uint8
    vdf_info: VDFInfo


@streamable
@dataclass(frozen=True)
class RequestCompactVDF(Streamable):
    height: uint32
    header_hash: bytes32
    field_vdf: uint8
    vdf_info: VDFInfo


@streamable
@dataclass(frozen=True)
class RespondCompactVDF(Streamable):
    height: uint32
    header_hash: bytes32
    field_vdf: uint8
    vdf_info: VDFInfo
    vdf_proof: VDFProof


@streamable
@dataclass(frozen=True)
class RequestPeers(Streamable):
    """
    Return full list of peers
    """


@streamable
@dataclass(frozen=True)
class RespondPeers(Streamable):
    peer_list: List[TimestampedPeerInfo]
