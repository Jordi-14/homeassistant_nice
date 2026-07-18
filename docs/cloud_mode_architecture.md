# MyNice Cloud Mode Architecture

`0.8.0b4` introduces one Nice integration with two connection methods.

## Setup Flow

New config flows start with a connection-method choice:

- `local`: recommended default. Uses the existing BiDi-WiFi / NHK / T4 local
  implementation and existing entities.
- `cloud`: signs in with a MyNice account, discovers doors automatically, and
  creates cloud-backed cover entities.

Existing config entries that do not have `connection_method` are treated as
`local` for backward compatibility.

## Maintenance Boundaries

The local path remains owned by the existing modules:

- `client.py`
- `coordinator.py`
- local platform files such as `cover.py`, `sensor.py`, `button.py`, `switch.py`,
  and `number.py`

The cloud path stays isolated:

- `cloud_api.py`: MyNice OAuth, account discovery, and NHK proxy socket.
- `cloud_coordinator.py`: cloud hub, proxy reconnect loop, state routing.
- `cloud_cover.py`: cloud cover entities.
- `cloud.py`: Home Assistant setup/unload wrapper for cloud entries.

The shared files only route between the two paths:

- `config_flow.py`: first-step connection method plus local/cloud credential
  forms.
- `__init__.py`: setup/unload dispatch based on `connection_method`.
- `cover.py`: forwards the cover platform to local or cloud cover entities.
- `diagnostics.py`: avoids reading local-only coordinator fields for cloud
  entries.

## User Model

Users should try local first when their device exposes the local protocol. Cloud
mode is intended for CU_WIFI or MyNice users who want simpler account-based
setup or whose device does not currently work through the local path.
