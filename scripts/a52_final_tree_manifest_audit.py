#!/usr/bin/env python3
"""Final assembled A52 5.10 source contract audit and reproducible identity.

Use --strict both before compiling and after compiling with --image.
Missing REQUIRED feature markers/logic fail closed. Experimental markers not
enabled by this lineage are deliberately NOT required.
"""
from __future__ import annotations
import argparse,hashlib,json,os,subprocess
from pathlib import Path

EXPECT={
 "P146 ION legacy mask":("drivers/staging/android/ion/ion.c",
     ("A52_ION_LEGACY_SYSTEM_HEAP_MASK_COMPAT",)),
 "P148 ION exporter flags":("drivers/staging/android/ion/ion_dma_buf.c",
     ("A52_ION_DMABUF_FLAGS_FALLBACK","*flags = buffer->flags;")),
 "P149 secure heap gate":("drivers/staging/android/ion/ion.c",
     ("A52_ION_SYSTEM_HEAP_NONSECURE_GATE",)),
 "P301 RSC trace":("drivers/soc/qcom/rpmh-rsc.c",
     ("A52_PHASE301_RPMH_RSC_CONTRACT_TRACE_V1",)),
 "P305 flush compat":("drivers/soc/qcom/rpmh-rsc.c",
     ("A52_PHASE305_DISPLAY_RPMH_FLUSH_COMPAT_V1",
      "int a52_rpmh_flush_compat(const struct device *dev)","spin_trylock(&drv->lock)")),
 "P305 display call":("a52-port-compat.h",
     ("#define rpmh_flush(d) a52_rpmh_flush_compat((d))",)),
 "P423 solver compat":("drivers/soc/qcom/rpmh-rsc.c",
     ("A52_PHASE423_RPMH_SOLVER_MODE_COMPAT_V1",
      "int a52_rpmh_mode_solver_set_compat(const struct device *dev, bool enable)",
      "P446R2 RPMH_TCS_DRAIN_TIMEOUT","return -ETIMEDOUT;")),
 "P423 routed SDE solver":("a52-port-compat.h",
     ("#define rpmh_mode_solver_set(d,e) a52_rpmh_mode_solver_set_compat((d), (e))",)),
 "P438 devbw boolean":("drivers/devfreq/devfreq_devbw.c",
     ("A52_PHASE438_DEVBW_SEMANTIC_PARITY_V1",
      "atomic_read(&d->df->suspend_count)","if (count <= 0)")),
 "P446N IRQ rearm":("drivers/a52_display/msm/dsi/dsi_ctrl.c",
     ("A52_PHASE446N_DSI_IRQ_REARM_V1",)),
 "P446P DSI packets":("drivers/a52_display/msm/dsi/dsi_ctrl.c",
     ("A52_PHASE446P_QCOM_DSI_PACKET_V1","a52_p446p_qcom_create_packet(&packet, msg)")),
 "P446Q GEM PRIME":("drivers/a52_display/msm/msm_gem_prime.c",
     ("A52_PHASE446Q_ION_PRIME_TRACE_V1",)),
 "P446R commit planes":("drivers/a52_display/msm/msm_atomic.c",
     ("A52_PHASE446R_COMMIT_GEM_LIFETIME_V1","P446R COMMIT")),
 "P446R GEM free":("drivers/a52_display/msm/msm_gem.c",
     ("A52_PHASE446R_COMMIT_GEM_LIFETIME_V1","WARN_ON(is_active(msm_obj))")),
 "P446R2 RPMH":("drivers/soc/qcom/rpmh-rsc.c",
     ("A52_PHASE446R2_BOUNDED_RPMH_TCS_V1","P446R2 RPMH_FLUSH_EBUSY")),
 "P446R2 PON":("drivers/a52_secure/a52_pon_wake_diag.c",
     ("A52_PHASE446R2_PON_WAKE_DIAG_V1","enable_irq_wake","KEY_POWER")),
 "P446S PON state":("drivers/a52_secure/a52_pon_wake_diag.c",
     ("A52_PHASE446S_PON_KEY_STATE_V1","!!(val & BIT(0))",
      "IRQF_TRIGGER_RISING","IRQF_TRIGGER_FALLING","P446S PON EV_KEY")),
 "P446S memlat ownership":("drivers/devfreq/governor_memlat.c",
     ("A52_PHASE446S_MEMLAT_LIFECYCLE_V1","monitor_ready",
      "p446s_memlat_owner(df)","P446S MEMLAT skip_monitor")),
 "P446S devfreq timer":("drivers/devfreq/devfreq.c",
     ("A52_PHASE446S_DEVFREQ_TIMER_GUARD_V1","!devfreq->work.work.func")),
 "P446S unsampled frames":("drivers/a52_display/msm/msm_atomic.c",
     ("A52_PHASE446S_UNSAMPLED_COMMIT_V1","P446S FRAME seq=")),
 "P446S ioctl caller":("drivers/gpu/drm/drm_atomic_uapi.c",
     ("A52_PHASE446S_ATOMIC_IOCTL_TRACE_V1","P446S IOCTL seq=")),
 "P446S ARM memlat PMU":("drivers/devfreq/arm-memlat-mon.c",
     ("A52_MEMLAT_NO_EXCLUDE_IDLE","attr->exclude_idle = 0;",
      "P446S MEMLAT PERF_FAIL","P446S MEMLAT START_ROLLBACK")),
 "P446S PON RESIN":("drivers/a52_secure/a52_pon_wake_diag.c",
     ("A52_PHASE446S_PON_RESIN_V1","KEY_VOLUMEDOWN",
      "!!(val & BIT(1))","P446S RESIN EV_KEY")),
 "P446S PMIC GPIO vendor":("drivers/pinctrl/qcom/a52-pinctrl-spmi-gpio.c",
     ("A52_PHASE446S_LEGACY_PMIC_GPIO_V1","P446T PMIC_GPIO probe",
      '.compatible = "qcom,spmi-gpio"','a52-qcom-spmi-gpio',
      "qcom,gpios-disallowed","platform_get_irq(pdev, pin_idx)")),
 "P446S PMIC GPIO Kbuild":("drivers/pinctrl/qcom/Makefile",
     ("obj-y += a52-pinctrl-spmi-gpio.o",)),
 "P446S GPU CX votes":("drivers/regulator/core.c",
     ("A52_PHASE446S_GPU_CX_VOTES_V1","P446S GPU_CX_CONSUMER")),
 "P446S GMU 5s poll":("drivers/gpu/msm/kgsl_gmu.c",
     ("A52_PHASE446S_GMU_SLUMBER_DIAG_V1","P446S GMU_CX POLL",
      "msecs_to_jiffies(CX_GDSC_TIMEOUT)")),
 "P446S KGSL lock timing":("drivers/gpu/msm/kgsl_device.h",
     ("A52_PHASE446S_KGSL_MUTEX_100MS_V1","P446S KGSL_MUTEX")),
 "P446S GPU vote API":("include/linux/a52_gpu_cx_trace.h",
     ("A52_PHASE446S_GPU_CX_VOTES_V1",)),
 "P446S KGSL SMMU runtime PM":("drivers/iommu/arm/arm-smmu/arm-smmu.c",
     ("A52_KGSL_SMMU_RPM_V1","smmu-kgsl@3d40000",
      "pm_runtime_set_autosuspend_delay(dev, 20)",
      "pm_runtime_get_noresume(dev)","pm_runtime_put_autosuspend(dev)",
      "P446S KGSL_SMMU_RPM","runtime_suspend_vdd_off",
      "runtime_resume_vdd_on")),
 "P446T KGSL hold":("drivers/gpu/msm/kgsl_pwrctrl.c",
     ("A52_PHASE446T_KGSL_SMMU_ACTIVE_HOLD_V1","P446T KGSL_SMMU_HOLD")),
 "P446T SMMU release":("drivers/gpu/msm/kgsl_gmu.c",
     ("A52_PHASE446T_SMMU_RELEASE_BEFORE_CX_POLL_V1",)),
 "P446T no nested GDSC":("drivers/regulator/a52-legacy-gdsc-regulator.c",
     ("A52_GDSC_NO_NESTED_REGULATOR",)),
 "P446U request all properties":("drivers/gpu/drm/drm_atomic_uapi.c",
     ("A52_PHASE446U_ATOMIC_REQUEST_PLANE91_V1","P446U PROP seq=",
      "P446U SET_FB seq=","P446U SET_CRTC seq=")),
 "P446U plane 91 swap":("drivers/a52_display/msm/msm_atomic.c",
     ("P446U FRAME_JOIN seq=","P446U PLANE91 seq=")),
 "P446U msm check":("drivers/a52_display/msm/msm_drv.c",
     ("P446U MSM_CHECK seq=",)),
 "P446U SDE plane check and prepare":("drivers/a52_display/msm/sde/sde_plane.c",
     ("P446U SDE_PLANE_CHECK","P446U PREPARE_FB")),
 "P446U SDE crtc check":("drivers/a52_display/msm/sde/sde_crtc.c",
     ("P446U SDE_CRTC_CHECK",)),
 "P446U brightness":("drivers/a52_display/msm/dsi/dsi_panel.c",
     ("P446U BRIGHTNESS ts=",)),
 "P446U Samsung brightness":("drivers/a52_display/msm/samsung/ss_dsi_panel_common.c",
     ("P446U SAMSUNG_BRIGHTNESS ts=",)),
 "P446U P445 directory":("drivers/a52_display/msm/a52_phase445.c",
     ("A52_PHASE446U_P445_240_SECTIONS_V1",
      "#define P445_HEADER_BYTES SZ_16K",
      "#define P445_MAX_SECTIONS 240U",
      "BUILD_BUG_ON(sizeof(struct p445_header)>P445_HEADER_BYTES)")),
 "P446U safe console and tty gate":("drivers/usb/gadget/function/u_serial.c",
     ("A52_PHASE446U_USB_SAFE_COORDINATOR_V2","U446 PRE count=",
      "U446 CONSOLE worker-stop-ack","U446 CONSOLE request-dequeue-enter",
      "U446 CONSOLE request-free","U446 UNBIND free_port before_close_wait",
      "a52u_block_opens","a52u_log_tty_holders")),
 "P446U safe gserial coordinator":("drivers/usb/gadget/legacy/serial.c",
     ('early_param("a52.usb_handover"',"U446 coordinator",
      "U446 UNBIND gs_unbind enter","U446 UNBIND func_put enter",
      "U446 UDC legacy-unregistered","U446 HANDOVER_DISABLED")),
 "P446U outside-lock ConfigFS":("drivers/usb/gadget/configfs.c",
     ("U446 UDC request","U446 UDC configfs probe-enter",
      "U446 UDC configfs probe-exit","U446 UDC adb-bound")),
 "P446U recorder persistence":("drivers/a52_secure/a52_ack_secure_flight_recorder.c",
     ('strncmp(fmt, "U446", 4)',)), 
 "P446V GETPROPERTIES GETPROPERTY and SETPROPERTY":("drivers/gpu/drm/drm_mode_object.c",
     ("A52_PHASE446V_HWC_READ_SIDE_V1","P446V READ_PROP","P446V READ_OBJ","P446V SETPROPERTY")),
 "P446V property metadata and blobs":("drivers/gpu/drm/drm_property.c",
     ("A52_PHASE446V_HWC_READ_SIDE_V1","P446V GETPROPERTY","P446V GETBLOB_DATA")),
 "P446V connector modes":("drivers/gpu/drm/drm_connector.c",
     ("A52_PHASE446V_HWC_READ_SIDE_V1","P446V CONNECTOR_MODE")),
 "P446V CRTC ioctl":("drivers/gpu/drm/drm_crtc.c",
     ("A52_PHASE446V_HWC_READ_SIDE_V1","P446V GETCRTC")),
 "P446V encoder ioctl":("drivers/gpu/drm/drm_encoder.c",
     ("A52_PHASE446V_HWC_READ_SIDE_V1","P446V GETENCODER")),
 "P446V SDE snapshot":("drivers/a52_display/msm/sde/sde_kms.c",
     ("A52_PHASE446V_SDE_AOD_STATE_DIFF_V1","P446V SNAP_CRTC","P446V SNAP_CONN","P446V SNAP_DSI",
      "P446V SNAP_SAMSUNG","P446V EVENT NORMAL_DISPLAY_ON")),
 "P446V SDE encoder details":("drivers/a52_display/msm/sde/sde_encoder.c",
     ("A52_PHASE446V_SDE_AOD_STATE_DIFF_V1","P446V SNAP_ENC_DETAIL")),
 "P446V physical command autorefresh":("drivers/a52_display/msm/sde/sde_encoder_phys_cmd.c",
     ("A52_PHASE446V_PHYS_CMD_AUTOREFRESH_STATE_V1","P446V SNAP_AUTORF")),
 "P446V normal-on request window":("drivers/gpu/drm/drm_atomic_uapi.c",
     ("P446V POST_WINDOW_BEGIN","a52v_post_window_next")),
 "P446V pre-off and after normal-on":("drivers/a52_display/msm/samsung/ss_dsi_panel_common.c",
     ("PRE_FIRST_PANEL_OFF","a52v_display_event(true)")),
 "P446V two-peripheral PMK8350 RTC":("drivers/rtc/rtc-pm8xxx.c",
     ("A52_PHASE446V_PMK8350_RTC_V1",'"qcom,pmk8350-rtc"',
      "0x6140","0x6240","P446V RTC PROBE","P446V RTC REGISTER")),

 "P446AB default retains splash votes, Plan B handoff OFF":("drivers/a52_display/msm/sde/sde_kms.c",
     ("A52_PHASE446W_SPLASH_SELECT_V1","static bool a52w_cold;",
      "static bool a52w_handoff = false;","P446W SPLASH COLD_SELECTED=0",
      "a52w_handoff_selected","early_param(\"a52.446w_cold\"")),
 "P446W-B actual first-frame HWC modeset":("drivers/a52_display/msm/msm_drv.c",
     ("A52_PHASE446W_HANDOFF_B_ACTUAL_FRAME_V2","P446W HANDOFF begin",
      "P446W HANDOFF end","drm_atomic_get_crtc_state","drm_atomic_get_connector_state")),
 "P446W-B MSM tail teardown and snapshot":("drivers/a52_display/msm/msm_atomic.c",
     ("A52_PHASE446W_HANDOFF_B_ACTUAL_FRAME_V2","P446W HANDOFF phase=disable-enter",
      "P446W HANDOFF phase=enable-enter","a52w_handoff_finish")),
 "P446W-B DSI lifecycle":("drivers/a52_display/msm/dsi/dsi_display.c",
     ("A52_PHASE446W_HANDOFF_B_DSI_LIFECYCLE_V1","P446W HANDOFF DSI dsi_display_prepare",
      "P446W HANDOFF DSI dsi_display_unprepare")),
 "P446W-B Samsung panel command trace":("drivers/a52_display/msm/dsi/dsi_panel.c",
     ("A52_PHASE446W_HANDOFF_B_DSI_LIFECYCLE_V1","P446W HANDOFF panel ON_CMDS begin",
      "P446W HANDOFF panel ON_CMDS end")),
 "P446W immediate full post-normal-on snapshot":("drivers/a52_display/msm/samsung/ss_dsi_panel_common.c",
     ("P446W_FULL_POST_NORMAL_ON","a52v_sde_snapshot")),
 "P446W early port3 and isolated U446 tty gate":("drivers/usb/gadget/function/u_serial.c",
     ("A52_PHASE446W_TTYGS3_EARLY_PORT_V1","P446W USB early legacy",
      "a52w_early_console_line","P446W U446 DEFER")),
 "P446W port-scoped coordinator":("drivers/usb/gadget/legacy/serial.c",
     ("P446W U446 COORDINATOR START","a52_446u_preflight_port(a52w_early_console_line())")),
 "P446W ConfigFS defer printk":("drivers/usb/gadget/configfs.c",
     ("P446W U446 CONFIGFS defer","P446W U446 CONFIGFS probe")),

 "P446X STM FTS touchscreen":("drivers/input/touchscreen/stm/fts5cu56a/fts_ts.c",
     ("A52_PHASE446X_STM_FTS_READ_ONLY_V1",
      "P446X TOUCH probe-rc=0","P446X TOUCH I2C_ID",
      "P446X TOUCH EVENT","P446X TOUCH I2C_READ_ERR",
      "P446X TOUCH REGULATOR_ENABLE","early_param(\"a52.touch\"",
      "struct proc_ops","wakeup_source_register")),
 "P446X firmware irrevocably blocked":("drivers/input/touchscreen/stm/fts5cu56a/fts_fwu.c",
     ("A52_PHASE446X_STM_FTS_READ_ONLY_V1","FW_WRITE_BLOCKED","P446X TOUCH FW_VERSIONS")),
 "P446X fts_sec.c factory included":("drivers/input/touchscreen/stm/fts5cu56a/fts_sec.c",
     ("ft_commands",)),
 "P446X Samsung input command helper":("drivers/input/common/sec_cmd.c",
     ("sec_cmd_init(",)),
 "P446X Samsung TCLM helper":("drivers/input/common/sec_tclm_v2.c",
     ("sec_tclm_initialize(",)),
 "P446X secure helper copied":("drivers/input/common/sec_secure_touch.c",
     ("sec_secure_touch_register(",)),
 "P446X wakeup-source header":("drivers/input/touchscreen/stm/fts5cu56a/fts_ts.h",
     ("struct wakeup_source *wakelock;",)),

 "P447 new-driver gates":("drivers/a52_447/a52_447_gates.c",
     ("A52_PHASE447_GATES_V1","P447 GATES initialized wave=",
      'early_param("a52.wave"', "a52_447_probe_allowed")),
 "P447 gate API":("include/linux/a52_phase447.h",
     ("A52_PHASE447_GATES_V1","A52_447_COUNT")),
}
FILES_EXTRA=[
 "drivers/a52_secure/Makefile",
 "drivers/a52_display/msm/msm_fb.c",
 "drivers/a52_display/msm/sde/sde_kms.c",
 "drivers/a52_display/msm/sde/sde_plane.c",
 "drivers/a52_display/msm/msm_smmu.c",
 "drivers/input/touchscreen/stm/fts5cu56a/fts_ts.c",
 "drivers/input/touchscreen/stm/fts5cu56a/fts_fwu.c",
 "drivers/input/touchscreen/stm/fts5cu56a/fts_sec.c",
 "drivers/input/common/sec_cmd.c",
 "drivers/soc/qcom/rpmh.c",
 "drivers/regulator/refgen.c",
 "arch/arm64/boot/dts/vendor/qcom/lagoon.dtsi",
]
def digest(p:Path)->str:
    h=hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda:f.read(1<<20),b""):h.update(b)
    return h.hexdigest()

def audit(root,build,out,strict,image):
    missing=[]
    rows=[]
    for label,(rel,markers) in EXPECT.items():
        p=root/rel
        s=p.read_text(errors="replace") if p.is_file() else ""
        absent=[m for m in markers if m not in s]
        rows.append({"contract":label,"path":rel,"present":not absent,
                     "missing":absent})
        if absent:missing.append({"contract":label,"missing":absent})
    # Legacy qcom,spmi-gpio compatible may be handled by ONE driver only.
    ack_gpio=root/"drivers/pinctrl/qcom/pinctrl-spmi-gpio.c"
    if ack_gpio.is_file():
        import re
        ack_text=ack_gpio.read_text(errors="replace")
        if re.search(r'\.compatible\s*=\s*"qcom,spmi-gpio"',ack_text):
            missing.append({"contract":"no native SPMI GPIO legacy compatible collision"})
    # Security-sensitive negative checks.
    g=(root/"drivers/a52_display/msm/msm_gem_prime.c")
    if g.is_file():
        s=g.read_text(errors="replace")
        if "flags = 0;" in s.replace("unsigned long flags = 0;",""):
            missing.append({"contract":"no importer-side secure-bypass flags=0"})
    cfg=build/".config"
    if not cfg.is_file():
        missing.append({"contract":"final kernel .config"})
    else:
        c=cfg.read_text()
        for t in ("CONFIG_PSTORE_RAM=y","CONFIG_PSTORE_CONSOLE=y",
                  "CONFIG_QCOM_IPCC=y"):
            if t not in c:missing.append({"contract":"config "+t})
        if 'no_console_suspend' not in c:
            missing.append({"contract":"no_console_suspend built-in CMDLINE"})
        if "CONFIG_CMDLINE_EXTEND=y" not in c or "console=ttyGS3,115200" not in c:
            missing.append({"contract":"P446W CONFIG_CMDLINE ttyGS3 and extended boot cmdline"})
        if "console=ttyGS0,115200" in c:
            missing.append({"contract":"P446W no stale CONFIG_CMDLINE ttyGS0"})
        if "CONFIG_LOG_BUF_SHIFT=21" not in c:
            missing.append({"contract":"P446U >=2MiB printk ring for unsampled prop capture"})
        for required in ("CONFIG_INPUT_TOUCHSCREEN=y",
                         "CONFIG_TOUCHSCREEN_STM_FTS5CU56A=y",
                         "CONFIG_INPUT_TOUCHSCREEN_TCLMV2=y"):
            if required not in c:
                missing.append({"contract":"P446X built-in touchscreen "+required})
        for prohibited in ("CONFIG_TOUCHSCREEN_DUMP_MODE=y",
                           "CONFIG_SEC_DEBUG_TSP_LOG=y",
                           "CONFIG_TOUCHSCREEN_DUAL_FOLDABLE=y"):
            if prohibited in c:
                missing.append({"contract":"P446X forbidden legacy helper "+prohibited})
        for required in ("CONFIG_RTC_DRV_PM8XXX=y","CONFIG_RTC_HCTOSYS=y",
                         'CONFIG_RTC_HCTOSYS_DEVICE="rtc0"'):
            if required not in c:
                missing.append({"contract":"P446V RTC config "+required})
    # Source fingerprint: enumerates full imported-display/secure code plus
    # explicit external contracts, then sorts path + SHA256 deterministically.
    roots=["drivers/a52_display","drivers/a52_secure",
           "drivers/staging/android/ion","drivers/devfreq"]
    files={root/p for p in FILES_EXTRA if (root/p).is_file()}
    for d in roots:
        p=root/d
        if p.is_dir():
            files.update(x for x in p.rglob("*") if x.is_file() and
                         x.suffix in (".c",".h",".S",".dts",".dtsi",".mk"))
    files.update(root/p for _,(p,_) in EXPECT.items() if (root/p).is_file())
    # Cover the ENTIRE final tree by anchoring the pinned git object tree,
    # and hashing every modified / staged / added / deleted path, even in
    # otherwise omitted subsystems (USB, pinctrl, regulators, DT, etc).
    proc=subprocess.run(
        ["git","-C",str(root),"ls-files","-m","-o","-d",
         "--exclude-standard","--full-name","-z"],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if proc.returncode:
        raise RuntimeError("strict final-tree git inventory unavailable: "+
                           proc.stderr.decode(errors="replace"))
    git_head=subprocess.run(
        ["git","-C",str(root),"rev-parse","HEAD"],
        capture_output=True,text=True,check=True).stdout.strip()
    touched={}
    for rel in sorted(set(x.decode("utf-8","surrogateescape")
                          for x in proc.stdout.split(b"\0") if x)):
        p=root/rel
        touched[rel]=digest(p) if p.is_file() else "<deleted>"
        if p.is_file():files.add(p)
    hashes={str(p.relative_to(root)):digest(p) for p in sorted(files)}
    h=hashlib.sha256()
    h.update(("GKI_BASE_HEAD\0"+git_head+"\n").encode())
    for p,v in sorted(touched.items()):
        h.update((p+"\0"+v+"\n").encode())
    # All phase scripts: record SHA256 even when an older phase is not active.
    script_dir=Path(__file__).resolve().parent
    scripts={}
    for p in sorted(script_dir.glob("*.py")):
        scripts[p.name]=digest(p)
    git_sha=os.environ.get("GKI_COMMON_SHA","UNKNOWN")
    record={
      "schema":"a52-final-source-manifest-v1",
      "workflow_name":os.environ.get("GITHUB_WORKFLOW","local"),
      "workflow_id":os.environ.get("GITHUB_RUN_ID","local"),
      "workflow_attempt":os.environ.get("GITHUB_RUN_ATTEMPT","local"),
      "workflow_commit_sha":os.environ.get("GITHUB_SHA","local"),
      "gki_common_pinned_sha":git_sha,
      "strict":bool(strict),
      "contracts":rows,
      "missing_required":missing,
      "source_fingerprint_sha256":h.hexdigest(),
      "gki_common_actual_git_head":git_head,
      "changed_or_added_or_removed_paths":len(touched),
      "changed_tree_path_sha256":touched,
      "fingerprinted_files":len(hashes),
      "source_hashes":hashes,
      "phase_script_sha256":scripts,
      "kernel_config_sha256":digest(cfg) if cfg.is_file() else None,
      "image_sha256":digest(image) if image and image.is_file() else None,
    }
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(record,indent=2,sort_keys=True)+"\n")
    print("A52 FINAL MANIFEST "+("PASS" if not missing else "FAIL")+
          " strict="+str(strict)+" files="+str(len(hashes))+
          " fingerprint="+h.hexdigest()+" missing="+str(missing),flush=True)
    if strict and missing:
        raise SystemExit("final tree strict audit FAILED: "+repr(missing))

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--build",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--strict",action="store_true")
    p.add_argument("--image",type=Path)
    a=p.parse_args()
    audit(a.root.resolve(),a.build.resolve(),a.output.resolve(),
          a.strict,a.image.resolve() if a.image else None)
if __name__=="__main__":main()
