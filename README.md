# RS DB Hygiene Manager

Retention times per entity for the Home Assistant recorder database.

The repository consists of two parts that share one version number:

- **App** `rs_db_hygiene/`: web interface (Ingress) with analysis, rules, dry run and deletion run.
- **Integration** `custom_components/rs_db_hygiene/`: device “RS DB Hygiene” with sensors, buttons and services. It deletes statistics and events through the recorder queue.

Requirements: Home Assistant OS or Supervised 2026.10 or later, recorder with SQLite.

Note: the app's user interface is currently in German.

## Screenshots (version 0.5.5)

Click an image to open it in full size.

<table>
  <tr>
    <td width="50%" align="center"><a href="https://raw.githubusercontent.com/Raketenschnecke/rs-db-hygiene-manager/main/docs/screenshots/01-entities.png"><img src="https://raw.githubusercontent.com/Raketenschnecke/rs-db-hygiene-manager/main/docs/screenshots/01-entities.png" alt="Entities tab with tiles, filters and column groups"></a><br><sub>Entities tab with tiles, filters and column groups</sub></td>
    <td width="50%" align="center"><a href="https://raw.githubusercontent.com/Raketenschnecke/rs-db-hygiene-manager/main/docs/screenshots/02-recommendations.png"><img src="https://raw.githubusercontent.com/Raketenschnecke/rs-db-hygiene-manager/main/docs/screenshots/02-recommendations.png" alt="Recommendations with suggested rules"></a><br><sub>Recommendations with suggested rules</sub></td>
  </tr>
  <tr>
    <td width="50%" align="center"><a href="https://raw.githubusercontent.com/Raketenschnecke/rs-db-hygiene-manager/main/docs/screenshots/03-top-writers.png"><img src="https://raw.githubusercontent.com/Raketenschnecke/rs-db-hygiene-manager/main/docs/screenshots/03-top-writers.png" alt="Who writes the most? Top N and savings"></a><br><sub>Who writes the most? Top N and savings</sub></td>
    <td width="50%" align="center"><a href="https://raw.githubusercontent.com/Raketenschnecke/rs-db-hygiene-manager/main/docs/screenshots/04-rules.png"><img src="https://raw.githubusercontent.com/Raketenschnecke/rs-db-hygiene-manager/main/docs/screenshots/04-rules.png" alt="Rules with rank, retention times and hits"></a><br><sub>Rules with rank, retention times and hits</sub></td>
  </tr>
  <tr>
    <td width="50%" align="center"><a href="https://raw.githubusercontent.com/Raketenschnecke/rs-db-hygiene-manager/main/docs/screenshots/05-forecast-per-rule.png"><img src="https://raw.githubusercontent.com/Raketenschnecke/rs-db-hygiene-manager/main/docs/screenshots/05-forecast-per-rule.png" alt="Forecast per rule"></a><br><sub>Forecast per rule</sub></td>
    <td width="50%" align="center"><a href="https://raw.githubusercontent.com/Raketenschnecke/rs-db-hygiene-manager/main/docs/screenshots/06-growth-forecast.png"><img src="https://raw.githubusercontent.com/Raketenschnecke/rs-db-hygiene-manager/main/docs/screenshots/06-growth-forecast.png" alt="Growth forecast of the database (5 years)"></a><br><sub>Growth forecast of the database (5 years)</sub></td>
  </tr>
</table>

## Installation

1. **App:** Settings → Apps → App Store → ⋮ → Repositories → add `https://github.com/Raketenschnecke/rs-db-hygiene-manager`. Then install and start the app “RS DB Hygiene Manager”.

   Or use this button:

   [![Add the app repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FRaketenschnecke%2Frs-db-hygiene-manager)

2. **Integration:** HACS → ⋮ → Custom repositories → the same URL, type “Integration”. Download “RS DB Hygiene” and restart Home Assistant. As soon as the app is running, it announces itself via discovery; confirm the integration under Settings → Devices & services.

   Or use this button:

   [![Open the repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Raketenschnecke&repository=rs-db-hygiene-manager&category=integration)


## Quick start

**Manual mode:**
Let the Hygiene Manager analyse the Home Assistant database → review the results in the tabs “Entitäten” (entities) and “Events” and derive deletion rules (or adopt suggestions from the tab “Empfehlungen”, recommendations) → assess the forecast deletion results in the tab “Prognosen je Regel” (forecasts per rule) → start the manual deletion run in the tab “Löschen” (delete) → done.

**Automatic mode:**
Create an automation in HA that starts a deletion run through the RS DB Hygiene integration (action `rs_db_hygiene.loeschlauf_starten`) → done.

Data is only deleted if “Löschen freigegeben” (deletion enabled, the main switch) is switched on in the app configuration; for control by an automation, “Automatik freigegeben” (automation enabled) must be switched on as well.

## Recommended setup

The idea: Home Assistant keeps everything for a long time by default, and the Hygiene Manager shortens specifically what you don't need for that long. This takes three steps.

### 1. Recorder in `configuration.yaml`

```yaml
recorder:
  purge_keep_days: 3650   # example and recommendation: 10 years as the upper limit for everything without its own rule (up to 7300 possible)
  auto_purge: false       # HA's nightly purge off – data is only deleted according to your rules
  auto_repack: false      # HA's monthly repack off – handled by the "Repack" automation
```

- **`purge_keep_days`** is the longest retention time; 3650 days (10 years) is an example and our recommended starting value. Everything without its own rule (“global”) stays in the database this long. Rules can only shorten it; a rule with a longer time is capped at this value.
- **`auto_purge: false`** switches off HA's own purge at 04:12. The database is then managed solely by the Hygiene Manager and its deletion run.
- **`auto_repack: false`** switches off HA's own repack on the 2nd Sunday of the month (it only runs together with `auto_purge` anyway). The repack is done by the “Repack” automation on the same schedule.
- Important: only switch off `auto_repack` once the “Repack” automation is in place, otherwise the file never shrinks after deletion runs. After the change, check the configuration and restart HA.

### 2. Automation “Deletion run”

Starts a deletion run in the RS DB Hygiene Manager every night (and thus merely automates “pressing the delete button” by hand).

```yaml
alias: RS DB Hygiene – Deletion run
mode: single
triggers:
  - trigger: time
    at: "03:00:00"
conditions:
  - condition: state
    entity_id: sensor.rs_db_hygiene_status
    state: bereit
actions:
  - action: rs_db_hygiene.loeschlauf_starten
  - wait_for_trigger:
      - trigger: event
        event_type: rs_db_hygiene_lauf_beendet
    timeout: "03:00:00"
    continue_on_timeout: true
  - action: persistent_notification.create
    data:
      title: RS DB Hygiene
      message: >-
        {% if wait.trigger %}Deletion run {{ wait.trigger.event.data.status }}:
        {{ wait.trigger.event.data.geloescht }} rows in {{ wait.trigger.event.data.dauer_s }} s.
        {% else %}Deletion run not finished after 3 h.{% endif %}
```

The notification at the end is optional. The event `rs_db_hygiene_lauf_beendet` also provides `attributzeilen` (attribute rows), `umfang` (scope) and `fehler` (error). The state `bereit` of `sensor.rs_db_hygiene_status` is shown as “ready” in the UI; in automations the internal value `bereit` is used.

### 3. Automation “Repack”

**Why a repack?** SQLite does not return space freed by deletion to the file system. Deleted rows leave free pages in the file, which new data gradually fills again. The file `home-assistant_v2.db` therefore stays as large after a deletion run as before, even if millions of rows are gone. Only a repack (SQLite `VACUUM`) rewrites the database without gaps; afterwards the file is smaller.

This is useful or necessary above all:

- **after large deletion runs**, e.g. the first run with new rules that removes years of data. Without a repack, the space on the storage stays occupied;
- **for backups:** HA backs up the database file including its free pages. A smaller file makes every backup smaller and faster;
- **against fragmentation:** after many delete and write operations the data is scattered across the file. A repack reorders it, and queries for history and statistics often become faster again.

If, on the other hand, the database stays roughly the same size because about as much is deleted each day as is newly written, a repack achieves little. Once a month is then entirely sufficient.

```yaml
alias: RS DB Hygiene – Repack
mode: single
triggers:
  - trigger: time
    at: "05:00:00"
conditions:
  # once a month: 2nd Sunday (like HA's own auto_repack)
  - condition: template
    value_template: "{{ now().weekday() == 6 and 8 <= now().day <= 14 }}"
actions:
  # if a deletion run is still active, wait for it to finish first
  - wait_template: "{{ not is_state('sensor.rs_db_hygiene_status', 'loeschlauf') }}"
    timeout: "03:00:00"
  - action: recorder.purge
    data:
      repack: true
```

- HA has no service just for the repack; it runs through `recorder.purge` with `repack: true`. Without `keep_days`, the service only deletes what is older than `purge_keep_days`, i.e. nothing your setting does not already allow.
- A repack locks the recorder for a few minutes, with large databases also longer. The recorder buffers new values during this time and writes them afterwards.
- It temporarily needs free space the size of the database. Do not run it at the same time as a backup.

## Updates

- **App:** appears as an update in the App Store or under Settings → Updates.
- **Integration:** appears in HACS or under Settings → Updates; restart Home Assistant afterwards.
- Changes are listed in [`rs_db_hygiene/CHANGELOG.md`](rs_db_hygiene/CHANGELOG.md) (German).

## Notes

- Deletion runs are only possible with the main switch enabled in the app configuration. Without it, the app deletes nothing.
- The value `purge_keep_days` in `configuration.yaml` determines the maximum retention time of states and 5-minute values in the database. If you switch off `auto_purge`, the manager or an automation takes over managing HA's SQLite data.

## License

MIT, see [LICENSE](LICENSE).
