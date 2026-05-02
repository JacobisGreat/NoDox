# Data attribution

`sherlock_data.json` is vendored verbatim from the **Sherlock** project's
`sherlock_project/resources/data.json`. The loader at
`backend/app/data/loader.py` normalizes Sherlock's schema into the shape
NoDoxx's pipelines consume — no offline merge step is needed.

- Sherlock — MIT license. Source:
  https://github.com/sherlock-project/sherlock
  See `systems/sherlock/LICENSE`.

If WhatsMyName data (CC-BY-SA 4.0, used by `systems/blackbird/`) is added
later, list it here with attribution to https://github.com/WebBreacher/WhatsMyName.

## Code attribution

`backend/app/services/spiderfoot_catalog.py`,
`backend/app/services/searchcode.py`, and
`backend/app/services/gravatar.py` are derivative async ports / pattern
extractions of these SpiderFoot modules (all MIT-licensed):

- `systems/spiderfoot/modules/sfp_pastebin.py`
- `systems/spiderfoot/modules/sfp_grep_app.py`
- `systems/spiderfoot/modules/sfp_searchcode.py`
- `systems/spiderfoot/modules/sfp_gravatar.py`
- `systems/spiderfoot/modules/sfp_filemeta.py`

Upstream project: https://github.com/smicallef/spiderfoot
See `systems/spiderfoot/LICENSE`.

`backend/app/services/dork_catalog.py` is translated from the MIT-
licensed DorkER project (`systems/DorkER/dorker.py`).
Upstream: https://github.com/JustDanio/DorkER (see `systems/DorkER/LICENSE`).
