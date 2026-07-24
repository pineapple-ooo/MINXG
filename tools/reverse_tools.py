"""Reverse Engineering Tools - MIT-licensed RE tools for all platforms.

Wraps popular open-source reverse engineering tools:
  - APKTool (Apache 2.0) - APK reverse engineering
  - Jadx (Apache 2.0) - DEX to Java decompiler
  - Frida (wxWindows) - Dynamic instrumentation
  - radare2 (LGPL) - Multi-architecture disassembler
  - Ghidra (Apache 2.0) - NSA RE framework (headless mode)
  - binwalk (MIT) - Firmware analysis
  - strings, objdump, nm, readelf - GNU binutils

All tools are MIT/Apache 2.0/LGPL licensed.
"""
import json, logging, subprocess, os, shutil, tempfile
from typing import Dict, Optional
from pathlib import Path
from tools.registry import registry

logger = logging.getLogger(__name__)

RE_TOOLS = {
    "apktool": {"cmd": "apktool", "pkg": "apktool", "license": "Apache 2.0"},
    "jadx": {"cmd": "jadx", "pkg": "jadx", "license": "Apache 2.0"},
    "frida": {"cmd": "frida", "pkg": "frida-tools", "license": "wxWindows"},
    "radare2": {"cmd": "r2", "pkg": "radare2", "license": "LGPL"},
    "ghidra": {"cmd": "analyzeHeadless", "pkg": "ghidra", "license": "Apache 2.0"},
    "binwalk": {"cmd": "binwalk", "pkg": "binwalk", "license": "MIT"},
    "strings": {"cmd": "strings", "pkg": "binutils", "license": "GPL"},
    "objdump": {"cmd": "objdump", "pkg": "binutils", "license": "GPL"},
    "nm": {"cmd": "nm", "pkg": "binutils", "license": "GPL"},
    "readelf": {"cmd": "readelf", "pkg": "binutils", "license": "GPL"},
    "upx": {"cmd": "upx", "pkg": "upx-ucl", "license": "GPL"},
    "dex2jar": {"cmd": "d2j-dex2jar", "pkg": "dex2jar", "license": "Apache 2.0"},
}

REVERSE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["decompile", "disassemble", "analyze", "extract", "strings", "info", "check"],
            "description": "RE action: decompile APK/JAR, disassemble binary, analyze firmware, extract resources, get strings, file info, check tools",
        },
        "file": {"type": "string", "description": "Target file path"},
        "output": {"type": "string", "description": "Output directory", "default": "./re_output"},
        "tool": {
            "type": "string",
            "enum": ["auto", "jadx", "apktool", "radare2", "ghidra", "binwalk", "frida", "strings"],
            "description": "Tool to use (auto = auto-detect)",
            "default": "auto",
        },
        "platform": {
            "type": "string",
            "enum": ["auto", "linux", "windows", "android", "harmonyos"],
            "description": "Target platform for analysis",
            "default": "auto",
        },
        "extra_args": {"type": "array", "items": {"type": "string"}, "description": "Extra tool arguments"},
    },
    "required": ["action"],
}


def _check_tool_available(name: str) -> bool:
    """Check if a RE tool is installed."""
    return shutil.which(RE_TOOLS[name]["cmd"]) is not None


def _get_install_hint(name: str) -> str:
    info = RE_TOOLS[name]
    return f"{info['cmd']} not found. Install: pip install {info['pkg']} (License: {info['license']})"


def _run_re_tool(cmd: list, timeout: int = 300, cwd: Optional[str] = None) -> Dict:
    """Run a RE tool and return structured output."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return {"ok": result.returncode == 0, "stdout": result.stdout[:50000],
                "stderr": result.stderr[:5000] if result.stderr else "", "exit_code": result.returncode}
    except FileNotFoundError:
        return {"ok": False, "error": f"Tool not found: {cmd[0]}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Tool timed out after {timeout}s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _handle_reverse(args: dict) -> str:
    action = args.get("action", "")
    target_file = args.get("file", "")
    output_dir = args.get("output", "./re_output")
    tool = args.get("tool", "auto")

    if action == "check":
        available = {name: _check_tool_available(name) for name in RE_TOOLS}
        install_hints = {name: _get_install_hint(name) for name in RE_TOOLS if not available[name]}
        return json.dumps({"available": available, "install_hints": install_hints, "total_tools": len(RE_TOOLS)})

    if action == "info":
        if not target_file or not os.path.exists(target_file):
            return json.dumps({"error": "file is required and must exist"})
        info = {"file": target_file, "size": os.path.getsize(target_file)}
        # Check file type
        if _check_tool_available("strings"):
            result = subprocess.run(["file", target_file], capture_output=True, text=True)
            info["file_type"] = result.stdout.strip()
        return json.dumps(info)

    if action == "strings":
        if not target_file or not os.path.exists(target_file):
            return json.dumps({"error": "file is required"})
        if _check_tool_available("strings"):
            return json.dumps(_run_re_tool(["strings", "-n", "4", target_file]))
        return json.dumps({"error": _get_install_hint("strings")})

    if action == "decompile":
        if not target_file or not os.path.exists(target_file):
            return json.dumps({"error": "file is required"})

        ext = os.path.splitext(target_file)[1].lower()
        os.makedirs(output_dir, exist_ok=True)

        if ext == ".apk":
            if tool in ("auto", "apktool") and _check_tool_available("apktool"):
                return json.dumps(_run_re_tool(["apktool", "d", target_file, "-o", output_dir, "-f"]))
            if tool in ("auto", "jadx") and _check_tool_available("jadx"):
                return json.dumps(_run_re_tool(["jadx", "-d", output_dir, target_file]))
            return json.dumps({"error": f"No decompiler available. Install: {_get_install_hint('jadx')}"})

        if ext in (".jar", ".class", ".dex"):
            if _check_tool_available("jadx"):
                return json.dumps(_run_re_tool(["jadx", "-d", output_dir, target_file]))
            return json.dumps({"error": _get_install_hint("jadx")})

        if ext in (".so", ".dll", ".o", ".bin", ".elf"):
            if _check_tool_available("radare2"):
                return json.dumps(_run_re_tool(["r2", "-A", "-q", "-c", "aaa", "-c", "afl", "-c", "exit", target_file]))
            if _check_tool_available("objdump"):
                return json.dumps(_run_re_tool(["objdump", "-d", target_file]))
            return json.dumps({"error": _get_install_hint("radare2")})

        return json.dumps({"error": f"Unsupported file type: {ext}. Use 'disassemble' action directly."})

    if action == "disassemble":
        if not target_file or not os.path.exists(target_file):
            return json.dumps({"error": "file is required"})

        if _check_tool_available("radare2"):
            return json.dumps(_run_re_tool(
                ["r2", "-A", "-q", "-c", "aaa", "-c", "pdf", "-c", "exit", target_file]))
        if _check_tool_available("objdump"):
            return json.dumps(_run_re_tool(["objdump", "-d", target_file]))
        return json.dumps({"error": _get_install_hint("radare2")})

    if action == "extract":
        if not target_file or not os.path.exists(target_file):
            return json.dumps({"error": "file is required"})
        os.makedirs(output_dir, exist_ok=True)

        ext = os.path.splitext(target_file)[1].lower()
        if ext in (".apk", ".zip", ".jar"):
            import zipfile
            with zipfile.ZipFile(target_file, "r") as zf:
                zf.extractall(output_dir)
            return json.dumps({"ok": True, "extracted_to": output_dir, "files": len(os.listdir(output_dir))})

        if _check_tool_available("binwalk"):
            return json.dumps(_run_re_tool(["binwalk", "-e", "--directory", output_dir, target_file]))

        return json.dumps({"error": "No extraction tool available. Install binwalk: pip install binwalk"})

    if action == "analyze":
        if not target_file or not os.path.exists(target_file):
            return json.dumps({"error": "file is required"})

        results = {"file": target_file, "size": os.path.getsize(target_file)}

        # Run file type detection
        try:
            r = subprocess.run(["file", target_file], capture_output=True, text=True)
            results["file_type"] = r.stdout.strip()
        except Exception:
            pass

        # Run strings
        if _check_tool_available("strings"):
            r = subprocess.run(["strings", "-n", "6", target_file], capture_output=True, text=True)
            results["notable_strings"] = [s.strip() for s in r.stdout.split("\n")[:50] if len(s.strip()) > 10]

        # Run binwalk
        if _check_tool_available("binwalk"):
            r = subprocess.run(["binwalk", target_file], capture_output=True, text=True, timeout=60)
            results["firmware_analysis"] = r.stdout.strip()[:5000] if r.returncode == 0 else ""

        return json.dumps(results)

    return json.dumps({"error": f"Unknown action: {action}"})


def _check_reverse_reqs() -> bool:
    return True


registry.register(
    name="reverse",
    toolset="dev",
    schema=REVERSE_SCHEMA,
    handler=_handle_reverse,
    check_fn=_check_reverse_reqs,
    emoji="",
    max_result_size_chars=50000,
)