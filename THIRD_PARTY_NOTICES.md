# Third-Party Notices

All fastagent source code in this repository is original work, written for this
project and licensed under the MIT License (see [LICENSE](LICENSE)). No source
code has been copied from any other project or organization.

fastagent **depends on** (but does not include or redistribute) the following
open-source packages, all under permissive licenses compatible with MIT:

| Package | License | Used for |
|---|---|---|
| [anthropic](https://pypi.org/project/anthropic/) | MIT | Official Anthropic SDK (the `anthropic` provider) |
| [pydantic](https://pypi.org/project/pydantic/) | MIT | Config validation, structured outputs, test-case models |
| [PyYAML](https://pypi.org/project/PyYAML/) | MIT | Reading `fastagent.yaml` |
| [httpx](https://pypi.org/project/httpx/) | BSD-3-Clause | HTTP client for OpenAI-compatible endpoints (installed transitively by `anthropic`) |

Notes:

- These packages are installed by the user via pip; their license texts ship
  inside the installed distributions. Nothing from them is vendored into this
  repository.
- The playground UI uses no third-party JavaScript, CSS frameworks, fonts, or
  icons — it is hand-written HTML/CSS/JS served from the Python source.
- "OpenAI-compatible" refers to a widely implemented open wire protocol; this
  project implements the protocol independently and includes no OpenAI code.
- Product names (Anthropic, Claude, OpenAI, Ollama, etc.) are trademarks of
  their respective owners and are used only to identify interoperability.

**Naming:** unrelated packages named `fastagent`, `fastagents`, and
`fast-agent-mcp` exist on PyPI. To avoid collision and confusion, this project
publishes under the distinct distribution name **`fastagent-framework`**
(import name: `fastagent`). This project is not affiliated with any of those
packages.
