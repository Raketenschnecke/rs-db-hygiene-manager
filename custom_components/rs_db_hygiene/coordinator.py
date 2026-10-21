"""Verbindung zur App „RS DB Hygiene Manager“: Status abfragen (30 s, im Lauf 5 s) und Befehle senden."""
from __future__ import annotations

from datetime import timedelta
import logging
import time
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import bruecke
from .const import DOMAIN, EVENT_LAUF_BEENDET, KOPF

_LOGGER = logging.getLogger(__name__)
NORMAL = timedelta(seconds=30)
IM_LAUF = timedelta(seconds=5)
# ab 0.4.4 (Punkt 108): erst nach so vielen Fehlschlägen in Folge werden die Entitäten unverfügbar; bis dahin bleibt der
# letzte Stand stehen (Attribut veraltet_seit). Die App antwortet ab 0.4.9 ohnehin sofort aus ihrem Zwischenspeicher.
FEHLER_BIS_UNVERFUEGBAR = 3
ANFRAGE_TIMEOUT_S = 30


def fehler_text(e) -> str:
    """Fehlermeldung mit Typ – ein TimeoutError hat keinen Text."""
    if isinstance(e, TimeoutError):
        return f"Zeitüberschreitung nach {ANFRAGE_TIMEOUT_S} s"
    return f"{type(e).__name__}: {e}" if str(e) else type(e).__name__


class DbhCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=NORMAL)
        d = entry.data
        self.basis = f"http://{d['host']}:{d['port']}/api/auto"
        self.kopf = {KOPF: d["schluessel"]}
        self.session = async_get_clientsession(hass)
        self.fehler_in_folge = 0
        self.veraltet_seit: float | None = None
        self.version: str | None = None    # Punkt 296/297: Version dieser Integration, gesetzt in async_setup_entry

    async def _anfrage(self, methode: str, pfad: str, daten: dict | None = None) -> tuple[int, dict]:
        async with self.session.request(methode, f"{self.basis}/{pfad}", json=daten, headers=self.kopf,
                                        timeout=aiohttp.ClientTimeout(total=ANFRAGE_TIMEOUT_S)) as r:
            try:
                inhalt = await r.json(content_type=None)
            except (aiohttp.ContentTypeError, ValueError):
                inhalt = {"fehler": await r.text()}
            return r.status, inhalt or {}

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            status, d = await self._anfrage("GET", "status")
            if status != 200:
                raise UpdateFailed(f"App antwortet mit HTTP {status}: {d.get('fehler') or d}")
        except (aiohttp.ClientError, TimeoutError, UpdateFailed) as e:
            text = str(e) if isinstance(e, UpdateFailed) else f"App nicht erreichbar: {fehler_text(e)}"
            self.fehler_in_folge += 1
            if self.data is not None and self.fehler_in_folge < FEHLER_BIS_UNVERFUEGBAR:
                # Punkt 108: einzelner Aussetzer – letzten Stand behalten, als veraltet markieren
                if self.veraltet_seit is None:
                    self.veraltet_seit = time.time()
                _LOGGER.warning("RS DB Hygiene: %s (Fehlschlag %d von %d, letzter Stand bleibt)", text, self.fehler_in_folge, FEHLER_BIS_UNVERFUEGBAR)
                return {**self.data, "veraltet_seit": self.veraltet_seit, "veraltet_grund": text}
            raise UpdateFailed(f"{text} ({self.fehler_in_folge} Fehlschläge in Folge)") from e
        if self.fehler_in_folge:
            _LOGGER.info("RS DB Hygiene: App wieder erreichbar nach %d Fehlschlag/-schlägen", self.fehler_in_folge)
        self.fehler_in_folge, self.veraltet_seit = 0, None
        ereignis = bruecke.lauf_beendet(self.data, d)
        if ereignis:
            self.hass.bus.async_fire(EVENT_LAUF_BEENDET, ereignis)
        self.update_interval = IM_LAUF if d.get("laeuft") else NORMAL
        return d

    async def befehl(self, pfad: str, daten: dict | None = None) -> dict:
        """Befehl an die App; Sperrgründe der App werden als Fehlermeldung weitergegeben."""
        try:
            status, d = await self._anfrage("POST", pfad, daten or {})
        except (aiohttp.ClientError, TimeoutError) as e:
            raise HomeAssistantError(f"RS DB Hygiene: App nicht erreichbar ({fehler_text(e)})") from e
        await self.async_request_refresh()
        if status >= 300:
            raise HomeAssistantError("RS DB Hygiene: " + (" ".join(d.get("sperren") or []) or str(d.get("fehler") or f"HTTP {status}")))
        return d
