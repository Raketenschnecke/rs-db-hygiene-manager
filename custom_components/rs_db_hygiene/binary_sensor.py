"""„läuft“ – ob die App gerade einen Löschlauf oder eine Analyse ausführt."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity

from .entitaet import DbhEntitaet


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([DbhLaeuft(entry.runtime_data, "laeuft")])


class DbhLaeuft(DbhEntitaet, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    @property
    def is_on(self):
        return self._wert()[0]
