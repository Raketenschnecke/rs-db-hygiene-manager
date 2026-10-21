"""Status-Sensoren der App RS DB Hygiene Manager."""
from __future__ import annotations

import time

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfInformation

from . import bruecke
from .const import DOMAIN
from .entitaet import DbhEntitaet

SENSOREN = {
    # schlüssel: (device_class, Einheit, state_class, Optionen, Diagnose)
    "status": (SensorDeviceClass.ENUM, None, None, bruecke.ZUSTAENDE, False),
    "fortschritt": (None, PERCENTAGE, None, None, False),
    "geplante_zeilen": (None, "Zeilen", SensorStateClass.MEASUREMENT, None, False),
    "letzter_lauf": (SensorDeviceClass.TIMESTAMP, None, None, None, False),
    "letzter_lauf_geloescht": (None, "Zeilen", SensorStateClass.MEASUREMENT, None, False),
    "db_groesse": (SensorDeviceClass.DATA_SIZE, UnitOfInformation.GIGABYTES, SensorStateClass.MEASUREMENT, None, True),
    "prognose": (SensorDeviceClass.DATA_SIZE, UnitOfInformation.GIGABYTES, None, None, True),
    "analyse_stand": (SensorDeviceClass.TIMESTAMP, None, None, None, True),
    "letzte_attribut_analyse": (SensorDeviceClass.TIMESTAMP, None, None, None, True),
    # Punkt 296 (0.5.6): installierte Version der Integration (aus manifest.json). Die Version der App liefert schon die
    # Supervisor-Integration (sensor.rs_db_hygiene_manager_…_version).
    "version_integration": (None, None, None, None, True),
}

# Punkt 244 (0.5.6b6): Sekunden, höchstens so oft übernimmt der Sensor einen neuen Wert (der Koordinator läuft weiter alle 30 s)
DROSSELUNG = {"db_groesse": 1800}


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities(DbhSensor(coordinator, k, *v) for k, v in SENSOREN.items())


class DbhSensor(DbhEntitaet, SensorEntity):
    def __init__(self, coordinator, schluessel, klasse, einheit, art, optionen, diagnose):
        super().__init__(coordinator, schluessel)
        self._attr_device_class = klasse
        self._attr_native_unit_of_measurement = einheit
        self._attr_state_class = art
        if optionen:
            self._attr_options = optionen
        if diagnose:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._drossel = DROSSELUNG.get(schluessel)
        self._gehalten = None      # zuletzt übernommener Wert
        self._uebernommen = None   # monotone Zeit der letzten Übernahme
        if schluessel == "version_integration":
            # feste ID wie die übrigen (sensor.rs_db_hygiene_…), nicht vom neuen Gerätenamen „… Manager“ abgeleitet
            self.entity_id = f"sensor.{DOMAIN}_version_integration"

    @property
    def available(self):
        # die eigene Version ist auch bekannt, wenn die App gerade nicht antwortet
        return self.schluessel == "version_integration" or super().available

    @property
    def native_value(self):
        if self.schluessel == "version_integration":
            return self.coordinator.version
        wert = self._wert()[0]
        if not self._drossel:
            return wert
        jetzt = time.monotonic()
        if wert is not None and (self._uebernommen is None or jetzt - self._uebernommen >= self._drossel):
            self._gehalten, self._uebernommen = wert, jetzt
        return self._gehalten
