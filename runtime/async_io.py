from rpython.rlib.rstring import StringBuilder, UnicodeBuilder
from rpython.rtyper.lltypesystem import rffi, lltype, llmemory
from space import *
import main
import rlibuv as uv

class Handle(Object):
    def __init__(self, handle):
        self.handle = handle
        self.close_task = None
        self.closed = False
        self.buffers = []

    def getattr(self, name):
        if name == u"active":
            return boolean(uv.is_active(self.handle))
        if name == u"closing":
            return boolean(uv.is_closing(self.handle))
        if name == u"closed":
            return boolean(self.closed)
#        if name == u"ref":
#            return boolean(uv.has_ref(self.handle))
        return Object.getattr(self, name)

@Handle.method(u"close", signature(Handle))
def Handle_close(self):
    assert self.close_task is None
    ec = main.get_ec()
    uv.close(self.handle, Handle_close_cb)
    self.close_task = ec.current
    ec.uv_closing[rffi.cast_ptr_to_adr(self.handle)] = self
    return main.switch([ec.eventloop])

def Handle_close_cb(handle):
    ec = main.get_ec()
    self = ec.uv_closing.pop(rffi.cast_ptr_to_adr(handle))
    self.closed = True
    # Should be safe to release them here.
    buffers, self.buffers = self.buffers, []
    for pointer in buffers:
        lltype.free(pointer, flavor='raw')

    task, self.close_task = self.close_task, None
    main.root_switch(ec, [task])

# TODO: uv.ref, uv.unref ?
# TODO: uv.send_buffer_size(handle, value_ptr=0), recv ?
# TODO: uv.fileno(handle, fd) ?
    
class Stream(Handle):
    def __init__(self, stream):
        self.stream = stream
        self.read_task = None
        self.read_obj = None

        self.read_buf = lltype.nullptr(uv.buf_t)
        self.read_offset = 0
        self.read_nread  = 0

        self.write_task = None
        self.write_obj = None
        self.write_req = uv.malloc_bytes(uv.write_ptr, uv.req_size(uv.WRITE))

        Handle.__init__(self, rffi.cast(uv.handle_ptr, stream))
        self.buffers.append(rffi.cast(rffi.CCHARP, self.write_req))

    def getattr(self, name):
        if name == u"readable":
            return boolean(uv.is_readable(self.stream))
        if name == u"writable":
            return boolean(uv.is_writable(self.stream))
        return Handle.getattr(self, name)

#@Stream.method(u"shutdown", signature(Stream))
#def Stream_shutdown(self):
#    uv.shutdown(shutdown_req, self.stream, Stream_shutdown_cb)
#def Stream_shutdown_cb(shutdown_req, status):
#    pass

#@Stream.method(u"listen", signature(Stream, Integer))
#def Stream_listen(self, backlog):
#    uv.listen(self.stream, backlog.value, Stream_listen_cb)
#def Stream_listen_cb(stream, status):
#    pass

# TODO: uv_accept(server.stream, client.stream) needs some awareness about stream type.

@Stream.method(u"write", signature(Stream, Object))
def Stream_write(self, obj):
    assert self.write_task is None
    if isinstance(obj, String): # TODO: do this with a cast instead?
        obj = to_uint8array(obj.string.encode('utf-8'))
    elif not isinstance(obj, Uint8Array):
        raise unwind(LError(u"expected a buffer"))
    ec = main.get_ec()
    buf = lltype.malloc(rffi.CArray(uv.buf_t), 1, flavor='raw')
    buf[0].base = rffi.cast(rffi.CCHARP, obj.uint8data)
    buf[0].size = rffi.r_size_t(obj.length)
    self.write_task = ec.current
    self.write_obj  = obj
    ec.uv_writers[rffi.cast_ptr_to_adr(self.write_req)] = self
    uv.write(self.write_req, self.stream, buf, 1, Stream_write_cb)
    return main.switch([ec.eventloop])

# TODO: write2, try_write ?

def Stream_write_cb(write_req, status):
    status = rffi.r_long(status)
    ec = main.get_ec()
    self = ec.uv_writers.pop(rffi.cast_ptr_to_adr(write_req))

    self.write_obj = None
    task, self.write_task = self.write_task, None
    if status < 0:
        task.unwinder = to_error(status)
    main.root_switch(ec, [task])

@Stream.method(u"read", signature(Stream, Uint8Array, optional=1))
def Stream_read(self, block):
    assert self.read_task is None
    if self.read_offset < self.read_nread:
        avail = self.read_nread - self.read_offset
        if block is not None:
            count = min(block.length, avail)
            rffi.c_memcpy(block.uint8data,
                rffi.ptradd(self.read_buf.base, self.read_offset),
                count)
            self.read_offset += count
            return Integer(count)
        else:
            builder = StringBuilder()
            builder.append_charpsize(
                rffi.ptradd(self.read_buf.base, self.read_offset),
                avail)
            self.read_offset += avail
            return String(builder.build().decode('utf-8'))
    ec = main.get_ec()
    if block is not None:
        check( uv.read_start(self.stream, stream_alloc_buffer,
            stream_read_callback) )
        self.read_obj = block
        self.read_task = ec.current
        ec.uv_readers[rffi.cast_ptr_to_adr(self.stream)] = self
        return main.switch([ec.eventloop])
    else:
        check( uv.read_start(self.stream, stream_alloc_buffer,
            stream_readline_callback) )
        self.read_task = ec.current
        ec.uv_readers[rffi.cast_ptr_to_adr(self.stream)] = self
        return main.switch([ec.eventloop])

def stream_alloc_buffer(stream, suggested_size, buf):
    ec = main.get_ec()
    self = ec.uv_readers[rffi.cast_ptr_to_adr(stream)]
    buf.base = ptr = lltype.malloc(rffi.CCHARP.TO, suggested_size, flavor='raw')
    buf.size = suggested_size
    self.read_buf = buf
    self.buffers.append(ptr)

def stream_read_callback(stream, nread, buf):
    ec = main.get_ec()
    self = ec.uv_readers.pop(rffi.cast_ptr_to_adr(stream))
    if nread >= 0:
        count = min(nread, self.read_obj.length)
        rffi.c_memcpy(self.read_obj.uint8data, buf.base, count)
        self.read_offset = count
        self.read_avail  = nread

        uv.read_stop(stream)

        self.read_obj = None
        task, self.read_task = self.read_task, None
        main.root_switch(ec, [task, Integer(count)])
    else:
        # TODO: At this event issuing read again is undefined.
        uv.read_stop(stream)
        self.read_obj = None
        task, self.read_task = self.read_task, None
        main.root_switch(ec, [task, Integer(0)])

def stream_readline_callback(stream, nread, buf):
    ec = main.get_ec()
    self = ec.uv_readers.pop(rffi.cast_ptr_to_adr(stream))

    if nread >= 0:
        builder = StringBuilder()
        builder.append_charpsize(buf.base, nread)
        uv.read_stop(stream)
        #TODO: str_decode_utf_8 to handle partial symbols.

        task, self.read_task = self.read_task, None
        main.root_switch(ec, [task, String(builder.build().decode('utf-8'))])
    else:
        # TODO: At this event issuing read again is undefined.
        uv.read_stop(stream)
        task, self.read_task = self.read_task, None
        task.unwinder = to_error(nread)
        main.root_switch(ec, [task])
        main.root_switch(ec, [task, String(u"")])

def initialize_tty(uv_loop, fd, readable):
    tty = uv.malloc_bytes(uv.tty_ptr, uv.handle_size(uv.TTY))
    check( uv.tty_init(uv_loop, tty, fd, readable) )
    return TTY(tty, fd)

# TODO: delete tty/pipe handle when done with it.
class TTY(Stream):
    def __init__(self, tty, fd):
        self.tty = tty
        self.fd = fd
        Stream.__init__(self, rffi.cast(uv.stream_ptr, tty))

@TTY.method(u"isatty", signature(TTY))
def TTY_isatty(self):
    return boolean(uv.guess_handle(self.fd) == uv.TTY)

@TTY.method(u"set_mode", signature(TTY, String))
def TTY_set_mode(self, modename_obj):
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
    width  = lltype.malloc(rffi.INTP.TO, 1, flavor='raw')
    height = lltype.malloc(rffi.INTP.TO, 1, flavor='raw')
    try:
        check( uv.tty_get_winsize(self.tty, width, height) )
        w = rffi.r_long(width[0])
        h = rffi.r_long(height[0])
        return List([Integer(w), Integer(h)])
    finally:
        lltype.free(width, flavor='raw')
        lltype.free(height, flavor='raw')

# class Pipe(Object):
#     def __init__(self, pipe_handle):
#         self.pipe_handle = pipe_handle
#         
# @Pipe.method(u"isatty", signature(Pipe))
# def Pipe_isatty(self):
#     return false

#from interface import Object, null, signature
#
#class Event(Object):
#    def __init__(self):
#        self.callbacks = []
#        self.waiters = []
#
#    #TODO: on delete/discard, drop
#    #      waiters to queue with
#    #      error handlers.
#
#@Event.instantiator2(signature())
#def _():
#    return Event()

# .close()
# .dispatch(args...) # should do the greenlet arg packing?
# .register(cb)
# .unregister(cb)
# .wait()       # with timeout perhaps?


# wait([x, y, z])
# do the same as with .wait(), but prepend the event
# emitter that fires first.


# Left this here for a moment. I'll perhaps need it later.
#     def new_task(self, func, argv):
#         ec = main.get_ec()
#         greenlet = ec.current
#         self.task_count += 1
#         # This side is not aware about task timeouts.
#         with self.task_lock:
#             self.task_queue.append((ec, greenlet, func, argv))
#             if self.task_wait_count > 0:
#                 self.task_wait_lock.release()
#             elif self.worker_quota > 0:
#                 self.worker_quota -= 1
#                 start_new_thread(async_io_thread, ())
#         return main.switch([ec.eventloop])
# 
# ## new starting thread starts and ends without arguments.
# def async_io_thread():
#     rthread.gc_thread_start()
#     async_io_loop(main.g.io)
#     rthread.gc_thread_die()
# 
# def async_io_loop(io):
#     while True:
#         # Checking whether there's task 
#         ec, greenlet, func, argv = None, None, None, []
#         with io.task_lock:
#             if len(io.task_queue) > 0:
#                 ec, greenlet, func, argv = io.task_queue.pop(0)
#             else:
#                 io.task_wait_lock.acquire(False)
#                 io.task_wait_count += 1
#         if func is None:
#             res = acquire_timed(io.task_wait_lock, 10000000) # timeout 10 seconds
#             # Either timeout or release happened.
#             with io.task_lock:
#                 io.task_wait_count -= 1 # we are not waiting for now.
#                 if res == RPY_LOCK_FAILURE and len(io.task_queue) == 0:
#                     io.worker_quota += 1
#                     return # At this point it is very clear that
#                            # this task is no longer needed.
#         else:
#             try:
#                 res = func(argv)
#                 greenlet.argv.append(res)
#             except Unwinder as unwinder:
#                 greenlet.unwinder = unwinder
#             except Exception as exc:
#                 greenlet.unwinder = unwind(LError(
#                     u"Undefined error at async_io_thread(): " +
#                         str(exc).decode('utf-8') + u"\n"))
#             # I hope these are atomic operations.
#             ec.queue.append(greenlet)
#             io.task_count -= 1
#             eventual.et_notify(ec.handle)
# 
# # Taken from pypy
# def acquire_timed(lock, microseconds):
#     """Helper to acquire an interruptible lock with a timeout."""
#     endtime = (time.time() * 1e6) + microseconds
#     while True:
#         result = lock.acquire_timed(microseconds)
#         if result == RPY_LOCK_INTR:
#             # Run signal handlers if we were interrupted
#             # TODO: lever signal handlers?
#             #space.getexecutioncontext().checksignals()
#             if microseconds >= 0:
#                 microseconds = r_longlong((endtime - (time.time() * 1e6))
#                                           + 0.999)
#                 # Check for negative values, since those mean block
#                 # forever
#                 if microseconds <= 0:
#                     result = RPY_LOCK_FAILURE
#         if result != RPY_LOCK_INTR:
#             break
#     return result
# 
# RPY_LOCK_FAILURE, RPY_LOCK_ACQUIRED, RPY_LOCK_INTR = range(3)

# http://docs.libuv.org/en/v1.x/request.html

def check(result):
    if result < 0:
        raise to_error(result)

def to_error(result):
    raise unwind(LUVError(
        rffi.charp2str(uv.err_name(result)).decode('utf-8'),
        rffi.charp2str(uv.strerror(result)).decode('utf-8')
    ))