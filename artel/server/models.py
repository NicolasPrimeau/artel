import uuid
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field

from ..store.db import norm_project as _norm_project


def new_id() -> str:
    return str(uuid.uuid4())


def _normalize_project_optional(v: str | None) -> str | None:
    return _norm_project(v)


def _normalize_project_required(v: str) -> str:
    normalized = _norm_project(v)
    if not normalized:
        raise ValueError("project name required")
    return normalized


ProjectName = Annotated[str | None, AfterValidator(_normalize_project_optional)]
ProjectNameRequired = Annotated[str, AfterValidator(_normalize_project_required)]


EntryType = Literal["memory", "doc", "directive", "skill"]
Scope = Literal["agent", "project"]
TaskStatus = Literal["open", "claimed", "completed", "failed"]
TaskCommentKind = Literal["comment", "claim", "unclaim", "complete", "fail"]
Priority = Literal["low", "normal", "high"]
LogLevel = Literal["info", "warning", "error"]


class MemoryWrite(BaseModel):
    type: EntryType = "memory"
    project: ProjectName = None
    scope: Scope = "project"
    content: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    parents: list[str] = []
    tags: list[str] = []
    expires_at: str | None = None


class MemoryPatch(BaseModel):
    content: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    tags: list[str] | None = None
    scope: Scope | None = None
    type: EntryType | None = None
    project: ProjectName = None


class MemoryEntry(BaseModel):
    id: str
    type: EntryType
    agent_id: str
    project: str | None
    scope: Scope
    content: str
    confidence: float
    parents: list[str]
    tags: list[str]
    created_at: str
    updated_at: str
    version: int
    expires_at: str | None = None
    origin: str | None = None
    distinct_reader_count: int = 0


class TaskCreate(BaseModel):
    title: str
    description: str = ""
    expected_outcome: str = ""
    project: ProjectName = None
    priority: Priority = "normal"
    assigned_to: str | None = None
    due_at: str | None = None
    tags: list[str] = []


class TaskEntry(BaseModel):
    id: str
    title: str
    description: str
    expected_outcome: str
    status: TaskStatus
    created_by: str
    assigned_to: str | None
    project: str | None
    priority: Priority
    due_at: str | None
    tags: list[str]
    created_at: str
    updated_at: str


class TaskUpdate(BaseModel):
    description: str | None = None
    append: bool = False
    title: str | None = None
    priority: Priority | None = None
    expected_outcome: str | None = None
    project: ProjectName = None
    tags: list[str] | None = None


class TaskAction(BaseModel):
    body: str = ""


class TaskCommentCreate(BaseModel):
    body: str


class TaskComment(BaseModel):
    id: str
    task_id: str
    agent_id: str
    kind: TaskCommentKind
    body: str
    created_at: str


class MessageSend(BaseModel):
    to: str
    subject: str = ""
    body: str


class MessageEntry(BaseModel):
    id: str
    from_agent: str
    to_agent: str
    subject: str
    body: str
    read: bool
    created_at: str


class EventEmit(BaseModel):
    type: str
    payload: dict = {}


class EventEntry(BaseModel):
    id: str
    type: str
    agent_id: str
    payload: dict
    created_at: str


class Participant(BaseModel):
    agent_id: str
    last_seen: str | None
    project: str | None = None
    active_task_id: str | None = None
    role: str = "agent"


class AgentRegister(BaseModel):
    agent_id: str
    project: ProjectName = None


class AgentSelfRegister(BaseModel):
    agent_id: str = "agent"
    project: ProjectName = None


class AgentRename(BaseModel):
    new_id: str


class AgentCreated(BaseModel):
    agent_id: str
    api_key: str
    project: str | None = None
    created_at: str
    role: str = "agent"
    last_seen: str | None = None
    active_task_id: str | None = None
    mcp_config: dict | None = None


class ProjectInfo(BaseModel):
    name: str
    agents: list[str]
    memory_count: int
    task_count: int
    last_activity: str | None


class FeedCreate(BaseModel):
    url: str
    name: str
    project: ProjectNameRequired
    tags: list[str] = []
    interval_min: int = Field(default=30, ge=1, le=1440)
    max_per_poll: int = Field(default=20, ge=1, le=100)


class FeedEntry(BaseModel):
    id: str
    agent_id: str
    project: str
    url: str
    name: str
    tags: list[str]
    interval_min: int
    max_per_poll: int
    last_fetched_at: str | None
    created_at: str


class FeedPatch(BaseModel):
    name: str | None = None
    tags: list[str] | None = None
    interval_min: int | None = Field(default=None, ge=1, le=1440)
    max_per_poll: int | None = Field(default=None, ge=1, le=100)


class BulkMemoryDelete(BaseModel):
    ids: list[str]


class ProjectCreate(BaseModel):
    name: ProjectNameRequired


class MeshTokenCreate(BaseModel):
    label: str | None = None
    project: ProjectName = None


class MeshTokenUpdate(BaseModel):
    label: str | None = None
    project: ProjectName = None


class MeshToken(BaseModel):
    id: str
    token: str
    label: str | None
    project: str | None
    created_by: str
    created_at: str


class PeerLinkCreate(BaseModel):
    peer_url: str
    peer_token: str
    project: ProjectName = None


class PeerLink(BaseModel):
    id: str
    peer_url: str
    project: str | None
    feed_id: str
    created_by: str
    created_at: str
    last_fetched_at: str | None = None


class HandoffPost(BaseModel):
    host: str = ""
    summary: str
    in_progress: list[str] = []
    next_steps: list[str] = []
    memory_refs: list[str] = []


class HandoffResponse(BaseModel):
    last_handoff: dict | None
    memory_delta: list[MemoryEntry]


class LogWrite(BaseModel):
    level: LogLevel = "info"
    source: str
    action: str
    message: str
    details: dict = {}


class LogEntry(BaseModel):
    id: str
    created_at: str
    level: LogLevel
    source: str
    action: str
    message: str
    details: dict


class DiscoveredPeer(BaseModel):
    instance_id: str
    url: str


class HandshakeRequest(BaseModel):
    initiator_url: str
    initiator_token: str
    project: ProjectName = None


class HandshakeResponse(BaseModel):
    token: str


class LinkDiscoveredRequest(BaseModel):
    instance_id: str
    project: ProjectName = None


class LeaseRequest(BaseModel):
    instance_id: str
    ttl_seconds: int = Field(default=120, ge=10, le=3600)


class LeaseResponse(BaseModel):
    granted: bool
    holder: str
    expires_at: str
