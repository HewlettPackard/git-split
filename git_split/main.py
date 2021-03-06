#!/usr/bin/python

from optparse import OptionParser
import os
import sys
import shutil
import re
import logging
from logging import handlers
from threading import Thread

import git

from git_split import filterbranch

try:
    from Queue import Queue, Empty
except ImportError:
    from queue import Queue, Empty


class Worker(Thread):
    """Thread executing tasks from a given tasks queue"""
    def __init__(self, tasks):
        Thread.__init__(self)
        self.tasks = tasks
        self.daemon = True
        self.start()

    def run(self):
        while True:
            func, args, kargs = self.tasks.get()
            try:
                func(*args, **kargs)
            except Exception as e:
                print(e)
            finally:
                self.tasks.task_done()


class ThreadPool:
    """Pool of threads consuming tasks from a queue"""
    def __init__(self, num_threads):
        self.tasks = Queue(num_threads)
        for _ in range(num_threads):
            Worker(self.tasks)

    def add_task(self, func, *args, **kargs):
        """Add a task to the queue"""
        self.tasks.put((func, args, kargs))

    def wait_completion(self):
        """Wait for completion of all the tasks in the queue"""
        self.tasks.join()


def shortest_exclusive_paths(excludes, includes):

    logger = logging.getLogger()

    includes_dict = {}
    for file in includes:
        dict = includes_dict
        for path_item in file.split(os.path.sep):
            if path_item not in dict:
                dict[path_item] = {}
            dict = dict[path_item]

    exclusive = []
    for file in excludes:
        dict = includes_dict
        file_paths = os.path.normpath(file).split(os.path.sep)
        for i, path_item in enumerate(file_paths, 1):
            logger.debug("searching %s for %s" % (dict, path_item))
            if path_item not in dict:
                short_path = os.path.join(*file_paths[:i])
                if short_path not in exclusive:
                    exclusive.append(short_path)
                else:
                    logger.debug("didn't find %s" % short_path)
                break

            dict = dict[path_item]

    return(exclusive)


def git_output_process(
        removed_files_list, proc, logger=None, ignore_removed=False):

    def enqueue_output(out, queue):
        for line in iter(out.readline, b''):
            queue.put(line)
        out.close()

    qstdout = Queue()
    tstdout = Thread(target=enqueue_output, args=(proc.stdout, qstdout))
    tstdout.daemon = True  # thread dies with the program
    tstdout.start()

    qstderr = Queue()
    tstderr = Thread(target=enqueue_output, args=(proc.stderr, qstderr))
    tstderr.daemon = True  # thread dies with the program
    tstderr.start()

    commit_regex = re.compile("^Rewrite [a-z0-9]{40} \((([^\/]*)\/([^\)]*))\)")
    files_removed_regex = re.compile(".*rm '([^']*)'$")
    print("Processing commit:", end="")

    if not logger:
        logger = logging.getLogger()

    string_len = 0
    files_removed = []
    while proc.poll() is None:
        # stdout
        try:
            stdout_line = qstdout.get_nowait()
        except Empty:
            pass
        else:  # got line

            stdout_line = stdout_line.decode("utf-8").strip()
            logger.debug(stdout_line)
            matches = commit_regex.match(stdout_line)
            if matches:
                print("\b" * string_len, end="")
                print(matches.group(1), end="")
                string_len = len(matches.group(1)) + 2

            if not ignore_removed:
                matches = files_removed_regex.match(stdout_line)
                if matches:
                    if matches.group(1) not in files_removed:
                        files_removed.append(matches.group(1))

        # stderr
        try:
            stderr_line = qstderr.get_nowait()
        except Empty:
            pass
        else:  # got line
            stderr_line = stderr_line.decode("utf-8").strip()
            logger.info(stderr_line)

    # finished
    print()

    if not ignore_removed:
        # store list of files removed for main loop to examine
        removed_files_list.append(files_removed)

    return(proc.returncode, stdout_line, stderr_line)


def split_repo(src_repo, include_file, include_pattern, authors_file, new_repo, branches, prune,
               keep_branches, removed_files, ignore_removed):

    includes = []
    if include_file is not None:
        if not os.path.exists(include_file):
            print("Specified include file does not exist: %s" % include_file)
            sys.exit(1)
        includes = [line.strip()
                    for line in open(include_file, 'r').read().split('\n')
                    if line and line[0] != "#"]

    if include_pattern:
        includes.extend([pattern.strip()
                         for pattern in include_pattern.split()
                         if pattern])

    if includes == []:
        print("No include pattern specified! Cannot prune repo!")
        return False

    # sort out logging
    logname = os.path.basename(new_repo.rstrip(os.path.sep))
    logfile = "%s.log" % logname
    print("Using logfile: %s" % logfile)
    logger = logging.getLogger(logname)
    rh = handlers.RotatingFileHandler(logfile, backupCount=10)
    rh.setFormatter(logging.Formatter("%(message)s"))
    logger.setLevel(logging.INFO)
    logger.addHandler(rh)

    if os.path.isfile(logfile) and os.path.getsize(logfile) > 0:
        rh.doRollover()

    print("Cloning local repo to new path")
    local_clone = git.Repo(src_repo)
    remote_ref = local_clone.git.config("--get", "remote.origin.url", with_exceptions=False)
    if remote_ref:
        local_clone.git.clone("--reference", src_repo, remote_ref,
                              os.path.abspath(new_repo))
    else:
        local_clone.git.clone(src_repo, os.path.abspath(new_repo))

    # make sure the git commands are run on the correct repo
    new_clone = git.Repo(new_repo)

    # make local branches of all remote branches, and prune them all
    remote_branches = new_clone.git.for_each_ref("--format", "%(refname:short)", "refs/remotes/origin/")
    ignore_remote_branches = ["HEAD"]
    ignore_remote_branches.append(new_clone.git.rev_parse("--abbrev-ref", "HEAD").strip())
    for origin_branch in remote_branches.split():
        if origin_branch is None:
            continue
        branch = origin_branch[7:]
        if keep_branches and branch not in keep_branches:
            continue
        if branch not in ignore_remote_branches:
            new_clone.git.branch(branch, origin_branch)
    new_clone.git.remote("rm", "origin")

    if branches is None or branches == []:
        branches = ["--", "--all"]

    print("Pruning branches \"%s\" of everything except the following paths:" % ", ".join(branches))
    print("\n".join(includes))
    print()

    debug_lvl = 3
    (status, last_output, last_error) = git_output_process(
        removed_files,
        new_clone.git.filter_branch(
            "--index-filter", filterbranch.FilterBranch.index_filter % ' '.join(['''-e \"^%s\"''' % p for p in includes]),
            "--commit-filter", filterbranch.FilterBranch.commit_filter % (debug_lvl, authors_file or ""),
            "--tag-name-filter", filterbranch.FilterBranch.tag_filter,
            "-f",
            *branches,
            as_process=True,
        ),
        logger,
        ignore_removed
    )

    if status != 0:
        logger.error("filter-branch failed")
        logger.info("last output: %s\nlast error: %s" % (last_output, last_error))
        print("Critical Failure")
        sys.exit(1)

    # clean up
    print("Removing refs/original/*")
    for ref in new_clone.git.for_each_ref("--format=%(refname)", "refs/original/").split():
        logger.info("Deleteing %s" % ref)
        new_clone.git.update_ref("-d", ref)

    new_clone.git.reflog("expire", "--expire=now", "--all")
    new_clone.git.gc(aggressive=True, prune="now")

    # prune branches that point to the same ref
    if keep_branches != []:
        print("Pruning duplicate branches")
        logger.info("Keeping branches %s" % keep_branches)
        for branch in keep_branches:
            new_clone.git.checkout(branch)
            prune_list = new_clone.git.branch("--no-color", "--merged", branch).split('\n')
            logger.info("Pruning branches %s" % prune_list)
            for prune_branch in prune_list:
                prune_branch = prune_branch.strip(' *')
                if prune_branch not in keep_branches:
                    logger.info("Pruning %s" % prune_branch)
                    new_clone.git.branch("-d", prune_branch)

        # switch back to default branch
        new_clone.git.checkout("master")


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]

    parser = OptionParser(version='%prog 1.0', usage='''Usage: %prog [options]''',
                          description='''
Splits an existing repository based on a list of files/patterns
to be kept, with all history preserved and any empty commits,
including merge commits, pruned and placed under a new repository.
'''.strip('\n'))

    parser.add_option('-i', '--include-file', action="append", dest='include_files',
                      default=[],
                      help='File containing patterns to include, one per line. '
                           'Basename of file will be used as the target repository '
                           'name and for the log file unless -n, --new-repo is set.')
    parser.add_option('-I', '--include', dest='file_pattern',
                      help='Whitespace separated list of files to include. '
                           'Appended to the pattern list specified by --include-file. '
                           'Cannot be used when multiple --include-file set.')
    parser.add_option('-r', '--src-repo', dest='src_repo',
                      help='Source repository to split. If this is local the script '
                           'will use it as a reference and still attempt to retrieve '
                           'from the original remote repo. To prevent querying of the '
                           'remote, remove the "origin" remote from the git repo.')
    parser.add_option('-n', '--new-repo', dest='target_repo',
                      help='Sets the target repository name. Created at the given path '
                           '(if value is a path), at the same level if just a name and '
                           'source repo is local or in the current working directory')
    parser.add_option('-f', '--force', action='store_true', default=False,
                      help='Force overwriting of the target path if it exists')
    parser.add_option('-b', '--branch', dest='branches', action='append',
                      help='Name of branch to process. May be specified multiple times. '
                           'Default is to process all branches.')
    parser.add_option('-p', '--prune', action='store_true', default=False,
                      help='Prune new repository of any branches that reference a commit '
                           'that is reachable from any branch specified by "--keep-branch"')
    parser.add_option('-k', '--keep-branch', action="append", dest="keep_branches",
                      help='Name of branches to prevent from being pruned. Specify one for '
                           'each branch to be kept.')
    parser.add_option('-x', '--ignore-removed', action='store_true', dest="ignore_removed", default=False,
                      help='Don\'t keep track of the files removed a print out whether the '
                           'resulting repositories have missed any files')
    parser.add_option('-a', '--authors',
                      help='Authors file to correct mistakes in authors names and emails '
                           'as part of the process of splitting the repository. File is '
                           'standard text file with each line in the format: '
                           '"old-name:new-email[:new-name:new-email]". Where '
                           '[...] denotes optional fields')

    (options, args) = parser.parse_args(argv)

    # resolve any options
    if not (options.include_files != [] or options.file_pattern):
        parser.error("No include pattern specified! Cannot prune repo! Set -i or -I.")

    # determine source repository to use, and whether we need to clone a local copy to
    # allow for rapid re-runs or just copy the existing local repo to the target repo and
    # prune
    clone_options = []
    if not options.src_repo:
        parser.error("No source repository specified to use! Set -s, --src-repo")
    else:
        if options.src_repo[:7] == "file://":
            src_repo = options.src_repo[7:]
        else:
            src_repo = options.src_repo

        if os.path.exists(src_repo):
            print("Using local path clone")
            clone_options.append("--no-hardlinks")
        else:
            print("Need to create a local clone of the remote repository")
            print("Currently not supported by this script")
            sys.exit(1)

    if not (options.target_repo or options.include_files != []):
        parser.error("No target repository set. Set -i or -n")

    # branch pruning options
    keep_branches = []
    if options.prune:
        if options.keep_branches != []:
            keep_branches = options.keep_branches
        else:
            keep_branches = ["master"]

    local_clone = git.Repo(src_repo)
    assert local_clone.bare is False

    authors = None
    if options.authors:
        if os.path.exists(options.authors):
            authors = os.path.abspath(options.authors)
        else:
            parser.error("Non-existant authors file given '%s', please specify a valid file for option '-a'")

    removed_files = []
    pool = ThreadPool(min(len(options.include_files), 6))
    for include_file in options.include_files:
        if not os.path.exists(include_file):
            parser.error("Specified include file does not exist: '%s'. Use a valid file with -i" % include_file)
            sys.exit(1)

        new_repo_name = options.target_repo
        if not new_repo_name:
            new_repo_name = os.path.splitext(os.path.basename(include_file))[0]

        # determine full target path for the target repository
        if os.path.sep in new_repo_name:
            # path
            new_repo = new_repo_name
        else:
            new_repo = os.path.join(os.path.dirname(src_repo.rstrip(os.path.sep)), new_repo_name)

        if os.path.exists(new_repo):
            if options.force:
                print("Existing copy found, removing to start from fresh")
                shutil.rmtree(new_repo)
            else:
                parser.error("Target repository path (%s) already exists, cannot create" % new_repo)

        pool.add_task(split_repo, src_repo, include_file, options.file_pattern, authors, new_repo,
                      options.branches, options.prune, keep_branches, removed_files, options.ignore_removed)

    # finished
    pool.wait_completion()

    if not options.ignore_removed and removed_files:
        # look to see if we included all files and directories in one of the splits
        list_sets = [set(list) for list in removed_files]
        removed_files = set.intersection(*list_sets)

        includes = []
        for include_file in options.include_files:
            includes.extend([line.strip()
                             for line in open(include_file, 'r').read().split('\n')
                             if line and line[0] != "#"])
        if options.file_pattern:
            includes.extend([pattern.strip()
                             for pattern in options.file_pattern.split() if pattern
                             ])

        # get the unique includes
        includes = set(includes)

        ignored_files = shortest_exclusive_paths(removed_files, includes)
        ignored_files.sort()
        if ignored_files != []:
            print("WARNING: after the split some files in the history were not included in any of the new split repos!")
            for file in ignored_files:
                print("\t%s" % file)


if __name__ == '__main__':
    main(sys.argv[1:])
