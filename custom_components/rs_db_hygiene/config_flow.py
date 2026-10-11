"""Einrichtung über die UI (Geräte & Dienste) und per Discovery der App (ab 0.4.0) – nur ein Eintrag."""
from homeassistant.config_entries import ConfigFlow
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from .const import DOMAIN


class RsDbHygieneFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovery: dict | None = None

    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(title="RS DB Hygiene", data={})
        return self.async_show_form(step_id="user")

    async def async_step_hassio(self, discovery_info: HassioServiceInfo):
        """Die App meldet Host, Port und Schlüssel. Vorhandenen Eintrag aktualisieren, sonst neu anlegen (mit Bestätigung)."""
        cfg = discovery_info.config
        self._discovery = {"host": cfg["host"], "port": int(cfg["port"]), "schluessel": cfg["schluessel"]}
        await self.async_set_unique_id(DOMAIN)
        for eintrag in self._async_current_entries(include_ignore=False):
            if eintrag.data != self._discovery:
                self.hass.config_entries.async_update_entry(eintrag, data=self._discovery, unique_id=DOMAIN)
                self.hass.config_entries.async_schedule_reload(eintrag.entry_id)
            return self.async_abort(reason="already_configured")
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="RS DB Hygiene", data=self._discovery)
        return self.async_show_form(step_id="hassio_confirm")
