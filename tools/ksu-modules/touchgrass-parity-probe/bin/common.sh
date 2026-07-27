#!/system/bin/sh

: "${MODDIR:=${0%/*}/..}"
OUTROOT=${OUTROOT:-/data/adb/tgprobe}
DEFAULT_FILE_LIMIT=${DEFAULT_FILE_LIMIT:-8388608}
TREE_FILE_LIMIT=${TREE_FILE_LIMIT:-524288}
TREE_COUNT_LIMIT=${TREE_COUNT_LIMIT:-800}

timestamp() {
  date +%Y%m%d-%H%M%S 2>/dev/null || echo unknown-time
}

find_tracefs() {
  for d in /sys/kernel/tracing /sys/kernel/debug/tracing; do
    if [ -r "$d/trace" ] && [ -r "$d/current_tracer" ]; then
      echo "$d"
      return 0
    fi
  done

  if [ -d /sys/kernel/tracing ]; then
    mount -t tracefs tracefs /sys/kernel/tracing 2>/dev/null || true
    [ -r /sys/kernel/tracing/trace ] && {
      echo /sys/kernel/tracing
      return 0
    }
  fi

  if [ -d /sys/kernel/debug ]; then
    mount -t debugfs debugfs /sys/kernel/debug 2>/dev/null || true
    [ -r /sys/kernel/debug/tracing/trace ] && {
      echo /sys/kernel/debug/tracing
      return 0
    }
  fi
  return 1
}

have() {
  command -v "$1" >/dev/null 2>&1
}

run_capture() {
  out=$1
  name=$2
  shift 2
  mkdir -p "$out"
  {
    echo "\$ $*"
    if have timeout; then
      timeout 30 "$@"
    else
      "$@"
    fi
    rc=$?
    echo
    echo "return_code=$rc"
  } >"$out/$name.txt" 2>&1
  return 0
}

run_capture_long() {
  out=$1
  name=$2
  seconds=$3
  shift 3
  mkdir -p "$out"
  {
    echo "\$ $*"
    if have timeout; then
      timeout "$seconds" "$@"
    else
      "$@"
    fi
    rc=$?
    echo
    echo "return_code=$rc"
  } >"$out/$name.txt" 2>&1
  return 0
}

run_shell_capture() {
  out=$1
  name=$2
  command_text=$3
  mkdir -p "$out"
  {
    echo "\$ $command_text"
    if have timeout; then
      timeout 30 sh -c "$command_text"
    else
      sh -c "$command_text"
    fi
    rc=$?
    echo
    echo "return_code=$rc"
  } >"$out/$name.txt" 2>&1
  return 0
}

copy_capped() {
  src=$1
  dst=$2
  limit=${3:-$DEFAULT_FILE_LIMIT}
  [ -r "$src" ] || return 0
  mkdir -p "${dst%/*}"
  {
    echo "source=$src"
    echo "limit_bytes=$limit"
    echo "reported_source_bytes=$(stat -c %s "$src" 2>/dev/null || echo unknown)"
    echo "---"
  } >"$dst.meta" 2>/dev/null || true
  if have timeout && have head; then
    timeout 5 head -c "$limit" "$src" >"$dst" 2>/dev/null || true
  elif have head; then
    head -c "$limit" "$src" >"$dst" 2>/dev/null || true
  else
    dd if="$src" of="$dst" bs=4096 count=$((limit / 4096)) 2>/dev/null || true
  fi
}

copy_binary_capped() {
  src=$1
  dst=$2
  limit=${3:-$DEFAULT_FILE_LIMIT}
  [ -r "$src" ] || return 0
  mkdir -p "${dst%/*}"
  if have timeout && have head; then
    timeout 10 head -c "$limit" "$src" >"$dst" 2>/dev/null || true
  elif have head; then
    head -c "$limit" "$src" >"$dst" 2>/dev/null || true
  else
    dd if="$src" of="$dst" bs=4096 count=$((limit / 4096)) 2>/dev/null || true
  fi
}

sanitize_name() {
  echo "$1" | sed 's#^/##' | tr '/ :,' '____' | tr -cd 'A-Za-z0-9_.-'
}

copy_tree_capped() {
  base=$1
  out=$2
  pattern=${3:-'.*'}
  max_files=${4:-$TREE_COUNT_LIMIT}
  max_bytes=${5:-$TREE_FILE_LIMIT}
  [ -d "$base" ] || return 0
  mkdir -p "$out"
  find "$base" -maxdepth 8 -type f 2>/dev/null \
    | grep -E "$pattern" \
    | head -n "$max_files" \
    | while IFS= read -r f; do
        rel=${f#"$base"/}
        safe=$(echo "$rel" | tr '/' '_')
        copy_capped "$f" "$out/$safe.txt" "$max_bytes"
      done
  {
    echo "base=$base"
    echo "pattern=$pattern"
    echo "max_files=$max_files"
    echo "max_bytes_per_file=$max_bytes"
    echo "matched_files=$(find "$base" -maxdepth 8 -type f 2>/dev/null | grep -E "$pattern" | wc -l)"
  } >"$out/TREE-META.txt" 2>/dev/null || true
}

list_tree() {
  base=$1
  dst=$2
  [ -e "$base" ] || return 0
  mkdir -p "${dst%/*}"
  {
    echo "base=$base"
    find "$base" -maxdepth 8 2>/dev/null | head -n 5000 | while IFS= read -r p; do
      ls -ldZ "$p" 2>/dev/null || ls -ld "$p" 2>/dev/null || true
    done
  } >"$dst" 2>/dev/null || true
}

hash_or_stat() {
  src=$1
  dst=$2
  [ -e "$src" ] || return 0
  mkdir -p "${dst%/*}"
  {
    ls -lZ "$src" 2>/dev/null || ls -l "$src" 2>/dev/null
    stat "$src" 2>/dev/null || true
    if [ -f "$src" ] && [ -r "$src" ]; then
      if have timeout; then
        timeout 30 sha256sum "$src" 2>/dev/null || true
      else
        sha256sum "$src" 2>/dev/null || true
      fi
    fi
  } >"$dst" 2>&1
}
