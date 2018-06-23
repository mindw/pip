# The following comment should be removed at some point in the future.
# mypy: disallow-untyped-defs=False

from __future__ import absolute_import

import logging
import os
from email.parser import FeedParser

from pip._vendor import pkg_resources
from pip._vendor.packaging.utils import canonicalize_name

from pip._internal.cli.base_command import Command
from pip._internal.cli.req_command import SessionCommandMixin
from pip._internal.cli.status_codes import ERROR, SUCCESS
from pip._internal.models.index import PyPI
from pip._internal.utils.misc import write_output

logger = logging.getLogger(__name__)


class ShowCommand(Command, SessionCommandMixin):
    """
    Show information about one or more installed packages.

    The output is in RFC-compliant mail header format.
    """

    usage = """
      %prog [options] <package> ..."""
    ignore_require_venv = True

    def __init__(self, *args, **kw):
        super(ShowCommand, self).__init__(*args, **kw)
        self.cmd_opts.add_option(
            '-f', '--files',
            dest='files',
            action='store_true',
            default=False,
            help='Show the full list of installed files for each package.')

        self.cmd_opts.add_option(
            '-i', '--index',
            dest='index',
            metavar='URL',
            default=PyPI.pypi_url,
            help='Base URL of Python Package Index (default %default)')

        self.cmd_opts.add_option(
            '-p', '--pypi',
            dest='pypi',
            action='store_true',
            default=False,
            help='Show PyPi version')

        self.parser.insert_option_group(0, self.cmd_opts)

    def run(self, options, args):
        if not args:
            logger.warning('ERROR: Please provide a package name or names.')
            return ERROR
        query = args

        if options.pypi:
            with self.get_default_session(options) as session:
                results = search_packages_info(query, options.index, session)
        else:
            results = search_packages_info(query, options.index)
        if not print_results(
                results, list_files=options.files, verbose=options.verbose):
            return ERROR

        return SUCCESS


def _format_package(requirement):
    r = requirement
    installed_ver = '-'
    try:
        d = pkg_resources.get_distribution(r.project_name)
        installed_ver = str(d.version)
    except pkg_resources.DistributionNotFound:
        pass
    return "%s [%s]" % (r, installed_ver)


def search_packages_info(query, index_url=None, session=None):
    """
    Gather details from installed distributions. Print distribution name,
    version, location, and installed files. Installed files requires a
    pip generated 'installed-files.txt' in the distributions '.egg-info'
    directory.
    """
    installed = {}
    for p in pkg_resources.working_set:
        installed[canonicalize_name(p.project_name)] = p

    query_names = [canonicalize_name(name) for name in query]
    missing = sorted(
        [name for name, pkg in zip(query, query_names) if pkg not in installed]
    )
    if missing:
        logger.warning('Package(s) not found: %s', ', '.join(missing))

    def get_requiring_packages(package_name):
        canonical_name = canonicalize_name(package_name)
        return [
            pkg.project_name for pkg in pkg_resources.working_set
            if canonical_name in
               [canonicalize_name(required.name) for required in
                pkg.requires()]
        ]

    distributions = [installed[pkg] for pkg in query_names if pkg in installed]
    for dist in distributions:
        required_by = []
        for _, p in installed.items():
            r = next((r for r in p.requires() if r.key == dist.key), None)
            if r:
                required_by.append("%s %s" % (p.project_name, r.specifier))
            else:
                for e in p.extras:
                    r = next(
                        (r for r in p.requires([e]) if r.key == dist.key), None
                    )
                    if r:
                        required_by.append(
                            "%s[%s] %s" % (p.project_name, e, r.specifier))
        extras = {}
        for e in dist.extras:
            reqs = set(dist.requires([e])) - set(dist.requires())
            extras[e] = map(_format_package, reqs)

        if session:
            from pip._internal.network.xmlrpc import PipXmlrpcTransport
            from pip._vendor.six.moves import xmlrpc_client

            transport = PipXmlrpcTransport(index_url, session)
            pypi = xmlrpc_client.ServerProxy(index_url, transport)
            pypi_releases = pypi.package_releases(dist.project_name)
            pypi_version = pypi_releases[0] if pypi_releases else 'UNKNOWN'
        else:
            pypi_version = None

        requires = [_format_package(r_) for r_ in dist.requires()]
        package = {
            'name': dist.project_name,
            'version': dist.version,
            'pypi_version': pypi_version,
            'location': dist.location,
            'requires': requires,
            'required_by': required_by,
            'extras': extras
        }
        file_list = None
        metadata = None
        if isinstance(dist, pkg_resources.DistInfoDistribution):
            # RECORDs should be part of .dist-info metadatas
            if dist.has_metadata('RECORD'):
                lines = dist.get_metadata_lines('RECORD')
                paths = [l.split(',')[0] for l in lines]
                paths = [os.path.join(dist.location, p) for p in paths]
                file_list = [os.path.relpath(p, dist.location) for p in paths]

            if dist.has_metadata('METADATA'):
                metadata = dist.get_metadata('METADATA')
        else:
            # Otherwise use pip's log for .egg-info's
            if dist.has_metadata('installed-files.txt'):
                paths = dist.get_metadata_lines('installed-files.txt')
                paths = [os.path.join(dist.egg_info, p) for p in paths]
                file_list = [os.path.relpath(p, dist.location) for p in paths]

            if dist.has_metadata('PKG-INFO'):
                metadata = dist.get_metadata('PKG-INFO')

        if dist.has_metadata('entry_points.txt'):
            entry_points = dist.get_metadata_lines('entry_points.txt')
            package['entry_points'] = entry_points

        if dist.has_metadata('INSTALLER'):
            for line in dist.get_metadata_lines('INSTALLER'):
                if line.strip():
                    package['installer'] = line.strip()
                    break

        # @todo: Should pkg_resources.Distribution have a
        # `get_pkg_info` method?
        feed_parser = FeedParser()
        feed_parser.feed(metadata)
        pkg_info_dict = feed_parser.close()
        for key in ('metadata-version', 'summary',
                    'home-page', 'author', 'author-email', 'license'):
            package[key] = pkg_info_dict.get(key)

        # It looks like FeedParser cannot deal with repeated headers
        classifiers = []
        for line in metadata.splitlines():
            if line.startswith('Classifier: '):
                classifiers.append(line[len('Classifier: '):])
        package['classifiers'] = classifiers

        if file_list:
            package['files'] = sorted(file_list)
        yield package


def print_results(distributions, list_files=False, verbose=False):
    """
    Print the informations from installed distributions found.
    """
    results_printed = False
    for i, dist in enumerate(distributions):
        results_printed = True
        if i > 0:
            write_output("---")

        write_output("Name: %s", dist.get('name', ''))
        write_output("Version: %s", dist.get('version', ''))
        if dist['pypi_version']:
            write_output("PyPi Version: %s", dist['pypi_version'])
        write_output("Summary: %s", dist.get('summary', ''))
        write_output("Home-page: %s", dist.get('home-page', ''))
        write_output("Author: %s", dist.get('author', ''))
        write_output("Author-email: %s", dist.get('author-email', ''))
        write_output("License: %s", dist.get('license', ''))
        write_output("Location: %s", dist.get('location', ''))
        write_output("Metadata-Version: %s",
                     dist.get('metadata-version', ''))
        write_output("Installer: %s", dist.get('installer', ''))
        if verbose:
            write_output("Classifiers:")
            for classifier in dist.get('classifiers', []):
                write_output("  %s", classifier)
				
        write_output("Requires:")
        for line in sorted(dist.get('requires', [])):
                write_output("  %s", line)
        for extra_name, deps in dist['extras'].items():
            write_output("Extra Require [%s]:", extra_name)
            for line in sorted(deps):
                write_output("  %s", line.strip())
        write_output("Required by(%d):", len(dist['required_by']))
        for line in sorted(dist['required_by']):
            write_output("  %s", line.strip())

        write_output("Entry-points:")
        for entry in dist.get('entry_points', []):
            write_output("  %s", entry.strip())
            
        if list_files:
            write_output("Files:")
            for line in dist.get('files', []):
                write_output("  %s", line.strip())
            if "files" not in dist:
                write_output("Cannot locate installed-files.txt")
    return results_printed
