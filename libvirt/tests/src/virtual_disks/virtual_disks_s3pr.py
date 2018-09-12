import os
import re
import locale
import logging
import base64

import aexpect

from avocado.utils import process

from virttest import remote
from virttest import data_dir
from virttest import virt_vm
from virttest import virsh
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml, xcepts
from virttest.libvirt_xml import secret_xml
from virttest.libvirt_xml.devices.disk import Disk
from virttest.libvirt_xml.devices.controller import Controller

from provider import libvirt_version


def run(test, params, env):
    """
    Test disk encryption option.

    1.Prepare test environment, destroy or suspend a VM.
    2.Prepare tgtd and secret config.
    3.Edit disks xml and start the domain.
    4.Perform test operation.
    5.Recover test environment.
    6.Confirm the test result.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    virsh_dargs = {'debug': True, 'ignore_status': True}

    # Disk specific attributes.
    device = params.get("virt_disk_device", "lun")
    device_target = params.get("virt_disk_device_target", "vdb")
    device_format = params.get("virt_disk_device_format", "raw")
    device_type = params.get("virt_disk_device_type", "block")
    device_bus = params.get("virt_disk_device_bus", "scsi")

    # iscsi options.
    iscsi_target = params.get("iscsi_target")
    iscsi_host = params.get("iscsi_host")
    iscsi_port = params.get("iscsi_port")
    emulated_size = params.get("iscsi_image_size", "1")
    uuid = params.get("uuid", "")
    auth_uuid = "yes" == params.get("auth_uuid", "")
    auth_usage = "yes" == params.get("auth_usage", "")
    chap_user = params.get("chap_user", "")
    chap_passwd = params.get("chap_passwd", "")


    reservations_managed = "yes" == params.get("reservations_managed", "yes")
    reservations_source_type = params.get("reservations_source_type", "unix")
    reservations_source_path = params.get("reservations_source_path",
                                          "/var/run/qemu-pr-helper.sock")
    reservations_source_mode = params.get("reservations_source_mode", "client")
    secret_uuid = ""

    # Start vm and get all partions in vm.
    if vm.is_dead():
        vm.start()
    session = vm.wait_for_login()
    old_parts = libvirt.get_parts_list(session)
    session.close()
    vm.destroy(gracefully=False)

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        # Setup iscsi target
        blk_dev = libvirt.setup_or_cleanup_iscsi(is_setup=True,
                                                 is_login=True,
                                                 image_size=emulated_size,
                                                 chap_user=chap_user,
                                                 chap_passwd=chap_passwd,
                                                 portal_ip=iscsi_host)

        # Add disk xml.
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)

        disk_xml = Disk(type_name=device_type)
        disk_xml.device = device

        disk_xml.target = {"dev": device_target, "bus": device_bus}
        driver_dict = {"name": "qemu", "type": device_format}

        disk_xml.driver = driver_dict
        # Check if we want to use a faked uuid.
        if not uuid:
            uuid = secret_uuid
        auth_dict = {}
        if auth_uuid:
            auth_dict = {"auth_user": chap_user,
                         "secret_type": secret_usage_type,
                         "secret_uuid": uuid}
        elif auth_usage:
            auth_dict = {"auth_user": chap_user,
                         "secret_type": secret_usage_type,
                         "secret_usage": secret_usage_target}
        disk_source = disk_xml.new_disk_source(
            **{"attrs": {"dev": blk_dev}})
        if auth_dict:
            disk_auth = disk_xml.new_auth(**auth_dict)
            if 'source' in auth_place_in_location:
                disk_source.auth = disk_auth
            if 'disk' in auth_place_in_location:
                disk_xml.auth = disk_auth
        if reservations_managed:
            reservations_dict = {"reservations_managed": "yes"}
        else:
            reservations_dict = {"reservations_managed": "no",
                                 "reservations_source_type": reservations_source_type,
                                 "reservations_source_path": reservations_source_path,
                                 "reservations_source_mode": reservations_source_mode}
        disk_source.reservations = disk_xml.new_reservations(**reservations_dict)


        disk_xml.source = disk_source
        logging.debug("1111111 %s" % disk_xml)
        # Sync VM xml.
        vmxml.add_device(disk_xml)

        try:
            # Start the VM and check status.
            vmxml.sync()
            vm.start()
            if status_error:
                test.fail("VM started unexpectedly.")

            # Check Qemu command line
            if test_qemu_cmd:
                check_qemu_cmd()

        except virt_vm.VMStartError as e:
            if status_error:
                if re.search(uuid, str(e)):
                    pass
            else:
                test.fail("VM failed to start."
                          "Error: %s" % str(e))
        except xcepts.LibvirtXMLError as xml_error:
            if not define_error:
                test.fail("Failed to define VM:\n%s" % xml_error)
        else:
            # Check partitions in VM.
            if check_partitions:
                if not check_in_vm(device_target, old_parts):
                    test.fail("Check disk partitions in VM failed")
            # Test domain save/restore/snapshot.
            if test_save_snapshot:
                save_file = os.path.join(data_dir.get_tmp_dir(), "%.save" % vm_name)
                check_save_restore(save_file)
                check_snapshot()
                if os.path.exists(save_file):
                    os.remove(save_file)

    finally:
        # Delete snapshots.
        libvirt.clean_up_snapshots(vm_name, domxml=vmxml_backup)

        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync("--snapshots-metadata")

        # Delete the tmp files.
        libvirt.setup_or_cleanup_iscsi(is_setup=False)

        # Clean up secret
        if secret_uuid:
            virsh.secret_undefine(secret_uuid)
