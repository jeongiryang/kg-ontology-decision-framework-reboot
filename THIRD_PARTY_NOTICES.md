# Third-party notices

## Codex Harness

- Source: <https://github.com/jeongiryang/codex-harness>
- Pinned commit: `79b82281d305c89181fbb216499d5f1e962c14ed`
- Version observed at installation: `0.1.0`
- License: [Apache License 2.0](LICENSES/Apache-2.0.txt)

Files installed under `.agents/skills/harness/` and the five `harness-*` agent definitions originate from Codex Harness. Project-specific agents, skills, contracts, documentation and reporting tools in this repository are maintained separately.

Codex Harness is itself described as an independent Codex migration based on `revfactory/harness` commit `cceac68ea1d0ad198ef4b7b906cd238375836387`. Its installed notice is preserved in [LICENSES/codex-harness-NOTICE.txt](LICENSES/codex-harness-NOTICE.txt).

## pypdfium2 / PDFium

- Distribution: [pypdfium2 5.10.1](https://pypi.org/project/pypdfium2/5.10.1/)
- Source and license notices: [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) (Apache-2.0 or BSD-3-Clause).
- Bundled PDFium has its own BSD-style and third-party notices shipped in the installed distribution. Preserve those notices when redistributing its binary; see [licensing guidance](https://pypdfium2.readthedocs.io/en/stable/readme.html#licensing).
- Used only for in-memory PDF text extraction and rendering, not to license this project's own code.
