"""Read the OpenRouter key without persisting it or displaying its value.
Windows User/Machine environment registry fallback handles an already-open host.
"""
from __future__ import annotations
import os

NAME = "OPENROUTER_API_KEY"

def resolve_key():
    value = os.environ.get(NAME, "").strip()
    if value:
        return value, "process"
    return registry_key()

def registry_key():
    """Current Windows User/System value; a long-running host may hold a stale copy."""
    # Isolated tests set this so a key stored in the Windows registry is ignored.
    if os.name == "nt" and os.environ.get("LUNA_ROUTER_IGNORE_REGISTRY_KEY") != "1":
        try:
            import winreg
        except ImportError:
            return "", "missing"
        for scope, hive, path in (
            ("user_environment", winreg.HKEY_CURRENT_USER, r"Environment"),
            ("system_environment", winreg.HKEY_LOCAL_MACHINE,
             r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        ):
            try:
                with winreg.OpenKey(hive, path, 0, winreg.KEY_READ) as handle:
                    found, kind = winreg.QueryValueEx(handle, NAME)
                if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) and isinstance(found, str) and found.strip():
                    return found.strip(), scope
            except OSError:
                continue
    return "", "missing"

def key_status():
    key, source = resolve_key()
    return {"present": bool(key), "source": source}
