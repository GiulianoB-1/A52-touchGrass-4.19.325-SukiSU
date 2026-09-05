#!/system/bin/sh

ui_print "*******************************"
ui_print " A52 Phase319 Golden Collector"
ui_print " v1.2"
ui_print "*******************************"

set_perm "$MODPATH/post-fs-data.sh" 0 0 0755
set_perm "$MODPATH/action.sh" 0 0 0755
set_perm "$MODPATH/module.prop" 0 0 0644
