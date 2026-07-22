from __future__ import annotations

import logging

from .store import Store

logger = logging.getLogger(__name__)

_N_RUNS = 3


def check_alarms(store: Store, cfg: dict) -> list[str]:
    """Return list of alarm messages for sources that failed health checks in last 3 runs."""
    thresholds: dict = cfg.get("health_min_count", {})
    alarms = []
    for source, _threshold in thresholds.items():
        consecutive = store.consecutive_below_threshold(source, n=_N_RUNS)
        if consecutive >= _N_RUNS:
            msg = (
                f"⚠️ Quelle '{source}' liefert seit {_N_RUNS} Laeufen"
                " zu wenig Output. Bitte pruefen."
            )
            logger.warning(msg)
            alarms.append(msg)
    return alarms
