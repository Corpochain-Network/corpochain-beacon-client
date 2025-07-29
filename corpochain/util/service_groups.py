from __future__ import annotations

from typing import Generator, KeysView

SERVICES_FOR_GROUP = {
    "all": [
        "corpochain_harvester",
        "corpochain_timelord_launcher",
        "corpochain_timelord",
        "corpochain_farmer",
        "corpochain_beacon",
    ],
    "beacon": ["corpochain_beacon"],
    "harvester": ["corpochain_harvester"],
    "farmer": ["corpochain_harvester", "corpochain_farmer", "corpochain_beacon"],
    "farmer-only": ["corpochain_farmer"],
    "timelord": ["corpochain_timelord_launcher", "corpochain_timelord", "corpochain_beacon"],
    "timelord-only": ["corpochain_timelord"],
    "timelord-launcher-only": ["corpochain_timelord_launcher"],
    "introducer": ["corpochain_introducer"],
    "crawler": ["corpochain_crawler"],
    "seeder": ["corpochain_crawler", "corpochain_seeder"],
    "seeder-only": ["corpochain_seeder"],
}


def all_groups() -> KeysView[str]:
    return SERVICES_FOR_GROUP.keys()


def services_for_groups(groups) -> Generator[str, None, None]:
    for group in groups:
        for service in SERVICES_FOR_GROUP[group]:
            yield service


def validate_service(service: str) -> bool:
    return any(service in _ for _ in SERVICES_FOR_GROUP.values())
