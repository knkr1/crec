import requests
import importlib.metadata
from typing import Optional, Tuple

def get_current_version() -> str:
    """Get the current installed version."""
    try:
        return importlib.metadata.version("crec")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"

def get_latest_version() -> Optional[str]:
    """Get the latest version from PyPI."""
    try:
        response = requests.get("https://pypi.org/pypi/crec/json", timeout=5)
        if response.status_code == 200:
            return response.json()["info"]["version"]
        return None
    except:
        return None

def check_version() -> Tuple[bool, str, str]:
    """
    Check if a new version is available.
    Returns: (is_update_available, current_version, latest_version)
    """
    current = get_current_version()
    latest = get_latest_version()
    
    if current == "unknown" or latest is None:
        return False, current, latest or "unknown"
    
    # Split versions into components and compare
    current_parts = [int(x) for x in current.split('.')]
    latest_parts = [int(x) for x in latest.split('.')]
    
    # Compare each component
    for i in range(max(len(current_parts), len(latest_parts))):
        current_part = current_parts[i] if i < len(current_parts) else 0
        latest_part = latest_parts[i] if i < len(latest_parts) else 0
        
        if latest_part > current_part:
            return True, current, latest
        elif latest_part < current_part:
            return False, current, latest
    
    return False, current, latest 