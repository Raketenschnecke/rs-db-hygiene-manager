"""Gemeinsame Basis der Entitäten: ein Gerät „RS DB Hygiene Manager“ (bis 0.5.6b29 „RS DB Hygiene“), Daten aus dem Coordinator, Attribute nicht im Recorder."""
from __future__ import annotations

from homeassistant.const import MATCH_ALL
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import bruecke
from .const import DOMAIN, NAME
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
        # Punkt 297 (0.5.6): sw_version = Version dieser Integration, HA zeigt sie unter „Dienst-Informationen“
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, "app")}, name=NAME, manufacturer="RS",
                                            model="DB Hygiene Manager", sw_version=coordinator.version,
                                            entry_type=DeviceEntryType.SERVICE)

    def _wert(self):
        return bruecke.werte(self.coordinator.data or {}).get(self.schluessel, (None, {}))

    @property
    def extra_state_attributes(self):
        return self._wert()[1] or None
