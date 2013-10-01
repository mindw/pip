import os

from pip.basecommand import Command
from pip.log import logger
from pip._vendor import pkg_resources

import StringIO
import rfc822

fields = ['name' ,'version' ,'platform' ,'summary' ,'description'
        ,'keywords' ,'home_page' ,'author' ,'author_email' ,'license']

class PkgInfoParsed(object):
    def __init__(self, dist, missingMsg=None):
        if dist.has_metadata('PKG-INFO'):
            metadata = StringIO.StringIO(dist.get_metadata('PKG-INFO'))
        elif dist.has_metadata('METADATA'):
            metadata = StringIO.StringIO(dist.get_metadata('METADATA'))
        messages = rfc822.Message(metadata)
        #print messages.items()
        for field in fields:
            if field in ['home_page', 'author_email']:
                prop = field.replace('_','-')
            else:
                prop = field
            value=messages.getheader(prop)
            if missingMsg:
                if not value or value == 'UNKNOWN':
                    value = missingMsg
            setattr(self, field, value)
            
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

        self.parser.insert_option_group(0, self.cmd_opts)

    def run(self, options, args):
        if not args:
            logger.warn('ERROR: Please provide a package name or names.')
            return
        query = args

        results = search_packages_info(query)
        print_results(results, options.files)


def search_packages_info(query):
    """
    Gather details from installed distributions. Print distribution name,
    version, location, and installed files. Installed files requires a
    pip generated 'installed-files.txt' in the distributions '.egg-info'
    directory.
    """
    installed_packages = dict(
        [(p.project_name.lower(), p) for p in pkg_resources.working_set])
    for name in query:
        normalized_name = name.lower()
        if normalized_name in installed_packages:
            dist = installed_packages[normalized_name]

            required_by = []
            for _, p in installed_packages.iteritems():
                if dist.project_name.lower() in [dep.project_name.lower() for dep in p.requires()]:
                    required_by += [p.project_name]
                else:
                    for e in p.extras:
                        if dist.project_name.lower() in [dep.project_name.lower() for dep in p.requires([e])]:
                            required_by += ["%s[%s]" % (p.project_name, e)]
            extras = {}
            requires = [dep.project_name for dep in dist.requires()]
            make_ext = lambda pkg_name: (pkg_name, True if pkg_name in installed_packages else False)
            for e in dist.extras:
                extras[e] = [make_ext(dep.project_name.lower()) for dep in dist.requires([e]) if dep.project_name not in requires]
            
            package = {
                'name': dist.project_name,
                'version': dist.version,
                'location': dist.location,
                'requires': requires,
                'required_by': required_by,
                'extras': extras,
                'metadata' : PkgInfoParsed(dist)
            }
            filelist = os.path.join(
                       dist.location,
                       dist.egg_name() + '.egg-info',
                       'installed-files.txt')
            if os.path.isfile(filelist):
                package['files'] = filelist
            yield package


def print_results(distributions, list_all_files):
    """
    Print the informations from installed distributions found.
    """
    for dist in distributions:
        logger.notify("---")
        logger.notify("Name: %s" % dist['name'])
        logger.notify("Version: %s" % dist['version'])
        logger.notify("Location: %s" % dist['location'])
        logger.notify("home_page: %s" % dist['metadata'].home_page)
        logger.notify("Summary: %s" % dist['metadata'].summary)
        logger.notify("Requires: %s" % ', '.join(dist['requires']))
        for extra_name, deps in dist['extras'].items():
            deps = ["%s%s" % (dep[0], "" if dep[1] else "(-)") for dep in deps]
            logger.notify("Extra Require [%s]: %s", extra_name, ', '.join(deps))
        logger.notify("Required by(%d): %s" % (len(dist['required_by']), ', '.join(dist['required_by'])))
        if list_all_files:
            logger.notify("Files:")
            if 'files' in dist:
                for line in open(dist['files']):
                    logger.notify("  %s" % line.strip())
            else:
                logger.notify("Cannot locate installed-files.txt")
