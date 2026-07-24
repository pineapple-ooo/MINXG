"""Agent Communication Bus — giant communication network for multi-agent collaboration.

All agents can:
  - Post messages to the shared bus
  - Read other agents' status
  - Negotiate joining/leaving groups
  - See what others are working on
  - Share context and results

This enables ultra-long tasks (10+ days) by keeping agents
continuously informed and coordinated.
"""
import json, logging, threading, time, os
from typing import Any, Dict, List, Optional, Callable

from pathlib import Path
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class AgentMessage:
    """A message posted to the communication bus."""
    sender: str           # agent id or group name
    receiver: str = ""    # target agent/group, "" = broadcast
    msg_type: str = "info"  # info, request, response, status, error, negotiate
    content: str = ""
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)


class CommunicationBus:
    """Shared message bus for all agents. Singleton pattern.

    Usage:
        bus = CommunicationBus.instance()
        bus.post(AgentMessage(sender="worker-1", content="Task T1 done"))
        msgs = bus.read("architecture-1")  # read all messages
        status = bus.get_agent_status("worker-1")
    """

    _instance: Optional["CommunicationBus"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._messages: List[AgentMessage] = []
        self._agent_status: Dict[str, Dict[str, Any]] = {}
        self._group_members: Dict[str, List[str]] = {}
        self._read_positions: Dict[str, int] = {}  # agent -> last read index
        self._checkpoint_file: Optional[Path] = None
        self._subscribers: Dict[str, List[Callable]] = {}  # agent -> callback list
        self._lock = threading.RLock()

    @classmethod
    def instance(cls) -> "CommunicationBus":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        with cls._lock:
            cls._instance = None

    def enable_checkpoint(self, path: str):
        """Enable persistence to disk for ultra-long tasks."""
        self._checkpoint_file = Path(path)
        self._checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
        if self._checkpoint_file.exists():
            self._load_checkpoint()

    def _save_checkpoint(self):
        if not self._checkpoint_file:
            return
        try:
            data = {
                "messages": [
                    {"sender": m.sender, "receiver": m.receiver, "msg_type": m.msg_type,
                     "content": m.content[:5000], "timestamp": m.timestamp, "metadata": m.metadata}
                    for m in self._messages[-500:]  # keep last 500
                ],
                "agent_status": self._agent_status,
                "group_members": self._group_members,
                "read_positions": self._read_positions,
            }
            with open(self._checkpoint_file, "w") as f:
                json.dump(data, f)
        except Exception as e:
            logger.warning("Checkpoint save failed: %s", e)

    def _load_checkpoint(self):
        if not self._checkpoint_file or not self._checkpoint_file.exists():
            return
        try:
            with open(self._checkpoint_file) as f:
                data = json.load(f)
            self._messages = [AgentMessage(**m) for m in data.get("messages", [])]
            self._agent_status = data.get("agent_status", {})
            self._group_members = data.get("group_members", {})
            self._read_positions = data.get("read_positions", {})
            logger.info("Loaded %d messages from checkpoint", len(self._messages))
        except Exception as e:
            logger.warning("Checkpoint load failed: %s", e)

    def post(self, msg: AgentMessage):
        """Post a message to the bus. Broadcast or directed."""
        with self._lock:
            self._messages.append(msg)
            if msg.sender not in self._read_positions:
                self._read_positions[msg.sender] = len(self._messages)

            # Notify subscribers
            if msg.receiver and msg.receiver in self._subscribers:
                for cb in self._subscribers.get(msg.receiver, []):
                    try:
                        cb(msg)
                    except Exception:
                        pass
            # Broadcast to all subscribers
            for agent_id, callbacks in self._subscribers.items():
                if not msg.receiver or msg.receiver == agent_id:
                    for cb in callbacks:
                        try:
                            cb(msg)
                        except Exception:
                            pass

            # Auto-save checkpoint every 50 messages
            if len(self._messages) % 50 == 0:
                self._save_checkpoint()

    def read(self, agent_id: str, since: Optional[int] = None) -> List[AgentMessage]:
        """Read messages for an agent. Returns new messages since last read."""
        with self._lock:
            last_read = since if since is not None else self._read_positions.get(agent_id, 0)
            new_msgs = self._messages[last_read:]
            self._read_positions[agent_id] = len(self._messages)
            return new_msgs

    def query(self, agent_id: str, msg_type: Optional[str] = None,
              sender: Optional[str] = None, limit: int = 50) -> List[AgentMessage]:
        """Query messages with filters."""
        with self._lock:
            msgs = self._messages
            if msg_type:
                msgs = [m for m in msgs if m.msg_type == msg_type]
            if sender:
                msgs = [m for m in msgs if m.sender == sender]
            return msgs[-limit:]

    def update_agent_status(self, agent_id: str, status: Dict[str, Any]):
        """Update an agent's status for others to see."""
        with self._lock:
            status["updated_at"] = time.time()
            self._agent_status[agent_id] = status
            self._save_checkpoint()

    def get_agent_status(self, agent_id: str) -> Dict[str, Any]:
        """Get an agent's status."""
        return self._agent_status.get(agent_id, {})

    def get_all_status(self) -> Dict[str, Dict[str, Any]]:
        """Get all agents' statuses."""
        return dict(self._agent_status)

    def get_group_status(self, group_name: str) -> Dict[str, Any]:
        """Get aggregated status for a group."""
        members = self._group_members.get(group_name, [])
        statuses = {aid: self._agent_status.get(aid, {}) for aid in members}
        working = sum(1 for s in statuses.values() if s.get("state") == "working")
        idle = sum(1 for s in statuses.values() if s.get("state") == "idle")
        dead = sum(1 for s in statuses.values() if s.get("state") == "dead")
        return {
            "group": group_name,
            "members": len(members),
            "working": working,
            "idle": idle,
            "dead": dead,
            "agent_statuses": statuses,
        }

    def register_group(self, group_name: str, agent_ids: List[str]):
        """Register agents to a group."""
        self._group_members[group_name] = agent_ids

    def subscribe(self, agent_id: str, callback: Callable):
        """Subscribe to message notifications."""
        if agent_id not in self._subscribers:
            self._subscribers[agent_id] = []
        self._subscribers[agent_id].append(callback)

    def negotiate_join(self, agent_id: str, target_group: str) -> str:
        """Agent requests to join a group. Returns 'accepted' or 'rejected'."""
        self.post(AgentMessage(
            sender=agent_id, receiver=target_group, msg_type="negotiate",
            content=f"Agent {agent_id} requests to join group {target_group}",
        ))
        # Auto-accept for now — in future, group members vote
        return "accepted"

    def get_latest_status_snapshot(self) -> Dict[str, Any]:
        """Get a snapshot of the entire communication network."""
        with self._lock:
            emergency_calls = [m for m in self._messages if m.msg_type == "emergency"]
            return {
                "total_messages": len(self._messages),
                "active_agents": len(self._agent_status),
                "emergency_calls": len(emergency_calls),
                "groups": {
                    g: self.get_group_status(g)
                    for g in self._group_members
                },
                "recent_messages": [
                    {"sender": m.sender, "msg_type": m.msg_type, "content": m.content[:100]}
                    for m in self._messages[-5:]
                ],
            }

    # ── Emergency call system ──
    def emergency_call(self, caller_id: str, description: str) -> str:
        """An agent requests help. Returns 'pending' until responded to."""
        msg = AgentMessage(
            sender=caller_id, receiver="", msg_type="emergency",
            content=f"EMERGENCY: {caller_id} needs help: {description}",
            metadata={"status": "pending", "description": description},
        )
        self.post(msg)
        self.update_agent_status(caller_id, {"state": "emergency", "emergency_msg": description})
        logger.warning("EMERGENCY CALL from %s: %s", caller_id, description)
        return "pending"

    def respond_to_emergency(self, responder_id: str, caller_id: str, decision: str, reason: str = ""):
        """Another agent responds to an emergency call. 'approved' or 'rejected'."""
        # Find the emergency message
        for i, m in enumerate(self._messages):
            if m.sender == caller_id and m.msg_type == "emergency" and m.metadata.get("status") == "pending":
                self._messages[i].metadata["status"] = decision
                self._messages[i].metadata["responder"] = responder_id
                self._messages[i].metadata["reason"] = reason
                break
        self.post(AgentMessage(
            sender=responder_id, receiver=caller_id, msg_type="emergency_response",
            content=f"{responder_id} {decision} emergency call from {caller_id}: {reason}",
        ))
        if decision == "approved":
            self.update_agent_status(caller_id, {"state": "working", "helped_by": responder_id})
        else:
            self.update_agent_status(caller_id, {"state": "idle", "emergency_msg": ""})

    def get_emergency_calls(self, status: str = "pending") -> List[AgentMessage]:
        """Get all emergency calls with a given status."""
        return [m for m in self._messages if m.msg_type == "emergency" and m.metadata.get("status") == status]

    def summarize_for_agent(self, agent_id: str, max_tokens: int = 2000) -> str:
        """Generate a summary of the communication bus for an agent."""
        msgs = self.read(agent_id)
        if not msgs:
            return "No new messages."

        summary_parts = []
        for m in msgs[-20:]:
            kind = m.msg_type.upper()
            summary_parts.append(f"[{kind}] {m.sender}: {m.content[:200]}")

        statuses = self.get_all_status()
        working = [aid for aid, s in statuses.items() if s.get("state") == "working"]
        idle = [aid for aid, s in statuses.items() if s.get("state") == "idle"]
        dead = [aid for aid, s in statuses.items() if s.get("state") == "dead"]

        summary = (
            f"Network Status: {len(working)} working, {len(idle)} idle, {len(dead)} dead\n"
            f"Recent messages ({len(msgs)}):\n"
            + "\n".join(summary_parts)
        )
        return summary[:max_tokens]


# Global singleton accessor
def get_bus() -> CommunicationBus:
    return CommunicationBus.instance()