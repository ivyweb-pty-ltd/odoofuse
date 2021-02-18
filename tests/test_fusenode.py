from odoo.tests import TransactionCase
import errno
from pathlib import PurePath
from stat import *
import base64


class FuseNodeTesting(TransactionCase):
    def setup_static_node(self):
        node = self.env['fuse.node'].create({'name': 'Test1',
                                             'type': 'dir',
                                             'parent_id': self.env.ref('fuse.root_node').id})
        return node

    def setup_dynamic_node(self):
        node = self.setup_static_node()
        node.model_id = self.env['ir.model'].search([('model', '=', 'res.partner')])
        return node

    def setup_attachment_node(self):
        node1 = self.env['fuse.node'].create({'name': 'Test3',
                                              'type': 'file',
                                              'parent_id': self.env.ref('fuse.root_node').id,
                                              'parent_field_id': self.env.ref('base.field_ir_attachment__res_id').id,
                                              'model_id': self.env.ref('base.model_ir_attachment').id,
                                              'bin_field': self.env.ref('base.field_ir_attachment__datas').id,
                                              'file_content': 'bin',
                                              'field_value_ids': [
                                                  (0, 0, {
                                                      'field_id': self.env.ref(
                                                          'base.field_ir_attachment__res_model').id,
                                                      'field_value': "'res.partner'"}),
                                              ],
                                              'name_re_pattern': '(?P<name>.+)'})
        return node1

    def test_compute_display_name(self):
        item = self.setup_static_node()
        item._compute_display_name()
        self.assertEqual(item.display_name, 'Test1')
        item = self.setup_dynamic_node()
        self.assertEqual(item.display_name, '(res.partner) Test1')

    def test_find_node(self):
        '''find_node should find a node and model associated with it.

        You supply the base node (last directory) and it will return the node that matches
        the path (part) specified

        type can by ['dir'] or ['dir','file']
        '''

        node1 = self.setup_static_node()
        ierrno, inode, imodel = self.env.ref('fuse.root_node').find_node('Test1')
        self.assertEqual(ierrno, 0)
        self.assertEqual(inode, node1)
        self.assertEqual(imodel, None)

        # Test for dynamic entries
        node2 = self.setup_dynamic_node()
        partner = self.env['res.partner'].create({'name': 'SomePartner'})
        ierrno, inode, imodel = self.env.ref('fuse.root_node').find_node('SomePartner')
        self.assertEqual(ierrno, 0)
        self.assertEqual(imodel, partner)
        self.assertEqual(inode, node2)

        # TODO: Test for failure

    def test_paths(self):
        # Testing paths list all paths on node

        paths_meta = self.env.ref('fuse.root_node').paths()
        self.assertEqual(paths_meta[0]['filename'], '/')

        node1 = self.env['fuse.node'].create({'name': 'Something1'})
        paths = node1.paths()
        self.assertEqual(paths[0]['filename'], 'Something1')

        node2 = self.env['fuse.node'].create(
            {'name': 'Something2', 'model_id': self.env.ref('base.model_res_partner').id})
        partner1 = self.env['res.partner'].create({'name': 'TestingPartner1'})
        paths_meta = node2.paths()
        paths = [node['filename'] for node in paths_meta]
        self.assertTrue('TestingPartner1' in paths)

        # TODO: Test for meta data

    def test_getattr(self):
        # Testing getattr

        # Check root
        attr1 = self.env['fuse.node'].getattr('/')

        self.assertEqual(attr1['errno'], 0)
        self.assertEqual(attr1['st_mode'], S_IFDIR | S_IRUSR | S_IXUSR | S_IRGRP | S_IXGRP)

        # Check static dir
        self.env['fuse.node'].create({'name': 'SomePath1', 'parent_id': self.env.ref('fuse.root_node').id})
        attr1 = self.env['fuse.node'].getattr('/SomePath1')

        self.assertEqual(attr1['errno'], 0)
        self.assertEqual(attr1['st_mode'], S_IFDIR | S_IRUSR | S_IXUSR | S_IRGRP | S_IXGRP)

        # Check dynamic dir
        self.env['fuse.node'].create({'name': 'SomePath2',
                                      'parent_id': self.env.ref('fuse.root_node').id,
                                      'model_id': self.env.ref('base.model_res_partner').id})

        partner1 = self.env['res.partner'].create({'name': 'TestingPartner1'})
        attr1 = self.env['fuse.node'].getattr('/TestingPartner1')

        self.assertEqual(attr1['errno'], 0)
        self.assertEqual(attr1['st_mode'], S_IFDIR | S_IRUSR | S_IXUSR | S_IRGRP | S_IXGRP)

        # Check static file

        # Check dynamic file

    def test_setattr(self):
        self.env['fuse.node'].setattr('/', {'st_mtime': 10})
        self.assertNotEqual(self.env.ref('fuse.root_node').write_date.timestamp(),
                            10)  # Statoc node should not set time

        node1 = self.setup_dynamic_node()
        partner1 = self.env['res.partner'].create({'name': 'Partner1'})
        self.env['fuse.node'].setattr('/Partner1', {'st_mtime': 20})
        self.assertEqual(partner1.write_date.timestamp(), 20)

    def test_findpath(self):
        # Testing path

        # Find path static
        node1 = self.setup_static_node()
        inode, imodel = self.env['fuse.node'].findpath('/Test1')
        self.assertEqual(imodel, None)
        self.assertEqual(inode, node1)

        # Find path dynamic
        node2 = self.setup_dynamic_node()
        partner = self.env['res.partner'].create({'name': 'Some Partner'})

        inode, imodel = self.env['fuse.node'].findpath('/Some Partner')
        self.assertEqual(inode, node2)
        self.assertEqual(imodel, partner)

        # Find path multi directory static/static

        node3 = self.env['fuse.node'].create({'name': 'Dir2', 'parent_id': node1.id})
        inode, imodel = self.env['fuse.node'].findpath('/Test1/Dir2')
        self.assertEqual(inode, node3)
        self.assertEqual(imodel, None)

        # TODO: Find path multi directory static/static/dynamic
        node4 = self.env['fuse.node'].create(
            {'name': 'Dir3',
             'parent_id': node3.id,
             'model_id': self.env.ref('base.model_res_partner').id})
        inode, imodel = self.env['fuse.node'].findpath('/Test1/Dir2/Some Partner')
        self.assertEqual(inode, node4)
        self.assertEqual(imodel, partner)

        # TODO: Find path multi directory dynamic/dynamic

    def test_readdir(self):
        node1 = self.setup_static_node()
        ierr, ipaths = self.env['fuse.node'].readdir('/')
        self.assertEqual(ierr, 0)
        self.assertTrue('Test1' in [i['filename'] for i in ipaths])

        node2 = self.setup_dynamic_node()
        partner = self.env['res.partner'].create({'name': 'PartnerTest'})

        ierr, ipaths = self.env['fuse.node'].readdir('/')
        self.assertEqual(ierr, 0)
        self.assert_('PartnerTest' in [i['filename'] for i in ipaths])

    def test_download(self):
        # Test Open file
        node1 = self.setup_attachment_node()

        attachment1 = self.env['ir.attachment'].create({'name': 'TestAttach1', 'datas': base64.b64encode(b'123456789')})

        # Check for existing item
        ibin = self.env['fuse.node'].download('/TestAttach1')
        self.assertEqual(base64.b64decode(ibin), b'123456789')

        # check if no model (directory)
        ibin = self.env['fuse.node'].download('/')
        self.assertEqual(ibin, None)

        # check if no model (file)
        ibin = self.env['fuse.node'].download('/somerandomstuff')
        self.assertEqual(ibin, None)

    def test_attachment(self):
        node2 = self.setup_dynamic_node()
        node1 = self.setup_attachment_node()
        node1.parent_id = node2
        node1.parent_model_id = self.env.ref('base.model_res_partner')

        partner1 = self.env['res.partner'].create({'name': 'Test1Partner'})
        errno = self.env['fuse.node'].file_create('/Test1Partner/something.txt')
        attachment1 = self.env['ir.attachment'].search([('name', '=', 'something.txt')])
        self.assertEqual(errno, 0)
        self.assertEqual(attachment1.name, 'something.txt')
        self.assertEqual(attachment1.res_id, partner1.id)

    def test_upload(self):
        # Test Open file
        node1 = self.setup_attachment_node()

        attachment1 = self.env['ir.attachment'].create({'name': 'TestAttach1', 'datas': base64.b64encode(b'123456789')})

        # Check for existing item
        ibin = base64.b64encode(b'987654321')
        self.env['fuse.node'].upload('/TestAttach1', ibin)
        self.assertEqual(attachment1.datas, ibin)

    def test_create(self):
        node1 = self.setup_dynamic_node()
        node1.type = 'file'

        node2 = self.env['fuse.node'].create(
            {'name': 'Test2',
             'model_id': self.env.ref('base.model_res_partner').id,
             'parent_field_id': self.env.ref('base.field_res_partner__parent_id').id,
             'parent_id': node1.id,
             'type': 'file'
             }
        )
        err1 = self.env['fuse.node'].file_create('/PartnerTest')

        partner1 = self.env['res.partner'].search([('name', '=', 'PartnerTest')])
        self.assertEqual(err1, 0)
        self.assertTrue(partner1)
        self.assertEqual(partner1.name, 'PartnerTest')

        node1.type = 'dir'

        err1 = self.env['fuse.node'].file_create('/PartnerTest/PartnerTest2')
        partner2 = self.env['res.partner'].search([('name', '=', 'PartnerTest2')])
        self.assertEqual(err1, 0)
        self.assertTrue(partner2)
        self.assertEqual(partner2.name, 'PartnerTest2')
        self.assertEqual(partner2.parent_id, partner1)
