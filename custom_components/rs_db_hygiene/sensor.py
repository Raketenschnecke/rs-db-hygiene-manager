"""Status-Sensoren der App RS DB Hygiene Manager."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfInformation

from . import bruecke
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
}


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

    @property
    def native_value(self):
        return self._wert()[0]
