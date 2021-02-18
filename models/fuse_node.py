# -*- coding: utf-8 -*-

from odoo import models, fields, api
import errno
from pathlib import Path
from stat import *
from datetime import datetime
import re


class FUSEDefaultValues(models.Model):
    _name = "fuse.default_values"
    _description = "Define default values attached to a node. Allows res_id, model_id type linking"

    display_name = fields.Char(compute='_compute_eval')
    node_id = fields.Many2one('fuse.node')
    model_id = fields.Many2one(related='node_id.model_id')
    field_id = fields.Many2one('ir.model.fields', help='Field where value should be assigned')
    field_value = fields.Char('Value', help='Can be integer or string, string in quotes, can also refer '
                                            'to another field using fieldname')

    @api.depends('field_id', 'field_value')
    def _compute_eval(self):
        for default_value in self:
            default_value.display_name = f"{default_value.field_id.model} = {default_value.field_value}"


# What is the right symantics, when creating a file node.
# 1. If parent node exist, then use parent_field_id to attach current node to parents model.
# 2. If the parent does not have a model attached, then the parent node is used as the model is used for lookup and assignments

class FUSE(models.Model):
    _name = 'fuse.node'
    _description = 'Fuse Node describing directory or file properties'
    _rec_name = 'display_name'

    display_name = fields.Char(compute='_compute_display_name', store=True)
    name = fields.Char('Name', help='Static name or {item} to access object')

    # Name can be static (just a name) or dynamic (composed of fields). The field uses python format syntax and node
    # variable will be assigned to self.
    description = fields.Char('Description')
    type = fields.Selection(selection=[('file', 'File'), ('dir', 'Directory')], default='dir')
    parent_id = fields.Many2one('fuse.node')
    model_id = fields.Many2one('ir.model',
                               help='For static directories (where name does not change) no model must be assigned')  # Model this node applies to (can be parent_field_id)
    res_model = fields.Char(related='model_id.model')
    parent_field_id = fields.Many2one('ir.model.fields')  # Field on parent model this file attaches to
    parent_model_id = fields.Many2one(related='parent_id.model_id', string='Parent Model')  # The parent model
    name_pattern = fields.Char('Filename Pattern',
                               help='Pattern used for dynamic nodes, eg. {item.name} uses python format',
                               default='{item.name}')
    name_re_pattern = fields.Char('Filename Regular Expression',
                                  help='Pattern used to convert filename to object elements, uses python named regular'
                                       ' expressions can only use field names',
                                  default='(?P<name>[\w\s]+)')
    field_value_ids = fields.One2many('fuse.default_values', 'node_id')
    path_name = fields.Char('Path Name', help='Path name', default='The Static file/directory Name')
    filter_domain = fields.Char('Filter Domain', default='[]')  # Domain to filter
    full_path = fields.Char(compute="_compute_full_path")
    file_content = fields.Selection([('bin', 'Binary'), ('json', 'JSON Format'), ('report', 'Report')])
    file_size = fields.Char('File Size Script',
                            help='This is a script to determine file size. In most cases just a field name')
    bin_field = fields.Many2one('ir.model.fields')
    report_id = fields.Many2one('ir.actions.report', 'Report')
    json_fields = fields.Many2many('ir.model.fields')

    @api.depends('name', 'model_id')
    def _compute_display_name(self):
        for item in self:
            if item.model_id:
                item.display_name = f'({item.model_id.model}) {item.name}'
            else:
                item.display_name = f'{item.name}'

    @api.onchange('parent_model_id')
    def _change_parent_model_id(self):
        return {'domain': {'parent_field_id': [('model_id', '=', self.parent_model_id.id)]}}

    @api.depends('name', 'parent_id')
    def _compute_full_path(self):
        for item in self:
            full_path = Path()
            sitem = item
            while sitem:
                # TODO: Make full path easier to follow
                # Format partner/(res.company)/[company_id] <= (res.partner)/[parent_id] <= (res.partner)
                if sitem.parent_field_id and sitem.parent_model_id:
                    full_path = Path(f'[{sitem.parent_field_id.name}] <= ({sitem.model_id.model})').joinpath(
                        full_path)
                elif sitem.model_id:
                    full_path = Path(f"({sitem.model_id.model})").joinpath(full_path)
                else:
                    full_path = Path(f"{sitem.name}").joinpath(full_path)
                sitem = sitem.parent_id
            item.full_path = full_path

    @api.model
    def find_node(self, path, parent_model_id=None, types=['dir']):
        """This function return a node associate with a path

            For static return name
            For dynamic filter using filter_domain and construct name using name_pattern
            inputs: parent_model_id - Model that is associated with the parent
                    path - The path part to search for
            output: ierr, inode, imodel
        """
        for node in self.env['fuse.node'].search([('parent_id', '=', self.id)]):
            # If static node matches then return found with parent_model
            if not node.model_id and node.name == path:
                return 0, node, parent_model_id  # Static so return parent_model and node
            elif node.model_id:
                if not node.filter_domain:
                    node.filter_domain = '[]'
                domain = []
                if node.parent_field_id and parent_model_id:
                    domain.extend([(node.parent_field_id.name, '=', parent_model_id.id)])
                domain.extend(eval(node.filter_domain))
                for model_id in self.env[node.model_id.model].search(domain):
                    path_name = node.name_pattern.format(item=model_id, parent=parent_model_id)
                    path_name = path_name.replace('/', '_')
                    if path_name == path:
                        return 0, node, model_id
        return errno.ENOENT, None, None

    @api.model
    def findpath(self, path, inode=None):
        '''Need to get the path node/model associated with the path.

        returns: node - The node that the path refers to
                 model - If there is a model attached to the path then the model associated with that path'''

        # TODO: Handle Files.

        # Path search method
        # Root path is always static so it is skipped

        path = Path(path)
        if path == Path('/'):
            return self.env.ref('fuse.root_node'), None

        parts = path.parts[1:]
        parent_model = None
        parent_node = self.env.ref('fuse.root_node')

        while len(parts) > 1:
            # Search all nodes that are attach to the parent_node.
            # If the node is static match the name field (no model attached.)
            nodes = self.env['fuse.node'].search([('parent_id', '=', parent_node.id),
                                                  ('type', '=', 'dir')])
            ierr, inode, imodel = parent_node.find_node(parts[0], parent_model)
            if not inode:
                break
            parent_node = inode
            parent_model = imodel
            parts = parts[1:]

        if len(parts) == 1:
            ierr, inode, imodel = parent_node.find_node(parts[0], parent_model, types=['dir', 'file'])
            return inode, imodel

        return None, None

    def paths(self, parent_model_id=None):
        """This function returns all the path meta data associated with a node

            For static return metadata for node
            For dynamic filter using filter_domain and construct metadata using name_pattern
            inputs: parent_model - The model of the parent that is associated with this node (the instance of the model)
            output: list with paths
        """

        # TODO: Add parent filter

        path_list = []
        st_mode = 0
        if self.type == 'dir':
            st_mode |= S_IFDIR | S_IXUSR | S_IXGRP | S_IRUSR | S_IRGRP
        if self.type == 'file':
            st_mode |= S_IFREG | S_IRUSR | S_IWUSR | S_IRGRP | S_IWGRP

        if not self.model_id:
            meta1 = {
                'filename': self.name,
                'st_mtime': self.write_date.timestamp(),
                'st_ctime': self.write_date.timestamp(),
                'st_atime': self.write_date.timestamp(),
                'st_size': 1024,
                'st_mode': st_mode,
                'errno': 0}
            path_list.append(meta1)
        else:
            if not self.filter_domain:
                self.filter_domain = '[]'
            domain = []
            if self.parent_field_id and parent_model_id:
                domain.extend([(self.parent_field_id.name, '=', parent_model_id.id)])
            domain.extend(eval(self.filter_domain))
            for model_id in self.env[self.model_id.model].search(domain):
                path_name = self.name_pattern.format(item=model_id, parent=parent_model_id)
                path_name = path_name.replace('/', '_')
                if 'file_size' in model_id:
                    st_size = eval(self.file_size,
                                   {'item': model_id})  # TODO: Change to better solution to determine file size.
                else:
                    st_size = 0

                meta1 = {
                    'filename': path_name,
                    'st_mtime': model_id.write_date.timestamp() if model_id.write_date else 0,
                    'st_atime': model_id.write_date.timestamp() if model_id.write_date else 0,
                    'st_ctime': model_id.create_date.timestamp() if model_id.create_date else 0,
                    'st_size': st_size,
                    'st_mode': st_mode,
                    'errno': 0
                }
                path_list.append(meta1)
        return path_list

    @api.model
    def setattr(self, path, attr):
        (node, model) = self.findpath(path)
        if model and 'st_mtime' in attr:
            model.write_date = datetime.fromtimestamp(attr['st_mtime'])

    @api.model
    def getattr(self, path, fh=None):
        """return errno, attrs"""
        path = Path(path)
        (node, model) = self.findpath(path)
        # TODO: Get permissions from odoo
        oattr = {
            'st_mode': 0,
            'st_atime': datetime.timestamp(datetime.now()),
            'st_ctime': datetime.timestamp(datetime.now()),
            'st_mtime': datetime.timestamp(datetime.now()),
            'st_size': 1024,
            'st_nlink': 0,
            'errno': 0}

        if not node:
            oattr.update({'errno': errno.ENOENT})
            return oattr

        if node.type == 'dir':
            oattr['st_mode'] |= S_IFDIR | S_IXUSR | S_IXGRP | S_IRUSR | S_IRGRP
        if node.type == 'file':
            oattr['st_mode'] |= S_IFREG | S_IRUSR | S_IWUSR | S_IRGRP | S_IWGRP
        if model and model.create_date:
            oattr['st_ctime'] = model.create_date.timestamp()
        if model and model.write_date:
            oattr['st_mtime'] = model.write_date.timestamp()

        # Default size
        if node and node.type == 'dir':
            oattr['st_size'] = 1024
        if node and node.type == 'file':
            oattr['st_size'] = 0

        # Default model.file_size
        if model and 'file_size' in model:
            oattr['st_size'] = model.file_size

        # Default node.file_Size
        if node and node.file_size:
            oattr['st_size'] = eval(node.file_size,
                                    {'item': model})

        return oattr

    @api.model
    def readdir(self, path):
        # TODO: Speedup directory listing
        ierr = 0
        path = Path(path)
        dirnode, parent_model = self.findpath(path)
        dirents = [{'filename': '.',
                    'st_mode': S_IFDIR | S_IXUSR | S_IXGRP | S_IRUSR | S_IRGRP,
                    'st_atime': dirnode.write_date.timestamp(),
                    'st_mtime': dirnode.write_date.timestamp(),
                    'st_ctime': dirnode.create_date.timestamp(),
                    'st_size': 1024,
                    'st_size': 1024,
                    'errno': 0},
                   {'filename': '..',
                    'st_mode': S_IFDIR | S_IXUSR | S_IXGRP | S_IRUSR | S_IRGRP,
                    'st_atime': dirnode.write_date.timestamp(),
                    'st_mtime': dirnode.write_date.timestamp(),
                    'st_ctime': dirnode.create_date.timestamp(),
                    'st_size': 1024,
                    'errno': 0}]

        fuse_error = 0
        if dirnode and dirnode.type == 'dir':
            dirnodes = self.env['fuse.node'].search([('parent_id', '=', dirnode.id)])
            for node in dirnodes:
                dirents.extend(node.paths(parent_model))
        else:
            ierr = errno.ENOTDIR
        return ierr, dirents

    @api.model
    def rmdir(self, path):
        ierr = 0
        path = Path(path)
        dirnode, imodel = self.findpath(path)
        if dirnode.type == 'dir':
            if imodel:
                imodel.unlink()
                return 0
            else:
                return errno.ENOENT

    @api.model
    def mkdir(self, path):
        path = Path(path)
        parent_path = path.parent
        parent_node, parent_model = self.env['fuse.node'].findpath(parent_path)
        error = errno.EACCES
        if parent_node and parent_node.type == 'dir':
            nodes = self.env['fuse.node'].search([('parent_id', '=', parent_node.id),
                                                  ('type', '=', 'dir')])
            for node in nodes:
                if node.name_re_pattern and node.model_id:
                    match1 = re.fullmatch(node.name_re_pattern, str(path.name))
                    if not match1:
                        continue
                    field_values = match1.groupdict()
                    if parent_model and node.parent_field_id:
                        field_values.update({node.parent_field_id.name: parent_model.id})
                    field_values.update(
                        {field1.field_id.name: eval(field1.field_value) for field1 in node.field_value_ids})
                    self.env[node.model_id.model].create(field_values)
                    error = 0
                    break

        return error

    @api.model
    def unlink(self, path):
        ierr = 0
        path = Path(path)
        dirnode, imodel = self.findpath(path)
        if dirnode.type == 'file':
            if imodel:
                imodel.unlink()
                return 0
            else:
                return errno.ENOENT

    @api.model
    def rename(self, old, new):
        old_path = Path(old)
        new_path = Path(new)
        old_node, old_model = self.findpath(old_path)
        error = errno.EACCES
        parent_path = new_path.parent
        parent_node, parent_model = self.env['fuse.node'].findpath(parent_path)
        error = errno.EACCES
        if parent_node and parent_node.type == 'dir':
            nodes = self.env['fuse.node'].search([('parent_id', '=', parent_node.id)])
            for node in nodes:
                # check each node for re
                # TODO: Handle Duplicates
                if node.name_re_pattern and node.model_id:
                    match1 = re.fullmatch(node.name_re_pattern, str(new_path.name))
                    if not match1:
                        continue
                    field_values = match1.groupdict()
                    if parent_model and node.parent_field_id:
                        field_values.update({node.parent_field_id.name: parent_model.id})
                        # TODO: Set Default values here
                    field_values.update(
                        {field1.field_id.name: eval(field1.field_value) for field1 in node.field_value_ids})
                    old_model.write(field_values)
                    error = 0
                    break

        return error

    @api.model
    def file_create(self, path):
        # TODO: If no model assigned on parent the use node as parent object
        path = Path(path)
        parent_path = path.parent
        parent_node, parent_model = self.env['fuse.node'].findpath(parent_path)
        error = errno.EACCES
        if parent_node and parent_node.type == 'dir':
            nodes = self.env['fuse.node'].search([('parent_id', '=', parent_node.id),
                                                  ('type', '=', 'file')])
            for node in nodes:
                # check each node for re
                # TODO: Handle Duplicates
                if node.name_re_pattern and node.model_id:
                    match1 = re.fullmatch(node.name_re_pattern, str(path.name))
                    if not match1:
                        continue
                    field_values = match1.groupdict()
                    if parent_model and node.parent_field_id:
                        field_values.update({node.parent_field_id.name: parent_model.id})
                        # TODO: Set Default values here
                    field_values.update(
                        {field1.field_id.name: eval(field1.field_value) for field1 in node.field_value_ids})
                    self.env[node.model_id.model].create(field_values)
                    error = 0
                    break

        return error

    @api.model
    def upload(self, path, bin_data):
        """Receives bin_data in b64 format and loads it into a binary object
        input: path, ibin
        """
        path = Path(path)
        inode, imodel = self.findpath(path)
        if imodel and inode and inode.bin_field:
            exec(f'imodel.{inode.bin_field.name} = bin_data')

    @api.model
    def download(self, path):
        """Opens and returns the binary data store in object references by path
        input: path
        output: obin - Binary object BASE64 encoded.
                        {'st_ctime', 'st_mtime', 'st_atime', 'st_size', 'st_mode'
                      """
        path = Path(path)
        inode, imodel = self.findpath(path)
        if imodel and inode and inode.bin_field:
            ibin = eval(f'imodel.{inode.bin_field.name}')
        else:
            ibin = None
        return ibin

        # Look for the path object
        # if the path exit then return the binary field to the odoofs file
