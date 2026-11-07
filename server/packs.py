from functools import lru_cache

import yaml

from .config import PACKS_DIR
from .schemas import Pack


@lru_cache
def all_packs() -> dict[str, Pack]:
    packs = {}
    for path in sorted(PACKS_DIR.glob("*.yaml")):
        pack = Pack.model_validate(yaml.safe_load(path.read_text()))
        packs[pack.id] = pack
    return packs


def get_pack(pack_id: str) -> Pack:
    try:
        return all_packs()[pack_id]
    except KeyError:
        raise KeyError(f"Unknown pack '{pack_id}'. Add packs/{pack_id}.yaml") from None
