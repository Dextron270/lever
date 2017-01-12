from rpython.rtyper.lltypesystem import rffi, lltype, llmemory
from space import *
import core
import rlibuv as uv

# consider this instead of cast_ptr_to_adr
#from rpython.rlib.objectmodel import current_object_addr_as_int

def response_handler(name):
    uv_name = "uv__" + name

    def _callback_(handle, *data):
        ec = core.get_ec()
        resp = pop_handle(getattr(ec, uv_name), handle)
        if len(data) > 0:
            resp.data = data
        resp.pending = False
        if resp.greenlet is not None:
            core.root_switch(ec, [resp.greenlet])
    _callback_.__name__ = name + "_cb"
    
    @jit.dont_look_inside
    def pop_handle(table, handle):
        return table.pop(rffi.cast_ptr_to_adr(handle))

    @jit.dont_look_inside
    def push_handle(table, handle, response):
        adr = rffi.cast_ptr_to_adr(handle)
        if adr in table:
            raise unwind(LError(u"libuv handle/request busy"))
        table[adr] = response

    class response:
        _immutable_fields_ = ["ec", "handle"]
        cb = _callback_
        def __init__(self, handle):
            self.ec = core.get_ec()
            self.handle = handle
            self.data = None
            self.greenlet = None
            self.pending = True
            push_handle(getattr(self.ec, uv_name), handle, self)

        def wait(self, status=0):
            if status < 0:
                pop_handle(getattr(self.ec, uv_name), self.handle)
                raise to_error(status)
            elif self.pending:
                self.greenlet = self.ec.current
                core.switch([self.ec.eventloop])
                # TODO: prepare some mechanic, so that
                #       we can be interrupted.
            return self.data

    response.__name__ = name + "_response"
    return response

def to_error(result):
    raise unwind(LUVError(
        rffi.charp2str(uv.err_name(result)).decode('utf-8'),
        rffi.charp2str(uv.strerror(result)).decode('utf-8')
    ))

# TODO: Organize this stuff.
# int uv_fs_mkdir(uv_loop_t* loop, uv_fs_t* req, const char* path, int mode, uv_fs_cb cb)
# uv_fs_cb must be a callback, in this case it returns the request handle req.
# it's possible after a request I'd havy to call uv_fs_req_cleanup
# one thing that makes this troublesome is that the request may return when it's being called.
# so it calls the callback during the call of uv_fs_mkdir, or later in the eventloop
# the callback doesn't get all the data to process the response. I have to make a table and pass some data along in that table.
# the table must be unique to the current thread.
# either the request call or the response may produce an error message. I have to react on the error message.
# then finally, there are several tens of these handles that have to behave like this.
# each have to pass slightly different data to the callback.
# finally, the delayed action may have to be halted. Some of the actions can be halted, some not.
# the rffi.cast_ptr_to_adr(handle) is required to index the handle into hashtable.
# jit doesn't know how to handle it, so it has to be hidden by the jit

# potentially the problem is that I have logic in the async handler.
# the async handler should just return and inform me that it has returned.
# oh. there is the problem. :)

# pass all data from the async call into the request.
# I have to mark oneshot vs. not oneshot.
#   there are variations on that behavior for some of these.

# @uv_callback.response
# def stream_read_req(a, b, uv_stuff):
#     return value
# 
# req = stream_read_req(handle, a, b)
# return req.wait( uv.some_function(stream_read_req.cb) )
# 
# #core.get_ec() has uv__ handlename for each handle.
# 
# def response(cb):
#     name = "uv_" + cb.__name__
#     assert name not in uv_init_list
#     uv_init_list.append(name)
# 
#     @jit.dont_look_inside
#     def push_handle(ec, handle, req):
#         adr = rffi.cast_ptr_to_adr(handle)
#         if adr in table:
#             raise unwind(LError(u"async request collision"))
#         getattr(ec, name)[adr] = req
# 
#     @jit.dont_look_inside
#     def pop_handle(ec, handle):
#         return getattr(ec, name).pop(
#             rffi.cast_ptr_to_adr(handle))
# 
#     def _callback_(handle, *uv_stuff):
#         ec = core.get_ec()
#         req = pop_handle(ec.this_table, handle)
#         try:
#             val = cb(*(req.data + uv_stuff))
#         except Unwinder as unwinder:
#             req.failure(unwinder)
#         else:
#             req.response(val)
# 
# 
#     class Request:
#         cb = _callback_
#         def __init__(self, handle, *data):
#             self.ec = core.get_ec()
#             self.data = data
#             push_handle(ec, handle, self)
# 
#         def wait(self, status):
#             pass
# 
#     return Request

read = response_handler("read")
write = response_handler("write")
connect = response_handler("connect")
shutdown = response_handler("shutdown")
connection = response_handler("connection")
close = response_handler("close")
poll = response_handler("poll")
timer = response_handler("timer")
#async = response_handler("async")
#prepare = response_handler("prepare")
#check = response_handler("check")
#idle = response_handler("idle")
#exit = response_handler("exit")
#walk = response_handler("walk")
fs = response_handler("fs")
#work = response_handler("work")
#after_work = response_handler("after_work")
getaddrinfo = response_handler("getaddrinfo")
getnameinfo = response_handler("getnameinfo")