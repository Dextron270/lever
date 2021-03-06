#!/usr/bin/env python2
#"""
#    This script should help setting up environment for lever.
#    Run it without arguments, and it just setups the environment.
#    Run it with 'compile' -argument, eg. "./setup.py compile" and it compiles.
#
#    Compiling takes some time to finish.
#"""
from StringIO import StringIO
from subprocess import call, check_call
from urllib import urlopen
from zipfile import ZipFile
import shutil
import argparse
import glob
import os
import platform
import re
import sys

def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()

    cmd = subparsers.add_parser('build-local',
        help="Compile the statically linked third party dependencies for Lever")
    cmd.set_defaults(func=build_local)

    cmd = subparsers.add_parser('compile',
        help="Compile the lever runtime")
    cmd.set_defaults(func=compile_lever)
    cmd.add_argument("--lldebug", action="store_true",
        help="Compile in debug mode")
    cmd.add_argument("--nojit", action="store_true",
        help="Compile without jit")
    cmd.add_argument("--stm", action="store_true",
        help="Waiting for this day")
    cmd.add_argument("--use-pypy", action="store_true",
        help="Use pypy for compiling")

    cmd = subparsers.add_parser('compile-lib',
        help="Compile lib/ contents",
        description=compile_lib_desc)
    cmd.set_defaults(func=compile_lib)
    cmd.add_argument("--all", action="store_true",
        help="recompile all")

    cmd = subparsers.add_parser('win32-dist',
        help="Create win32 distribution",
        description=win32_dist.__doc__)
    cmd.set_defaults(func=win32_dist)

    cmd = subparsers.add_parser('refresh-docs',
        help="Refresh the source index and then update all documentation")
    cmd.add_argument("--tag", type=str, default="latest",
        help="Update doc/$tag instead of the doc/latest, should be done along new version releases.")
    cmd.set_defaults(func=refresh_docs)

    cmd = subparsers.add_parser('stub',
        help="Produce documentation stub for an item in the source index")
    cmd.add_argument("name", type=str)
    cmd.add_argument("--src", action="store_true",
        help="Produce just the source link tag")
    cmd.set_defaults(func=documentation_stub)

    cmd = subparsers.add_parser('update-html-docs',
        help="Update HTML documentation",
        description=update_html_docs.__doc__)
    cmd.add_argument("--tag", type=str, default="latest",
        help="Update doc/$tag instead of the doc/latest, should be done along new version releases.")
    cmd.set_defaults(func=update_html_docs)

    args = parser.parse_args()
    return args.func(args)

def build_local(args):
    local_abs = os.path.abspath("local")
    system = platform.system()

    # The dependencies are cloned in-place so you don't need to do this.
    ninja_path = os.path.join(local_abs, "ninja")
    rule_git_clone("git://github.com/ninja-build/ninja.git", ninja_path, "release")

    zlib_path = os.path.join(local_abs, "zlib")
    rule_git_clone("git://github.com/madler/zlib.git", zlib_path, "master")

    libuv_path = os.path.join(local_abs, "libuv")
    rule_git_clone("git://github.com/libuv/libuv.git", libuv_path, "v1.13.1") # or v1.10.0

    libuv_gyp_path = os.path.join(local_abs, "libuv", "build", "gyp")
    rule_git_clone("https://chromium.googlesource.com/external/gyp.git", libuv_gyp_path)

    if system == 'Windows':
        windows_build_dependencies()
    else:
        linux_build_depedencies(libuv_is_local=True)
    # TODO: once things work on win32 as well, add the pypy download here?
    
    # We compile our own ninja because it's easy to build and we can ensure
    # we have a fresh ninja that way.
    ninja_bin = os.path.join(local_abs, "ninja/ninja")
    if system == 'Windows':
        ninja_bin += ".exe"

    if not os.path.exists(ninja_bin):
        os.chdir(ninja_path)
        check_call(["python", os.path.join(ninja_path, "configure.py"), "--bootstrap"])

    # cmake required ninja in the path, but the last two commands also need it to work.
    insert_to_env("PATH", ninja_path)

    # The cmake is particularly nasty dependency compared to the Ninja.
    # Only the zlib needs the cmake, though it compiles quite well despite it.
    if system == 'Windows':
        if not os.path.exists(os.path.join(zlib_path, "zlib.lib")):
            os.chdir(zlib_path)
            check_call(["nmake", "-f", "win32/Makefile.msc", "zlib.lib"])
                    # zlib.lib, zlib.h, zconf.h
    else:
        if not os.path.exists(os.path.join(zlib_path, "build.ninja")):
            os.chdir(zlib_path)
            check_call(["cmake", "-G", "Ninja"])
        check_call(["ninja", "-C", zlib_path, "libz.a"])


    # The libuv produces the ninja build through a crummy gyp_uv wrapper
    # that uses the libuv/build/gyp to produce the files.

    libuv_build_path = os.path.join(libuv_path, "out", "Release")
        
    if not os.path.exists(os.path.join(libuv_build_path, "build.ninja")):
        os.chdir(libuv_path)
        if system == 'Windows':
            os.environ["GYP_MSVS_VERSION"] = "auto"
            check_call(["python", os.path.join(libuv_path, "gyp_uv.py"), "-f", "ninja",
                "-Dtarget_arch=ia32", "-Duv_library=shared_library"])
        else:
            check_call(["python", os.path.join(libuv_path, "gyp_uv.py"), "-f", "ninja"])
    
    if system == 'Windows':
        check_call(["ninja", "-C", libuv_build_path])
        # I don't know what they're thinking at libuv, but lets fix this.
        shutil.copy(
            os.path.join(libuv_build_path, "libuv.dll.lib"),
            os.path.join(libuv_build_path, "libuv.lib"))
        shutil.copy(
            os.path.join(libuv_build_path, "libuv.dll"),
            "libuv.dll")
    else:
        check_call(["ninja", "-C", libuv_build_path, "libuv.a"])

    print("Now you can run: python setup.py compile")

def rule_git_clone(url, dst, branch='master'):
    if not os.path.exists(dst):
        check_call(["git", "clone", "--depth", "1", "-b", branch, url, dst])

# On linux the system checks whether the tools required to build it respond.
# Then it checks whether the system has the required C libraries to have
# a chance of successful build.

# When there's a chance that the thing actually builds, it downloads and extracts
# pypy sources. PyPy changes often and the newer versions introduce nice improvements
# so an attempt has been made to keep up with the version changes.

# There's no need to compile pypy itself. We run rpython with python 2.7
# Presence of python 2.7 is not being checked, because if you can run this script,
# as described on the README, you should have python 2.x installed on your system.

# It's been 7 years after 2.7 has been released, and pretty much the libraries listed
# are stabilized, so I don't expect to have trouble with versions.
# Maybe it happens some day... On someone's computer. That he doesn't have recent enough
# versions of these libraries.
pypy_src_url = 'https://bitbucket.org/pypy/pypy/downloads/pypy2-v5.10.0-src'
pypy_src_dir = os.path.basename(pypy_src_url)

# If the untagged 'pypy' -directory is around, we will use that.
# But the pypy_src_url is still downloaded.

# These are listed in "Building PyPy from Source":
#                     http://doc.pypy.org/en/latest/build.html
command_depends = "pkg-config gcc make bzip2".split(' ')
library_depends = "libffi zlib sqlite3 ncurses expat libssl".split(' ')

def compile_lever(args):
    system = platform.system()
    if system == "Linux":
        zlib_abs = os.path.abspath("local/zlib")
        if os.path.exists(zlib_abs):
            insert_to_env("DEPENDENCY_LIBRARY_PATH", zlib_abs)
            insert_to_env("DEPENDENCY_INCLUDE_PATH", zlib_abs)

        libuv_abs = os.path.abspath("local/libuv")
        if os.path.exists(libuv_abs):
            linux_build_depedencies(libuv_is_local=True)
            insert_to_env("DEPENDENCY_LIBRARY_PATH", os.path.join(libuv_abs, "out/Release"))
            insert_to_env("DEPENDENCY_LIBRARY_PATH", os.path.join(libuv_abs, "out/Debug"))
            insert_to_env("DEPENDENCY_INCLUDE_PATH", os.path.join(libuv_abs, "include"))
        else:
            linux_build_depedencies(libuv_is_local=False)
    elif system == "Windows":
        zlib_abs = os.path.abspath("local/zlib")
        if os.path.exists(zlib_abs):
            insert_to_env("LIB", zlib_abs)
            insert_to_env("INCLUDE", zlib_abs)

        libuv_abs = os.path.abspath("local/libuv")
        if os.path.exists(libuv_abs):
            insert_to_env('LIB',     os.path.join(libuv_abs, "out", "Release"))
            #insert_to_env('LIB',     os.path.join(libuv_abs, "out", "Debug"))
            insert_to_env('INCLUDE', os.path.join(libuv_abs, "include"))

        windows_build_dependencies()

        #local_abs = os.path.abspath("local")
        #if os.path.exists(local_abs):
        #    insert_to_env('PATH',    os.path.join(local_abs, "bin"))
        #    insert_to_env('INCLUDE', os.path.join(local_abs, "include"))
        #    insert_to_env('LIB',     os.path.join(local_abs, "lib"))
    else:
        assert False, "no dependency fetching script for {}".format(system)
    if os.path.exists('pypy'):
        os.environ['PYTHONPATH'] = 'pypy'
    else:
        os.environ['PYTHONPATH'] = pypy_src_dir
    rpython_bin = os.path.join(pypy_src_dir, 'rpython', 'bin', 'rpython')
    
    build_flags = []
    if not args.nojit:
        build_flags.append('--translation-jit')
    build_flags.append('--gc=incminimark')
    build_flags.append('--opt=2')
    if args.lldebug:
        build_flags.append('--lldebug')
    # PyPy STM was tried once. The plans are to
    # pick it up when it improves.
    if args.stm:
        build_flags.append('--stm')
    if args.use_pypy:
        check_call(['pypy', rpython_bin] +
            build_flags + ["runtime/goal_standalone.py"])
    else:
        check_call(['python', rpython_bin] +
            build_flags + ["runtime/goal_standalone.py"])
    compile_libraries(preserve_cache=False)

# To handle dependencies, I'm adding them into the environment
# variables before compiling.
def insert_to_env(name, path, separator=os.pathsep):
    value = os.environ.get(name, '').strip()
    if value == "":
        os.environ[name] = path
    else:
        os.environ[name] = path + separator + value

# The 'libuv_is_local' here is a bit of a mess.
def linux_build_depedencies(libuv_is_local):
    devnull = open(os.devnull, 'w')
    for cmd in command_depends:
        if call([cmd, '--version'], stdout=devnull, stderr=devnull) != 0:
            return linux_troubleshoot(cmd)
    extra = []
    extra_apt = []
    if not libuv_is_local:
        extra.append("libuv")
        extra_apt.append("libuv1-dev")
    for dependency in library_depends + extra:
        if call(['pkg-config', '--exists', dependency]) != 0:
            return linux_troubleshoot(dependency, extra_apt)
    linux_download_and_extract(pypy_src_dir, pypy_src_url + '.tar.bz2')

# On windows, you're going to need Visual studio 9.0 and you need to
# know how to use it. (You might obtain it by checking for "visual studio for python 2.7")
# Having frequent releases start to seem relevant, so I may have to setup a build computer that
# automates it. Maybe some day...
def windows_build_dependencies():
    if not os.path.exists(pypy_src_dir):
        print("Downloading the PyPy SRC")
        print("Note that building from source for windows isn't frequent, hitches may occur.")
        url = urlopen(pypy_src_url + '.zip')
        zipfile = ZipFile(StringIO(url.read()))
        zipfile.extractall()


compile_lib_desc = """This command compiles the scripts in the lib/

If this command has not been run successfully,
the lever runtime will exclaim that bytecode
compiler is stale or missing. 

This command is automatically run along the compile.
"""

def compile_lib(args):
    compile_libraries(not args.all)

def compile_libraries(preserve_cache=True):
    from compiler import compile
    import traceback
    print("Compiling libraries for lever")
    for dirname, subdirs, files in os.walk("lib"):
        for name in files:
            if name.endswith(".lc"):
                lc_name = os.path.join(dirname, name)
                cb_name = re.sub(".lc$", ".lc.cb", lc_name)
                # Compiling the whole `lib/` is not needed during development.
                # Chances are it will take whole lot of time eventually.
                # Trouble-free parsing isn't free.
                if preserve_cache and (os.path.isfile(cb_name)
                        and os.path.getmtime(cb_name) >= os.path.getmtime(lc_name)):
                    continue
                try:
                    compile.compile_file(cb_name, lc_name)
                except Exception as e:
                    if not isinstance(e, KeyError):
                        traceback.print_exc()
                    print("{}:{}".format(lc_name, e))

stop_troubleshoot = False
def linux_troubleshoot(item, extra_apt=[]):
    global stop_troubleshoot
    if stop_troubleshoot:
        print("That did not solve the problem")
        sys.exit(1)
    stop_troubleshoot = True
    print("Dependencies to compile or run:")
    print(' '.join(command_depends + library_depends))
    print()
    print("{} is missing".format(item))
    if re.search("ubuntu|debian", platform.platform(), re.IGNORECASE):
        print("Ubuntu/debian detected, trying to install")
        check_call(['sudo', 'apt-get', 'install'] +
            "gcc make libffi-dev pkg-config libz-dev libbz2-dev".split(' ') +
            "libsqlite3-dev libncurses-dev libexpat1-dev libssl-dev libuv1 libuv1-dev".split(' ') +
            extra_apt)
        print("Re-attempt")
        main()
    else:
        sys.exit(1)

def linux_download_and_extract(target, archive):
    if not os.path.exists(target):
        if len(glob.glob(target + '.tar.bz2')) == 0:
            check_call(['wget', archive])
        check_call(["tar", "-xf", target + '.tar.bz2'])

def win32_dist(args):
    """
        This script creates a win32 distribution for lever
    """
    import shutil
    if not os.path.exists("lever.exe"):
        print("Need something to distribute first. Run setup.py compile on win32")
        sys.exit(1)
    mtime = os.path.getmtime("lever.exe")
# I do not want to release stale distribution.
    for root, dirs, fils in os.walk("evaluator"):
        for fil in fils:
            mtime = min(mtime, os.path.getmtime(os.path.join(root, fil)))
    for root, dirs, fils in os.walk("space"):
        for fil in fils:
            mtime = min(mtime, os.path.getmtime(os.path.join(root, fil)))
    for root, dirs, fils in os.walk("runtime"):
        for fil in fils:
            mtime = min(mtime, os.path.getmtime(os.path.join(root, fil)))
    if mtime > os.path.getmtime("lever.exe"):
        print("Stale executable, re-run setup.py compile on win32")
        sys.exit(1)
    VERSION = open("VERSION").read().strip()
    archive = 'lever-{}.zip'.format(VERSION)
    zf = zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED)
    def include(path, to=None):
        to = path if to is None else to
        assert not os.path.isabs(path)
        zf.write(path, os.path.join("lever", to))
    def include_dir(dirname):
        for root, dirs, fils in os.walk(dirname):
            for fil in fils:
                include(os.path.join(root, fil))
    def include_contents(dirname):
        for root, dirs, fils in os.walk(dirname):
            for fil in fils:
                path = os.path.join(root, fil)
                include(path, os.path.relpath(path, dirname))

    include("lever.exe")
    include("lever-0.8.0.grammar")
    include("VERSION")
    include_dir("app")
    include_dir("lib")
    include_dir("doc")

    include_dir("samples")
    include_dir("headers")
    include("LICENSE.md", "LICENSE.lever.txt")
    include_contents("win32_extras")

    zf.close()
    print os.path.abspath(archive)


def refresh_docs(args):
    assert check_call(["./lever", "tools/print_module_index.lc",
        'doc/source_index.json' ]) == 0
    update_html_docs(args)

def documentation_stub(args):
    if args.src:
        check_call(["./lever", "tools/print_stub.lc",
            "doc/source_index.json",
            "source", args.name])
    else:
        check_call(["./lever", "tools/print_stub.lc",
            "doc/source_index.json",
            "group", args.name])

def update_html_docs(args):
    """
        Updates the html docs at 'www/doc'. By default updates the 'latest'.
    """
    check_call(["./lever", "tools/update_html_docs.lc",
        'doc', 'www/doc/' + args.tag ])
    print '(as a reference for a maintainer) rsync -avz www/ name@dest:www'

if __name__=='__main__':
    main()
