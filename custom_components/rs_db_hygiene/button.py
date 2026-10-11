"""Knöpfe: Löschlauf starten (alle aktiven Regeln), Analyse starten, Attribut-Analyse starten (ab 0.4.1), Löschlauf abbrechen."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity

from .entitaet import DbhEntitaet

KNOEPFE = {"loeschlauf_starten": "loeschlauf", "analyse_starten": "analyse", "attribut_analyse_starten": "attribut_analyse", "abbrechen": "abbrechen"}


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(DbhKnopf(entry.runtime_data, k, pfad) for k, pfad in KNOEPFE.items())


class DbhKnopf(DbhEntitaet, ButtonEntity):
    def __init__(self, coordinator, schluessel, pfad):
        super().__init__(coordinator, schluessel)
        self.pfad = pfad

    @property
    def extra_state_attributes(self):
        return None

    async def async_press(self) -> None:
        # Sperrgründe der App (Hauptschalter, Automatik, Analyse zu alt …) kommen als Fehlermeldung zurück
        await self.coordinator.befehl(self.pfad)
