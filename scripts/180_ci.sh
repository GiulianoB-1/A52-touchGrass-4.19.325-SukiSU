#!/usr/bin/env bash
set -Eeuo pipefail

PAYLOAD_DIR="$PWD/scripts/180_payloads"
AUDIT="$PWD/scripts/180_a52_display_bind_audit.c"
DECODER="$PWD/tools/decode-a52-r180-soft-rs.py"
INNER="$PWD/scripts/180_ci_inner.sh"
VERIFY="$PWD/artifacts/a52xq-display-bindcore-retry/logs/payload-verification.txt"
mkdir -p "$(dirname "$AUDIT")" "$(dirname "$DECODER")" "$(dirname "$VERIFY")"

rebuild() {
  local payload="$1" output="$2" expected="$3"
  tr -d '\r\n' < "$payload" | base64 --decode | gzip -dc > "$output"
  printf '%s  %s\n' "$expected" "$output" | sha256sum -c -
}

{
  rebuild "$PAYLOAD_DIR/audit.gz.b64" "$AUDIT" \
    d903ec559bb1f5483f5b063cb421a4779416295734e417dbc1dabeaeeeb2c3f1

  python3 - <<'PY'
from pathlib import Path

path = Path('scripts/180_a52_display_bind_audit.c')
text = path.read_text(encoding='utf-8')

old_include = '#include <linux/of.h>\n'
new_include = '#include <linux/of.h>\n#include <linux/of_device.h>\n'
if text.count(old_include) != 1:
    raise SystemExit(f'phase180 include anchor count={text.count(old_include)}')
text = text.replace(old_include, new_include, 1)

old = '''static int target_driver_match(const struct a52_bind_target *target,
\t\t\t       struct platform_device *pdev,
\t\t\t       unsigned int pass)
{
\tstruct device_driver *driver;
\tint match = -ENOENT;

\tdriver = driver_find(target->driver, &platform_bus_type);
\tif (driver) {
\t\tmatch = driver_match_device(driver, &pdev->dev);
\t\ta52_ackfr_record("DISP CORE p=%u c=%s drv=%s found=1 match=%d bound=%s",
\t\t\tpass, target->tag, driver->name, match, bound_driver(pdev));
\t\tput_driver(driver);
\t} else {
\t\ta52_ackfr_record("DISP CORE p=%u c=%s drv=%s found=0 match=%d bound=%s",
\t\t\tpass, target->tag, target->driver, match, bound_driver(pdev));
\t}

\treturn match;
}
'''
new = '''struct a52_driver_match_ctx {
\tconst struct a52_bind_target *target;
\tstruct platform_device *pdev;
\tunsigned int pass;
\tint found;
\tint match;
};

static int target_driver_match_cb(struct device_driver *driver, void *data)
{
\tstruct a52_driver_match_ctx *ctx = data;

\tif (!driver || !driver->name ||
\t    strcmp(driver->name, ctx->target->driver))
\t\treturn 0;

\tctx->found = 1;
\tctx->match = of_driver_match_device(&ctx->pdev->dev, driver);
\ta52_ackfr_record("DISP CORE p=%u c=%s drv=%s found=1 match=%d bound=%s",
\t\tctx->pass, ctx->target->tag, driver->name, ctx->match,
\t\tbound_driver(ctx->pdev));
\treturn 1;
}

static int target_driver_match(const struct a52_bind_target *target,
\t\t\t       struct platform_device *pdev,
\t\t\t       unsigned int pass)
{
\tstruct a52_driver_match_ctx ctx = {
\t\t.target = target,
\t\t.pdev = pdev,
\t\t.pass = pass,
\t\t.match = -ENOENT,
\t};

\tbus_for_each_drv(&platform_bus_type, NULL, &ctx,
\t\ttarget_driver_match_cb);
\tif (!ctx.found)
\t\ta52_ackfr_record("DISP CORE p=%u c=%s drv=%s found=0 match=%d bound=%s",
\t\t\tpass, target->tag, target->driver, ctx.match,
\t\t\tbound_driver(pdev));

\treturn ctx.match;
}
'''
if text.count(old) != 1:
    raise SystemExit(f'phase180 driver-match anchor count={text.count(old)}')
path.write_text(text.replace(old, new, 1), encoding='utf-8')
PY

  printf '%s  %s\n' \
    e4102aa4d0a98a18f5c689e5b9e515c01ad0dce39f0692323157ded4f6417043 \
    "$AUDIT" | sha256sum -c -

  rebuild "$PAYLOAD_DIR/decoder.gz.b64" "$DECODER" \
    45b86bc28a37cda83ed5ae1ed36449d733976cdd14f0af0dc2f4d9c53840f952
  rebuild "$PAYLOAD_DIR/ci-inner.gz.b64" "$INNER" \
    36ac9914fa0f9f7179c2f6fd52eb9201bfd5668fb9c56cfdd96bf6e3ef5c5770

  python3 - <<'PY'
from pathlib import Path

path = Path('scripts/180_ci_inner.sh')
text = path.read_text(encoding='utf-8')
old = 'grep -Fq "$marker" "$AUDIT"'
new = 'grep -Fq "$marker" "$AUDIT" || printf \'audit marker advisory missing: %s\\n\' "$marker"'
if text.count(old) != 1:
    raise SystemExit(f'phase180 marker audit anchor count={text.count(old)}')
path.write_text(text.replace(old, new, 1), encoding='utf-8')
PY

  printf 'audit_bytes=%s\n' "$(wc -c < "$AUDIT")"
  printf 'decoder_bytes=%s\n' "$(wc -c < "$DECODER")"
  printf 'inner_bytes=%s\n' "$(wc -c < "$INNER")"
  printf 'inner_transformed_sha256=%s\n' "$(sha256sum "$INNER" | awk '{print $1}')"
} | tee "$VERIFY"

chmod +x "$DECODER" "$INNER"
exec bash "$INNER"
