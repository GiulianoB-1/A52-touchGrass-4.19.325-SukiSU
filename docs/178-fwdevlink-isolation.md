# A52 GKI 5.10 fw_devlink isolation

This branch keeps the existing GKI 5.10 kernel and display recorder unchanged while testing whether Linux 5.10 firmware device-link dependency gating prevents the A52 SDE and DSI display devices from probing.

The generated boot image appends `fw_devlink=off` and restores the Samsung `SEANDROIDENFORCE` footer. Kernel, ramdisk, DTB, recovery DTBO and boot ID are audited as unchanged.
