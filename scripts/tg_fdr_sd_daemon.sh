#!/system/bin/sh
set -u

OUTDIR="$1"
PIDFILE=/data/local/tmp/tg_fdr_daemon.pid
LOGFILE=/data/local/tmp/tg_fdr_daemon.log

mkdir -p "$OUTDIR" "$OUTDIR/static" "$OUTDIR/logs" || exit 2
echo $$ > "$PIDFILE"
echo "tg_fdr daemon pid=$$ out=$OUTDIR" > "$LOGFILE"
exec cat /dev/tg_fdr > "$OUTDIR/stream.tgfdr"
