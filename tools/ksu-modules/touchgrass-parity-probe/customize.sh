#!/system/bin/sh
ui_print " "
ui_print "A52 TouchGrass Runtime Parity Probe v1.1"
ui_print "Broad, bounded and read-only reference capture"
ui_print " "
ui_print "- Automatic post-fs-data boot capture"
ui_print "- Action button runs a 90-second deep screen-cycle trace"
ui_print "- Captures secure memory, display, power and driver state"
ui_print "- Uses size caps and restores all tracing settings"
ui_print "- Results are written to /sdcard/Download when available"
ui_print " "
set_perm_recursive "$MODPATH" 0 0 0755 0644
for f in \
  "$MODPATH/post-fs-data.sh" \
  "$MODPATH/service.sh" \
  "$MODPATH/action.sh" \
  "$MODPATH/bin/common.sh" \
  "$MODPATH/bin/collect.sh" \
  "$MODPATH/bin/snapshot.sh" \
  "$MODPATH/bin/trace-session.sh" \
  "$MODPATH/bin/package-result.sh"; do
  [ -f "$f" ] && set_perm "$f" 0 0 0755
done
