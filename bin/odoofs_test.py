import unittest
from threading import Thread
from fusepy import FUSE
from odoofs import *
import odoorpc
import os
import tempfile
from subprocess import Popen
import random
from datetime import datetime
import stat
import errno
from base64 import b64decode, b64encode

odoo_username = 'jacobus'
odoo_password = 'kijfrd5hg'
odoo_db = 'fuse'
odoo_host = 'localhost'
odoofs_dir = 'test'
odoo_port = 8069


def _filename():
    filename = ''
    for i in range(10):
        filename += random.choice('ABCEDFGHIJKLMNOPQRSTUWXYZ_.abcdefghijklmnopqrstuvwxyz[]{};:'"<>,._-!@#$%^&*()+=`~?")
    return filename


def _now():
    return datetime.now().timestamp()


class Config:
    def __init__(self, host='localhost', username=None, password=None, database=None, port=8069):
        self.server = host
        self.username = username
        self.password = password
        self.database = database
        self.port = port


class MyTestCase(unittest.TestCase):
    def _full_filename(self, path):
        return Path(self.config.cache) / Path(path).relative_to('/')

    def setUp(self):
        self.config = Config(host=odoo_host, username=odoo_username, password=odoo_password, database=odoo_db)
        self.odoo = odoorpc.ODOO(host=self.config.server, port=self.config.port)
        self.odoo.login(self.config.database, self.config.username, self.config.password)
        config_cache = tempfile.mkdtemp(prefix='.test')
        self.config.cache = config_cache
        self.odoofs = OdooFS(self.config, self.odoo)
        self.fuse = self.odoo.env['fuse.node']

        #        FUSE(OdooFS(config, odoo), odoofs_dir, nothreads=True, foreground=True)
        #        serverthread = Thread(main(config,odoo))

    def dearDown(self):
        os.rmdir(config_cache)

    # def test_something(self):
    #     self.assertEqual(True, False)

    def setup_irattachment_node(self):
        fuse1 = self.fuse.create(
            {'name': 'Test2',
             'type': 'file',
             'parent_id': self.odoo.env.ref('fuse.root_node').id,  # Node Parent
             'model_id': self.odoo.env.ref('base.model_ir_attachment').id,  # The node model will list these
             'bin_field': self.odoo.env.ref('base.field_ir_attachment__datas').id,  # Where is the bin data stored
             'name_pattern': '{item.name}',  # Pattern to find the specific object
             'file_size': 'item.file_size',  # The script to determine the file size stored
             'name_re_pattern': '(?P<name>[\\w\\s]+)',
             # The regular expression to convert the filename into object field
             'parent_field_id': self.odoo.env.ref('base.field_ir_attachment__res_id').id,
             # Field in current model that points to parent
             'filter_domain': "[('res_model','=','fuse.node')]",
             'field_value_ids': [(0, 0, {  # Default values for certain fields
                 'field_id': self.odoo.env.ref('base.field_ir_attachment__res_model').id,
                 # Field that needs to be assigned
                 'field_value': "'fuse.node'"})]
             }  # The model this points to.
        )
        return fuse1

    def test_open(self):
        with self.assertRaises(FuseOSError) as fuse_error:
            self.odoofs.open('/test1', os.O_RDWR)
        fuse_error = fuse_error.exception
        self.assertEqual(fuse_error.errno, 2)

        # TODO: Test for file exists on odoo and not local and directory
        file1 = _filename()
        node1 = self.odoofs.fuse.create({'name': file1, 'parent_id': self.odoofs.odoo.env.ref('fuse.root_node').id})
        file1 = '/' + file1
        with self.assertRaises(FuseOSError) as fuse_error:
            fh1 = self.odoofs.open(file1, os.O_RDWR)
        fuse_error = fuse_error.exception
        self.assertEqual(fuse_error.errno, errno.EISDIR)

        ff1 = self._full_filename(file1)
        self.assertFalse(os.path.exists(ff1))

        # TODO: Test for file exists on odoo and not local and file
        node1 = self.odoofs.fuse.browse(node1)
        node1.type = 'file'
        fh1 = self.odoofs.open(file1, os.O_RDWR)
        self.assertTrue(os.path.exists(ff1))
        os.close(fh1)

        # TODO: Test for file exists local and not odoo
        node1.unlink()
        with self.assertRaises(FuseOSError) as fuse_error:
            fh1 = self.odoofs.open(file1, os.O_RDWR)
        fuse_error = fuse_error.exception
        self.assertEqual(fuse_error.errno, errno.ENOENT)

        # Check if file exists locally
        self.assertTrue(os.path.exists(ff1))
        node1 = self.odoofs.fuse.create({'name': str(Path(file1).relative_to('/')),
                                         'parent_id': self.odoofs.odoo.env.ref('fuse.root_node').id,
                                         'type': 'file'})
        fh1 = self.odoofs.open(file1, os.O_RDWR)
        os.close(fh1)

    def test_create(self):
        # TODO: Create file where odoo cannot access denied
        with self.assertRaises(FuseOSError) as fuse_error:
            fh1 = self.odoofs.create('/test1', stat.S_IRUSR | stat.S_IWUSR)
        fuse_error = fuse_error.exception
        self.assertEqual(fuse_error.errno, errno.EACCES)

        # TODO: Create file of child node with no model assigned on parent
        node2 = self.setup_irattachment_node()
        data1 = b'123456789'
        fh1 = self.odoofs.create('/test2', stat.S_IRUSR | stat.S_IWUSR)
        self.odoofs.write('/test2', data1, 0, fh1)
        self.odoofs.release('/test2',fh1)

        fh1 = self.odoofs.open('/test2', os.O_RDWR)
        data2 = self.odoofs.read('/test2', len(data1), 0, fh1)
        self.odoofs.release('/test2', fh1)
        self.assertEqual(data2, b'123456789')

        node2_obj = self.fuse.browse(node2)
        node2_obj.unlink()

        # TODO: Create file where parent is a dynamic node (with a model assigned)

    def test_attrcache(self):
        path = '/'
        at1 = self.odoofs.attr[path]
        self.assertEqual(at1.errno, 0)
        self.assertEqual(at1.filename, Path('/'))

        path = _filename()
        at1 = self.odoofs.attr['/' + path]
        self.assertEqual(at1.errno, 2)

        # TODO: attribute for existing item
        path = _filename()
        time1 = _now()
        fuse_id = self.odoofs.fuse.create({'name': path, 'parent_id': self.odoofs.odoo.env.ref('fuse.root_node').id})
        fuse1 = self.odoofs.fuse.browse(fuse_id)
        path = '/' + path
        at1 = self.odoofs.attr[path]
        self.assertEqual(at1.errno, 0)
        self.assertEqual(at1.filename, Path(path))
        self.assertGreaterEqual(at1.ctime, time1)
        self.assertGreaterEqual(at1.mtime, time1)
        self.assertGreaterEqual(at1.atime, time1)
        self.assertTrue(at1.mode | stat.S_IFDIR)

        # TODO: attribute for item removed
        fuse1.unlink()
        at1 = self.odoofs.attr[path]
        self.assertEqual(at1.errno, 2)


if __name__ == '__main__':
    unittest.main()
