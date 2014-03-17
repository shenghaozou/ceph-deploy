import argparse
import logging
import os

from ceph_deploy import hosts
from ceph_deploy.cliutil import priority
from ceph_deploy.lib.remoto import process


LOG = logging.getLogger(__name__)


def install(args):
    # XXX This whole dance is because --stable is getting deprecated
    if args.stable is not None:
        LOG.warning('the --stable flag is deprecated, use --release instead')
        args.release = args.stable
    if args.version_kind == 'stable':
        version = args.release
    else:
        version = getattr(args, args.version_kind)
    # XXX Tango ends here.

    version_str = args.version_kind

    if version:
        version_str += ' version {version}'.format(version=version)
    LOG.debug(
        'Installing %s on cluster %s hosts %s',
        version_str,
        args.cluster,
        ' '.join(args.host),
    )

    for hostname in args.host:
        LOG.debug('Detecting platform for host %s ...', hostname)
        distro = hosts.get(hostname, username=args.username)
        LOG.info(
            'Distro info: %s %s %s',
            distro.name,
            distro.release,
            distro.codename
        )
        rlogger = logging.getLogger(hostname)
        rlogger.info('installing ceph on %s' % hostname)

        cd_conf = getattr(args, 'cd_conf', None)

        # custom repo arguments
        repo_url = os.environ.get('CEPH_DEPLOY_REPO_URL') or args.repo_url
        gpg_url = os.environ.get('CEPH_DEPLOY_GPG_URL') or args.gpg_url
        gpg_fallback = 'https://ceph.com/git/?p=ceph.git;a=blob_plain;f=keys/release.asc'

        if gpg_url is None and repo_url:
            LOG.warning('--gpg-url was not used, will fallback')
            LOG.warning('using GPG fallback: %s', gpg_fallback)
            gpg_url = gpg_fallback

        if repo_url:  # triggers using a custom repository
            # the user used a custom repo url, this should override anything
            # we can detect from the configuration, so warn about it
            if cd_conf:
                if cd_conf.get_default_repo():
                    rlogger.warning('a default repo was found but it was \
                        overridden on the CLI')
                if args.release in cd_conf.get_repos():
                    rlogger.warning('a custom repo was found but it was \
                        overridden on the CLI')

            rlogger.info('using custom repository location: %s', repo_url)
            distro.mirror_install(
                distro,
                repo_url,
                gpg_url,
                args.adjust_repos
            )

        # Detect and install custom repos here if needed
        elif should_use_custom_repo(args, cd_conf, repo_url):
            LOG.info('detected valid custom repositories from config file')
            custom_repo(distro, args, cd_conf, rlogger)

        else:  # otherwise a normal installation
            distro.install(
                distro,
                args.version_kind,
                version,
                args.adjust_repos
            )

        # Check the ceph version we just installed
        hosts.common.ceph_version(distro.conn)
        distro.conn.exit()


def should_use_custom_repo(args, cd_conf, repo_url):
    """
    A boolean to determine the logic needed to proceed with a custom repo
    installation instead of cramming everything nect to the logic operator.
    """
    if repo_url:
        # repo_url signals a CLI override, return False immediately
        return False
    if cd_conf:
        if cd_conf.has_repos:
            has_valid_release = args.release in cd_conf.get_repos()
            has_default_repo = cd_conf.get_default_repo()
            if has_valid_release or has_default_repo:
                return True
    return False


def custom_repo(distro, args, cd_conf, rlogger):
    """
    A custom repo install helper that will go through config checks to retrieve
    repos (and any extra repos defined) and install those

    ``cd_conf`` is the object built from argparse that holds the flags and
    information needed to determine what metadata from the configuration to be
    used.
    """
    default_repo = cd_conf.get_default_repo()
    if args.release in cd_conf.get_repos():
        LOG.info('will use repository from conf: %s' % args.release)
        default_repo = args.release
    elif default_repo:
        LOG.info('will use default repository: %s' % default_repo)

    # At this point we know there is a cd_conf and that it has custom
    # repos make sure we were able to detect and actual repo
    if not default_repo:
        LOG.warning('a ceph-deploy config was found with repos \
            but could not default to one')
    else:
        options = dict(cd_conf.items(default_repo))
        options['install_ceph'] = True
        extra_repos = cd_conf.get_list(default_repo, 'extra-repos')
        rlogger.info('adding custom repository file')
        try:
            distro.repo_install(
                distro,
                default_repo,
                options.pop('baseurl'),
                options.pop('gpgkey'),
                **options
            )
        except KeyError as err:
            raise RuntimeError('missing required key: %s in config section: %s' % (err, default_repo))

        for xrepo in extra_repos:
            rlogger.info('adding extra repo file: %s.repo' % xrepo)
            options = dict(cd_conf.items(xrepo))
            try:
                distro.repo_install(
                    distro,
                    xrepo,
                    options.pop('baseurl'),
                    options.pop('gpgkey'),
                    **options
                )
            except KeyError as err:
                raise RuntimeError('missing required key: %s in config section: %s' % (err, xrepo))


def uninstall(args):
    LOG.debug(
        'Uninstalling on cluster %s hosts %s',
        args.cluster,
        ' '.join(args.host),
        )

    for hostname in args.host:
        LOG.debug('Detecting platform for host %s ...', hostname)

        distro = hosts.get(hostname, username=args.username)
        LOG.info('Distro info: %s %s %s', distro.name, distro.release, distro.codename)
        rlogger = logging.getLogger(hostname)
        rlogger.info('uninstalling ceph on %s' % hostname)
        distro.uninstall(distro.conn)
        distro.conn.exit()


def purge(args):
    LOG.debug(
        'Purging from cluster %s hosts %s',
        args.cluster,
        ' '.join(args.host),
        )

    for hostname in args.host:
        LOG.debug('Detecting platform for host %s ...', hostname)

        distro = hosts.get(hostname, username=args.username)
        LOG.info('Distro info: %s %s %s', distro.name, distro.release, distro.codename)
        rlogger = logging.getLogger(hostname)
        rlogger.info('purging host ... %s' % hostname)
        distro.uninstall(distro.conn, purge=True)
        distro.conn.exit()


def purge_data(args):
    LOG.debug(
        'Purging data from cluster %s hosts %s',
        args.cluster,
        ' '.join(args.host),
        )

    installed_hosts = []
    for hostname in args.host:
        distro = hosts.get(hostname, username=args.username)
        ceph_is_installed = distro.conn.remote_module.which('ceph')
        if ceph_is_installed:
            installed_hosts.append(hostname)
        distro.conn.exit()

    if installed_hosts:
        LOG.error("ceph is still installed on: %s", installed_hosts)
        raise RuntimeError("refusing to purge data while ceph is still installed")

    for hostname in args.host:
        distro = hosts.get(hostname, username=args.username)
        LOG.info(
            'Distro info: %s %s %s',
            distro.name,
            distro.release,
            distro.codename
        )

        rlogger = logging.getLogger(hostname)
        rlogger.info('purging data on %s' % hostname)

        # Try to remove the contents of /var/lib/ceph first, don't worry
        # about errors here, we deal with them later on
        process.check(
            distro.conn,
            [
                'rm', '-rf', '--one-file-system', '--', '/var/lib/ceph',
            ]
        )

        # If we failed in the previous call, then we probably have OSDs
        # still mounted, so we unmount them here
        if distro.conn.remote_module.path_exists('/var/lib/ceph'):
            rlogger.warning(
                'OSDs may still be mounted, trying to unmount them'
            )
            process.run(
                distro.conn,
                [
                    'find', '/var/lib/ceph',
                    '-mindepth', '1',
                    '-maxdepth', '2',
                    '-type', 'd',
                    '-exec', 'umount', '{}', ';',
                ]
            )

            # And now we try again to remove the contents, since OSDs should be
            # unmounted, but this time we do check for errors
            process.run(
                distro.conn,
                [
                    'rm', '-rf', '--one-file-system', '--', '/var/lib/ceph',
                ]
            )

        process.run(
            distro.conn,
            [
                'rm', '-rf', '--one-file-system', '--', '/etc/ceph/',
            ]
        )

        distro.conn.exit()


class StoreVersion(argparse.Action):
    """
    Like ``"store"`` but also remember which one of the exclusive
    options was set.

    There are three kinds of versions: stable, testing and dev.
    This sets ``version_kind`` to be the right one of the above.

    This kludge essentially lets us differentiate explicitly set
    values from defaults.
    """
    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values)
        if self.dest == 'release':
            self.dest = 'stable'
        namespace.version_kind = self.dest


@priority(20)
def make(parser):
    """
    Install Ceph packages on remote hosts.
    """

    version = parser.add_mutually_exclusive_group()

    # XXX deprecated in favor of release
    version.add_argument(
        '--stable',
        nargs='?',
        action=StoreVersion,
        metavar='CODENAME',
        help='[DEPRECATED] install a release known as CODENAME\
                (done by default) (default: %(default)s)',
    )

    version.add_argument(
        '--release',
        nargs='?',
        action=StoreVersion,
        metavar='CODENAME',
        help='install a release known as CODENAME\
                (done by default) (default: %(default)s)',
    )

    version.add_argument(
        '--testing',
        nargs=0,
        action=StoreVersion,
        help='install the latest development release',
    )

    version.add_argument(
        '--dev',
        nargs='?',
        action=StoreVersion,
        const='master',
        metavar='BRANCH_OR_TAG',
        help='install a bleeding edge build from Git branch\
                or tag (default: %(default)s)',
    )

    version.add_argument(
        '--adjust-repos',
        dest='adjust_repos',
        action='store_true',
        help='install packages modifying source repos',
    )

    version.add_argument(
        '--no-adjust-repos',
        dest='adjust_repos',
        action='store_false',
        help='install packages without modifying source repos',
    )

    version.set_defaults(
        func=install,
        stable=None,  # XXX deprecated in favor of release
        release='emperor',
        dev='master',
        version_kind='stable',
        adjust_repos=True,
    )

    parser.add_argument(
        'host',
        metavar='HOST',
        nargs='+',
        help='hosts to install on',
    )

    parser.add_argument(
        '--repo-url',
        nargs='?',
        dest='repo_url',
        help='specify a repo URL that mirrors/contains ceph packages',
    )

    parser.add_argument(
        '--gpg-url',
        nargs='?',
        dest='gpg_url',
        help='specify a GPG key URL to be used with custom repos\
                (defaults to ceph.com)'
    )

    parser.set_defaults(
        func=install,
    )


@priority(80)
def make_uninstall(parser):
    """
    Remove Ceph packages from remote hosts.
    """
    parser.add_argument(
        'host',
        metavar='HOST',
        nargs='+',
        help='hosts to uninstall Ceph from',
        )
    parser.set_defaults(
        func=uninstall,
        )


@priority(80)
def make_purge(parser):
    """
    Remove Ceph packages from remote hosts and purge all data.
    """
    parser.add_argument(
        'host',
        metavar='HOST',
        nargs='+',
        help='hosts to purge Ceph from',
        )
    parser.set_defaults(
        func=purge,
        )


@priority(80)
def make_purge_data(parser):
    """
    Purge (delete, destroy, discard, shred) any Ceph data from /var/lib/ceph
    """
    parser.add_argument(
        'host',
        metavar='HOST',
        nargs='+',
        help='hosts to purge Ceph data from',
        )
    parser.set_defaults(
        func=purge_data,
        )
