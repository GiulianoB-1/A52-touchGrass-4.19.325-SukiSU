# UN1CA SM7225 ROM intake

This checkpoint pins and inspects `matei9/UN1CA_SM7125` branch `sm7225` at commit `49ec9fa5481808a9906d850297a69e029356dfeb`.

## Confirmed integration behavior

The A52 target does not create a new boot environment for its custom kernel. It unpacks the generated `boot.img`, downloads a TouchGrass kernel package, extracts `Image.gz`, replaces only the kernel payload, repacks with the original `mkbootimg` arguments, and appends the Samsung `SEANDROIDENFORCE` trailer.

This changes the GKI project target from a generic boot image to a hybrid payload compatible with the existing UN1CA boot container.

## Confirmed ROM constraints

- Target: Galaxy A52 5G, `a52xq`
- Platform: Snapdragon 750G, `sm7225`
- Board and shipping API level: 30
- Target platform SDK: 34
- Boot partition: 96 MiB
- DTBO partition: 24 MiB
- Vendor boot partition: 96 MiB
- Dynamic partitions enabled
- EROFS system image format
- Logical first-stage partitions include system, vendor, product, and odm
- Installer configuration includes boot, dtbo, vendor_boot, vbmeta_system, and vbmeta_samsung

## Engineering consequences

The first GKI-derived boot probe must:

1. preserve the existing UN1CA boot header, ramdisk, offsets, page size, command line, DTB arrangement, and Samsung trailer;
2. output a validated `Image.gz` payload;
3. fit within both the unpacked kernel allocation and the final 96 MiB boot partition;
4. keep all early storage, Qualcomm platform, security, and first-stage filesystem dependencies built in until module loading is proven;
5. retain the existing A52 DTB and DTBO selection for the first probe.

The source tree defines the packaging contract, but the final generated `boot.img`, `vendor_boot.img`, and `dtbo.img` are still required for exact binary measurements before flashing.
