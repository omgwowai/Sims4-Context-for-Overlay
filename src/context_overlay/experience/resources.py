"""Load rules from source or a bytecode-only ts4script using the same hashes."""

from functools import lru_cache
import hashlib
import json
import pkgutil

PACKAGE = "context_overlay.experience"
FILES = ("filter_events.py", "event_sequence.py", "experience_policy.py", "experience_labels.py",
         "experience_digest.py", "experience_view.py", "experience_recap.py", "experience_quality.py",
         "resources.py", "experience_resources.json", "experience_labels.json")


@lru_cache(maxsize=1)
def implementation_hashes():
    try:
        manifest = pkgutil.get_data(PACKAGE, "core_manifest.json")
    except (OSError, IOError):
        manifest = None
    if manifest is not None:
        result = json.loads(manifest.decode("utf-8"))
        if set(result) != set(FILES):
            raise ValueError("Incomplete experience core manifest")
        return result
    # Source checkout only. A bytecode-only build without its manifest fails.
    return {name: hashlib.sha256(pkgutil.get_data(PACKAGE, name)).hexdigest() for name in FILES}


def resource_bytes(name):
    data = pkgutil.get_data(PACKAGE, name)
    if data is None or hashlib.sha256(data).hexdigest() != implementation_hashes().get(name):
        raise ValueError("Missing or modified experience resource: " + name)
    return data
