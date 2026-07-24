"""Platform Dev Tools - Cross-platform development tools for Linux, Windows, HarmonyOS, Android.

Provides wrappers for platform-specific build tools, SDK managers, and device interaction.
All tools are MIT-licensed or similar permissive licenses.
"""
import json, logging, subprocess, os, shutil
from typing import Dict, Optional
from tools.registry import registry

logger = logging.getLogger(__name__)

PLATFORM_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["build", "run", "package", "sign", "deploy", "sdk", "info", "cross-compile", "adb"],
            "description": "Platform action to perform",
        },
        "platform": {
            "type": "string",
            "enum": ["linux", "windows", "harmonyos", "android", "auto"],
            "description": "Target platform",
            "default": "auto",
        },
        "command": {"type": "string", "description": "Build command or script to run"},
        "source": {"type": "string", "description": "Source directory or file"},
        "output": {"type": "string", "description": "Output directory or file"},
        "arch": {"type": "string", "description": "Target architecture (arm64, x86_64, etc.)"},
        "args": {"type": "array", "items": {"type": "string"}, "description": "Additional arguments"},
        "device": {"type": "string", "description": "Device serial (for adb)"},
    },
    "required": ["action"],
}


def _check_tool(tool_name: str) -> bool:
    """Check if a tool is available."""
    return shutil.which(tool_name) is not None


def _run_cmd(cmd: list, timeout: int = 120, cwd: Optional[str] = None) -> Dict:
    """Run a command and return structured output."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
                                shell=False if isinstance(cmd, list) else True)
        return {"ok": result.returncode == 0, "stdout": result.stdout[:50000],
                "stderr": result.stderr[:5000] if result.stderr else "", "exit_code": result.returncode}
    except FileNotFoundError:
        return {"ok": False, "error": f"Command not found: {cmd[0] if isinstance(cmd, list) else cmd}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Command timed out after {timeout}s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _detect_platform() -> str:
    """Auto-detect current platform."""
    if os.name == "nt":
        return "windows"
    if os.path.exists("/system/build.prop") or "ANDROID_ROOT" in os.environ:
        return "android"
    if os.path.exists("/system/app") and os.path.exists("/system/lib64"):
        return "harmonyos"
    return "linux"


def _handle_platform(args: dict) -> str:
    action = args.get("action", "")
    platform = args.get("platform", "auto")
    if platform == "auto":
        platform = _detect_platform()

    if action == "info":
        info = {
            "platform": platform,
            "os": os.name,
            "arch": os.uname().machine if hasattr(os, "uname") else "unknown",
            "python": os.sys.version,
            "tools": {},
        }
        tool_checks = {
            "linux": ["gcc", "g++", "make", "cmake", "cargo", "rustc", "go", "node", "npm", "docker"],
            "android": ["aapt", "adb", "dx", "zipalign", "apksigner", "buildozer", "gradle"],
            "harmonyos": ["hdc", "ark", "ace", "hvigor", "ohpm"],
            "windows": ["cl", "msbuild", "dotnet", "powershell"],
        }
        for plat, tools in tool_checks.items():
            info["tools"][plat] = {t: _check_tool(t) for t in tools}
        return json.dumps(info, indent=2)

    elif action == "build":
        source = args.get("source", ".")
        cmd_str = args.get("command", "")
        if cmd_str:
            return json.dumps(_run_cmd(cmd_str.split() if isinstance(cmd_str, str) else cmd_str, cwd=source))

        # Auto-detect build system
        if os.path.exists(os.path.join(source, "Cargo.toml")):
            return json.dumps(_run_cmd(["cargo", "build", "--release"], cwd=source))
        if os.path.exists(os.path.join(source, "go.mod")):
            return json.dumps(_run_cmd(["go", "build", "-o", args.get("output", "app")], cwd=source))
        if os.path.exists(os.path.join(source, "package.json")):
            return json.dumps(_run_cmd(["npm", "run", "build"], cwd=source))
        if os.path.exists(os.path.join(source, "Makefile")):
            return json.dumps(_run_cmd(["make"], cwd=source))
        if os.path.exists(os.path.join(source, "CMakeLists.txt")):
            build_dir = os.path.join(source, "build")
            os.makedirs(build_dir, exist_ok=True)
            subprocess.run(["cmake", source, "-B", build_dir], capture_output=True, cwd=source)
            return json.dumps(_run_cmd(["cmake", "--build", build_dir], cwd=source))
        return json.dumps({"error": "No recognized build system found. Use 'command' to specify."})

    elif action == "run":
        cmd_str = args.get("command", "")
        if not cmd_str:
            return json.dumps({"error": "command is required"})
        return json.dumps(_run_cmd(cmd_str.split() if isinstance(cmd_str, str) else cmd_str))

    elif action == "package":
        if platform == "android":
            source = args.get("source", ".")
            if os.path.exists(os.path.join(source, "buildozer.spec")):
                return json.dumps(_run_cmd(["buildozer", "android", "debug"], cwd=source))
            return json.dumps({"error": "Android packaging requires buildozer.spec or gradle"})
        elif platform == "windows":
            return json.dumps({"error": "Use 'build' action with MSBuild or dotnet publish"})
        elif platform == "linux":
            return json.dumps(_run_cmd(["tar", "-czf", args.get("output", "app.tar.gz"), args.get("source", ".")]))
        return json.dumps({"error": f"Packaging not supported for {platform}"})

    elif action == "sign":
        if platform == "android":
            apk = args.get("output", "")
            if not apk:
                return json.dumps({"error": "output (APK path) is required"})
            return json.dumps(_run_cmd(["apksigner", "sign", "--ks", "debug.keystore", apk]))
        return json.dumps({"error": f"Signing not supported for {platform}"})

    elif action == "adb":
        device = args.get("device", "")
        cmd_args = args.get("args", [])
        cmd = ["adb"]
        if device:
            cmd += ["-s", device]
        cmd += cmd_args if cmd_args else ["devices"]
        return json.dumps(_run_cmd(cmd))

    elif action == "cross-compile":
        arch = args.get("arch", "arm64")
        source = args.get("source", ".")
        if os.path.exists(os.path.join(source, "Cargo.toml")):
            target_map = {"arm64": "aarch64-linux-android", "x86_64": "x86_64-linux-android",
                          "arm": "armv7-linux-androideabi"}
            target = target_map.get(arch, arch)
            return json.dumps(_run_cmd(["cargo", "build", "--release", "--target", target], cwd=source))
        if os.path.exists(os.path.join(source, "go.mod")):
            go_map = {"arm64": "arm64", "x86_64": "amd64", "arm": "arm"}
            go_arch = go_map.get(arch, arch)
            return json.dumps(_run_cmd(["env", "GOOS=linux", f"GOARCH={go_arch}", "go", "build"], cwd=source))
        return json.dumps({"error": "Cross-compile supports Cargo and Go projects"})

    return json.dumps({"error": f"Unknown action: {action}"})


def _check_platform_reqs() -> bool:
    return True


registry.register(
    name="platform",
    toolset="dev",
    schema=PLATFORM_SCHEMA,
    handler=_handle_platform,
    check_fn=_check_platform_reqs,
    emoji="",
    max_result_size_chars=50000,
)