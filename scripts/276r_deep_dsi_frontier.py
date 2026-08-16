#!/usr/bin/env python3
from __future__ import annotations
import re, sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit(f"usage: {sys.argv[0]} <gki-common-root>")
ROOT=Path(sys.argv[1])
PANEL=ROOT/'drivers/a52_display/msm/dsi/dsi_panel.c'
DISPLAY=ROOT/'drivers/a52_display/msm/dsi/dsi_display.c'
CTRL=ROOT/'drivers/a52_display/msm/dsi/dsi_ctrl.c'
PANEL_MARK='A52_PHASE276R_DEEP_TARGET_TRACKER_V1'
DISPLAY_MARK='A52_PHASE276R_DSI_HOST_DEEP_FRONTIER_V1'
CTRL_MARK='A52_PHASE276R_DSI_CTRL_DEEP_FRONTIER_V1'
TARGET='TX_LEVEL1_KEY_ENABLE'

def replace_once(s,o,n,w):
    c=s.count(o)
    if c!=1: raise RuntimeError(f'{w}: anchor count {c}')
    return s.replace(o,n,1)

def function_span(text,name):
    pat=re.compile(r'^[ \t]*(?:static[ \t]+)?(?:int|ssize_t|void|bool)[ \t]+'+re.escape(name)+r'\s*\([^;]*?\)\s*\{',re.M|re.S)
    ms=list(pat.finditer(text))
    if len(ms)==1:
        m=ms[0]; start=m.start(); brace=text.find('{',m.start(),m.end())
    elif name == 'dsi_panel_tx_cmd_set':
        sigs=list(re.finditer(r'^[ \t]*(?:static[ \t]+)?int[ \t]+'+re.escape(name)+r'\s*\(', text, re.M))
        if len(sigs)!=2: raise RuntimeError(f'{name}: conditional signature count {len(sigs)}')
        pp=text.rfind('#if defined(CONFIG_DISPLAY_SAMSUNG)',0,sigs[0].start())
        if pp < 0: raise RuntimeError(f'{name}: conditional #if missing')
        endif=text.find('#endif',sigs[1].start())
        if endif < 0: raise RuntimeError(f'{name}: conditional #endif missing')
        brace=text.find('{',endif)
        start=pp
    else:
        raise RuntimeError(f'{name}: definition count {len(ms)}')
    d=0; ins=inc=esc=False
    for i in range(brace,len(text)):
        c=text[i]
        if esc: esc=False; continue
        if c=='\\' and (ins or inc): esc=True; continue
        if c=='"' and not inc: ins=not ins; continue
        if c=="'" and not ins: inc=not inc; continue
        if ins or inc: continue
        if c=='{': d+=1
        elif c=='}':
            d-=1
            if d==0:return start,i+1
    raise RuntimeError(name+' unterminated')

def replace_func(text,name,fn):
    a,b=function_span(text,name); return text[:a]+fn(text[a:b])+text[b:]

def add_include(text):
    inc='#include <linux/a52_ack_secure_flight_recorder.h>\n'
    if inc in text:return text
    m=re.search(r'^#include .*\n',text,re.M)
    if not m: raise RuntimeError('no include anchor')
    return text[:m.end()]+inc+text[m.end():]

def inject_stmt(body, needle, pre, post, occurrence=0):
    starts=[m.start() for m in re.finditer(re.escape(needle),body)]
    if len(starts)<=occurrence: raise RuntimeError(f'{needle}: occurrence {occurrence}, have {len(starts)}')
    p=starts[occurrence]; line=body.rfind('\n',0,p)+1
    d=0; ins=inc=esc=False; end=None
    for i in range(p,len(body)):
        c=body[i]
        if esc: esc=False; continue
        if c=='\\' and (ins or inc): esc=True; continue
        if c=='"' and not inc: ins=not ins; continue
        if c=="'" and not ins: inc=not inc; continue
        if ins or inc: continue
        if c=='(': d+=1
        elif c==')': d-=1
        elif c==';' and d==0: end=i+1; break
    if end is None: raise RuntimeError('statement end not found '+needle)
    indent=re.match(r'[ \t]*', body[line:]).group(0)
    return body[:line]+indent+pre+';\n'+body[line:end]+'\n'+indent+post+';'+body[end:]

def patch_panel(text):
    if PANEL_MARK in text:return text
    if 'A52_PHASE276_DSI_PANEL_TX_FRONTIER_V1' not in text: raise RuntimeError('Phase276 panel frontier missing')
    anchor='/* A52_PHASE276_DSI_PANEL_TX_FRONTIER_V1\n'
    block=(
        '/* '+PANEL_MARK+'\n'
        ' * Diagnostic-only target correlation and cmd_lock owner tracking.\n'
        ' * No lock acquisition/release, command, flag, payload, or return changes.\n'
        ' */\n'
        'static atomic_t a52_p276r_deep_pid = ATOMIC_INIT(-1);\n'
        'static atomic_t a52_p276r_cmd_owner_pid = ATOMIC_INIT(-1);\n'
        'static atomic_t a52_p276r_cmd_owner_tgid = ATOMIC_INIT(-1);\n'
        'static atomic_t a52_p276r_cmd_owner_type = ATOMIC_INIT(-1);\n'
        'bool a52_p276r_deep_active(void)\n{\n\treturn atomic_read(&a52_p276r_deep_pid) == task_pid_nr(current);\n}\n\n'
    )
    text=replace_once(text,anchor,block+anchor,'panel deep tracker marker')
    def f(body):
        old=(f'\tif (type == {TARGET})\n'
             '\t\ta52_ackfr_record("P276 T L ty=%d p=0 h=%u", type,\n'
             '\t\t\tmutex_is_locked(&vdd->cmd_lock));\n'
             '\tmutex_lock(&vdd->cmd_lock);\n')
        new=(f'\tif (type == {TARGET}) {{\n'
             '\t\ta52_ackfr_record("P276 T Q p=%d g=%d y=%d",\n'
             '\t\t\tatomic_read(&a52_p276r_cmd_owner_pid),\n'
             '\t\t\tatomic_read(&a52_p276r_cmd_owner_tgid),\n'
             '\t\t\tatomic_read(&a52_p276r_cmd_owner_type));\n'
             '\t\ta52_ackfr_record("P276 T L ty=%d p=0 h=%u", type,\n'
             '\t\t\tmutex_is_locked(&vdd->cmd_lock));\n\t}\n'
             '\tmutex_lock(&vdd->cmd_lock);\n'
             '\tatomic_set(&a52_p276r_cmd_owner_pid, task_pid_nr(current));\n'
             '\tatomic_set(&a52_p276r_cmd_owner_tgid, task_tgid_nr(current));\n'
             '\tatomic_set(&a52_p276r_cmd_owner_type, type);\n')
        body=replace_once(body,old,new,'cmd_lock owner tracker')
        old2=(f'\t\tif (type == {TARGET})\n'
              '\t\t\ta52_ackfr_record("P276 T O i=%d p=0 mt=%u tl=%u fl=%x", i,\n'
              '\t\t\t\t(unsigned int)cmds->msg.type,\n'
              '\t\t\t\t(unsigned int)cmds->msg.tx_len,\n'
              '\t\t\t\t(unsigned int)cmds->msg.flags);\n'
              '\t\tlen = ops->transfer(panel->host, &cmds->msg);\n')
        new2=(f'\t\tif (type == {TARGET}) {{\n'
              '\t\t\ta52_ackfr_record("P276 T O i=%d p=0 mt=%u tl=%u fl=%x", i,\n'
              '\t\t\t\t(unsigned int)cmds->msg.type,\n'
              '\t\t\t\t(unsigned int)cmds->msg.tx_len,\n'
              '\t\t\t\t(unsigned int)cmds->msg.flags);\n'
              '\t\t\tatomic_set(&a52_p276r_deep_pid, task_pid_nr(current));\n\t\t}\n'
              '\t\tlen = ops->transfer(panel->host, &cmds->msg);\n'
              f'\t\tif (type == {TARGET})\n\t\t\tatomic_set(&a52_p276r_deep_pid, -1);\n')
        body=replace_once(body,old2,new2,'deep target around host transfer')
        unlock='\tmutex_unlock(&vdd->cmd_lock);\n'
        repl=('\tatomic_set(&a52_p276r_cmd_owner_pid, -1);\n'
              '\tatomic_set(&a52_p276r_cmd_owner_tgid, -1);\n'
              '\tatomic_set(&a52_p276r_cmd_owner_type, -1);\n'+unlock)
        return replace_once(body,unlock,repl,'cmd owner clear')
    return replace_func(text,'dsi_panel_tx_cmd_set',f)

def patch_display(text):
    if DISPLAY_MARK in text:return text
    text=add_include(text)
    marker=('/* '+DISPLAY_MARK+'\n * Exact type-42 call-stack checkpoints; gated by the synchronous target PID.\n */\n'
            'extern bool a52_p276r_deep_active(void);\n')
    a,b=function_span(text,'dsi_host_transfer'); text=text[:a]+marker+text[a:]
    def f(body):
        anchor='#endif\n\n\tif (!host || !msg) {'
        body=replace_once(body, anchor, '#endif\n\n\tif (a52_p276r_deep_active())\n\t\ta52_ackfr_record("P276 D H s=0");\n\n\tif (!host || !msg) {', 'host entry checkpoint')
        step=1
        for needle in ['dsi_display_clk_ctrl(', 'dsi_display_wake_up(', 'dsi_display_cmd_engine_enable(',
                       'dsi_host_alloc_cmd_tx_buffer(', 'dsi_display_broadcast_cmd(', 'dsi_ctrl_cmd_transfer(',
                       'dsi_display_cmd_engine_disable(']:
            n=body.count(needle)
            for k in range(n):
                body=inject_stmt(body,needle,
                    f'if (a52_p276r_deep_active()) a52_ackfr_record("P276 D H s={step} p=0")',
                    f'if (a52_p276r_deep_active()) a52_ackfr_record("P276 D H s={step} p=1")',k)
                step+=1
        if 'dsi_ctrl_cmd_transfer(' not in body: raise RuntimeError('host transfer has no dsi_ctrl_cmd_transfer')
        return body
    return replace_func(text,'dsi_host_transfer',f)

def patch_ctrl(text):
    if CTRL_MARK in text:return text
    text=add_include(text)
    marker=('/* '+CTRL_MARK+'\n * Deep target-only controller/message/kickoff/wait checkpoints. Observation only.\n */\n'
            'extern bool a52_p276r_deep_active(void);\n')
    a,b=function_span(text,'dsi_ctrl_cmd_transfer'); text=text[:a]+marker+text[a:]
    def ctf(body):
        body=replace_once(body, '\tint rc = 0;\n', '\tint rc = 0;\n\n\tif (a52_p276r_deep_active())\n\t\ta52_ackfr_record("P276 D C s=0 f=%x", flags ? *flags : 0);\n', 'ctrl entry checkpoint')
        old='\tmutex_lock(&dsi_ctrl->ctrl_lock);\n'
        new=('\tif (a52_p276r_deep_active()) a52_ackfr_record("P276 D C s=1 p=0 h=%u", mutex_is_locked(&dsi_ctrl->ctrl_lock));\n'
             '\tmutex_lock(&dsi_ctrl->ctrl_lock);\n'
             '\tif (a52_p276r_deep_active()) a52_ackfr_record("P276 D C s=1 p=1");\n')
        body=replace_once(body,old,new,'ctrl_lock checkpoints')
        for step,needle in [(2,'dsi_ctrl_check_state('),(3,'dsi_message_rx('),(4,'dsi_message_tx('),(5,'dsi_ctrl_update_state(')]:
            if needle in body:
                body=inject_stmt(body,needle,
                    f'if (a52_p276r_deep_active()) a52_ackfr_record("P276 D C s={step} p=0")',
                    f'if (a52_p276r_deep_active()) a52_ackfr_record("P276 D C s={step} p=1")')
        old2='\tmutex_unlock(&dsi_ctrl->ctrl_lock);\n'
        new2=('\tif (a52_p276r_deep_active()) a52_ackfr_record("P276 D C s=6 r=%d", rc);\n'+old2)
        return replace_once(body,old2,new2,'ctrl exit')
    text=replace_func(text,'dsi_ctrl_cmd_transfer',ctf)
    def mtx(body):
        anchor='#endif\n\n\t/* Select the tx mode to transfer the command */'
        body=replace_once(body, anchor, '#endif\n\n\tif (a52_p276r_deep_active())\n\t\ta52_ackfr_record("P276 D M s=0 f=%x mt=%u l=%u", flags ? *flags : 0, (unsigned int)msg->type, (unsigned int)msg->tx_len);\n\n\t/* Select the tx mode to transfer the command */', 'message entry checkpoint')
        step=1
        for needle in ['dsi_message_validate_tx_mode(', 'mipi_dsi_create_packet(', 'dsi_ctrl_copy_and_pad_cmd(']:
            if needle in body:
                body=inject_stmt(body,needle,
                    f'if (a52_p276r_deep_active()) a52_ackfr_record("P276 D M s={step} p=0")',
                    f'if (a52_p276r_deep_active()) a52_ackfr_record("P276 D M s={step} p=1 r=%d", rc)')
                step+=1
        if body.count('dsi_kickoff_msg_tx(')!=1: raise RuntimeError('dsi_message_tx kickoff call count '+str(body.count('dsi_kickoff_msg_tx(')))
        body=inject_stmt(body,'dsi_kickoff_msg_tx(',
            'if (a52_p276r_deep_active()) a52_ackfr_record("P276 D M k=0")',
            'if (a52_p276r_deep_active()) a52_ackfr_record("P276 D M k=1")')
        return body
    text=replace_func(text,'dsi_message_tx',mtx)
    def kickoff(body):
        anchor='#endif\n\tSDE_EVT32(dsi_ctrl->cell_index, SDE_EVTLOG_FUNC_ENTRY, flags,\n'
        body=replace_once(body, anchor, '#endif\n\tif (a52_p276r_deep_active())\n\t\ta52_ackfr_record("P276 D K s=0 f=%x", flags);\n\tSDE_EVT32(dsi_ctrl->cell_index, SDE_EVTLOG_FUNC_ENTRY, flags,\n', 'kickoff entry checkpoint')
        step=1
        for needle in ['kickoff_command_non_embedded_mode(', 'kickoff_command(', 'kickoff_fifo_command(']:
            n=body.count(needle)
            for k in range(n):
                body=inject_stmt(body,needle,
                    f'if (a52_p276r_deep_active()) a52_ackfr_record("P276 D K s={step} p=0")',
                    f'if (a52_p276r_deep_active()) a52_ackfr_record("P276 D K s={step} p=1")',k)
                step+=1
        branch='\t\tif (flags & DSI_CTRL_CMD_ASYNC_WAIT) {\n'
        branch_new=('\t\tif (a52_p276r_deep_active())\n'
                    '\t\t\ta52_ackfr_record("P276 D K a=%u", !!(flags & DSI_CTRL_CMD_ASYNC_WAIT));\n'+branch)
        body=replace_once(body,branch,branch_new,'async wait branch')
        body=inject_stmt(body,'dsi_ctrl_dma_cmd_wait_for_done(',
            'if (a52_p276r_deep_active()) a52_ackfr_record("P276 D K w=0")',
            'if (a52_p276r_deep_active()) a52_ackfr_record("P276 D K w=1")')
        return body
    text=replace_func(text,'dsi_kickoff_msg_tx',kickoff)
    def waitfn(body):
        body=replace_once(body, '\tstruct dsi_ctrl_hw_ops dsi_hw_ops;\n', '\tstruct dsi_ctrl_hw_ops dsi_hw_ops;\n\n\tif (a52_p276r_deep_active())\n\t\ta52_ackfr_record("P276 D W s=0");\n', 'wait entry checkpoint')
        if body.count('wait_for_completion_timeout(')!=1:
            raise RuntimeError('DMA completion wait count '+str(body.count('wait_for_completion_timeout(')))
        body=inject_stmt(body,'wait_for_completion_timeout(',
            'if (a52_p276r_deep_active()) a52_ackfr_record("P276 D M w=0")',
            'if (a52_p276r_deep_active()) a52_ackfr_record("P276 D M w=1 v=%d", ret)')
        return body
    text=replace_func(text,'dsi_ctrl_dma_cmd_wait_for_done',waitfn)
    return text

PANEL.write_text(patch_panel(PANEL.read_text()))
DISPLAY.write_text(patch_display(DISPLAY.read_text()))
CTRL.write_text(patch_ctrl(CTRL.read_text()))
print('Phase276R deep DSI root-cause frontier staged')
