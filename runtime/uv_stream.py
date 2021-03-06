from rpython.rlib.objectmodel import specialize, always_inline
from rpython.rlib.rstring import StringBuilder, UnicodeBuilder
from rpython.rtyper.lltypesystem import rffi, lltype, llmemory
from space import *
import core
from uv_handle import Handle, check
import rlibuv as uv
from stdlib.fs import File
import uv_callback
from uv_callback import to_error

class Stream(Handle):
    def __init__(self, stream):
        Handle.__init__(self, rffi.cast(uv.handle_ptr, stream))
        self.stream = stream
        self.listening = False
        self.listen_count = 0
        self.listen_status = 0
        self.accept_greenlet = None

        self.alloc_buffers = []
        self.read_buffer_size = 0
        self.read_queue = []
        self.read_greenlet = None

    def getattr(self, name):
        if name == u"readable":
            return boolean(uv.is_readable(self.stream))
        if name == u"writable":
            return boolean(uv.is_writable(self.stream))
        return Handle.getattr(self, name)

    def new_stream(self):
        assert False, "abstract"

@Stream.method(u"shutdown", signature(Stream))
def Stream_shutdown(self):
    self.check_closed()
    req = lltype.malloc(uv.shutdown_ptr.TO, flavor='raw', zero=True)
    try:
        response = uv_callback.shutdown(req)
        check( response.wait(uv.shutdown(req, self.stream, uv_callback.shutdown.cb))[0] )
        return null
    finally:
        lltype.free(req, flavor='raw')

@Stream.method(u"listen", signature(Stream, Integer))
def Stream_listen(self, backlog):
    self.check_closed()
    ec = core.get_ec()
    uv_callback.push(ec.uv__connection, self)
    status = uv.listen(self.stream, backlog.value, _listen_callback_)
    if status < 0:
        uv_callback.drop(ec.uv__connection, self.stream)
        raise uv_callback.to_error(status)
    else:
        self.listening = True
        return null

def _listen_callback_(handle, status):
    status = int(status)
    ec = core.get_ec()
    if status < 0:
        self = uv_callback.drop(ec.uv__connection, handle)
        self.listen_status = status
    else:
        self = uv_callback.peek(ec.uv__connection, handle)
        self.listen_count += 1
    if self.accept_greenlet is not None:
        greenlet, self.accept_greenlet = self.accept_greenlet, None
        core.root_switch(ec, [greenlet])

@Stream.method(u"accept", signature(Stream))
def Stream_accept(self):
    self.check_closed()
    if self.listen_count > 0:
        if self.accept_greenlet is not None:
            raise unwind(LError(u"async collision"))
        ec = core.get_ec()
        self.accept_greenlet = ec.current
        core.switch([ec.eventloop])
    check( self.listen_status )
    self.listen_count -= 1
    client = self.new_stream()
    check( uv.accept(self.stream, client.stream) )
    return client

@Stream.method(u"write", signature(Stream, Object))
def Stream_write(self, data):
    self.check_closed()
    bufs, nbufs = uv_callback.obj2bufs(data)
    req = lltype.malloc(uv.write_ptr.TO, flavor='raw', zero=True)
    try:
        response = uv_callback.write(req)
        check( response.wait( uv.write(req, self.stream,
            bufs, nbufs, uv_callback.write.cb))[0] )
        return null
    finally:
        lltype.free(bufs, flavor='raw')
        lltype.free(req, flavor='raw')

@Stream.method(u"write2", signature(Stream, Object, Stream))
def Stream_write2(self, data, send_handle):
    self.check_closed()
    bufs, nbufs = uv_callback.obj2bufs(data)
    req = lltype.malloc(uv.write_ptr.TO, flavor='raw', zero=True)
    try:
        response = uv_callback.write(req)
        check( response.wait( uv.write2(req, self.stream,
            bufs, nbufs, send_handle.stream, uv_callback.write.cb))[0] )
        return null
    finally:
        lltype.free(bufs, flavor='raw')
        lltype.free(req, flavor='raw')

@Stream.method(u"try_write", signature(Stream, Object))
def Stream_try_write(self, data):
    self.check_closed()
    bufs, nbufs = uv_callback.obj2bufs(data)
    try:
        status = uv.try_write(self.stream, bufs, nbufs)
        check(status)
        return Integer(rffi.r_long(status))
    finally:
        lltype.free(bufs, flavor='raw')

# TODO: see what happens when you call this.
@Stream.method(u"stop", signature(Stream))
def Stream_stop(self):
    check( uv.read_stop(self.stream) )
    return null

@Stream.method(u"read", signature(Stream))
def Stream_read(self):
    self.check_closed()
    ec = core.get_ec()
    if len(self.read_queue) == 0:
        uv_callback.push(ec.uv__read, self)
        status = uv.read_start(self.stream, _alloc_callback_, _read_callback_once_)
        if status < 0:
            uv_callback.drop(ec.uv__read, self.stream)
            raise uv_callback.to_error(status)
    if len(self.read_queue) == 0:
        if self.read_greenlet is not None:
            raise unwind(LError(u"async collision"))
        self.read_greenlet = ec.current
        core.switch([ec.eventloop])
    array, nread, status = self.read_queue.pop(0)
    if nread < 0:
        raise uv_callback.to_error(nread)
    if status < 0:
        raise uv_callback.to_error(status)
    if array is None:
        return Uint8Slice(lltype.nullptr(rffi.UCHARP.TO), 0, None)
    if array.length == nread:
        return array
    return array.subslice(nread)

def _alloc_callback_(handle, suggested_size, buf):
    ec = core.get_ec()
    self = uv_callback.peek(ec.uv__read, handle)
    if self.read_buffer_size > 0:
        array = alloc_uint8array(self.read_buffer_size)
    else:
        array = alloc_uint8array(rffi.r_long(suggested_size))
    self.alloc_buffers.append(array)
    buf.c_base = rffi.cast(rffi.CCHARP, array.uint8data)
    buf.c_len = rffi.r_size_t(array.length)

def _read_callback_once_(stream, nread, buf):
    ec = core.get_ec()
    self = uv_callback.drop(ec.uv__read, stream)
    for array in self.alloc_buffers:
        if rffi.cast(rffi.CCHARP, array.uint8data) == buf.c_base:
            break
    else:
        array = None
    status = uv.read_stop(stream)
    self.read_queue.append((array, nread, status))
    if self.read_greenlet is not None:
        greenlet, self.read_greenlet = self.read_greenlet, None
        core.root_switch(ec, [greenlet])

class TTY(Stream):
    def __init__(self, tty, fd):
        Stream.__init__(self, rffi.cast(uv.stream_ptr, tty))
        self.tty = tty
        self.fd = fd

@TTY.method(u"set_mode", signature(TTY, String))
def TTY_set_mode(self, modename_obj):
    self.check_closed()
    modename = string_upper(modename_obj.string)
    if modename == u"NORMAL":
        mode = uv.TTY_MODE_NORMAL
    elif modename == u"RAW":
        mode = uv.TTY_MODE_RAW
    elif modename == u"IO":
        mode = uv.TTY_MODE_IO
    else:
        raise unwind(LError(u"unknown mode: " + modename_obj.repr()))
    check( uv.tty_set_mode(self.tty, mode) )
    return null

@TTY.method(u"get_winsize", signature(TTY))
def TTY_get_winsize(self):
    self.check_closed()
    width  = lltype.malloc(rffi.INTP.TO, 1, flavor='raw', zero=True)
    height = lltype.malloc(rffi.INTP.TO, 1, flavor='raw', zero=True)
    try:
        check( uv.tty_get_winsize(self.tty, width, height) )
        w = rffi.r_long(width[0])
        h = rffi.r_long(height[0])
        obj = Exnihilo()
        obj.setattr(u"width", Integer(w))
        obj.setattr(u"height", Integer(h))
        return obj
    finally:
        lltype.free(width, flavor='raw')
        lltype.free(height, flavor='raw')

class Pipe(Stream):
    def __init__(self, pipe):
        Stream.__init__(self, rffi.cast(uv.stream_ptr, pipe))
        self.pipe = pipe

@Pipe.instantiator2(signature(Boolean))
def Pipe_init(ipc):
    ipc = 1 if is_true(ipc) else 0
    return alloc_pipe(ipc)

@Pipe.method(u"bind", signature(Pipe, String))
def Pipe_bind(self, name):
    self.check_closed()
    string = name.string.encode('utf-8')
    check( uv.pipe_bind(self.pipe, string) )
    return null

@Pipe.method(u"connect", signature(Pipe, String))
def Pipe_connect(self, name):
    self.check_closed()
    string = name.string.encode('utf-8')
    req = lltype.malloc(uv.connect_ptr.TO, flavor='raw', zero=True)
    try:
        response = uv_callback.connect(req)
        uv.pipe_connect(req, self.pipe, string, uv_callback.connect.cb)
        check( response.wait()[0] )
        return null
    finally:
        lltype.free(req, flavor='raw')

@Pipe.method(u"pending_instances", signature(Pipe, Integer))
def Pipe_pending_instances(self, count):
    self.check_closed()
    uv.pipe_pending_instances(self.pipe, count.value)
    return null

@Pipe.method(u"pending_count", signature(Pipe))
def Pipe_pending_count(self):
    self.check_closed()
    result = uv.pipe_pending_count(self.pipe)
    return Integer(rffi.r_long(result))

def alloc_pipe(ipc):
    ec = core.get_ec()
    pipe = lltype.malloc(uv.pipe_ptr.TO, flavor="raw", zero=True)
    status = uv.pipe_init(ec.uv_loop, pipe, ipc)
    if status < 0:
        lltype.free(pipe, flavor='raw')
        raise uv_callback.to_error(status)
    return Pipe(pipe)


@Pipe.method(u"open_fd", signature(Pipe, Integer))
def Pipe_open_fd(self, fd):
    check( uv.pipe_open(self.pipe, fd.value) )
    return null

# if uv.pipe_pending_count(self.pipe) == 0:
#       raise unwind(LError("No pending handles in pipe"))
# uv_handle_type uv_pipe_pending_type(uv_pipe_t* handle)

# int uv_pipe_getsockname(const uv_pipe_t* handle, char* buffer, size_t* size)
# int uv_pipe_getpeername(const uv_pipe_t* handle, char* buffer, size_t* size)

## wait([x, y, z])
## do the same as with .wait(), but prepend the event
## emitter that fires first.
#
#
## Left this here for a moment. I'll perhaps need it later.
##     def new_task(self, func, argv):
##         ec = core.get_ec()
##         greenlet = ec.current
##         self.task_count += 1
##         # This side is not aware about task timeouts.
##         with self.task_lock:
##             self.task_queue.append((ec, greenlet, func, argv))
##             if self.task_wait_count > 0:
##                 self.task_wait_lock.release()
##             elif self.worker_quota > 0:
##                 self.worker_quota -= 1
##                 start_new_thread(async_io_thread, ())
##         return core.switch([ec.eventloop])
## 
## ## new starting thread starts and ends without arguments.
## def async_io_thread():
##     rthread.gc_thread_start()
##     async_io_loop(core.g.io)
##     rthread.gc_thread_die()
## 
## def async_io_loop(io):
##     while True:
##         # Checking whether there's task 
##         ec, greenlet, func, argv = None, None, None, []
##         with io.task_lock:
##             if len(io.task_queue) > 0:
##                 ec, greenlet, func, argv = io.task_queue.pop(0)
##             else:
##                 io.task_wait_lock.acquire(False)
##                 io.task_wait_count += 1
##         if func is None:
##             res = acquire_timed(io.task_wait_lock, 10000000) # timeout 10 seconds
##             # Either timeout or release happened.
##             with io.task_lock:
##                 io.task_wait_count -= 1 # we are not waiting for now.
##                 if res == RPY_LOCK_FAILURE and len(io.task_queue) == 0:
##                     io.worker_quota += 1
##                     return # At this point it is very clear that
##                            # this task is no longer needed.
##         else:
##             try:
##                 res = func(argv)
##                 greenlet.argv.append(res)
##             except Unwinder as unwinder:
##                 greenlet.unwinder = unwinder
##             except Exception as exc:
##                 greenlet.unwinder = unwind(LError(
##                     u"Undefined error at async_io_thread(): " +
##                         str(exc).decode('utf-8') + u"\n"))
##             # I hope these are atomic operations.
##             ec.queue.append(greenlet)
##             io.task_count -= 1
##             eventual.et_notify(ec.handle)
## 
## # Taken from pypy
## def acquire_timed(lock, microseconds):
##     """Helper to acquire an interruptible lock with a timeout."""
##     endtime = (time.time() * 1e6) + microseconds
##     while True:
##         result = lock.acquire_timed(microseconds)
##         if result == RPY_LOCK_INTR:
##             # Run signal handlers if we were interrupted
##             # TODO: lever signal handlers?
##             #space.getexecutioncontext().checksignals()
##             if microseconds >= 0:
##                 microseconds = r_longlong((endtime - (time.time() * 1e6))
##                                           + 0.999)
##                 # Check for negative values, since those mean block
##                 # forever
##                 if microseconds <= 0:
##                     result = RPY_LOCK_FAILURE
##         if result != RPY_LOCK_INTR:
##             break
##     return result
## 
## RPY_LOCK_FAILURE, RPY_LOCK_ACQUIRED, RPY_LOCK_INTR = range(3)
#
## http://docs.libuv.org/en/v1.x/request.html


# This is not a proper place for doing this, figure out later what to do.
import stdlib.net
# TODO: Consider to fix this later, this thing isn't capable of letting one to wait.
@Pipe.method(u"accept", signature(Pipe))
def Pipe_accept(self):
    tp = uv.pipe_pending_type(self.pipe)
    if tp == uv.NAMED_PIPE or tp == uv.UNKNOWN_HANDLE or tp == uv.FILE:
        client = alloc_pipe(1) # The libuv reports an external_memory file handle as UNKNOWN_HANDLE
    elif tp == uv.TCP:         # I guess it might be safe to alloc a pipe when that happens.
        client = stdlib.net.tcp_alloc()
    elif tp == uv.UDP:
        client = stdlib.net.udp_alloc()
    else:
        raise OldError(u"got fd of type=" + str(tp).decode('utf-8'))
    check( uv.accept(self.stream, client.stream) )
    return client

