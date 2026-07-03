# Nice

Local Home Assistant control for compatible Nice gates and garage doors.

This integration provides a cover entity with open, stop, close, and current
position, plus helper diagnostic sensors and buttons. It uses compatible local
NHK/T4 services over TCP 443 and user-supplied credentials extracted from the
normal MyNice app data.

It was originally tested with BiDi-WiFi devices. Some devices reporting
`interface_product: CU_WIFI` may also work in basic command-only mode when they
expose the same local NHK/T4 command surface. Full status and position support
depends on the local services and DMP registers exposed by the device.

Known working BiDi-WiFi firmware is `2.6.4`. Updating BiDi-WiFi firmware may
break the local protocol used by this integration.
