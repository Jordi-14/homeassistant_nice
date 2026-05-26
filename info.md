# Nice BiDi-WiFi

Local Home Assistant control for a Nice gate through a BiDi-WiFi interface.

This integration provides a cover entity with open, stop, close, and current
position, plus helper diagnostic sensors and buttons. It requires local TCP 443
access to the BiDi-WiFi and user-supplied credentials extracted from the normal
MyNice app data.

Known working BiDi-WiFi firmware is `2.6.4`. Updating BiDi-WiFi firmware may
break the local protocol used by this integration.
