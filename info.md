# Nice

Home Assistant control for compatible Nice gates and garage doors.

Local setup remains the recommended default. It provides a cover entity with
open, stop, close, and current position, plus helper diagnostic sensors and
buttons. It uses compatible local NHK/T4 services over TCP 443 and
user-supplied credentials extracted from the normal MyNice app data.

The `0.8.0b4` beta also adds an optional MyNice cloud setup path. Cloud setup
signs in with a MyNice account, discovers doors automatically, and creates cloud
cover entities for doors with Nice proxy credentials. Use it when local control
is not available or when the simpler account-based setup is preferable.

For normal dashboards, use the cover entity first. Advanced BusT4 diagnostics
and configuration entities may be hidden or disabled by default: hidden means
advanced or diagnostic, not unavailable. Writable entities ending in `setting`
change controller configuration registers, so note original values before
testing them.
Do not change raw mode setting entities unless you already know the exact byte
your controller expects; the integration exposes those bytes but does not decode
their meaning.

It was originally tested with BiDi-WiFi devices. Some devices reporting
`interface_product: CU_WIFI` expose enough of the same local NHK/T4 command
surface for open, stop, and close. Beta builds also include experimental
CU_WIFI status support from live NHK/T4 events; CU_WIFI position may be coarser
and less frequent than encoder-derived BiDi-WiFi position.

Known working BiDi-WiFi firmware is `2.6.4`. Updating BiDi-WiFi firmware may
break the local protocol used by this integration.
