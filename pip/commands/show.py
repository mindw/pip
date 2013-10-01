from __future__ import absolute_import

from email.parser import FeedParser
import logging
import os

from pip.basecommand import Command
from pip.status_codes import SUCCESS, ERROR
from pip._vendor import pkg_resources

logger = logging.getLogger(__name__)


class ShowCommand(Command):
    """Show information about one or more installed packages."""
    name = 'show'
    usage = """
      %prog [options] <package> ..."""
    summary = 'Show information about installed packages.'

    def __init__(self, *args, **kw):
        super(ShowCommand, self).__init__(*args, **kw)
        self.cmd_opts.add_option(
            '-f', '--files',
            dest='files',
            action='store_true',
            default=False,
            help='Show the full list of installed files for each package.')

        self.cmd_opts.add_option(
            '--index',
            dest='index',
            metavar='URL',
            default='https://pypi.python.org/pypi',
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
            with self._build_session(options) as session:
                results = search_packages_info(query, options.index, session)
        else:
            results = search_packages_info(query, options.index)

        if not print_results(results, options.files):
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
    installed = dict(
        [(p.key, p) for p in pkg_resources.working_set])
    query_names = [name.lower() for name in query]
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
                        (r for r in p.requires([e]) if r.key == dist.key), None)
                    if r:
                        required_by.append(
                            "%s[%s] %s" % (p.project_name, e, r.specifier))
        extras = {}
        for e in dist.extras:
            reqs = set(dist.requires([e])) - set(dist.requires())
            extras[e] = map(_format_package, reqs)

        if session:
            from pip.download import PipXmlrpcTransport
            from pip._vendor.six.moves import xmlrpc_client

            transport = PipXmlrpcTransport(index_url, session)
            pypi = xmlrpc_client.ServerProxy(index_url, transport)
            pypi_releases = pypi.package_releases(dist.project_name)
            pypi_version = pypi_releases[0] if pypi_releases else 'UNKNOWN'
        else:
            pypi_version = None

        requires = [_format_package(r) for r in dist.requires()]
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

        # @todo: Should pkg_resources.Distribution have a
        # `get_pkg_info` method?
        feed_parser = FeedParser()
        feed_parser.feed(metadata)
        pkg_info_dict = feed_parser.close()
        for key in ('metadata-version', 'summary',
                    'home-page', 'author', 'author-email', 'license'):
            package[key] = pkg_info_dict.get(key)

        # use and short-circuit to check for None
        package['files'] = file_list and sorted(file_list)
        yield package


def print_results(distributions, list_all_files):
    """
    Print the informations from installed distributions found.
    """
    results_printed = False
    for dist in distributions:
        results_printed = True
        logger.info("---")
        logger.info("Metadata-Version: %s" % dist.get('metadata-version'))
        logger.info("Name: %s" % dist['name'])
        logger.info("Version: %s" % dist['version'])
        if dist['pypi_version']:
            logger.info("PyPi Version: %s" % dist['pypi_version'])
        logger.info("Summary: %s" % dist.get('summary'))
        logger.info("Home-page: %s" % dist.get('home-page'))
        logger.info("Author: %s" % dist.get('author'))
        logger.info("Author-email: %s" % dist.get('author-email'))
        logger.info("License: %s" % dist.get('license'))
        logger.info("Location: %s" % dist['location'])
        logger.info("Requires:")
        for line in sorted(dist['requires']):
                logger.info("  %s" % line)
        for extra_name, deps in dist['extras'].items():
            logger.info("Extra Require [%s]:", extra_name)
            for line in sorted(deps):
                logger.info("  %s" % line.strip())
        logger.info("Required by(%d):" % len(dist['required_by']))
        for line in sorted(dist['required_by']):
            logger.info("  %s" % line.strip())

        if list_all_files:
            logger.info("Files:")
            if dist['files'] is not None:
                for line in dist['files']:
                    logger.info("  %s" % line.strip())
            else:
                logger.info("Cannot locate installed-files.txt")
        if 'entry_points' in dist:
            logger.info("Entry-points:")
            for line in dist['entry_points']:
                logger.info("  %s" % line.strip())

    return results_printed
