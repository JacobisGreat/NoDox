# Data attribution

`sherlock_data.json` is vendored verbatim from the **Sherlock** project's
`sherlock_project/resources/data.json`. The loader at
`backend/app/data/loader.py` normalizes Sherlock's schema into the shape
NoDox's pipelines consume — no offline merge step is needed.

- Sherlock — MIT license. Source:
  https://github.com/sherlock-project/sherlock
  See `systems/sherlock/LICENSE`.

If WhatsMyName data (CC-BY-SA 4.0, used by `systems/blackbird/`) is added
later, list it here with attribution to https://github.com/WebBreacher/WhatsMyName.
