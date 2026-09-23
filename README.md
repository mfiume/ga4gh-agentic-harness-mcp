# GA4GH MCP Service

A **universal [Model Context Protocol](https://modelcontextprotocol.io/) server for
GA4GH services**, built around the
[GA4GH Implementation Registry](https://registry.ga4gh.org/v1). It lets any
MCP-compatible AI client (Claude Desktop, Claude Code, Google Vertex AI, Amazon
Bedrock) **discover** registered GA4GH services and **access** them — tolerating the
reality that implementations vary in liveness, spec compliance, and version.

- **One server, two transports:** `stdio` (Claude Desktop / Claude Code) and
  streamable **HTTP** (Vertex, Bedrock, remote).
- **Registry-first:** list, search, detail, health-check, service types, implementations.
- **Generic + type-aware:** works against *any* GA4GH type via `service-info`, with
  specialized tools for **DRS**, **TRS**, **WES**.
- **Robust by design:** down / non-compliant / version-mismatched / auth-gated services
  never crash the server — every tool returns `{ok, data|error, warnings}`.
- **Pluggable auth:** public, static bearer, OAuth2 device-code & client-credentials,
  token refresh, GA4GH Passport pass-through, plus per-service auth discovery.

See [`PLAN.md`](PLAN.md) for architecture/design and
[`docs/compatibility.md`](docs/compatibility.md) for the live compatibility matrix.

---

## Quickstart

```bash
git clone https://github.com/mfiume/ga4gh-mcp-service
cd ga4gh-mcp-service
uv venv && uv pip install -e ".[dev]"      # or: uv sync --extra dev

# List the 23 tools (headless, no client needed)
uv run ga4gh-mcp tools

# Call a tool directly and print JSON
uv run ga4gh-mcp call registry_list_service_types
uv run ga4gh-mcp call registry_list_services --arg artifact=drs --arg limit=5
uv run ga4gh-mcp call registry_check_health --arg service_id_or_url=ai.viral
```

Run the server:

```bash
uv run ga4gh-mcp serve --transport stdio          # for Claude Desktop / Code
GA4GH_MCP_TRANSPORT=http uv run ga4gh-mcp serve    # HTTP on :8080 (/mcp, /healthz)
```

Over HTTP/SSE, outbound requests (and every redirect hop) to loopback, private, link-local
(including the cloud metadata address 169.254.169.254) and other non-public addresses are
refused. Set `GA4GH_MCP_BLOCK_PRIVATE_ADDRESSES=false` to reach services on a private network,
or `true` to apply the same rule under stdio.

With Docker (HTTP):

```bash
docker build -t ga4gh-mcp . && docker run -p 8080:8080 ga4gh-mcp
curl -s localhost:8080/healthz     # {"status":"ok",...}
```

Run straight from GitHub, no clone:

```bash
uvx --from git+https://github.com/mfiume/ga4gh-mcp-service ga4gh-mcp tools
```

## Tools (23)

| Group | Tools |
|---|---|
| **Registry** | `registry_list_services`, `registry_get_service`, `registry_search`, `registry_list_service_types`, `registry_list_implementations`, `registry_service_info`, `registry_check_health` |
| **Generic** | `service_get_info`, `service_request`, `list_supported_service_types` |
| **DRS** | `drs_get_object`, `drs_get_access_url`, `drs_resolve_curie` |
| **TRS** | `trs_list_tools`, `trs_get_tool` |
| **WES** | `wes_get_service_info`, `wes_list_runs`, `wes_get_run` |
| **Auth** | `auth_status`, `auth_discover`, `auth_set_token`, `auth_login`, `auth_revoke` |

Most tools accept a `service_id_or_url` — either a registry id (e.g. `ai.viral`) or a
raw base URL. Use `registry_list_services` to find valid ids.

## Client setup

Copy-paste configs and a verification step for each:

- [Claude Desktop](docs/clients/claude-desktop.md)
- [Claude Code](docs/clients/claude-code.md)
- [Google Vertex AI (ADK)](docs/clients/vertex-ai.md)
- [Amazon Bedrock (AgentCore / Strands)](docs/clients/amazon-bedrock.md)

## Authentication

Public and static-token access work out of the box; OAuth device-code /
client-credentials and per-service auth discovery are built in. Full details and
environment variables: [`docs/auth.md`](docs/auth.md).

```bash
# What does a service require?
uv run ga4gh-mcp call auth_discover --arg service_id_or_url=bio.terra.data
# Supply a token you hold:
export GA4GH_MCP_TOKEN_DATA_TERRA_BIO="eyJ..."
# Or interactive device-code login (needs a registered client_id):
uv run ga4gh-mcp auth login bio.terra.data
```

## Robustness

The registry is a mix of live, dead, private, non-compliant, and version-drifted
services. This server:

- **normalizes inconsistent URLs** (e.g. strips a registered `.../objects/` suffix to
  recover the DRS base) and re-adds each spec's nested prefix;
- **reconciles versions** — the registry's declared version vs. what the service
  reports — surfacing mismatches as warnings;
- **rejects false-positive liveness** (SPAs/proxies that 200 with HTML aren't "live");
- **times out, retries, and caches**, and returns structured errors (`auth_required`,
  `not_found`, `unreachable`, `upstream_error`, ...) so one bad endpoint never takes
  down the server.

## Verify (CLI, no UI)

```bash
uv run pytest -q
# expected: 46 passed, 3 skipped

uv run python scripts/smoke.py
# expected: "Summary: 15/15 checks passed"  (drives BOTH stdio + HTTP via a real MCP client)

uv run python scripts/compat_matrix.py
# regenerates docs/compatibility.md from the live registry (36 services)

# live integration tests (network):
GA4GH_MCP_LIVE=1 uv run pytest tests/test_live.py -q
```

## Development

```bash
uv run ruff check src tests        # lint
uv run pytest -q                   # tests
```

Layout and design decisions are documented in [`PLAN.md`](PLAN.md). Adding a new
GA4GH service type is a matter of registering a `ServiceTypePlugin`
(`src/ga4gh_mcp/ga4gh/plugins.py`) and, optionally, a typed client.

## License

Apache-2.0 — see [`LICENSE`](LICENSE).
