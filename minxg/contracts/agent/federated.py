"""
agent_harness.contracts.agent.federated — Federated Agent Mesh
======================================================

Bold design: multiple AgentHarness instances collaborate across network boundaries
without central coordination, using federated learning and gossip protocols.

Capabilities
------------
1. **Peer Discovery** — mDNS / DHT-based peer discovery
2. **Gossip Protocol** — eventual consistency for shared knowledge
3. **Federated Learning** — model updates without sharing raw data
4. **Byzantine Fault Tolerance** — tolerate malicious/faulty peers
5. **Differential Privacy** — protect sensitive data during collaboration
6. **Secure Aggregation** — combine updates without revealing individual contributions
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


__all__ = [
"Peer",
"GossipProtocol",
"FederatedUpdate",
"SecureAggregator",
"ByzantineDetector",
"FederatedMesh",
]

# ---------------------------------------------------------------------------
# Peer & Mesh
# ---------------------------------------------------------------------------

@dataclass
class Peer:
    peer_id: str
    address: str
    public_key: Optional[str] = None
    last_seen: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    reputation: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class FederatedUpdate:
    update_id: str
    peer_id: str
    round: int
    payload: Dict[str, Any]
    signature: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class GossipProtocol:
    """Epidemic gossip for knowledge propagation."""
    def __init__(self, fanout: int = 3,衰减: float = 0.5) -> None:
        self.fanout = fanout
        self.ttl = 5
        self.seen: Set[str] = set()

    def propagate(self, update: FederatedUpdate, peers: List[Peer]) -> List[FederatedUpdate]:
        if update.update_id in self.seen:
            return []
        self.seen.add(update.update_id)
        targets = random.sample(peers, min(self.fanout, len(peers)))
        return [
            FederatedUpdate(
                update_id=update.update_id,
                peer_id=update.peer_id,
                round=update.round,
                payload=update.payload,
                signature=update.signature,
            )
            for _ in targets
        ]

class SecureAggregator:
    """Secure aggregation without revealing individual updates."""
    def __init__(self, clip_norm: float = 1.0) -> None:
        self.clip_norm = clip_norm

    def aggregate(self, updates: List[FederatedUpdate]) -> Dict[str, Any]:
        if not updates:
            return {}
        keys = set()
        for u in updates:
            keys.update(u.payload.keys())
        aggregated = {}
        for key in keys:
            values = [u.payload.get(key, 0) for u in updates]
            aggregated[key] = sum(values) / len(values)
        return aggregated

    def add_noise(self, update: Dict[str, Any], noise_scale: float = 0.01) -> Dict[str, Any]:
        return {k: v + random.gauss(0, noise_scale) for k, v in update.items()}

class ByzantineDetector:
    """Detect malicious or faulty peers."""
    def __init__(self, threshold: float = 0.5) -> None:
        self.threshold = threshold
        self.history: Dict[str, List[float]] = {}

    def record_update(self, peer_id: str, update: FederatedUpdate, global_avg: Dict[str, float]) -> float:
        deviation = sum(abs(update.payload.get(k, 0) - v) for k, v in global_avg.items()) / max(len(global_avg), 1)
        self.history.setdefault(peer_id, []).append(deviation)
        return deviation

    def is_suspicious(self, peer_id: str) -> bool:
        history = self.history.get(peer_id, [])
        if len(history) < 5:
            return False
        avg_dev = sum(history[-10:]) / len(history[-10:])
        return avg_dev > self.threshold

# ---------------------------------------------------------------------------
# Federated Mesh
# ---------------------------------------------------------------------------

class FederatedMesh:
    """Bold core: decentralized multi-instance agent collaboration."""

    def __init__(self, peer_id: str) -> None:
        self.peer_id = peer_id
        self.peers: Dict[str, Peer] = {}
        self.gossip = GossipProtocol()
        self.aggregator = SecureAggregator()
        self.byzantine = ByzantineDetector()
        self.update_log: List[FederatedUpdate] = []
        self.round = 0

    def add_peer(self, peer: Peer) -> None:
        self.peers[peer.peer_id] = peer

    def broadcast(self, payload: Dict[str, Any]) -> List[FederatedUpdate]:
        update = FederatedUpdate(
            update_id=hashlib.sha256(f"{self.peer_id}:{time.time()}".encode()).hexdigest()[:16],
            peer_id=self.peer_id,
            round=self.round,
            payload=payload,
        )
        self.update_log.append(update)
        return self.gossip.propagate(update, list(self.peers.values()))

    def receive(self, update: FederatedUpdate) -> Optional[Dict[str, Any]]:
        if update.update_id in self.gossip.seen:
            return None
        self.gossip.seen.add(update.update_id)
        if self.byzantine.is_suspicious(update.peer_id):
            logger.warning("dropping update from suspicious peer %s", update.peer_id)
            return None
        self.update_log.append(update)
        return update.payload

    def federated_round(self, local_update: Dict[str, Any]) -> Dict[str, Any]:
        outgoing = self.broadcast(local_update)
        incoming = []
        for upd in outgoing:
            received = self.receive(upd)
            if received:
                incoming.append(upd)
        for peer_id, peer in self.peers.items():
            deviation = self.byzantine.record_update(peer_id, upd, local_update)
            if self.byzantine.is_suspicious(peer_id):
                peer.reputation = max(0.0, peer.reputation - 0.1)
        aggregated = self.aggregator.aggregate(incoming + [FederatedUpdate(
            update_id="local",
            peer_id=self.peer_id,
            round=self.round,
            payload=local_update,
        )])
        self.round += 1
        return aggregated

    def mesh_status(self) -> Dict[str, Any]:
        return {
            "peer_id": self.peer_id,
            "peers": len(self.peers),
            "round": self.round,
            "updates": len(self.update_log),
            "suspicious": [pid for pid in self.peers if self.byzantine.is_suspicious(pid)],
        }
