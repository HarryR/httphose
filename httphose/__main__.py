from __future__ import absolute_import, print_function
import sys
import argparse
import logging
import pkg_resources
import os
from . import HTTPHose


class writable_dir(argparse.Action):
    def __call__(self,parser, namespace, values, option_string=None):
        prospective_dir=values
        if not os.path.isdir(prospective_dir):
            raise argparse.ArgumentTypeError("{0} is not a valid path".format(prospective_dir))
        if os.access(prospective_dir, os.W_OK):
            prospective_dir = os.path.realpath(prospective_dir)
            setattr(namespace, self.dest, prospective_dir)
        else:
            raise argparse.ArgumentTypeError("{0} is not a readable dir".format(prospective_dir))


def main():
    parser = argparse.ArgumentParser(description='HTTP server reflector')
    parser.add_argument('-p', '--progress', action='store_true',
                        help='Show progress bar with ETA')
    parser.add_argument('-q', '--quiet', action='store_true',
                        help="Don't print results to console")
    parser.add_argument('-v', '--verbose', action='store_const',
                        dest="loglevel", const=logging.INFO,
                        help="Log informational messages")
    parser.add_argument('--debug', action='store_const', dest="loglevel",
                        const=logging.DEBUG, default=logging.WARNING,
                        help="Log debugging messages")
    parser.add_argument('-j', '--json', metavar='OUTJSON',
                        type=argparse.FileType('w+'),
                        help="Output results, as JSON to file")
    parser.add_argument('-n', '--names', metavar='NAMES_FILE',
                        default=pkg_resources.resource_stream(__name__, "common.txt"),
                        type=argparse.FileType('r'),
                        help="Load target directory names from file")
    parser.add_argument('-b', '--beanstalk', metavar='HOST:PORT',
                        help="Connect to Beanstalk server for jobs")
    # TODO: beanstalk pipes for requests & responses
    parser.add_argument('-x', '--extra', metavar='K=V', action='append',
                        help="Extra variables for JSON output")
    parser.add_argument('-d', '--domains', metavar='DOMAINS_FILE',
                        type=argparse.FileType('r'),
                        help="Load target domains from file")
    parser.add_argument('-s', '--storage', metavar='DIRECTORY',
                        action=writable_dir, help="Save files into this dir")
    parser.add_argument('-A', '--agent', default="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_7_2) AppleWebKit/535.2 (KHTML, like Gecko) Chrome/15.0.874.106 Safari/535.2",
                        help="HTTP User Agent")
    parser.add_argument('-R', '--retries', default=2, type=int, metavar='N',
                        help="Retries on failed DNS request, default: 2")
    parser.add_argument('-C', '--concurrency', default=20, type=int,
                        help="Concurrent DNS requests, default: 20", metavar='N')
    parser.add_argument('-T', '--timeout', default=1.5, type=float, metavar='SECS',
                        help="Timeout for DNS request in seconds, default: 1.5")
    parser.add_argument('domain', nargs='*', help='One or more domains')
    args = parser.parse_args()
    logging.basicConfig(level=args.loglevel)
    args.extra = dict([X.split('=', 1) for X in args.extra or []])
    program = HTTPHose(args)
    if not program.valid():
        parser.print_help()
        return 1
    program.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
