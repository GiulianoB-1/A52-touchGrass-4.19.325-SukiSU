#!/usr/bin/env python3
from __future__ import annotations

import base64
import zlib
from pathlib import Path

payload_path = Path(__file__).with_name(Path(__file__).name + ".z64")
payload = base64.b64decode(payload_path.read_text().strip())
source = zlib.decompress(payload).decode("utf-8")

# The proven Golden lineage inserts its own q2 observer between
# wait_for_completion_timeout() and Samsung's timeout branch. Retarget the
# compressed Phase443 patch operation to the stable wait call itself.
old_payload = r"""    old='''\tret = wait_for_completion_timeout(\n\t\t\t&dsi_ctrl->irq_info.cmd_dma_done,\n\t\t\tmsecs_to_jiffies(DSI_CTRL_TX_TO_MS));\n\tif (ret == 0 && !atomic_read(&dsi_ctrl->dma_irq_trig)) {\n'''\n    new='''\tret = wait_for_completion_timeout(\n\t\t\t&dsi_ctrl->irq_info.cmd_dma_done,\n\t\t\tmsecs_to_jiffies(DSI_CTRL_TX_TO_MS));\n\tif (a52_p443_active()) {\n\t\ta52_p443_terminal(dsi_ctrl, ret);\n\t\tif (!atomic_read(&dsi_ctrl->dma_irq_trig))\n\t\t\tdsi_ctrl_disable_status_interrupt(dsi_ctrl,\n\t\t\t\tDSI_SINT_CMD_MODE_DMA_DONE);\n\t\tgoto done;\n\t}\n\tif (ret == 0 && !atomic_read(&dsi_ctrl->dma_irq_trig)) {\n'''\n"""
new_payload = r"""    old='''\tret = wait_for_completion_timeout(\n\t\t\t&dsi_ctrl->irq_info.cmd_dma_done,\n\t\t\tmsecs_to_jiffies(DSI_CTRL_TX_TO_MS));\n'''\n    new='''\tret = wait_for_completion_timeout(\n\t\t\t&dsi_ctrl->irq_info.cmd_dma_done,\n\t\t\tmsecs_to_jiffies(DSI_CTRL_TX_TO_MS));\n\tif (a52_p443_active()) {\n\t\ta52_p443_terminal(dsi_ctrl, ret);\n\t\tif (!atomic_read(&dsi_ctrl->dma_irq_trig))\n\t\t\tdsi_ctrl_disable_status_interrupt(dsi_ctrl,\n\t\t\t\tDSI_SINT_CMD_MODE_DMA_DONE);\n\t\tgoto done;\n\t}\n'''\n"""
if source.count(old_payload) != 1:
    raise SystemExit(
        "Phase443 compatibility preflight failed: paired-timeout block count=" +
        str(source.count(old_payload))
    )
source = source.replace(old_payload, new_payload, 1)

exec(compile(source, str(Path(__file__).with_suffix(".expanded.py")), "exec"), globals())
