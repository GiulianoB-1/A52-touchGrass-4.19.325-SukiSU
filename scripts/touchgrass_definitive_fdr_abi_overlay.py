#!/usr/bin/env python3
from pathlib import Path
import sys

INC = '#include <linux/tg_fdr.h>\n'


def main(root: Path):
    p = root / 'fs/ioctl.c'
    s = p.read_text()
    if INC.strip() not in s:
        anchor = '#include <linux/sched/signal.h>\n'
        if s.count(anchor) != 1:
            raise SystemExit('fs/ioctl.c include anchor mismatch')
        s = s.replace(anchor, anchor + INC, 1)

    old = '''long vfs_ioctl(struct file *filp, unsigned int cmd, unsigned long arg)
{
\tint error = -ENOTTY;

\tif (!filp->f_op->unlocked_ioctl)
\t\tgoto out;

\terror = filp->f_op->unlocked_ioctl(filp, cmd, arg);
\tif (error == -ENOIOCTLCMD)
\t\terror = -ENOTTY;
 out:
\treturn error;
}
'''
    new = '''long vfs_ioctl(struct file *filp, unsigned int cmd, unsigned long arg)
{
\tint error = -ENOTTY;
\tbool fdr = tg_fdr_streaming_active();
\tu32 fop_obj = 0;
\tu64 rdev = 0;

\tif (fdr) {
\t\tstruct inode *inode = file_inode(filp);
\t\tfop_obj = tg_fdr_object_id(TG_FDR_OBJ_OTHER, filp->f_op);
\t\tif (inode)
\t\t\trdev = (u64)inode->i_rdev;
\t\tTG_FDR_TAG(TG_FDR_SUBSYS_ANDROID, "ABI:IOCTL_PRE", 0, fop_obj,
\t\t\t   cmd, arg, rdev, (u64)(unsigned long)filp, 0);
\t}

\tif (!filp->f_op->unlocked_ioctl)
\t\tgoto out;

\terror = filp->f_op->unlocked_ioctl(filp, cmd, arg);
\tif (error == -ENOIOCTLCMD)
\t\terror = -ENOTTY;
 out:
\tif (fdr)
\t\tTG_FDR_TAG(TG_FDR_SUBSYS_ANDROID, "ABI:IOCTL_POST", error, fop_obj,
\t\t\t   cmd, arg, rdev, (u64)(unsigned long)filp,
\t\t\t   error < 0 && error != -ENOTTY ? TG_FDR_FLAG_CRITICAL : 0);
\treturn error;
}
'''
    if new not in s:
        if s.count(old) != 1:
            raise SystemExit('fs/ioctl.c vfs_ioctl anchor mismatch')
        s = s.replace(old, new, 1)
    p.write_text(s)
    print('TouchGrass FDR device-ioctl ABI recorder staged')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('usage: touchgrass_definitive_fdr_abi_overlay.py <kernel-root>')
    main(Path(sys.argv[1]).resolve())
