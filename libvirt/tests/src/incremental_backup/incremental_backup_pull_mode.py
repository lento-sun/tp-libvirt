import os
import re
import logging

from avocado.utils import process

from virttest import virsh
from virttest.libvirt_xml.backup_xml import BackupXML
from virttest.libvirt_xml.checkpoint_xml import CheckpointXML

def run(test, params, env):
    """
    Just a test
    """
#    logging.debug("nothing to test")
#    backup_xml = BackupXML()
#    backup_xml.incremental = "full_checkpoint"
#    disk_xml_vda = backup_xml.DiskXML()
#    disk_xml_vda.name = 'vda'
#    disk_xml_vda.backup = 'no'
#    disk_xml_vdb = backup_xml.DiskXML()
#    disk_xml_vdb.name = 'vdb'
#    disk_xml_vdb.backup = "yes"
#    disk_xml_vdb.target = {'file':'/tmp/target.img'}
#    logging.debug("backup_xml is: %s", backup_xml)
#    logging.debug("disks_xml is: %s", disk_xml_vda)
#    logging.debug("disks_xml is: %s", disk_xml_vdb)
#    logging.debug("2222222 type: %s", type(disk_xml_vdb))
#    backup_xml.add_disk(disk_xml_vda)
#    backup_xml.add_disk(disk_xml_vdb)
#    logging.debug("backup_xml is: %s", backup_xml)
    cp_xml = CheckpointXML()
    cp_xml.name = 'checkpoint_1'
    cp_xml.description = 'desc of checkpoint_1'
    disks = []
    disk1 = {'name':'vda', 'checkpoint':'no'}
    disk2 = {'name':'vdb', 'checkpoint':'bitmap', 'bitmap':'bt1'}
    disks.append(disk1)
    disks.append(disk2)
    cp_xml.disks = disks

    logging.debug("cp_xml is: %s", cp_xml)
