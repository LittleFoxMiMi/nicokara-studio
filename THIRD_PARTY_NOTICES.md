# Third-party notices

## Kirakara-Player

Nicokara Studio's subtitle preview follows the layout and rendering concepts from
`Kirakara-Player` in this repository (`Kirakara-Player/`), including `rubySpan`
word grouping, Ruby isolation, dual karaoke slots, and progressive coloring.

The upstream project is distributed under the MIT License. The copyright notice
from `Kirakara-Player/LICENSE` is reproduced here:

```text
MIT License

Copyright (c) 2026 键盘Office
```

The upstream source remains available in `Kirakara-Player/`.

The server-side full-effect export loads the corresponding upstream modules from
`frontend/public/kirakara/`. Nicokara adds only a small callback hook in
`js/exporter.js` so the backend worker can receive the generated Blob instead of
triggering a browser download; rendering and encoding logic remains upstream.
