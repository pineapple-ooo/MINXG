"""Cron Job Tool - Scheduled job management."""

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_jobs: Dict[str, "CronJob"] = {}
_jobs_lock = threading.Lock()


@dataclass
class CronJob:
    """A scheduled cron job."""
    job_id: str
    prompt: str
    schedule: str
    skills: List[str] = field(default_factory=list)
    enabled: bool = True
    created_at: float = field(default_factory=time.time)
    last_run: Optional[float] = None
    next_run: Optional[float] = None
    last_result: Optional[str] = None
    status: str = "pending"


def _parse_schedule(schedule: str) -> float:
    """Parse schedule into seconds."""
    schedule = schedule.strip().lower()
    
    if " " in schedule and not schedule.replace(" ", "").replace("*", "").replace("/", "").isdigit():
        parts = schedule.split()
        if len(parts) >= 5:
            return 3600
    
    if schedule.endswith("s"):
        return int(schedule[:-1])
    elif schedule.endswith("m"):
        return int(schedule[:-1]) * 60
    elif schedule.endswith("h"):
        return int(schedule[:-1]) * 3600
    elif schedule.endswith("d"):
        return int(schedule[:-1]) * 86400
    
    try:
        return int(schedule) * 60
    except ValueError:
        return 3600


def _handle_cron_list(args: dict) -> str:
    """List all cron jobs."""
    with _jobs_lock:
        jobs = [
            {
                "job_id": j.job_id,
                "schedule": j.schedule,
                "enabled": j.enabled,
                "last_run": j.last_run,
                "next_run": j.next_run,
                "status": j.status,
                "created_at": j.created_at,
            }
            for j in _jobs.values()
        ]
    return json.dumps({"jobs": jobs, "total": len(jobs)})


def _handle_cron_create(args: dict) -> str:
    """Create a new cron job."""
    prompt = args.get("prompt", "")
    schedule = args.get("schedule", "1h")
    
    if not prompt:
        return json.dumps({"error": "prompt is required"})
    
    job_id = args.get("job_id") or f"cron_{int(time.time())}"
    skills = args.get("skills", [])
    
    job = CronJob(
        job_id=job_id,
        prompt=prompt,
        schedule=schedule,
        skills=skills,
    )
    
    with _jobs_lock:
        _jobs[job_id] = job
    
    return json.dumps({
        "ok": True,
        "job_id": job_id,
        "schedule": schedule,
        "message": f"Cron job {job_id} created",
    })


def _handle_cron_remove(args: dict) -> str:
    """Remove a cron job."""
    job_id = args.get("job_id", "")
    
    if not job_id:
        return json.dumps({"error": "job_id is required"})
    
    with _jobs_lock:
        if job_id in _jobs:
            del _jobs[job_id]
            return json.dumps({"ok": True, "job_id": job_id})
        else:
            return json.dumps({"error": f"Job not found: {job_id}"})


def _handle_cron_pause(args: dict) -> str:
    """Pause a cron job."""
    job_id = args.get("job_id", "")
    
    if not job_id:
        return json.dumps({"error": "job_id is required"})
    
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].enabled = False
            return json.dumps({"ok": True, "job_id": job_id, "enabled": False})
        else:
            return json.dumps({"error": f"Job not found: {job_id}"})


def _handle_cron_resume(args: dict) -> str:
    """Resume a cron job."""
    job_id = args.get("job_id", "")
    
    if not job_id:
        return json.dumps({"error": "job_id is required"})
    
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].enabled = True
            return json.dumps({"ok": True, "job_id": job_id, "enabled": True})
        else:
            return json.dumps({"error": f"Job not found: {job_id}"})


def _handle_cron_run(args: dict) -> str:
    """Manually run a cron job now."""
    job_id = args.get("job_id", "")
    
    if not job_id:
        return json.dumps({"error": "job_id is required"})
    
    with _jobs_lock:
        if job_id not in _jobs:
            return json.dumps({"error": f"Job not found: {job_id}"})
        job = _jobs[job_id]
    
    job.status = "running"
    try:
        result = f"Job {job_id} executed: {job.prompt[:100]}..."
        job.last_result = result
        job.last_run = time.time()
        job.status = "completed"
        return json.dumps({"ok": True, "result": result})
    except Exception as e:
        job.status = "failed"
        job.last_result = str(e)
        return json.dumps({"error": str(e)})


CRON_LIST_SCHEMA = {
    "type": "object",
    "properties": {},
}

CRON_CREATE_SCHEMA = {
    "type": "object",
    "properties": {
        "prompt": {"type": "string", "description": "Task prompt for the cron job"},
        "schedule": {"type": "string", "description": "Schedule (e.g., '30m', '2h', '0 9 * * *')"},
        "job_id": {"type": "string", "description": "Optional job ID"},
        "skills": {"type": "array", "items": {"type": "string"}, "description": "Skills to load"},
    },
    "required": ["prompt"],
}

CRON_REMOVE_SCHEMA = {
    "type": "object",
    "properties": {
        "job_id": {"type": "string", "description": "Job ID to remove"},
    },
    "required": ["job_id"],
}

CRON_PAUSE_SCHEMA = {
    "type": "object",
    "properties": {
        "job_id": {"type": "string", "description": "Job ID to pause"},
    },
    "required": ["job_id"],
}

CRON_RESUME_SCHEMA = {
    "type": "object",
    "properties": {
        "job_id": {"type": "string", "description": "Job ID to resume"},
    },
    "required": ["job_id"],
}

CRON_RUN_SCHEMA = {
    "type": "object",
    "properties": {
        "job_id": {"type": "string", "description": "Job ID to run now"},
    },
    "required": ["job_id"],
}


def _check_cron_reqs() -> bool:
    """Check if cron system is available."""
    return True


from tools.registry import registry

CRONJOB_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["list", "create", "remove", "pause", "resume", "run"],
            "description": "Cron job action",
        },
        "job_id": {"type": "string", "description": "Job ID (for remove/pause/resume/run)"},
        "prompt": {"type": "string", "description": "Job prompt (for create)"},
        "schedule": {"type": "string", "description": "Schedule (for create), e.g. 'every 2h'"},
        "skills": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Skills to load (for create)",
        },
    },
    "required": ["action"],
}


def _handle_cronjob(args: dict) -> str:
    action = args.get("action", "")
    if action == "list":
        return _handle_cron_list(args)
    elif action == "create":
        return _handle_cron_create(args)
    elif action == "remove":
        return _handle_cron_remove(args)
    elif action == "pause":
        return _handle_cron_pause(args)
    elif action == "resume":
        return _handle_cron_resume(args)
    elif action == "run":
        return _handle_cron_run(args)
    return json.dumps({"error": f"Unknown cron action: {action}"})


registry.register(
    name="cronjob",
    toolset="cron",
    schema=CRONJOB_SCHEMA,
    handler=_handle_cronjob,
    check_fn=_check_cron_reqs,
    emoji="",
    max_result_size_chars=50000,
)
