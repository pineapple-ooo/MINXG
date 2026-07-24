"""Git Tools - Complete git operations for all platforms."""
import json, logging, subprocess, os
from typing import Dict, Optional
from tools.registry import registry

logger = logging.getLogger(__name__)

GIT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["clone", "commit", "push", "pull", "branch", "merge", "status", "log", "diff", "checkout", "stash", "rebase", "init", "remote", "tag"],
            "description": "Git action to perform",
        },
        "repo": {"type": "string", "description": "Repository URL (for clone/remote)"},
        "path": {"type": "string", "description": "Local path or directory"},
        "message": {"type": "string", "description": "Commit message (for commit/merge)"},
        "branch": {"type": "string", "description": "Branch name (for branch/checkout/merge/push/pull)"},
        "files": {"type": "array", "items": {"type": "string"}, "description": "Files to add (for commit, default: all)"},
        "remote": {"type": "string", "description": "Remote name (for push/pull/fetch/remote)", "default": "origin"},
        "tag": {"type": "string", "description": "Tag name (for tag)"},
        "force": {"type": "boolean", "description": "Force operation (for push/branch delete)", "default": False},
        "no_verify": {"type": "boolean", "description": "Skip pre-commit hooks (for commit)", "default": False},
    },
    "required": ["action"],
}


def _run_git(args: list, cwd: Optional[str] = None, timeout: int = 60) -> Dict:
    """Run a git command and return structured output."""
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True, text=True, timeout=timeout, cwd=cwd,
        )
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout.strip()[:20000],
            "stderr": result.stderr.strip()[:5000] if result.stderr else "",
            "exit_code": result.returncode,
        }
    except FileNotFoundError:
        return {"ok": False, "error": "Git not found. Install with: pkg install git"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Git command timed out after {timeout}s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _handle_git(args: dict) -> str:
    action = args.get("action", "")
    cwd = args.get("path") or os.getcwd()

    if action == "clone":
        repo = args.get("repo", "")
        if not repo:
            return json.dumps({"error": "repo URL is required"})
        target = args.get("path") or "."
        branch = args.get("branch", "")
        cmd = ["clone", repo]
        if branch:
            cmd += ["-b", branch]
        cmd.append(target)
        return json.dumps(_run_git(cmd, cwd=os.getcwd()))

    elif action == "commit":
        msg = args.get("message", "")
        if not msg:
            return json.dumps({"error": "commit message is required"})
        files = args.get("files") or "."
        cmd = ["commit", "-m", msg]
        if args.get("no_verify"):
            cmd.append("--no-verify")
        if isinstance(files, list):
            subprocess.run(["git", "add"] + files, cwd=cwd, capture_output=True)
        else:
            subprocess.run(["git", "add", "."], cwd=cwd, capture_output=True)
        return json.dumps(_run_git(cmd, cwd=cwd))

    elif action == "push":
        remote = args.get("remote", "origin")
        branch = args.get("branch", "")
        cmd = ["push", remote]
        if branch:
            cmd.append(branch)
        if args.get("force"):
            cmd.append("--force")
        return json.dumps(_run_git(cmd, cwd=cwd))

    elif action == "pull":
        remote = args.get("remote", "origin")
        branch = args.get("branch", "")
        cmd = ["pull", remote]
        if branch:
            cmd.append(branch)
        return json.dumps(_run_git(cmd, cwd=cwd))

    elif action == "branch":
        branch = args.get("branch", "")
        if branch:
            force = args.get("force", False)
            cmd = ["branch", "-D" if force else "-d", branch] if not args.get("branch", "").startswith("new") else ["checkout", "-b", branch]
            if args.get("force") and "checkout" not in cmd:
                cmd = ["branch", "-D", branch]
            return json.dumps(_run_git(cmd, cwd=cwd))
        return json.dumps(_run_git(["branch", "-a"], cwd=cwd))

    elif action == "merge":
        branch = args.get("branch", "")
        if not branch:
            return json.dumps({"error": "branch to merge is required"})
        msg = args.get("message", "")
        cmd = ["merge", branch]
        if msg:
            cmd += ["-m", msg]
        return json.dumps(_run_git(cmd, cwd=cwd))

    elif action == "checkout":
        branch = args.get("branch", "")
        if not branch:
            return json.dumps({"error": "branch name is required"})
        return json.dumps(_run_git(["checkout", branch], cwd=cwd))

    elif action == "stash":
        return json.dumps(_run_git(["stash"], cwd=cwd))

    elif action == "rebase":
        branch = args.get("branch", "")
        if not branch:
            return json.dumps({"error": "branch name is required"})
        return json.dumps(_run_git(["rebase", branch], cwd=cwd))

    elif action == "init":
        return json.dumps(_run_git(["init"], cwd=cwd))

    elif action == "remote":
        repo = args.get("repo", "")
        remote = args.get("remote", "origin")
        if repo:
            return json.dumps(_run_git(["remote", "add", remote, repo], cwd=cwd))
        return json.dumps(_run_git(["remote", "-v"], cwd=cwd))

    elif action == "tag":
        tag = args.get("tag", "")
        if not tag:
            return json.dumps(_run_git(["tag", "-l"], cwd=cwd))
        msg = args.get("message", "")
        cmd = ["tag", "-a", tag, "-m", msg] if msg else ["tag", tag]
        return json.dumps(_run_git(cmd, cwd=cwd))

    elif action == "status":
        return json.dumps(_run_git(["status", "--short"], cwd=cwd))

    elif action == "log":
        return json.dumps(_run_git(["log", "--oneline", "-20"], cwd=cwd))

    elif action == "diff":
        branch = args.get("branch", "")
        cmd = ["diff"]
        if branch:
            cmd.append(branch)
        return json.dumps(_run_git(cmd, cwd=cwd))

    return json.dumps({"error": f"Unknown git action: {action}"})


def _check_git_reqs() -> bool:
    return True


registry.register(
    name="git",
    toolset="dev",
    schema=GIT_SCHEMA,
    handler=_handle_git,
    check_fn=_check_git_reqs,
    emoji="",
    max_result_size_chars=20000,
)