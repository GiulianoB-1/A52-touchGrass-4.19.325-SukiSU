#!/system/bin/sh

ui_print "- TG Composer Reference Recorder"
ui_print "- KernelSU companion for TouchGrass/GKI display debugging"

[ "$KSU" = "true" ] || abort "KernelSU is required"

set_perm "$MODPATH/service.sh" 0 0 0755
set_perm "$MODPATH/action.sh" 0 0 0755
set_perm "$MODPATH/boot-completed.sh" 0 0 0755
set_perm "$MODPATH/module.prop" 0 0 0644
set_perm "$MODPATH/README.md" 0 0 0644

ui_print "- Recorder is diagnostic-only"
ui_print "- No ptrace, property mutation, service restart, or device-node writes"
ui_print "- Reboot after installation, then use the hybrid collector BAT on the PC"
