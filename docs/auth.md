# Authentication

GA4GH auth is heterogeneous: some services are public, some require OAuth2/OIDC
bearer tokens (incl. GA4GH Passport/AAI), some use API keys. This server has a
**pluggable, host-scoped** auth layer.

## Resolution order (per request host)

1. Cached **OAuth** token (device-code / client-credentials), auto-refreshed near expiry.
2. **Static bearer** token for the host: env `GA4GH_MCP_TOKEN_<HOST>`, YAML `hosts.<host>.token`, or runtime `auth_set_token`.
3. **Global** bearer `GA4GH_MCP_BEARER_TOKEN`, only for hosts listed in `GA4GH_MCP_BEARER_HOSTS`.
4. No auth.

Tokens are only sent to the host they are scoped to, and only over `https://` (plain
`http://` is tolerated for `localhost` / `127.0.0.1` / `::1`). Tools accept raw URLs chosen
by the model, so the global token has an allow-list: when `GA4GH_MCP_BEARER_HOSTS` is empty
it is never sent. The
host slug for env vars uppercases and replaces non-alphanumerics: `data.terra.bio` →
`GA4GH_MCP_TOKEN_DATA_TERRA_BIO`.

## Environment variables

| Variable | Purpose |
|---|---|
| `GA4GH_MCP_TOKEN_<HOST>` | Static bearer token for one host |
| `GA4GH_MCP_BEARER_TOKEN` | Global bearer, sent only to `GA4GH_MCP_BEARER_HOSTS` |
| `GA4GH_MCP_BEARER_HOSTS` | Comma-separated hosts (`host` or `host:port`) the global bearer may be sent to |
| `GA4GH_MCP_CLIENT_ID_<HOST>` | OAuth client_id for device-code / client-credentials |
| `GA4GH_MCP_CLIENT_SECRET_<HOST>` | OAuth client_secret (if the client is confidential) |
| `GA4GH_MCP_CONFIG_FILE` | Path to a YAML auth config (default `~/.ga4gh-mcp/config.yaml`) |

## Discovering what a service needs

```bash
uv run ga4gh-mcp call auth_discover --arg service_id_or_url=bio.terra.data
```

This provokes a `WWW-Authenticate` challenge on a protected endpoint and runs OIDC
`.well-known` discovery, reporting `requires_auth`, the parsed challenge, discovered
token/device/authorization endpoints, and the recommended flow.

Discovery results are informational. Endpoints learned from a service's own response (its
`WWW-Authenticate` realm or its origin's `.well-known`) never receive a `client_secret`:
client-credentials requires `hosts.<host>.oauth.token_endpoint` or `.issuer` in the YAML
config, and device-code against discovered endpoints runs as a public client (no secret).

## Flows

- **No-auth / public** — nothing to do. ✅ testable headless.
- **Static bearer** — you already hold a token:
  ```bash
  export GA4GH_MCP_TOKEN_DATA_TERRA_BIO="eyJ..."     # or:
  uv run ga4gh-mcp call auth_set_token --arg service_id_or_url=bio.terra.data --arg token=eyJ...
  ```
  ✅ testable headless.
- **OAuth device-code** (interactive, good for laptops/headless-with-browser):
  ```bash
  export GA4GH_MCP_CLIENT_ID_DATA_TERRA_BIO="your-registered-client-id"
  uv run ga4gh-mcp auth login bio.terra.data      # prints a URL + code, then polls
  ```
  Requires a **registered client_id** for the service's IdP. The token is cached in
  `~/.ga4gh-mcp/tokens.json` (0600) and refreshed automatically.
- **OAuth client-credentials** (machine-to-machine): configure `client_id` + `client_secret`
  (+ `token_endpoint`, discovered if omitted) in the YAML config with `grant: client_credentials`.
- **GA4GH Passport/AAI**: Passports/visas are JWTs carried as the bearer access token; supply
  the broker-issued token via the static-bearer or device-code paths above. The token is passed
  through unchanged to the target service.

## YAML config example

See [`config.example.yaml`](../config.example.yaml). Copy to `~/.ga4gh-mcp/config.yaml`.

## Headless status

- **Fully verifiable now:** public access + static-bearer (unit-tested + live-testable).
- **Implemented + documented:** device-code (CLI `auth login`), client-credentials, refresh,
  and discovery. Device-code needs a registered client_id, so end-to-end interactive login is
  documented for when you have one.
