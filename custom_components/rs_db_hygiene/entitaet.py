"""Gemeinsame Basis der Entitäten: ein Gerät „RS DB Hygiene“, Daten aus dem Coordinator, Attribute nicht im Recorder."""
from __future__ import annotations

from homeassistant.const import MATCH_ALL
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import bruecke
from .const import DOMAIN
from .coordinator import DbhCoordinator


class DbhEntitaet(CoordinatorEntity[DbhCoordinator]):
    _attr_has_entity_name = True
    # Das Werkzeug soll selbst keine Attribut-Datenflut erzeugen
    _unrecorded_attributes = frozenset({MATCH_ALL})

    def __init__(self, coordinator: DbhCoordinator, schluessel: str) -> None:
        super().__init__(coordinator)
        self.schluessel = schluessel
        self._attr_translation_key = schluessel
        self._attr_unique_id = f"{DOMAIN}_{schluessel}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, "app")}, name="RS DB Hygiene", manufacturer="RS",
                                            model="DB Hygiene Manager", entry_type=DeviceEntryType.SERVICE)

    def _wert(self):
        return bruecke.werte(self.coordinator.data or {}).get(self.schluessel, (None, {}))

    @property
    def extra_state_attributes(self):
        return self._wert()[1] or None
