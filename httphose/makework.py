from __future__ import absolute_import, print_function
import sys
import argparse
import logging
import pkg_resources
import os
import json
import random
from . import BeanstalkChannel


class MakeWorkProgram(object):
    __slots__ = ('options', 'domains', 'names', 'channel')

    def __init__(self, options):
        self.options = options
        self.channel = BeanstalkChannel(options) if options.beanstalk else None
        self.domains = options.domain
        if options.domains:
            self.domains += filter(None, options.domains.read().split("\n"))
        random.shuffle(self.domains)
        self.names = [X for X in self._load_names(options.names)]
        random.shuffle(self.names)

    def _load_names(self, names_file):
        for name in names_file:
            name = name.strip()
            if not len(name) or name[0] == '#':
                continue
            yield name

    def valid(self):
        return len(self.domains)

    def _output_batch(self, batch):
        if self.channel:
            self.channel.put("\n".join(batch))

    def run(self):
        batch = []
        max_bytes = 1024*32
        batch_bytes = 0
        quiet = self.options.quiet
        output = self.options.output
        for domain in self.domains:
            url = domain.strip('/')
            if not url.startswith('http:') and not url.startswith('https:'):
                url = 'http://' + url
            for name in self.names:
                name_url = url + '/' + name
                if not quiet:
                    print(name_url)
                if output:
                    output.write(name_url + "\n")                    
                line = json.dumps([name_url])
                if batch_bytes + len(line) > max_bytes:
                    self._output_batch(batch)
                    batch = []
                    batch_bytes = 0
                batch.append(line)
                batch_bytes += len(line)
        self._output_batch(batch)


def main():
    parser = argparse.ArgumentParser(description='Work generator for httphose')
    parser.add_argument('-q', '--quiet', action='store_true',
                        help="Don't print results to console")
    parser.add_argument('-v', '--verbose', action='store_const',
                        dest="loglevel", const=logging.INFO,
                        help="Log informational messages")
    parser.add_argument('--debug', action='store_const', dest="loglevel",
                        const=logging.DEBUG, default=logging.WARNING,
                        help="Log debugging messages")
    parser.add_argument('-n', '--names', metavar='NAMES_FILE',
                        default=pkg_resources.resource_stream(__name__, "common.txt"),
                        type=argparse.FileType('r'),
                        help="Load target directory names from file")
    parser.add_argument('-o', '--output', metavar='OUTFILE',
                        type=argparse.FileType('w+'),
                        help="Output results to file")
    parser.add_argument('-b', '--beanstalk', metavar='HOST:PORT',
                        help="Connect to Beanstalk server for jobs")
    parser.add_argument('--tube-fetch', metavar='NAME', default='httphose_jobs',
                        help='Beanstalk tube to fetch jobs from, default: httphose_jobs')
    parser.add_argument('--tube-resp', metavar='NAME', default='httphose_resp',
                        help='Beanstalk tube to respond to, default: httphose_resp')
    parser.add_argument('-x', '--extra', metavar='K=V', action='append',
                        help="Extra variables for JSON output")
    parser.add_argument('-d', '--domains', metavar='DOMAINS_FILE',
                        type=argparse.FileType('r'),
                        help="Load target domains from file")
    parser.add_argument('domain', nargs='*', help='One or more domains')
    args = parser.parse_args()
    logging.basicConfig(level=args.loglevel)
    args.extra = dict([X.split('=', 1) for X in args.extra or []])
    program = MakeWorkProgram(args)
    if not program.valid():
        parser.print_help()
        return 1
    program.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
