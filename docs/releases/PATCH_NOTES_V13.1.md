# Portfolio Monitor V13.1 — clean multi-asset architecture

This release intentionally starts with empty runtime data. It does not migrate or read the old single-asset `data/`, `logs/`, or `output/` files.

## New layout

- `config/global/` — shared settings such as model, email, market data, report defaults and power settings.
- `config/assets/<asset_id>.json` — one independent configuration file per asset.
- `data/assets/<asset_id>/` — transactions, cache, latest result, spreadsheets and diagnostics for one asset.
- `output/assets/<asset_id>/` — latest report and report history for one asset.
- `data/global/` — shared token cache, run status and Windows task metadata.
- `logs/` — shared application logs.

## Behavior changes

- A new installation starts with no assets and guides the user to add the first asset.
- Reports and history are selected by `asset_id`; an asset with no report displays a clear empty-state page.
- Transaction files are isolated per asset. The old response-key mismatch that caused “saved but list is empty” was fixed.
- Asset creation and editing write individual asset JSON files.
- Deleting an asset also deletes only that asset’s local runtime data and reports.
- Scheduled plans must be bound to a specific asset and pass that asset to `run.py`.
- The home page no longer contains duplicate “enter asset” or duplicate system-settings buttons.
- “Latest report” is inside the selected asset card area and points only to that asset.
- The asset edit page no longer repeats the “new asset” action.

## First run

1. Start the control server.
2. Click `＋ 新增资产`.
3. Add the held fund code and its tracking target.
4. Add transactions under that asset.
5. Run a report. Historical market data and caches will be downloaded again.
