# Rationale

The previous persistent trace showed the DSI PHY binding while the SDE, DSI display, and DSI controller devices remained unbound. This phase tests whether Linux 5.10 firmware device-link supplier gating is preventing those consumers from entering their probe callbacks.
