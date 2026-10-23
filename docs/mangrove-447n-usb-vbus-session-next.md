# P447N USB VBUS-at-start — queued, not implemented
Capture 2026-10-23 16:51, same boot 5.10.252-Mangrove, three collector
v1.14 GKI bundles. User confirms cable was attached before boot.

2.106s CHG_GUARD reports VBUS=1; 52.57s configfs FunctionFS -ENODEV
before descriptors; 124.42s FunctionFS and ACM bind rc=0; UDC start
and connect rc=0; at 178/214/251s state=not attached and
sys.usb.configured=none, with ffs.ready=1. No suspend entry.
447K connected when cable was inserted only after gadget bind.

Separate follow-up: review DWC3, MUIC/extcon/role ownership and the
gadget UDC locking contract. At a confirmed successful final g1 bind
with independently validated VBUS, perform one bounded, serialized
usb_gadget_vbus_connect() or safe soft reconnect *only if the
underlying driver requires it*; log P447N USB VBUS_SESSION and state.
Preserve USB PHY/suspend, ttyGS3 and CHG_GUARD. Do not blindly trigger
pullup during early g_serial or before adbd descriptors are ready.
Test both cable-at-boot and hotplug. No charger policy changes.
