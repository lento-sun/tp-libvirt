import os
import logging
from shutil import copyfile

from avocado.core import exceptions
from avocado.utils import process

from virttest import virsh
from virttest import libvirt_storage
from virttest import libvirt_xml
from virttest import data_dir
from virttest import libvirt_vm as lib_vm
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt as utlv

from npiv import npiv_nodedev_create_destroy as nodedev


_DELAY_TIME = 5


def mount_and_dd(session, mount_disk):
    """
    Mount and perform a dd operation on guest
    """
    output = session.cmd_status_output('mount %s /mnt' % mount_disk)
    logging.debug("mount: %s", output[1])
    output = session.cmd_status_output(
        'dd if=/dev/zero of=/mnt/testfile bs=4k count=8000', timeout=300)
    logging.debug("dd output: %s", output[1])
    output = session.cmd_status_output('mount')
    logging.debug("Mount output: %s", output[1])
    if '/mnt' in output[1]:
        logging.debug("Mount Successful")
        return True
    return False


def check_status(cmd_result, msg):
    """
    Check command status
    """
    err = cmd_result.stderr.strip()
    status = cmd_result.exit_status
    if status:
        raise exceptions.TestFail(err)
    else:
        logging.debug(msg)


def find_mpath_devs():
    """
    Find all mpath devices in /dev/mapper which is start with "mpath"
    and not ending with a digit (which means it's a partition)
    """
    mpath_devs = []
    cmd = "ls -l /dev/mapper/ | grep mpath | awk -F ' ' '{print $9}' \
           | grep -Ev [0-9]$ |sort -d"
    cmd_result = process.run(cmd, shell=True)
    mpath_devs = cmd_result.stdout.split("\n")
    return mpath_devs


def is_mpath_devs_added(old_mpath_devs):
    """
    Check if a mpath device is added
    :param old_mpaths: Pre-existing mpaths
    :return: True/False based on addition
    """
    new_mpath_devs = find_mpath_devs()
    new_mpath_devs.sort()
    old_mpath_devs.sort()
    if len(new_mpath_devs) - len(old_mpath_devs) >= 1:
        return True
    else:
        return False


def run(test, params, env):
    """
    Test command: virsh pool-define; pool-define-as; pool-start;
    vol-list pool; attach-device LUN to guest; mount the device;
    dd to the mounted device; unmount; pool-destroy; pool-undefine;

    Pre-requiste:
    Host needs to have a wwpn and wwnn of a vHBA which is zoned and mapped to
    SAN controller.
    """
    pool_xml_f = params.get("pool_create_xml_file", "/PATH/TO/POOL.XML")
    pool_name = params.get("pool_create_name", "virt_test_pool_tmp")
    pre_def_pool = params.get("pre_def_pool", "no")
    define_pool = params.get("define_pool", "no")
    define_pool_as = params.get("define_pool_as", "no")
    need_pool_build = params.get("need_pool_build", "no")
    need_vol_create = params.get("need_vol_create", "no")
    pool_type = params.get("pool_type", "dir")
    source_format = params.get("pool_src_format", "")
    source_name = params.get("pool_source_name", "")
    source_path = params.get("pool_source_path", "/")
    pool_target = params.get("pool_target", "pool_target")
    pool_adapter_type = params.get("pool_adapter_type", "")
    pool_adapter_parent = params.get("pool_adapter_parent", "")
    target_device = params.get("pool_target_dev", "sdc")
    pool_wwnn = params.get("pool_wwnn", "")
    pool_wwpn = params.get("pool_wwpn", "")
    vhba_wwnn = params.get("vhba_wwnn", "")
    vhba_wwpn = params.get("vhba_wwpn", "")
    volume_name = params.get("volume_name", "imagefrommapper.qcow2")
    volume_capacity = params.get("volume_capacity", '1G')
    allocation = params.get("allocation", '1G')
    vol_format = params.get("volume_format", 'raw')
    test_unit = None
    mount_disk = None
    pool_kwargs = {}
    pool_extra_args = ""
    emulated_image = "emulated-image"
    disk_xml = ""
    new_vhbas = []
    source_dev = ""

    if pool_type == "scsi":
        if not pool_wwnn and not pool_wwpn:
            raise exceptions.TestSkipError(
                    "No wwpn and wwnn provided for npiv scsi pool")

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()

    if not vm.is_alive():
        vm.start()

    libvirt_vm = lib_vm.VM(vm_name, vm.params, vm.root_dir,
                           vm.address_cache)
    #pvt = utlv.PoolVolumeTest(test, params)

    pool_ins = libvirt_storage.StoragePool()
    if pool_ins.pool_exists(pool_name):
        raise exceptions.TestFail("Pool %s already exist" % pool_name)
    online_hbas_list = nodedev.find_hbas("hba")
    logging.debug("The online hbas are: %s", online_hbas_list)
    if not online_hbas_list:
        raise exceptions.TestSkipError(
            "Host doesn't have online hba cards")
    old_vhbas = nodedev.find_hbas("vhba")

    if pool_type == "scsi":
        if define_pool == "yes":
            if pool_adapter_parent == "":
                pool_adapter_parent = online_hbas_list[0]
            pool_kwargs = {'source_path': source_path,
                           'source_name': source_name,
                           'source_format': source_format,
                           'pool_adapter_type': pool_adapter_type,
                           'pool_adapter_parent': pool_adapter_parent,
                           'pool_wwnn': pool_wwnn,
                           'pool_wwpn': pool_wwpn}
    elif pool_type == "logical":
        if (not vhba_wwnn) or (not vhba_wwpn):
            raise exceptions.TestFail("No wwnn/wwpn provided to create vHBA.")
        old_mpath_devs = find_mpath_devs()
        new_vhba = nodedev.nodedev_create_from_xml({
                "nodedev_parent": online_hbas_list[0],
                "scsi_wwnn": vhba_wwnn,
                "scsi_wwpn": vhba_wwpn})
        utils_misc.wait_for(
            lambda: nodedev.is_vhbas_added(old_vhbas), timeout=_DELAY_TIME*2)
        if not new_vhba:
            raise exceptions.TestFail("vHBA not sucessfully generated.")
        new_vhbas.append(new_vhba)
        utils_misc.wait_for(
            lambda: is_mpath_devs_added(old_mpath_devs), timeout=_DELAY_TIME*2)
        if not is_mpath_devs_added(old_mpath_devs):
            raise exceptions.TestFail("mpath dev not generated.")
        cur_mpath_devs = find_mpath_devs()
        new_mpath_devs = list(set(cur_mpath_devs).difference(
            set(old_mpath_devs)))
        logging.debug("The newly added mpath dev is: %s", new_mpath_devs)
        source_dev = "/dev/mapper/" + new_mpath_devs[0]
        logging.debug("We are going to use \"%s\" as our source device"
                      " to create a logical pool", source_dev)
        # Make sure no partion on the mpath device, run twice to avoid failure
        cmd = "parted %s mklabel msdos yes" % source_dev
        cmd_result = process.run(cmd, shell=True)
        cmd_result = process.run(cmd, shell=True)
        if define_pool_as == "yes":
            pool_extra_args = ""
            if source_dev:
                pool_extra_args = ' --source-dev %s' % source_dev
    if pre_def_pool == "yes":
        try:
            pvt = utlv.PoolVolumeTest(test, params)
            pvt.pre_pool(pool_name, pool_type,
                         pool_target, emulated_image,
                         **pool_kwargs)
            utils_misc.wait_for(
                    lambda: nodedev.is_vhbas_added(old_vhbas),
                    _DELAY_TIME*2)
            virsh.pool_dumpxml(pool_name, to_file=pool_xml_f)
            virsh.pool_destroy(pool_name)
        except Exception, e:
            pvt.cleanup_pool(pool_name, pool_type, pool_target,
                             emulated_image, **pool_kwargs)
            raise exceptions.TestError(
                "Error occurred when prepare pool xml:\n %s" % e)
        if os.path.exists(pool_xml_f):
            f = open(pool_xml_f, 'r')
            try:
                logging.debug("Create pool from file: %s", f.read())
            finally:
                f.close()
    try:
        if (pre_def_pool == "yes") and (define_pool == "yes"):
            cmd_result = virsh.pool_define(pool_xml_f, ignore_status=True,
                                           debug=True)
            check_status(cmd_result, "Successfully define pool: %s"
                         % pool_name)

        if define_pool_as == "yes":
            cmd_result = virsh.pool_define_as(
                pool_name, pool_type,
                pool_target, pool_extra_args,
                ignore_status=True, debug=True
                )
            check_status(cmd_result,
                         "Successfully defined pool: %s"
                         % pool_name)
        if need_pool_build == "yes":
            cmd_result = virsh.pool_build(pool_name)
            check_status(cmd_result, "Successfully built pool: %s"
                         % pool_name)
        # Start the POOL
        cmd_result = virsh.pool_start(pool_name)
        check_status(cmd_result, "Successfully start pool: %s" % pool_name)
        utlv.check_actived_pool(pool_name)
        pool_detail = libvirt_xml.PoolXML.get_pool_details(pool_name)
        logging.debug("Pool detail: %s", pool_detail)

        if need_vol_create == "yes":
            cmd_result = virsh.vol_create_as(
                    volume_name, pool_name,
                    volume_capacity, allocation,
                    vol_format, "", debug=True
                    )
            check_status(
                    cmd_result,
                    "Successfully created volume out of pool: %s"
                    % pool_name
                    )

        vol_list = utlv.get_vol_list(pool_name, vol_check=True,
                                     timeout=_DELAY_TIME*3)
        logging.debug('Volume list is: %s' % vol_list)
        test_unit = vol_list.keys()[0]
        logging.info(
            "Using the first volume %s to attach to a guest", test_unit)

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        session = vm.wait_for_login()
        output = session.cmd_status_output('lsblk')
        logging.debug("%s", output[1])
        old_count = vmxml.get_disk_count(vm_name)
        bf_disks = libvirt_vm.get_disks()
        disk_params = {'type_name': 'volume', 'target_dev': target_device,
                       'target_bus': 'virtio', 'source_pool': pool_name,
                       'source_volume': test_unit, 'driver_type': vol_format}
        disk_xml = os.path.join(data_dir.get_tmp_dir(), 'disk_xml.xml')
        lun_disk_xml = utlv.create_disk_xml(disk_params)

        copyfile(lun_disk_xml, disk_xml)
        attach_success = virsh.attach_device(
            vm_name, disk_xml, debug=True)

        check_status(attach_success, 'Disk attached successfully')
        logging.info("Checking disk availability in domain")
        if not vmxml.get_disk_count(vm_name):
            raise exceptions.TestFail("No disk in domain %s." % vm_name)
        new_count = vmxml.get_disk_count(vm_name)

        if new_count <= old_count:
            raise exceptions.TestFail(
                "Failed to attach disk %s" % lun_disk_xml)
        output = session.cmd_status_output('lsblk')
        logging.debug("%s", output[1])
        logging.debug("Disks before attach: %s", bf_disks)

        af_disks = libvirt_vm.get_disks()
        logging.debug("Disks after attach: %s", af_disks)

        mount_disk = "".join(list(set(bf_disks) ^ set(af_disks)))
        if not mount_disk:
            raise exceptions.TestFail("Can not get attached device in vm.")
        logging.debug("Attached device in vm:%s", mount_disk)

        logging.debug("Creating file system for %s", mount_disk)
        output = session.cmd_status_output(
            'echo yes | mkfs.ext4 %s' % mount_disk)
        logging.debug("%s", output[1])
        if mount_disk:
            mount_success = mount_and_dd(session, mount_disk)
            if not mount_success:
                raise exceptions.TestFail("Mount failed")
        else:
            raise exceptions.TestFail("Partition not available for disk")

        logging.debug("Unmounting disk")
        session.cmd_status_output('umount %s' % mount_disk)

#        virsh.reboot(vm_name, debug=True)

#        session = vm.wait_for_login()
        output = session.cmd_status_output('mount')
        logging.debug("%s", output[1])
        mount_success = mount_and_dd(session, mount_disk)
        if not mount_success:
            raise exceptions.TestFail("Mount failed")

        logging.debug("Unmounting disk")
        session.cmd_status_output('umount %s' % mount_disk)
        session.close()

        detach_status = virsh.detach_device(vm_name, disk_xml,
                                            debug=True)
        check_status(detach_status, "Disk detach successful")

    finally:
        vm.destroy(gracefully=False)
        vmxml_backup.sync()
        logging.debug('Destroying pool %s', pool_name)
        virsh.pool_destroy(pool_name)
        logging.debug('Undefining pool %s', pool_name)
        virsh.pool_undefine(pool_name)
        if os.path.exists(pool_xml_f):
            os.remove(pool_xml_f)
        if os.path.exists(disk_xml):
            data_dir.clean_tmp_files()
            logging.debug("Cleanup disk xml")
        if pre_def_pool == "yes":
            # Do not apply cleanup_pool for logical pool, logical pool will
            # be cleaned below
            pvt.cleanup_pool(pool_name, pool_type, pool_target,
                             emulated_image, **pool_kwargs)
        if (test_unit and (need_vol_create == "yes" and (pre_def_pool == "no"))
                and (pool_type == "logical")):
            process.system('lvremove -f %s/%s' % (pool_name, test_unit),
                           verbose=True)
            process.system('vgremove -f %s' % pool_name, verbose=True)
            process.system('pvremove -f %s' % source_dev, verbose=True)
        if new_vhbas:
            nodedev.vhbas_cleanup(new_vhbas)
        # Restart multipathd, this is to avoid bz1399075
        process.system('service multipathd restart', verbose=True)
