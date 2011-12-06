##############################################################################
#
# Copyright (c) Zope Foundation and Contributors.
# All Rights Reserved.
#
# This software is subject to the provisions of the Zope Public License,
# Version 2.0 (ZPL).  A copy of the ZPL should accompany this distribution.
# THIS SOFTWARE IS PROVIDED "AS IS" AND ANY AND ALL EXPRESS OR IMPLIED
# WARRANTIES ARE DISCLAIMED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF TITLE, MERCHANTABILITY, AGAINST INFRINGEMENT, AND FITNESS
# FOR A PARTICULAR PURPOSE.
#
##############################################################################
"""Testing support

This module provides a mock of zookeeper needed to test use of zc.zk.
It's especially useful for testing packages that build on zc.zk.

It provides setUp and tearDown functions that can be used with
doctests or with regular ```unittest`` tests.
"""
import json
import mock
import threading
import time
import zc.zk
import zookeeper

__all__ = ['assert_', 'setUp', 'tearDown']

def assert_(cond, mess=''):
    """A simple assertion function for use in doctests.
    """
    if not cond:
        print 'assertion failed: ', mess

def wait_until(func=None, timeout=9):
    """Wait until a function returns true.

    Raise an AssertionError on timeout.
    """
    if func():
        return
    deadline = time.time()+timeout
    while not func():
        time.sleep(.01)
        if time.time() > deadline:
            raise AssertionError('timeout')

def setUp(test, tree=None, connection_string='zookeeper.example.com:2181'):
    """Set up zookeeper emulation.

    The first argument is a test case object (either doctest or unittest).

    You can optionally pass:

    tree
       An initial ZooKeeper tree expressed as an import string.
       If not passed, an initial tree will be created with examples
       used in the zc.zk doctests.

    connection_string
       The connection string to use for the emulation server. This
       defaults to 'zookeeper.example.com:2181'.
    """
    if tree:
        zk = ZooKeeper(connection_string, Node())
    else:
        zk = ZooKeeper(
            connection_string,
            Node(
                fooservice = Node(
                    json.dumps(dict(
                        database = "/databases/foomain",
                        threads = 1,
                        favorite_color= "red",
                        )),
                    providers = Node()
                    ),
                zookeeper = Node('', quota=Node()),
                ),
            )
    teardowns = []
    for name in ZooKeeper.__dict__:
        if name[0] == '_':
            continue
        cm = mock.patch('zookeeper.'+name)
        m = cm.__enter__()
        m.side_effect = getattr(zk, name)
        teardowns.append(cm.__exit__)

    if tree:
        zk = zc.zk.ZooKeeper(connection_string)
        zk.import_tree(tree)
        zk.close()

    globs = getattr(test, 'globs', test.__dict__)
    globs['wait_until'] = wait_until
    globs['zc.zk.testing'] = teardowns

def tearDown(test):
    """The matching tearDown for setUp.

    The single argument is the test case passed to setUp.
    """
    globs = getattr(test, 'globs', test.__dict__)
    for cm in globs['zc.zk.testing']:
        cm()

class ZooKeeper:

    def __init__(self, connection_string, tree):
        self.connection_string = connection_string
        self.root = tree
        self.sessions = {}
        self.lock = threading.RLock()

    def init(self, addr, watch=None):
        with self.lock:
            assert_(addr==self.connection_string, addr)
            handle = 0
            while handle in self.sessions:
                handle += 1
            self.sessions[handle] = set()
            if watch:
                watch(handle,
                      zookeeper.SESSION_EVENT, zookeeper.CONNECTED_STATE, '')

    def _check_handle(self, handle):
        with self.lock:
            if handle not in self.sessions:
                raise zookeeper.ZooKeeperException('handle out of range')

    def _traverse(self, path):
        with self.lock:
            node = self.root
            for name in path.split('/')[1:]:
                if not name:
                    continue
                try:
                    node = node.children[name]
                except KeyError:
                    raise zookeeper.NoNodeException('no node')

            return node

    def close(self, handle):
        with self.lock:
            self._check_handle(handle)
            for path in list(self.sessions[handle]):
                self.delete(handle, path)
            del self.sessions[handle]
            self.root.clear_watchers(handle)

    def state(self, handle):
        with self.lock:
            self._check_handle(handle)
            return zookeeper.CONNECTED_STATE

    def create(self, handle, path, data, acl, flags=0):
        with self.lock:
            self._check_handle(handle)
            base, name = path.rsplit('/', 1)
            node = self._traverse(base)
            if name in node.children:
                raise zookeeper.NodeExistsException()
            node.children[name] = newnode = Node(data)
            newnode.acls = acl
            newnode.flags = flags
            node.children_changed(handle, zookeeper.CONNECTED_STATE, base)
            if flags & zookeeper.EPHEMERAL:
                self.sessions[handle].add(path)
            return path

    def delete(self, handle, path):
        with self.lock:
            self._check_handle(handle)
            node = self._traverse(path)
            base, name = path.rsplit('/', 1)
            bnode = self._traverse(base)
            del bnode.children[name]
            node.deleted(handle, zookeeper.CONNECTED_STATE, path)
            bnode.children_changed(handle, zookeeper.CONNECTED_STATE, base)
            if path in self.sessions[handle]:
                self.sessions[handle].remove(path)

    def exists(self, handle, path):
        with self.lock:
            self._check_handle(handle)
            try:
                self._traverse(path)
                return True
            except zookeeper.NoNodeException:
                return False

    def get_children(self, handle, path, watch=None):
        with self.lock:
            self._check_handle(handle)
            node = self._traverse(path)
            if watch:
                node.child_watchers += ((handle, watch), )
            return sorted(node.children)

    def get(self, handle, path, watch=None):
        with self.lock:
            self._check_handle(handle)
            node = self._traverse(path)
            if watch:
                node.watchers += ((handle, watch), )
            return node.data, dict(
                ephemeralOwner=(1 if node.flags & zookeeper.EPHEMERAL else 0),
                )

    def set(self, handle, path, data):
        with self.lock:
            self._check_handle(handle)
            node = self._traverse(path)
            node.data = data
            node.changed(handle, zookeeper.CONNECTED_STATE, path)

    def get_acl(self, handle, path):
        with self.lock:
            self._check_handle(handle)
            node = self._traverse(path)
            return dict(aversion=node.aversion), node.acl

    def set_acl(self, handle, path, aversion, acl):
        with self.lock:
            self._check_handle(handle)
            node = self._traverse(path)
            if aversion != node.aversion:
                raise zookeeper.BadVersionException("bad version")
            node.aversion += 1
            node.acl = acl

class Node:
    watchers = child_watchers = ()
    flags = 0
    aversion = 0
    acl = zc.zk.OPEN_ACL_UNSAFE

    def __init__(self, data='', **children):
        self.data = data
        self.children = children

    def children_changed(self, handle, state, path):
        watchers = self.child_watchers
        self.child_watchers = ()
        for h, w in watchers:
            w(h, zookeeper.CHILD_EVENT, state, path)

    def changed(self, handle, state, path):
        watchers = self.watchers
        self.watchers = ()
        for h, w in watchers:
            w(h, zookeeper.CHANGED_EVENT, state, path)

    def deleted(self, handle, state, path):
        watchers = self.watchers
        self.watchers = ()
        for h, w in watchers:
            w(h, zookeeper.DELETED_EVENT, state, path)
        watchers = self.child_watchers
        self.watchers = ()
        for h, w in watchers:
            w(h, zookeeper.DELETED_EVENT, state, path)

    def clear_watchers(self, handle):
        self.watchers = tuple(
            (h, w) for (h, w) in self.watchers
            if h != handle
            )
        self.child_watchers = tuple(
            (h, w) for (h, w) in self.child_watchers
            if h != handle
            )
        for child in self.children.itervalues():
            child.clear_watchers(handle)
