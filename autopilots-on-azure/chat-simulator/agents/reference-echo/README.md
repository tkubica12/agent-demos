# Official reference echo sample

`agent.py` is copied byte-for-byte from Microsoft's MIT-licensed
[Python quickstart](https://github.com/microsoft/Agents/blob/93be4925735518cba1e63592542c388576253a37/samples/python/quickstart/src/agent.py).
Upstream commit: `93be4925735518cba1e63592542c388576253a37`.
SHA-256: `2e277d416ba76dc03b8d3d37689456eb5bf3d98899b45c57a6486c2ccdc81f77`.
The local attributes preserve its bytes across checkouts. `LICENSE` and
`cards\LICENSE` retain upstream notices.

This is reference code, not a configured or hosted agent. No credentials,
deployment wrappers or tenant bindings are included. Its separate `pyproject.toml`
and `uv.lock` describe the official SDK dependencies. Supply your own authenticated
HTTP host and SDK connection configuration before importing the sample; set
`PYTHON_DOTENV_DISABLED=1` to avoid its upstream automatic dotenv loading.

The sample welcomes added members and echoes messages. It does not implement
typing, message editing/deletion, proactive behavior or refreshed Execute cards.
The simulator's existing agent-channel and Adaptive Card runtime remains available
independently of this sample; see `..\..\SETUP.md`.
