#!/usr/bin/env python3

# C: Jacobus Erasmus
# C: IvyWeb (Pty) Ltd

import os
import sys
import errno
import argparse
import odoorpc
from urllib.parse import urlparse
from pathlib import Path
from datetime import datetime
from fusepy import FUSE, FuseOSError, Operations
from stat import *
from base64 import b64encode, b64decode
import shelve
import unittest
import pathlib
import math


# ---- [Helpers] -----

def _now():
    return datetime.now().timestamp()


class FileMeta:
    # This needs to be compatible with fuse_node

    """This class stores data per file needed for cache file persistence

    Stores Local File Attributes
    filename -
    ctime,atime,mtime - Create, Access, Modified time localy
    rctime,rmtime - Create, Modified time remotely
    stime - Last file data sync
    astime - Last attribute sync time
    size - File Size
    mode - File Model (Dir,File,Access rights)
    errno - Error accessing the file, (used for referse cache)"""

    def __init__(self, filename, errno=2, ctime=0, mtime=0, atime=0,
                 rctime=0, rmtime=0, size=0, mode=0, stime=0, astime=0):
        """filename - The virtual file system file name
           errno - Error Number
           ctime - Create time
           mtime - Modify time
           atime - Access Time
           size - Size
           mode - File Mode
           rctime - Remote Create Time
           rmtime - Rmote Modify Time
           stime - Sync File Time
           astime - Attribute Sync Time
        """

        self.filename = Path(filename)
        self.ctime = ctime
        self.mtime = mtime
        self.atime = atime
        self.size = size
        self.mode = mode
        self.errno = errno
        self.rctime = rctime
        self.rmtime = rmtime
        self.stime = stime
        self.astime = astime

    def update(self, rattrs={}):
        self.size = rattrs['st_size'] if rattrs else 0
        self.mode = rattrs['st_mode'] if rattrs else 0
        self.errno = rattrs['errno'] if rattrs else 0
        self.rctime = rattrs['st_ctime'] if rattrs else 0
        self.rmtime = rattrs['st_mtime'] if rattrs else 0
        self.astime = _now() if rattrs else 0

    def touch(self):
        self.atime = _now()
        self.mtime = _now()

    def age(self):
        return _now() - self.stime

    def attr_age(self):
        return _now() - self.astime


# min,max - is based on access time cache timing will be kept in memory for open files.
class AttrCache:
    def __init__(self, cache_dir, fuse, min_refresh=60, max_timeout=3600):
        self.cache_dir = Path(cache_dir)
        self.min_time = min_refresh
        self.max_time = max_timeout
        self.meta = shelve.open(str(self.cache_dir / Path('.meta_cache')), writeback=True)
        self.filehandle = {}
        self.fuse = fuse

    def __setitem__(self, key, value):
        self.meta[str(key)] = value

    def __getitem__(self, key):
        path = str(key)

        if path not in self.meta:
            attr = self.fuse.getattr(path)
            if attr and path not in self.meta:
                self.meta[path] = FileMeta(path, errno=attr['errno'], ctime=attr['st_ctime'], mtime=attr['st_mtime'],
                                           atime=attr['st_atime'], mode=attr['st_mode'], size=attr['st_size'])
            else:
                self.meta[path] = FileMeta(path, errno=errno.ENOENT)

        return self.meta[path]

    def __delitem__(self, key):
        del self.meta[str(key)]

    def __iter__(self):
        return iter(self.meta)

    def __len__(self):
        return len(self.meta)

    def __contains__(self, key):
        return str(key) in self.meta

    def cache_open(self, path, bin_object):
        full_path = self.full_path(path)
        os.makedirs(full_path.parent, mode=0o700, exist_ok=True)
        fh = open(full_path, "w+b")
        self.meta[path].stime = _now()
        fh.write(bin_object)
        fh.close()

    def full_path(self, path):
        path = Path(path).relative_to('/')
        full_path = self.cache_dir / Path(path)
        return full_path


class OdooFS(Operations):

    def __init__(self, config, odoo):
        self.odoo = odoo
        self.config = config
        self.fuse = self.odoo.env['fuse.node']
        self.attr = AttrCache(self.config.cache, self.fuse)

    # Helpers
    # =======

    def _full_path(self, path):
        full_path = self.attr.full_path(path)
        return full_path

    def _upload(self, path):
        full_path = self._full_path(path)
        with open(full_path, 'rb') as f:
            bin_data = b64encode(f.read())
            self.odoo.env['fuse.node'].upload(str(path), bin_data.decode('utf-8'))

    def _download(self, path):
        bin_data = self.fuse.download(path)
        if bin_data:
            self.attr.cache_open(path, b64decode(bin_data))
        else:
            self.attr.cache_open(path, b'')

    # Filesystem methods
    # ==================

    # TODO: Check if mode is same as on odoo if not return error
    def chmod(self, path, mode):
        if self.attr[path]:
            self.attr[path].mode = mode
            error = self.attr[path].errno
        if not self.attr[path]:
            error = errno.ENOENT
        if error:
            FuseOSError(error)

    # TODO: Check if ownership is same on odoo if not return error
    def chown(self, path, uid, gid):
        if self.attr[path]:
            self.attr[path].uid = uid
            self.attr[path].gid = gid
        if not self.attr[path]:
            FuseOSError(errno.ENOENT)
        if self.attr[path]:
            FuseOSError(self.attr[path].errno)

    def getattr(self, path, fh=None):
        """
        Returns the file information Works by checking if file have been synced if so return file information
        If file have not been sync check if attributes have been sync return synced attributes or sync if necessary
        """

        if path in self.attr:
            meta1 = self.attr[path]
        else:
            rattr = self.fuse.getattr(path)
            meta1 = FileMeta(path, mode=rattr['st_mode'], ctime=rattr['st_ctime'], mtime=rattr['st_mtime'],
                             atime=rattr['st_atime'], size=rattr['st_size'], errno=rattr['errno'])

        if not meta1:
            raise FuseOSError(errno.NOENT)
        elif meta1.errno != 0:
            raise FuseOSError(meta1.errno)

        oattr = {'st_uid': self.config.uid,
                 'st_gid': self.config.gid,
                 'st_mtime': meta1.mtime,
                 'st_atime': meta1.atime,
                 'st_ctime': meta1.ctime,
                 'st_mode': meta1.mode,
                 'st_size': meta1.size,
                 'st_nlink': math.ceil(meta1.size / 1024),
                 'errno': meta1.errno
                 }
        return oattr

    def readdir(self, path, fh):
        fuse_errno, dirents = self.fuse.readdir(path)
        if fuse_errno != 0:
            raise FuseOSError(fuse_errno)
        for entry in dirents:
            fm = FileMeta(filename=Path(path) / Path(entry['filename']), mode=entry['st_mode'], atime=entry['st_atime'],
                          mtime=entry['st_mtime'], ctime=entry['st_ctime'], size=entry['st_size'], errno=entry['errno'])
            self.attr[fm.filename] = fm

        for r in dirents:
            yield r['filename']

    def readlink(self, path):
        raise FuseOSError(errno.ENOSYS)

    # TODO: This should not be support? Use cache to handle this?
    def mknod(self, path, mode, dev):
        raise FuseOSError(errno.ENOSYS)

    # TODO: Remove object from odoo
    # TODO: If a path without object then create path in odoo
    def rmdir(self, path):
        errno = self.fuse.rmdir(path)
        if errno:
            raise FuseOSError(errno)

    # TODO: Create object on odoo
    # TODO: If a path without object then create path in odoo
    def mkdir(self, path, mode):
        errno = self.fuse.mkdir(path)
        if errno:
            raise FuseOSError(errno)

    def statfs(self, path):
        """
        Return
        file
        system
        information
        for the cache files"""
        full_path = self._full_path(path)
        stv = os.statvfs(full_path)
        return dict((key, getattr(stv, key)) for key in ('f_bavail', 'f_bfree',
                                                         'f_blocks', 'f_bsize', 'f_favail', 'f_ffree', 'f_files',
                                                         'f_flag',
                                                         'f_frsize', 'f_namemax'))

    # TODO: Remove object or attachment in odoo
    def unlink(self, path):
        errno = self.fuse.unlink(path)
        if errno:
            raise FuseOSError(errno)

    # TODO: Create symlink in cache? Can it be translated to Odoo
    def symlink(self, name, target):
        raise FuseOSError(errno.ENOSYS)

    # TODO: Rename field in odoo if possible.
    def rename(self, old, new):
        errno = self.fuse.rename(old, new)
        if errno:
            raise FuseOSError(errno)

    # TODO: Hardlink? Not Supported or supported in cache
    def link(self, target, name):
        raise FuseOSError(errno.ENOSYS)

    # TODO: Pull times from odoo for object
    def utimens(self, path, times=None):
        raise FuseOSError(errno.ENOSYS)

    # File methods
    # ============

    def open(self, path, flags):
        """Takes path and flags and open a file in the local cache
        if file does not exists or is not updated download from odoo first"""

        fm = self.attr[path]

        # Retrieve meta data
        if fm.errno == 0 and S_ISREG(fm.mode):
            if fm.mtime < fm.rmtime or not self._full_path(path).exists():
                self._download(path)
                self.attr[path].stime = _now()
        elif fm.errno == 0 and S_ISDIR(fm.mode):
            raise FuseOSError(errno.EISDIR)
        else:
            raise FuseOSError(fm.errno)
        fh = os.open(self._full_path(path), flags)
        return fh

    def create(self, path, mode, fi=None):
        # TODO: Check permissions and return appropriate error
        ierrno = self.odoo.env['fuse.node'].file_create(path)
        if ierrno == 0:
            full_path = self._full_path(path)
            fh = os.open(full_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o700)
            self.attr[path].file_name = path
            self.attr[path].ctime = _now()
            self.attr[path].size = 0
            self.attr[path].errno = ierrno
            self.attr[path].mode = mode | S_IFREG
            return fh
        else:
            raise FuseOSError(ierrno)

    def read(self, path, length, offset, fh):
        # TODO: Check file permissions and return appropriate error messages

        fm = self.attr[path]
        fm.atime = _now()
        if not fm.mode & S_IRUSR:
            raise FuseOSError(errno=errno.EACCES)
        os.lseek(fh, offset, os.SEEK_SET)
        return os.read(fh, length)

    # TODO: Write object data to cache, attachment or object
    def write(self, path, buf, offset, fh):
        fm = self.attr[path]
        fm.mtime = _now()
        self.attr[path] = fm
        os.lseek(fh, offset, os.SEEK_SET)
        return os.write(fh, buf)

    # TODO: Translate to odoo
    def truncate(self, path, length, fh=None):
        full_path = self._full_path(path)
        fm = self.attr[path]
        fm.mtime = _now()
        self.attr[path] = fm
        with open(full_path, 'r+') as f:
            f.truncate(length)

    # TODO: Update all files that changed to odoo objects/attachments
    def flush(self, path, fh):
        # TODO: Flush
        # Pushes the local cache to odoo
        ret1 = os.fsync(fh)
        fm = self.attr[path]
        if fm.mtime > fm.rmtime:
            self._upload(path)
        return ret1

    # TODO: Copy file to odoo object/attachment
    def release(self, path, fh):
        # TODO: release
        # Pushes the local cached object onto odoo.
        ret1 = os.close(fh)
        # Check if min_Age is reached. upload.
        fm = self.attr[path]
        if fm.mtime > fm.rmtime:
            self._upload(path)
        return ret1

    def fsync(self, path, fdatasync, fh):
        return self.flush(path, fh)


def main(config, odoo):
    print('Connected to odoo, ready to use')
    FUSE(OdooFS(config, odoo), config.mount_point, nothreads=True, foreground=True)


class Config:
    def __init__(self):
        self.username = ''
        self.password = ''
        self.port = ''
        self.server = 'localhost'
        self.port = 8069
        self.database = ''
        self.mount_point = ''
        self.uid = None
        self.gid = None
        self.cache = Path.home() / Path('.cache/odoofs')


def read_arguments():
    rconfig = Config()
    parse = argparse.ArgumentParser(description="FUSE file system to acces odoo attachments and objects")
    parse.add_argument('-u', '--username', help='Odoo Username')
    parse.add_argument('-p', '--password', help='Odoo user password')
    parse.add_argument('-s', '--server', help='Odoo server domain or IP')
    parse.add_argument('-d', '--database', help='Odoo database name')
    parse.add_argument('-P', '--port', help='Port number', default=8069)
    parse.add_argument('--cache', help='Cache directory')
    # Should this be ~/.local/share/Odoo/filestore/asterisk
    # Format of dir database/
    parse.add_argument('-m', '--maxage', type=int, help='Max age (days) of files in cache default to no limit',
                       default=0)
    parse.add_argument('-M', '--maxsize', type=int, help='Max size in MB the cache can grow to default to no limit',
                       default=0)
    parse.add_argument('-c', '--config', help='Config file name')
    parse.add_argument('-S', '--segment', help='Config file segment to apply to user, password etc')
    parse.add_argument('--uid', type=int, help='User ID to set file permissions to')
    parse.add_argument('--gid', type=int, help='Group permissions to set files to')
    parse.add_argument('mountpoint', help='Mount Point')
    parse.add_argument('url', nargs='?',
                       help='Odoo url to mount format http(s)://username:password@hostname:port url segments'
                            'are optional. port=80,443 if using url')
    # TODO: User mapping so single mount can be used for whole machine/server
    # TODO: Support so fs can be mounted with fstab
    args = parse.parse_args()
    if args.url:
        url = urlparse(args.url)
        if args.username and url.username:
            print('Username can only be specified once')
            exit(1)
        if args.password and url.password:
            print('Password can only be specified once')
            exit(1)
        if args.server and url.hostname:
            print('Hostname can only be specified once')
            exit(1)
        if args.port and url.port and args.port != 8069:
            print('Port can only be specified once')
            exit(1)
        if not args.username:
            rconfig.username = url.username
        else:
            rconfig.username = args.username
        if not args.password:
            rconfig.password = url.password
        else:
            rconfig.password = args.password
        if not args.server:
            rconfig.server = url.hostname
        else:
            rconfig.server = args.server
        if args.port == 8069 and url.port:
            rconfig.port = url.port
        elif args.port == 8069 and not url.port and url.scheme:
            if url.scheme == 'http':
                rconfig.port = 80
            elif url.scheme == 'https':
                rconfig.port = 443
        else:
            rconfig.port = args.port
    if args.database:
        rconfig.database = args.database
    rconfig.mount_point = args.mountpoint
    # Set uid and gid to the current user
    if not args.uid:
        rconfig.uid = os.getuid()
    else:
        rconfig.uid = args.uid
    if not args.gid:
        rconfig.gid = os.getgid()
    else:
        rconfig.gid = args.gid
    if args.cache:
        rconfig.cache = args.cache
    else:
        rconfig.cache = Path.home() / Path('.cache/odoofs')

    return rconfig


def setup_odoo(iconfig):
    # TODO: Connect to odoo database and return odoo access object
    odoo = odoorpc.ODOO(host=iconfig.server, port=iconfig.port)

    db_list = odoo.db.list()
    if len(db_list) == 1 and not iconfig.database:
        iconfig.database = db_list[0]
    else:
        print('More than one DB is accessable. You need to specify a DB name')
        exit(5)

    odoo.login(iconfig.database, iconfig.username, iconfig.password)
    return odoo


if __name__ == '__main__':
    config = read_arguments()
    odoo = setup_odoo(config)
    main(config, odoo)
