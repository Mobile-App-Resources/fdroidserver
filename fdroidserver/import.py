#!/usr/bin/env python3
#
# import.py - part of the FDroid server tools
# Copyright (C) 2010-13, Ciaran Gultnieks, ciaran@ciarang.com
# Copyright (C) 2013-2014 Daniel Martí <mvdan@mvdan.cc>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import binascii
import glob
import os
import re
import shutil
import urllib.request
from argparse import ArgumentParser
import logging

from . import _
from . import common
from . import metadata
from .exception import FDroidException

SETTINGS_GRADLE = re.compile(r'settings\.gradle(?:\.kts)?')
GRADLE_SUBPROJECT = re.compile(r'''['"]:([^'"]+)['"]''')
ANDROID_PLUGIN = re.compile(r'''\s*(:?apply plugin:|id)\(?\s*['"](android|com\.android\.application)['"]\s*\)?''')


# Get the repo type and address from the given web page. The page is scanned
# in a rather naive manner for 'git clone xxxx', 'hg clone xxxx', etc, and
# when one of these is found it's assumed that's the information we want.
# Returns repotype, address, or None, reason
def getrepofrompage(url):
    if not url.startswith('http'):
        return (None, _('{url} does not start with "http"!'.format(url=url)))
    req = urllib.request.urlopen(url)  # nosec B310 non-http URLs are filtered out
    if req.getcode() != 200:
        return (None, 'Unable to get ' + url + ' - return code ' + str(req.getcode()))
    page = req.read().decode(req.headers.get_content_charset())

    # Works for BitBucket
    m = re.search('data-fetch-url="(.*)"', page)
    if m is not None:
        repo = m.group(1)

        if repo.endswith('.git'):
            return ('git', repo)

        return ('hg', repo)

    # Works for BitBucket (obsolete)
    index = page.find('hg clone')
    if index != -1:
        repotype = 'hg'
        repo = page[index + 9:]
        index = repo.find('<')
        if index == -1:
            return (None, _("Error while getting repo address"))
        repo = repo[:index]
        repo = repo.split('"')[0]
        return (repotype, repo)

    # Works for BitBucket (obsolete)
    index = page.find('git clone')
    if index != -1:
        repotype = 'git'
        repo = page[index + 10:]
        index = repo.find('<')
        if index == -1:
            return (None, _("Error while getting repo address"))
        repo = repo[:index]
        repo = repo.split('"')[0]
        return (repotype, repo)

    return (None, _("No information found.") + page)


config = None
options = None


def get_app_from_url(url):

    app = metadata.App()

    # Figure out what kind of project it is...
    projecttype = None
    app.WebSite = url  # by default, we might override it
    if url.startswith('git://'):
        projecttype = 'git'
        repo = url
        repotype = 'git'
        app.SourceCode = ""
        app.WebSite = ""
    elif url.startswith('https://github.com'):
        projecttype = 'github'
        repo = url
        repotype = 'git'
        app.SourceCode = url
        app.IssueTracker = url + '/issues'
        app.WebSite = ""
    elif url.startswith('https://gitlab.com/'):
        projecttype = 'gitlab'
        # git can be fussy with gitlab URLs unless they end in .git
        if url.endswith('.git'):
            url = url[:-4]
        repo = url + '.git'
        repotype = 'git'
        app.WebSite = url
        app.SourceCode = url + '/tree/HEAD'
        app.IssueTracker = url + '/issues'
    elif url.startswith('https://notabug.org/'):
        projecttype = 'notabug'
        if url.endswith('.git'):
            url = url[:-4]
        repo = url + '.git'
        repotype = 'git'
        app.SourceCode = url
        app.IssueTracker = url + '/issues'
        app.WebSite = ""
    elif url.startswith('https://bitbucket.org/'):
        if url.endswith('/'):
            url = url[:-1]
        projecttype = 'bitbucket'
        app.SourceCode = url + '/src'
        app.IssueTracker = url + '/issues'
        # Figure out the repo type and adddress...
        repotype, repo = getrepofrompage(url)
        if not repotype:
            raise FDroidException("Unable to determine vcs type. " + repo)
    elif url.startswith('https://') and url.endswith('.git'):
        projecttype = 'git'
        repo = url
        repotype = 'git'
        app.SourceCode = ""
        app.WebSite = ""
    if not projecttype:
        raise FDroidException("Unable to determine the project type. "
                              + "The URL you supplied was not in one of the supported formats. "
                              + "Please consult the manual for a list of supported formats, "
                              + "and supply one of those.")

    # Ensure we have a sensible-looking repo address at this point. If not, we
    # might have got a page format we weren't expecting. (Note that we
    # specifically don't want git@...)
    if ((repotype != 'bzr' and (not repo.startswith('http://')
        and not repo.startswith('https://')
        and not repo.startswith('git://')))
            or ' ' in repo):
        raise FDroidException("Repo address '{0}' does not seem to be valid".format(repo))


    app.RepoType = repotype
    app.Repo = repo

    return app

def clone_to_tmp_dir(app):
    tmp_dir = 'tmp'
    if not os.path.isdir(tmp_dir):
        logging.info(_("Creating temporary directory"))
        os.makedirs(tmp_dir)

    tmp_dir = os.path.join(tmp_dir, 'importer')
    if os.path.exists(tmp_dir):
        shutil.rmtree(tmp_dir)
    vcs = common.getvcs(app.RepoType, app.Repo, tmp_dir)
    vcs.gotorevision(options.rev)

    return tmp_dir


def get_all_gradle_and_manifests(build_dir):
    paths = []
    for root, dirs, files in os.walk(build_dir):
        for f in sorted(files):
            if f == 'AndroidManifest.xml' \
               or f.endswith('.gradle') or f.endswith('.gradle.kts'):
                full = os.path.join(root, f)
                paths.append(full)
    return paths


def get_gradle_subdir(build_dir, paths):
    """get the subdir where the gradle build is based"""
    first_gradle_dir = None
    for path in paths:
        if not first_gradle_dir:
            first_gradle_dir = os.path.relpath(os.path.dirname(path), build_dir)
        if os.path.exists(path) and SETTINGS_GRADLE.match(os.path.basename(path)):
            with open(path) as fp:
                for m in GRADLE_SUBPROJECT.finditer(fp.read()):
                    for f in glob.glob(os.path.join(os.path.dirname(path), m.group(1), 'build.gradle*')):
                        with open(f) as fp:
                            while True:
                                line = fp.readline()
                                if not line:
                                    break
                                if ANDROID_PLUGIN.match(line):
                                    return os.path.relpath(os.path.dirname(f), build_dir)
    if first_gradle_dir and first_gradle_dir != '.':
        return first_gradle_dir

    return ''


def main():

    global config, options

    # Parse command line...
    parser = ArgumentParser()
    common.setup_global_opts(parser)
    parser.add_argument("-u", "--url", default=None,
                        help=_("Project URL to import from."))
    parser.add_argument("-s", "--subdir", default=None,
                        help=_("Path to main Android project subdirectory, if not in root."))
    parser.add_argument("-c", "--categories", default=None,
                        help=_("Comma separated list of categories."))
    parser.add_argument("-l", "--license", default=None,
                        help=_("Overall license of the project."))
    parser.add_argument("--rev", default=None,
                        help=_("Allows a different revision (or git branch) to be specified for the initial import"))
    metadata.add_metadata_arguments(parser)
    options = parser.parse_args()
    metadata.warnings_action = options.W

    config = common.read_config(options)

    apps = metadata.read_metadata()
    app = None

    build_dir = None

    local_metadata_files = common.get_local_metadata_files()
    if local_metadata_files != []:
        raise FDroidException(_("This repo already has local metadata: %s") % local_metadata_files[0])

    build = metadata.Build()
    if options.url is None and os.path.isdir('.git'):
        app = metadata.App()
        app.AutoName = os.path.basename(os.getcwd())
        app.RepoType = 'git'
        app.UpdateCheckMode = "Tags"

        if os.path.exists('build.gradle'):
            build.gradle = ['yes']

        import git
        repo = git.repo.Repo(os.getcwd())  # git repo
        for remote in git.Remote.iter_items(repo):
            if remote.name == 'origin':
                url = repo.remotes.origin.url
                if url.startswith('https://git'):  # github, gitlab
                    app.SourceCode = url.rstrip('.git')
                app.Repo = url
                break
        # repo.head.commit.binsha is a bytearray stored in a str
        build.commit = binascii.hexlify(bytearray(repo.head.commit.binsha))
        write_local_file = True
    elif options.url:
        app = get_app_from_url(options.url)
        build_dir = clone_to_tmp_dir(app)
        build.commit = '?'
        build.disable = 'Generated by import.py - check/set version fields and commit id'
        write_local_file = False
    else:
        raise FDroidException("Specify project url.")

    # Extract some information...
    paths = get_all_gradle_and_manifests(build_dir)
    subdir = get_gradle_subdir(build_dir, paths)
    if paths:
        versionName, versionCode, package = common.parse_androidmanifests(paths, app)
        if not package:
            raise FDroidException(_("Couldn't find package ID"))
        if not versionName:
            logging.warn(_("Couldn't find latest version name"))
        if not versionCode:
            logging.warn(_("Couldn't find latest version code"))
    else:
        raise FDroidException(_("No gradle project could be found. Specify --subdir?"))

    # Make sure it's actually new...
    if package in apps:
        raise FDroidException("Package " + package + " already exists")

    # Create a build line...
    build.versionName = versionName or 'Unknown'
    build.versionCode = versionCode or '0'  # TODO heinous but this is still a str
    if options.subdir:
        build.subdir = options.subdir
    elif subdir:
        build.subdir = subdir

    if options.license:
        app.License = options.license
    if options.categories:
        app.Categories = options.categories.split(',')
    if os.path.exists(os.path.join(subdir, 'jni')):
        build.buildjni = ['yes']
    if os.path.exists(os.path.join(subdir, 'build.gradle')):
        build.gradle = ['yes']

    metadata.post_metadata_parse(app)

    app.builds.append(build)

    if write_local_file:
        metadata.write_metadata('.fdroid.yml', app)
    else:
        # Keep the repo directory to save bandwidth...
        if not os.path.exists('build'):
            os.mkdir('build')
        if build_dir is not None:
            shutil.move(build_dir, os.path.join('build', package))
        with open('build/.fdroidvcs-' + package, 'w') as f:
            f.write(app.RepoType + ' ' + app.Repo)

        metadatapath = os.path.join('metadata', package + '.yml')
        metadata.write_metadata(metadatapath, app)
        logging.info("Wrote " + metadatapath)


if __name__ == "__main__":
    main()
