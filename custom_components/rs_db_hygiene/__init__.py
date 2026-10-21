"""RS DB Hygiene Manager (bis 0.5.6b29 „RS DB Hygiene“) – Mini-Integration für die App „RS DB Hygiene Manager“.

Ab 0.4.0 (Punkt 62) zusätzlich Brücke zur App: Die App meldet sich per Supervisor-Discovery an (Host, Port, Schlüssel);
die Integration fragt ihren Status ab und stellt Entitäten (Gerät „RS DB Hygiene Manager“), die Dienste
loeschlauf_starten / analyse_starten / attribut_analyse_starten (ab 0.4.1) und das Event rs_db_hygiene_lauf_beendet für Automationen bereit.

Stellt den Dienst rs_db_hygiene.purge bereit. Er löscht in 5-Min-Statistik, Langzeitstatistik und Events (ab 0.5.6b9 mit
tabelle states_attribute zusätzlich: Attribute alter States abtrennen, die States bleiben – Punkt 254),
wofür HA keinen eigenen Dienst je Entität bzw. Event-Typ hat. Ab 0.4.5 (Punkt 121) dazu rs_db_hygiene.purge_states:
States einzelner Entitäten vor einem exakten Zeitpunkt (z. B. lokale Mitternacht) über HAs PurgeEntitiesTask. Die Arbeit läuft als Aufgabe in der Warteschlange
des Recorders – wie HAs eigener Purge –, damit es keine Sperrkonflikte gibt und die Caches des Recorders stimmen.

Schutz: Die Integration arbeitet nur mit SQLite und nur beim Recorder-Schema, für das sie getestet ist.
Sie nutzt interne Schnittstellen des Recorders und muss nach HA-Updates geprüft werden.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import time
from typing import Any

import voluptuous as vol
from sqlalchemy import text

from homeassistant.components.recorder import statistics as rec_statistics
from homeassistant.components.recorder.const import SupportedDialect
from homeassistant.components.recorder.db_schema import SCHEMA_VERSION
from homeassistant.components.recorder.tasks import RecorderTask
from homeassistant.components.recorder.util import get_instance, session_scope

try:   # Punkt 121 (0.4.5): HAs eigene Purge-Aufgabe hinter recorder.purge_entities – nimmt einen exakten Zeitpunkt
    from homeassistant.components.recorder.tasks import PurgeEntitiesTask
except ImportError:   # andere HA-Version: purge_states wird nicht angeboten, die App löscht wie bisher mit ganzen Tagen
    PurgeEntitiesTask = None
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv

from homeassistant.const import Platform
from homeassistant.loader import async_get_integration

from . import kern
from .const import ALTER_TITEL, DOMAIN, KOPF_VERSION, NAME

_LOGGER = logging.getLogger(__name__)
SERVICE = "purge"
SERVICE_STATES = "purge_states"   # Punkt 121 (0.4.5)
PLATTFORMEN = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]
DIENSTE_BRUECKE = ("loeschlauf_starten", "analyse_starten", "attribut_analyse_starten")
SCHEMA_START = vol.Schema({vol.Optional("regel"): cv.string})
GETESTETES_SCHEMA = 53
TAG = 86400
WARTEN_S = 900

SCHEMA = vol.Schema({
    vol.Required("tabelle"): vol.In(kern.TABELLEN),
    vol.Required("namen"): vol.All(cv.ensure_list, [cv.string], vol.Length(min=1)),
    vol.Optional("keep_days"): vol.All(vol.Coerce(int), vol.Range(min=1, max=7300)),
    # Punkt 121 (0.4.5): exakter Zeitpunkt (Unix-Zeit, z. B. lokale Mitternacht) statt keep_days; hat Vorrang
    vol.Optional("vor_ts"): vol.All(vol.Coerce(float), vol.Range(min=0)),
    vol.Optional("alle", default=False): cv.boolean,
    vol.Optional("mit_meta", default=False): cv.boolean,
    vol.Optional("max_zeilen", default=100_000): vol.All(vol.Coerce(int), vol.Range(min=1, max=1_000_000)),
    vol.Optional("nur_zaehlen", default=False): cv.boolean,
})
# Punkt 121 (0.4.5): States bestimmter Entitäten vor einem exakten Zeitpunkt löschen (wie recorder.purge_entities,
# aber ohne Rundung auf „jetzt minus ganze Tage“). Immer eine explizite, nicht leere Entitätsliste.
SCHEMA_STATES = vol.Schema({
    vol.Required("entity_id"): vol.All(cv.ensure_list, [cv.entity_id], vol.Length(min=1)),
    vol.Required("vor_ts"): vol.All(vol.Coerce(float), vol.Range(min=0)),
})


class _Db:
    """Adapter für kern.loesche auf eine SQLAlchemy-Session."""

    def __init__(self, session):
        self.s = session

    def query(self, sql, params=None):
        return self.s.execute(text(sql), params or {}).all()

    def execute(self, sql, params=None):
        return self.s.execute(text(sql), params or {}).rowcount

    def commit(self):
        self.s.commit()


@dataclass(slots=True)
class PurgeTask(RecorderTask):
    """Läuft im Thread des Recorders; meldet das Ergebnis über ein Future an den Dienst zurück."""

    hass: HomeAssistant
    auftrag: dict[str, Any]
    future: asyncio.Future

    def run(self, instance) -> None:
        a = self.auftrag
        try:
            beginn = time.time()
            grenze = None if a["alle"] else (a["vor_ts"] if a.get("vor_ts") is not None else time.time() - a["keep_days"] * TAG)
            with session_scope(session=instance.get_session()) as session:
                erg = kern.loesche(_Db(session), a["tabelle"], a["namen"], grenze, a["max_zeilen"], a["nur_zaehlen"], a["alle"])
            frei = set(erg.pop("freie_data_ids"))
            if frei:
                # wie HAs eigener Purge: gelöschte event_data aus dem Cache nehmen, sonst zeigen neue Events darauf
                instance.event_data_manager.evict_purged(frei)
            frei_attr = set(erg.pop("freie_attr_ids", ()))
            if frei_attr:
                # Punkt 254: ebenso gelöschte state_attributes (sonst verweisen neue States auf eine gelöschte Zeile)
                instance.state_attributes_manager.evict_purged(frei_attr)
            if a["mit_meta"] and not a["nur_zaehlen"] and erg["rest"] == 0:
                # Langzeit „keine“ für Verwaiste / Statistik ohne Entität: samt statistics_meta (HA-Funktion)
                rec_statistics.clear_statistics(instance, list(a["namen"]))
                erg["meta_geloescht"] = True
            erg["dauer_s"] = round(time.time() - beginn, 2)
            _LOGGER.info("purge %s: %s Zeilen gelöscht, Rest %s (%s s)", a["tabelle"], erg["geloescht"], erg["rest"], erg["dauer_s"])
            self.hass.loop.call_soon_threadsafe(_setze, self.future, erg, None)
        except Exception as e:  # noqa: BLE001 – Fehler an den Dienstaufruf zurückgeben
            _LOGGER.exception("purge %s fehlgeschlagen", a.get("tabelle"))
            self.hass.loop.call_soon_threadsafe(_setze, self.future, None, e)


def _setze(future, ergebnis, fehler):
    if future.done():
        return
    if fehler is not None:
        future.set_exception(fehler)
    else:
        future.set_result(ergebnis)


def _pruefe_recorder(hass):
    instance = get_instance(hass)
    if instance.dialect_name != SupportedDialect.SQLITE:
        raise HomeAssistantError(f"{DOMAIN}: nur SQLite wird unterstützt (gefunden: {instance.dialect_name})")
    if SCHEMA_VERSION != GETESTETES_SCHEMA or instance.schema_version != GETESTETES_SCHEMA:
        raise HomeAssistantError(f"{DOMAIN}: Recorder-Schema {instance.schema_version}/{SCHEMA_VERSION} – getestet ist nur "
                                 f"{GETESTETES_SCHEMA}. Bitte erst die Integration prüfen.")
    return instance


async def _purge(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    a = dict(call.data)
    if not a["alle"] and "keep_days" not in a and "vor_ts" not in a:
        raise HomeAssistantError(f"{DOMAIN}: keep_days bzw. vor_ts fehlt (oder alle: true für Langzeit „keine“)")
    if a.get("vor_ts") is not None and a["vor_ts"] > time.time():
        raise HomeAssistantError(f"{DOMAIN}: vor_ts liegt in der Zukunft")
    if a["alle"] and a["tabelle"] == "events":
        raise HomeAssistantError(f"{DOMAIN}: alle: true ist für Events nicht erlaubt")
    if a["mit_meta"] and not (a["alle"] and a["tabelle"] == "statistics"):
        raise HomeAssistantError(f"{DOMAIN}: mit_meta nur zusammen mit alle: true für statistics")
    instance = _pruefe_recorder(hass)
    future = hass.loop.create_future()
    instance.queue_task(PurgeTask(hass, a, future))
    try:
        return await asyncio.wait_for(future, WARTEN_S)
    except TimeoutError as e:
        raise HomeAssistantError(f"{DOMAIN}: keine Antwort des Recorders nach {WARTEN_S} s (läuft ggf. weiter)") from e


async def _purge_states(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """Punkt 121 (0.4.5): reiht HAs PurgeEntitiesTask mit exaktem Zeitpunkt ein – dieselbe Arbeit wie recorder.purge_entities
    (States paketweise, nicht mehr benutzte Attribute, old_state_id). Kehrt sofort zurück; HA löscht in der Warteschlange."""
    ids = list(dict.fromkeys(call.data["entity_id"]))
    vor_ts = call.data["vor_ts"]
    if not ids:          # niemals ohne Entitäten (das Schema verlangt mindestens eine; doppelte Sicherung)
        raise HomeAssistantError(f"{DOMAIN}: purge_states ohne Entitäten verhindert")
    if vor_ts > time.time():
        raise HomeAssistantError(f"{DOMAIN}: vor_ts liegt in der Zukunft")
    instance = _pruefe_recorder(hass)
    ziel = frozenset(ids)
    vor = datetime.fromtimestamp(vor_ts, tz=timezone.utc)
    instance.queue_task(PurgeEntitiesTask(lambda eid: eid in ziel, vor))
    _LOGGER.info("purge_states: %d Entitäten, States vor %s", len(ids), vor.isoformat())
    return {"entitaeten": len(ids), "vor": vor.isoformat()} if call.return_response else None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry.title == ALTER_TITEL:
        # Punkt 298 (0.5.6): gespeicherten Titel einmal angleichen; einen vom Nutzer gewählten Titel nicht anfassen
        hass.config_entries.async_update_entry(entry, title=NAME)

    async def handler(call: ServiceCall) -> ServiceResponse:
        return await _purge(hass, call)

    hass.services.async_register(DOMAIN, SERVICE, handler, schema=SCHEMA, supports_response=SupportsResponse.ONLY)
    if PurgeEntitiesTask is not None:
        async def handler_states(call: ServiceCall) -> ServiceResponse:
            return await _purge_states(hass, call)

        hass.services.async_register(DOMAIN, SERVICE_STATES, handler_states, schema=SCHEMA_STATES, supports_response=SupportsResponse.OPTIONAL)
    else:
        _LOGGER.warning("RS DB Hygiene: PurgeEntitiesTask fehlt in dieser HA-Version – purge_states nicht verfügbar, "
                        "die App löscht States wie bisher mit ganzen Tagen")

    # Brücke zur App (Punkt 62) – erst, wenn die App sich per Discovery gemeldet hat
    if not entry.data.get("host"):
        _LOGGER.warning("RS DB Hygiene: noch keine Verbindungsdaten der App – App auf ≥ 0.4.0 aktualisieren und starten (Discovery)")
        return True
    from .coordinator import DbhCoordinator

    coordinator = DbhCoordinator(hass, entry)
    coordinator.version = str((await async_get_integration(hass, DOMAIN)).version)
    coordinator.kopf[KOPF_VERSION] = coordinator.version     # Punkt 272
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    async def starten(call: ServiceCall) -> ServiceResponse:
        antwort = await coordinator.befehl("loeschlauf", {"regel": call.data.get("regel")} if call.data.get("regel") else {})
        return antwort if call.return_response else None

    async def analyse(call: ServiceCall) -> None:
        await coordinator.befehl("analyse")

    async def attribut_analyse(call: ServiceCall) -> None:
        await coordinator.befehl("attribut_analyse")

    hass.services.async_register(DOMAIN, "loeschlauf_starten", starten, schema=SCHEMA_START, supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, "analyse_starten", analyse)
    hass.services.async_register(DOMAIN, "attribut_analyse_starten", attribut_analyse)
    await hass.config_entries.async_forward_entry_setups(entry, PLATTFORMEN)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = True
    # Ab 0.4.1 (Punkt 64): danach richten, ob die Brücke wirklich eingerichtet war – nicht nach entry.data. Beim Reload
    # durch die Discovery stehen dort schon die neuen Verbindungsdaten, Plattformen und Dienste gab es aber noch nicht.
    if getattr(entry, "runtime_data", None) is not None:
        ok = await hass.config_entries.async_unload_platforms(entry, PLATTFORMEN)
        for s in DIENSTE_BRUECKE:
            hass.services.async_remove(DOMAIN, s)
        if ok:
            entry.runtime_data = None
    hass.services.async_remove(DOMAIN, SERVICE)
    if hass.services.has_service(DOMAIN, SERVICE_STATES):
        hass.services.async_remove(DOMAIN, SERVICE_STATES)
    return ok
