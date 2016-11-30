from __future__ import print_function

from gevent import monkey
monkey.patch_all()
import gevent
import gevent.pool

import os
import logging
import random
import json
import requests
import progressbar
from hashlib import sha1
from base64 import b32encode
from requests.packages.urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

LOG = logging.getLogger(__name__)


# https://techblog.willshouse.com/2012/01/03/most-common-user-agents/
HTTP_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/54.0.2840.71 Safari/537.36",
    "Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/54.0.2840.71 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/54.0.2840.71 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; WOW64; rv:49.0) Gecko/20100101 Firefox/49.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/54.0.2840.71 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_12_1) AppleWebKit/602.2.14 (KHTML, like Gecko) Version/10.0.1 Safari/602.2.14",
    "Mozilla/5.0 (Windows NT 6.1; WOW64; rv:49.0) Gecko/20100101 Firefox/49.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_12_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/54.0.2840.71 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:49.0) Gecko/20100101 Firefox/49.0",
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/54.0.2840.99 Safari/537.36",
    "Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/54.0.2840.99 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/54.0.2840.99 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/53.0.2785.143 Safari/537.36",
    "Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/54.0.2840.71 Safari/537.36",
    "Mozilla/5.0 (Windows NT 6.1; WOW64; Trident/7.0; rv:11.0) like Gecko",
]


def sha1_b32(*args):
    """Hash all arguments in a way which avoids concatenation resulting in same hash"""
    hasher = sha1()
    for arg in args:
        hasher.update(str(arg))
        hasher.update(hasher.digest())
    return b32encode(hasher.digest())


class Worker(object):
    __slots__ = ('hose', 'domain', 'names')

    def __init__(self, hose, domain, names):
        self.hose = hose
        self.domain = domain
        self.names = names

    def run(self):
        options = self.hose.options
        url = self.domain.strip('/')
        if not url.startswith('http://') and not url.startswith('https://'):
            url = 'http://' + url
        headers = {
            'User-Agent': options.agent or random.choice(HTTP_USER_AGENTS)
        }
        session = requests.Session()
        session.max_redirects = options.redirects
        for name in self.names:
            try:
                name_url = url + '/' + name
                resp = session.get(name_url, headers=headers, stream=True,
                                   timeout=options.timeout, verify=False)
                if resp.status_code >= 200 and resp.status_code < 300:
                    if name in resp.url:
                        self.hose.on_result(name_url, resp)
            except Exception:
                LOG.exception("Failed to request %r", name_url)
        self.hose.on_finish()


def _connect_beanstalk(self, options):
    import beanstalkc
    host = options.beanstalk
    if ':' not in host:
        host += ':14711'
    host, port = host.split(':')
    return beanstalkc.Connection(host=host, port=port)


class BeanstalkChannel(object):
    __slots__ = ('beanstalk',)

    def __init__(self, options):
        if not options.beanstalk:
            raise RuntimeError("Not enough info to create beanstalk channel!")
        self.beanstalk = _connect_beanstalk(options)
        self.beanstalk.use(options.tube_resp)
        self.beanstalk.watch(options.tube_fetch)

    def put(self, data):
        return self.beanstalk.put(json.dumps(data))

    def reserve(self):
        while True:
            job = self.beanstalk.reserve()
            try:
                # One or more rows can exist in the job
                for data in job.body.split("\n"):
                    data = json.loads(job.body)
                    if not isinstance(data, (list, set, dict)):
                        LOG.warning('Invalid job JSON, bad type!')
                        job.bury()
                        continue
                    if isinstance(data, dict):
                        # {'domains':[...], 'extra':{?}}
                        if 'domains' not in data:
                            LOG.warning('Invalid job dict, no domains!')
                            job.bury()
                            continue
                        domain_list = data['domains']
                        extra = data.get('extra')
                        if not isinstance(domain_list, (list, set)) or not isinstance(extra, dict):
                            LOG.warning('Invalid job dict, bad type for domains or extra!')
                            job.bury()
                            continue
                        yield job, domain_list, extra
                    else:
                        # Simple list of domains
                        yield job, data, None
            except ValueError:
                LOG.exception('Error parsing job JSON')
                job.bury()
                continue


class ChannelWorkGenerator(object):
    __slots__ = ('hose', 'names', 'channel', 'total')

    def __init__(self, hose, channel):
        self.hose = hose
        self.names = self.hose.names
        self.channel = channel
        self.total = None

    def all(self):
        """Fetch batches of jobs from beanstalk"""
        while True:
            job, domain_list, extra = self.channel.reserve()
            try:
                for domain in domain_list:
                    yield Worker(self.hose, domain, self.names)
            except Exception:
                job.bury()
                return
            job.delete()


class WorkGenerator(object):
    __slots__ = ('hose', 'domains', 'names', 'total')

    def __init__(self, hose):
        self.hose = hose
        self.domains = self.hose.domains
        self.names = self.hose.names
        self.total = len(self.domains) * len(self.names)

    def all(self):
        for domain in self.domains:
            yield Worker(self.hose, domain, self.names)


class HTTPHose(object):
    def __init__(self, options):
        self._setup_options(options)
        self._setup_progress(options)
        self._setup_beanstalk(options)
        if not self.channel:
            LOG.info("%d file names, %d domains", len(self.names), len(self.domains))
        else:
            LOG.info("%d file names, attached to C&C channel", len(self.names))

    def valid(self):
        return len(self.domains) or self.channel

    def _setup_options(self, options):
        self.options = options
        self.domains = options.domain
        if options.domains:
            self.domains += filter(None, options.domains.read().split("\n"))
        random.shuffle(self.domains)
        self.names = [X for X in self._load_names(options.names)]

    def _setup_beanstalk(self, options):
        if options.beanstalk is None:
            self.channel = None
            return
        self.channel = BeanstalkChannel(options)

    def _setup_progress(self, options):
        if options.progress:
            self.progress = progressbar.ProgressBar(
                redirect_stdout=True,
                redirect_stderr=True,
                widgets=[
                    progressbar.Percentage(),
                    progressbar.Bar(),
                    ' (', progressbar.ETA(), ') ',
                ])
        else:
            self.progress = None
        self.finished = 0

    def _load_names(self, names_file):
        for name in names_file:
            name = name.strip()
            if not len(name) or name[0] == '#':
                continue
            yield name

    def on_result(self, url, resp, extra=None):
        status = dict(
            url=resp.url or url,
            hist=[(hist.status_code, hist.url) for hist in resp.history],
            sc=resp.status_code,
            hds=[K for K in resp.headers],
            cks=[C.name for C in resp.cookies],
            hd={k: v for k, v in dict(
                lm=resp.headers.get('Last-Modified'),
                ct=resp.headers.get('Content-Type'),
                cl=resp.headers.get('Content-Length'),
                sv=resp.headers.get('Server'),
            ).iteritems() if v}
        )
        if extra and isinstance(extra, dict):
            status.update(extra)
        # Save file to storage
        storage = self.options.storage
        if storage:
            url_hash = sha1_b32(status['url'],
                                resp.status_code,
                                resp.headers.get('Last-Modified'),
                                resp.headers.get('Date'),
                                resp.headers.get('Content-Len'))[:12]
            url_dir = os.path.join(storage, url_hash[1])
            url_path = os.path.join(url_dir, url_hash[1:])
            os.makedirs(url_dir)
            with open(url_path, 'wb') as handle:
                for chunk in resp.iter_content(chunk_size=1024*64):
                    handle.write(chunk)
            status['id'] = url_hash
        self._log_result(status)

    def _log_result(self, status):
        if self.options.extra:
            status.update(self.options.extra)
        json_status = json.dumps(status)
        if not self.options.quiet:
            print(json_status)
        if self.options.output:
            self.options.output.write(json_status + "\n")

    def on_finish(self):
        if self.progress:
            try:
                self.progress.update(self.finished)
            except Exception:
                self.progress.update(progressbar.UnknownLength)
        self.finished += 1

    def run(self):
        generator = WorkGenerator(self)
        pool = gevent.pool.Pool(self.options.concurrency)
        self.finished = 0
        if self.progress:
            self.progress.start(generator.total)
        try:
            for worker in generator.all():
                pool.add(gevent.spawn(worker.run))
        except KeyboardInterrupt:
            print("Ctrl+C caught... stopping")
        pool.join()
        if self.progress:
            self.progress.finish()
