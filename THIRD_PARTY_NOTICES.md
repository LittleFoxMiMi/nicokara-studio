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

## FA-Kara and YoHane model

Nicokara's optional FA-Kara alignment backend is adapted from the MIT-licensed
`FA-Kara-main/` reference implementation. Nicokara ports its deterministic text
preparation path into `backend/app/services/fa_kara_text.py`: Janome/IPADIC for
Japanese morphological readings, Pyphen plus CMUdict for English syllables, and
pypinyin for Chinese characters. These packages are declared in the backend
runtime dependencies; their upstream licenses remain applicable. The alignment
model still uses existing project Ruby data and is not bundled here.

The optional `NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn` YoHane model is
not bundled with Nicokara. When selected, it is downloaded on demand from
Hugging Face and remains subject to its `CC BY-NC-SA 4.0` license.
