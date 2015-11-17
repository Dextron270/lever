from rpython.rlib.objectmodel import specialize, always_inline
from interface import Error, Object, Interface, null
from builtin import Builtin, signature
from dictionary import Dict
from listobject import List
from module import Module
from multimethod import Multimethod
from numbers import Float, Integer, Boolean
from string import String
from uint8array import Uint8Array
from exnihilo import Exnihilo
from customobject import CustomObject
from rpython.rlib import jit

true = Boolean(True)
false = Boolean(False)

def is_true(flag):
    return flag is not null and flag is not false

def is_false(flag):
    return flag is null or flag is false

def boolean(cond):
    return true if cond else false

@specialize.arg(1, 2)
def argument(argv, index, cls):
    if index < len(argv):
        arg = argv[index]
        if isinstance(arg, cls):
            return arg
    raise Error(u"expected %s as argv: %d" % (cls.interface.name, index))

def as_cstring(value):
    if isinstance(value, String):
        return value.string.encode('utf-8')
    raise Error(u"expected string and got " + value.repr())

def from_cstring(value):
    assert isinstance(value, str)
    return String(value.decode('utf-8'))

def from_ustring(value):
    assert isinstance(value, unicode)
    return String(value)

@always_inline
def get_interface(obj):
    if isinstance(obj, CustomObject):
        return obj.custom_interface
    return obj.__class__.interface
