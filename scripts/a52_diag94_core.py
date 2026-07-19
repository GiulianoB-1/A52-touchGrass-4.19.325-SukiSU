#!/usr/bin/env python3
from __future__ import annotations

from a52_diag94_common import declare_helper, replace_once, triplet


def instrument_ufshcd(core: str) -> str:
    core = declare_helper(
        core,
        (
            '#include "ufshcd.h"\n',
            '#include "ufshcd-add-info.h"\n',
            "#include <linux/blkdev.h>\n",
        ),
        "declare persistent diagnostic helper in ufshcd.c",
    )

    init_decl = (
        "\tstruct request ***tmf_rqs = &ufs_hba_add_info(hba)->tmf_rqs;\n"
        "\tint err;\n"
        "\tstruct Scsi_Host *host = hba->host;\n"
        "\tstruct device *dev = hba->dev;\n"
        '\tchar eh_wq_name[sizeof("ufs_eh_wq_00")];\n'
    )
    core = replace_once(
        core,
        init_decl,
        init_decl
        + triplet(
            "CORE stage=init_begin dev=%s irq=%u mmio=%p host_no=%d",
            "dev_name(dev), irq, mmio_base, host ? host->host_no : -1",
            "\t",
        ),
        "instrument ufshcd_init entry",
    )

    hba_init = "\terr = ufshcd_hba_init(hba);\n"
    core = replace_once(
        core,
        hba_init,
        hba_init
        + triplet(
            "CORE stage=hba_init ret=%d dev=%s",
            "err, dev_name(dev)",
            "\t",
        ),
        "instrument ufshcd_hba_init result",
    )

    capabilities = "\terr = ufshcd_hba_capabilities(hba);\n"
    core = replace_once(
        core,
        capabilities,
        capabilities
        + triplet(
            "CORE stage=capabilities ret=%d cap=0x%x version=0x%x nutrs=%d nutmrs=%d",
            "err, hba->capabilities, hba->ufs_version, hba->nutrs, hba->nutmrs",
            "\t",
        ),
        "instrument UFS capabilities",
    )

    dma_mask = "\terr = ufshcd_set_dma_mask(hba);\n"
    core = replace_once(
        core,
        dma_mask,
        dma_mask
        + triplet(
            "CORE stage=dma_mask ret=%d dev=%s dma_mask=0x%llx",
            "err, dev_name(dev), dev->dma_mask ? (unsigned long long)*dev->dma_mask : 0ULL",
            "\t",
        ),
        "instrument UFS DMA mask",
    )

    memory_alloc = "\terr = ufshcd_memory_alloc(hba);\n"
    core = replace_once(
        core,
        memory_alloc,
        memory_alloc
        + triplet(
            "CORE stage=memory_alloc ret=%d dev=%s",
            "err, dev_name(dev)",
            "\t",
        ),
        "instrument UFS memory allocation",
    )

    request_irq = (
        "\terr = devm_request_irq(dev, irq, ufshcd_intr, IRQF_SHARED, UFSHCD, hba);\n"
    )
    core = replace_once(
        core,
        request_irq,
        request_irq
        + triplet(
            "CORE stage=request_irq ret=%d irq=%u dev=%s",
            "err, irq, dev_name(dev)",
            "\t",
        ),
        "instrument UFS IRQ registration",
    )

    scsi_host = "\terr = scsi_add_host(host, hba->dev);\n"
    core = replace_once(
        core,
        scsi_host,
        scsi_host
        + triplet(
            "CORE stage=scsi_add_host ret=%d host_no=%d can_queue=%d max_lun=%u",
            "err, host->host_no, host->can_queue, host->max_lun",
            "\t",
        ),
        "instrument SCSI host registration",
    )

    hba_enable = "\terr = ufshcd_hba_enable(hba);\n"
    core = replace_once(
        core,
        hba_enable,
        hba_enable
        + triplet(
            "CORE stage=hba_enable ret=%d state=%d intr=0x%x hcs=0x%x",
            "err, hba->ufshcd_state, ufshcd_readl(hba, REG_INTERRUPT_STATUS), "
            "ufshcd_readl(hba, REG_CONTROLLER_STATUS)",
            "\t",
        ),
        "instrument UFS host-controller enable",
    )

    async_schedule = "\tasync_schedule(ufshcd_async_scan, hba);\n"
    core = replace_once(
        core,
        async_schedule,
        async_schedule
        + triplet(
            "CORE stage=async_scan_scheduled state=%d host_no=%d",
            "hba->ufshcd_state, hba->host->host_no",
            "\t",
        ),
        "instrument UFS async scan scheduling",
    )

    success_return = (
        "\tufs_sysfs_add_nodes(hba);\n"
        "\tdevice_enable_async_suspend(dev);\n"
        "\treturn 0;\n"
    )
    core = replace_once(
        core,
        success_return,
        "\tufs_sysfs_add_nodes(hba);\n"
        "\tdevice_enable_async_suspend(dev);\n"
        + triplet(
            "CORE stage=init_return ret=0 state=%d host_no=%d",
            "hba->ufshcd_state, hba->host->host_no",
            "\t",
        )
        + "\treturn 0;\n",
        "instrument ufshcd_init success return",
    )

    error_return = (
        "out_error:\n"
        "\treturn err;\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(ufshcd_init);\n"
    )
    core = replace_once(
        core,
        error_return,
        "out_error:\n"
        + triplet(
            "CORE stage=init_fail ret=%d state=%d cap=0x%x version=0x%x host_no=%d",
            "err, hba->ufshcd_state, hba->capabilities, hba->ufs_version, "
            "hba->host ? hba->host->host_no : -1",
            "\t",
        )
        + "\treturn err;\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(ufshcd_init);\n",
        "instrument ufshcd_init failure return",
    )

    probe_start = (
        "\thba->ufshcd_state = UFSHCD_STATE_RESET;\n"
        "\tret = ufshcd_link_startup(hba);\n"
    )
    core = replace_once(
        core,
        probe_start,
        "\thba->ufshcd_state = UFSHCD_STATE_RESET;\n"
        + triplet(
            "CORE stage=probe_hba_begin async=%d state=%d link=%d devp=%d",
            "async, hba->ufshcd_state, hba->uic_link_state, ufshcd_is_device_present(hba)",
            "\t",
        )
        + "\tret = ufshcd_link_startup(hba);\n"
        + triplet(
            "CORE stage=link_startup ret=%d state=%d link=%d devp=%d intr=0x%x hcs=0x%x",
            "ret, hba->ufshcd_state, hba->uic_link_state, ufshcd_is_device_present(hba), "
            "ufshcd_readl(hba, REG_INTERRUPT_STATUS), ufshcd_readl(hba, REG_CONTROLLER_STATUS)",
            "\t",
        ),
        "instrument UFS link startup",
    )

    verify = "\tret = ufshcd_verify_dev_init(hba);\n"
    core = replace_once(
        core,
        verify,
        verify
        + triplet(
            "CORE stage=verify_dev_init ret=%d link=%d devp=%d",
            "ret, hba->uic_link_state, ufshcd_is_device_present(hba)",
            "\t",
        ),
        "instrument UFS device verification",
    )

    complete = "\tret = ufshcd_complete_dev_init(hba);\n"
    core = replace_once(
        core,
        complete,
        complete
        + triplet(
            "CORE stage=complete_dev_init ret=%d link=%d devp=%d",
            "ret, hba->uic_link_state, ufshcd_is_device_present(hba)",
            "\t",
        ),
        "instrument UFS device-init completion",
    )

    params = "\t\tret = ufshcd_device_params_init(hba);\n"
    core = replace_once(
        core,
        params,
        params
        + triplet(
            "CORE stage=device_params ret=%d manufacturer=0x%x max_lu=%d",
            "ret, hba->dev_info.wmanufacturerid, hba->dev_info.max_lu_supported",
            "\t\t",
        ),
        "instrument UFS device parameter discovery",
    )

    pwr = "\t\tret = ufshcd_config_pwr_mode(hba, &hba->max_pwr_info.info);\n"
    core = replace_once(
        core,
        pwr,
        pwr
        + triplet(
            "CORE stage=config_pwr_mode ret=%d gear_rx=%d gear_tx=%d lane_rx=%d lane_tx=%d",
            "ret, hba->max_pwr_info.info.gear_rx, hba->max_pwr_info.info.gear_tx, "
            "hba->max_pwr_info.info.lane_rx, hba->max_pwr_info.info.lane_tx",
            "\t\t",
        ),
        "instrument UFS power-mode switch",
    )

    probe_return = (
        "\ttrace_ufshcd_init(dev_name(hba->dev), ret,\n"
        "\t\t\t   ktime_to_us(ktime_sub(ktime_get(), start)),\n"
        "\t\t\t   hba->curr_dev_pwr_mode, hba->uic_link_state);\n"
        "\treturn ret;\n"
    )
    core = replace_once(
        core,
        probe_return,
        "\ttrace_ufshcd_init(dev_name(hba->dev), ret,\n"
        "\t\t\t   ktime_to_us(ktime_sub(ktime_get(), start)),\n"
        "\t\t\t   hba->curr_dev_pwr_mode, hba->uic_link_state);\n"
        + triplet(
            "CORE stage=probe_hba_end ret=%d state=%d link=%d devp=%d manufacturer=0x%x",
            "ret, hba->ufshcd_state, hba->uic_link_state, ufshcd_is_device_present(hba), "
            "hba->dev_info.wmanufacturerid",
            "\t",
        )
        + "\treturn ret;\n",
        "instrument UFS probe_hba final result",
    )

    async_begin = (
        "\tstruct ufs_hba *hba = (struct ufs_hba *)data;\n"
        "\tint ret;\n"
    )
    core = replace_once(
        core,
        async_begin,
        async_begin
        + triplet(
            "CORE stage=async_scan_begin host_no=%d state=%d",
            "hba->host->host_no, hba->ufshcd_state",
            "\t",
        ),
        "instrument async UFS scan entry",
    )

    async_probe = "\tret = ufshcd_probe_hba(hba, true);\n"
    core = replace_once(
        core,
        async_probe,
        async_probe
        + triplet(
            "CORE stage=async_probe_hba ret=%d state=%d link=%d",
            "ret, hba->ufshcd_state, hba->uic_link_state",
            "\t",
        ),
        "instrument async UFS probe result",
    )

    add_lus = "\tret = ufshcd_add_lus(hba);\n"
    core = replace_once(
        core,
        add_lus,
        add_lus
        + triplet(
            "CORE stage=add_lus ret=%d host_no=%d",
            "ret, hba->host->host_no",
            "\t",
        ),
        "instrument UFS logical-unit addition",
    )

    async_out = (
        "\tif (ret) {\n"
        "\t\tpm_runtime_put_sync(hba->dev);\n"
        "\t\tufshcd_hba_exit(hba);\n"
        "\t}\n"
        "}\n"
    )
    core = replace_once(
        core,
        async_out,
        "\tif (ret) {\n"
        "\t\tpm_runtime_put_sync(hba->dev);\n"
        "\t\tufshcd_hba_exit(hba);\n"
        "\t}\n"
        + triplet(
            "CORE stage=async_scan_end ret=%d state=%d link=%d",
            "ret, hba->ufshcd_state, hba->uic_link_state",
            "\t",
        )
        + "}\n",
        "instrument async UFS scan exit",
    )

    scsi_scan = "\tscsi_scan_host(hba->host);\n"
    core = replace_once(
        core,
        scsi_scan,
        triplet(
            "CORE stage=scsi_scan_begin host_no=%d",
            "hba->host->host_no",
            "\t",
        )
        + scsi_scan
        + triplet(
            "CORE stage=scsi_scan_end host_no=%d",
            "hba->host->host_no",
            "\t",
        ),
        "instrument SCSI scan",
    )

    return core
