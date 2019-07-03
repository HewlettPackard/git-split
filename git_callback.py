# git_callback
#
# Copyright (C) 2008, 2009 Michael Trier and contributors
#
# module that modifies the git class method execute in the python-git software
# to accept a callback as specified by the user.
#
# Can be replaced by using the as_process arg in more recent versions
#

import git
from git.errors import GitCommandError
import subprocess
import sys


GIT_PYTHON_TRACE = git.cmd.GIT_PYTHON_TRACE
ON_POSIX = 'posix' in sys.builtin_module_names

extra = git.cmd.extra
extra.update({'close_fds': ON_POSIX,
              'bufsize': 1,
             })
git.cmd.execute_kwargs = ('callback',) + git.cmd.execute_kwargs

# override the default execute method of the git class with a custom one that permits
# a callback to process the command output and provide feedback to the caller
def execute(self, command,
                istream=None,
                with_keep_cwd=False,
                with_extended_output=False,
                with_exceptions=True,
                with_raw_output=False,
                callback=None,
                ):
        """
        Handles executing the command on the shell and consumes and returns
        the returned information (stdout)

        ``command``
            The command argument list to execute

        ``istream``
            Standard input filehandle passed to subprocess.Popen.

        ``with_keep_cwd``
            Whether to use the current working directory from os.getcwd().
            GitPython uses get_work_tree() as its working directory by
            default and get_git_dir() for bare repositories.

        ``with_extended_output``
            Whether to return a (status, stdout, stderr) tuple.

        ``with_exceptions``
            Whether to raise an exception when git returns a non-zero status.

        ``with_raw_output``
            Whether to avoid stripping off trailing whitespace.

        ``callback``
            User supplied callback to handle data processing

        Returns
            str(output)                     # extended_output = False (Default)
            tuple(int(status), str(output)) # extended_output = True
            callback                            # callback != None
        """

        if GIT_PYTHON_TRACE and not GIT_PYTHON_TRACE == 'full':
            print ' '.join(command)

        # Allow the user to have the command executed in their working dir.
        if with_keep_cwd or self.git_dir is None:
            cwd = os.getcwd()
        else:
            cwd=self.git_dir

        # Start the process
        proc = subprocess.Popen(command,
                                cwd=cwd,
                                stdin=istream,
                                stderr=subprocess.PIPE,
                                stdout=subprocess.PIPE,
                                **extra
                                )

        # if the user supplied a callback, use that instead.
        if callback:
            return(callback(proc))

        # Wait for the process to return
        try:
            stdout_value = proc.stdout.read()
            stderr_value = proc.stderr.read()
            status = proc.wait()
        finally:
            proc.stdout.close()
            proc.stderr.close()

        # Strip off trailing whitespace by default
        if not with_raw_output:
            stdout_value = stdout_value.rstrip()
            stderr_value = stderr_value.rstrip()

        if with_exceptions and status != 0:
            raise GitCommandError(command, status, stderr_value)

        if GIT_PYTHON_TRACE == 'full':
            if stderr_value:
                print "%s -> %d: '%s' !! '%s'" % (command, status, stdout_value, stderr_value)
            elif stdout_value:
                print "%s -> %d: '%s'" % (command, status, stdout_value)
            else:
                print "%s -> %d" % (command, status)

        # Allow access to the command's status code
        if with_extended_output:
            return (status, stdout_value, stderr_value)
        else:
            return stdout_value

# monkey patch the git.execute_process method
git.cmd.Git.execute = execute
