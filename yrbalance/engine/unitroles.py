"""
User-defined unit role lists — "my rush units", "my bombardment units", etc.

Pure sidecar storage (~/.yrbalancetool/unit_roles.json): a mapping of role name →
ordered list of unit ids the user curated. Auto-suggestion (which units fit a role)
and the actual AITrigger generation live in wavegen/role_bundles.py, which can reach
the wavegen role detector + generator weights; this module stays I/O-only so the
engine keeps no dependency on wavegen.
"""

import os
import json

# Display names of the roles offered out of the box. Each maps to a canonical
# generator key in wavegen.role_bundles.ROLE_MAP.
DEFAULT_ROLES = [
    "Rush", "General Attack", "Anti-Infantry", "Anti-Vehicle",
    "Anti-Building", "Anti-Defenses", "Miner Killer", "Anti-Air",
]


def roles_path():
    return os.path.join(os.path.expanduser("~"), ".yrbalancetool", "unit_roles.json")


def load_roles(path=None):
    """Return an ordered dict {role_name: [unit_ids]}.

    On the very first load (no sidecar yet) the default role names are seeded with
    empty lists so the tab has something to show. Once a sidecar exists its contents
    are returned verbatim — so deleting or renaming a default role sticks."""
    path = path or roles_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (FileNotFoundError, ValueError):
        return {name: [] for name in DEFAULT_ROLES}
    return {name: [str(u) for u in units]
            for name, units in raw.get("roles", {}).items()}


def save_roles(roles, path=None):
    path = path or roles_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"roles": {k: list(v) for k, v in roles.items()}}, fh, indent=1)
    os.replace(tmp, path)


def set_role_units(name, units, path=None):
    """Replace the unit list of a role (creating it if needed), de-duplicated and
    order-preserving."""
    roles = load_roles(path)
    seen, clean = set(), []
    for u in units:
        k = str(u)
        if k.lower() not in seen:
            seen.add(k.lower())
            clean.append(k)
    roles[name] = clean
    save_roles(roles, path)
    return roles


def create_role(name, path=None):
    name = (name or "").strip()
    roles = load_roles(path)
    if name and name not in roles:
        roles[name] = []
        save_roles(roles, path)
    return roles


def delete_role(name, path=None):
    roles = load_roles(path)
    roles.pop(name, None)
    save_roles(roles, path)
    return roles


def rename_role(old, new, path=None):
    """Rename a role, preserving order. Returns the updated dict, or None if the new
    name already exists."""
    new = (new or "").strip()
    roles = load_roles(path)
    if not new or new == old or new in roles or old not in roles:
        return None if (new in roles and new != old) else roles
    out = {}
    for k, v in roles.items():
        out[new if k == old else k] = v
    save_roles(out, path)
    return out
