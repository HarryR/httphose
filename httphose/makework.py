from __future__ import absolute_import, print_function
import sys
import argparse
import logging
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

    def valid(self):
        return len(self.domains)

    def _output_batch(self, batch):
        if self.channel:
            self.channel.put(batch)

    def run(self):
        batch = []
        batch_len = 0
        max_bytes = 1024*32
        batch_data = dict()
        if self.options.extra:
            batch_data['extra'] = self.options.extra

        for domain in self.domains:
            url = domain.strip('/')
            if not url.startswith('http:') and not url.startswith('https:'):
                url = 'http://' + url
            batch_len += len(url)
            batch.append(url)
            if batch_len > max_bytes:
                batch_data['domains'] = batch
                self._output_batch(batch_data)
                batch = []
                batch_len = 0
        if len(batch):
            batch_data['domains'] = batch
            self._output_batch(batch_data)


def main():
    parser = argparse.ArgumentParser(description='Work generator for httphose')
    parser.add_argument('-v', '--verbose', action='store_const',
                        dest="loglevel", const=logging.INFO,
                        help="Log informational messages")
    parser.add_argument('--debug', action='store_const', dest="loglevel",
                        const=logging.DEBUG, default=logging.WARNING,
                        help="Log debugging messages")
    parser.add_argument('-b', '--beanstalk', metavar='HOST:PORT',
                        help="Connect to Beanstalk server for jobs")
    parser.add_argument('--tube-fetch', metavar='NAME', default='httphose_jobs',
                        help='Beanstalk tube to add jobs to, default: httphose_jobs')
    parser.add_argument('--tube-resp', metavar='NAME', default='httphose_resp',
                        help='Beanstalk tube to respond to, default: httphose_resp')
    parser.add_argument('-x', '--extra', metavar='K=V', action='append',
                        help="Extra variables for JSON output")
    parser.add_argument('-d', '--domains', metavar='DOMAINS_FILE',
                        type=argparse.FileType('r'),
                        help="Load target domains from file")
    parser.add_argument('domain', nargs='*', help='One or more domains')
    args = parser.parse_args()

    # XXX: job tubes are switched, here we put into fetch, and ignore resp
    tmp = args.tube_resp
    args.tube_resp = args.tube_fetch
    args.tube_fetch = tmp

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
