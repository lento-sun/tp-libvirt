import os
import logging
import time
from shutil import copyfile

from avocado.core import exceptions
from avocado.utils import process

from virttest import virsh
from virttest import libvirt_storage
from virttest import libvirt_xml
from virttest import data_dir
from virttest import libvirt_vm as lib_vm
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt as utlv
from virttest import utils_misc

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


def run(test, params, env):
    """
    Test command: virsh pool-define;pool-start;vol-list pool;
    attach-device LUN to guest; mount the device, dd; unmount;
    reboot guest; mount the device, dd again; pool-destroy; pool-undefine;

    Create a libvirt npiv pool from an XML file. The test needs to have a wwpn
    and wwnn of a vhba in host which is zoned & mapped to a SAN controller.

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
    test_unit = None
    mount_disk = None
    pool_kwargs = {}
    pool_extra_args = ""
    emulated_image = "emulated-image"

    source_dev = params.get("pool_source_dev", "/dev/mapper/mpathc")
    volume_name = params.get("volume_name", "imagefrommapper.qcow2")
    volume_capacity = params.get("volume_capacity", '1G')
    allocation = params.get("allocation", '1G')
    frmt = params.get("volume_format", 'qcow2')


    disk_xml = ""

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
    pvt = utlv.PoolVolumeTest(test, params)

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
        if define_pool_as == "yes":
            # This part is not implemented yet.
            pass
    elif pool_type == "logical":
        if define_pool == "yes":
            # This part is not implemented yet.
            pass
        if define_pool_as == "yes":
            pool_extra_args = ""
            if source_dev:
                pool_extra_args = ' --source-dev %s' % source_dev

    if pre_def_pool == "yes":
        try:
            pvt.pre_pool(pool_name, pool_type,
                         pool_target, emulated_image,
                         **pool_kwargs)
            utils_misc.wait_for(
                    lambda: nodedev.is_vhbas_added(old_vhbas),
                    _DELAY_TIME)
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
        #cmd = "mkfs.ext4 -F %s" % source_dev
        #cmd_result = process.run(cmd, shell=True)
        if need_pool_build == "yes":
            cmd_result = virsh.pool_build(pool_name)
            check_status(cmd_result, "Successfully built pool: %s"
                         % pool_name)
        cmd_result = virsh.pool_start(pool_name)
        check_status(cmd_result, "Successfully start pool: %s" % pool_name)
        utlv.check_actived_pool(pool_name)
        pool_detail = libvirt_xml.PoolXML.get_pool_details(pool_name)
        logging.debug("Pool detail: %s", pool_detail)

        # Sleep time to list the volume
#        time.sleep(5)
        if need_vol_create == "yes":
            cmd_result = virsh.vol_create_as(
                    volume_name, pool_name,
                    volume_capacity, allocation,
                    frmt, "", debug=True
                    )
            check_status(
                    cmd_result,
                    "Successfully created volume out of pool: %s"
                    % pool_name
                    )

        vol_list = utlv.get_vol_list(pool_name)
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
                       'source_volume': test_unit, 'driver_type': 'raw'}
        disk_xml = os.path.join(data_dir.get_tmp_dir(), 'disk_xml.xml')
        lun_disk_xml = utlv.create_disk_xml(disk_params)

        copyfile(lun_disk_xml, disk_xml)
        attach_success = virsh.attach_device(
            vm_name, disk_xml, debug=True)

        check_status(attach_success, 'Disk attached successfully')

        virsh.reboot(vm_name, debug=True)

        logging.info("Checking disk availability in domain")
        if not vmxml.get_disk_count(vm_name):
            raise exceptions.TestFail("No disk in domain %s." % vm_name)
        new_count = vmxml.get_disk_count(vm_name)

        if new_count <= old_count:
            raise exceptions.TestFail(
                "Failed to attach disk %s" % lun_disk_xml)

        session = vm.wait_for_login()
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

        virsh.reboot(vm_name, debug=True)

        session = vm.wait_for_login()
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
        if pool_type == "scsi":
            # Do not apply cleanup_pool for logical pool, logical pool will
            # be cleaned below
            pvt.cleanup_pool(pool_name, pool_type, pool_target,
                             emulated_image, **pool_kwargs)
        if (test_unit and (need_vol_create == "yes")
            and (pool_type == "logical")):
            process.system('lvremove -f %s/%s' % (pool_name, test_unit),
                           verbose=True)
            process.system('vgremove -f %s' % pool_name, verbose=True)
            process.system('pvremove -f %s' % source_dev, verbose=True)

