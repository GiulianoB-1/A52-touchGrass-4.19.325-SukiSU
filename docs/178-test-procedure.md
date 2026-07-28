# Hardware test procedure

1. Download the successful phase 178 Actions artifact.
2. Flash only `package/boot.img` to BOOT.
3. Observe whether the bootloader image remains visible through Android startup.
4. If the screen becomes black, collect the same raw 1 MiB RAMOOPS archive before restoring the working boot image.
5. Do not flash `package/boot-pre-fwdevlink-off.img`.
