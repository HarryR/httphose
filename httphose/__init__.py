from __future__ import print_function

from gevent import monkey
monkey.patch_all()
import gevent
import gevent.pool
from gevent.lock import Semaphore

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


# https://myip.ms/browse/comp_browseragents/Computer_Browser_Agents.html
HTTP_USER_AGENTS = [
"Mozilla/5.0 (Windows NT 6.1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/41.0.2228.0 Safari/537.36",
"Opera/9.80 (Windows NT 6.2; Win64; x64) Presto/2.12 Version/12.16",
"Mozilla/5.0 (Windows NT 6.1; WOW64; Trident/7.0; rv:11.0) like Gecko",
"Mozilla/5.0 (compatible; MSIE 9.0; Windows NT 6.1; Trident/5.0)",
"Mozilla/5.0 (compatible; MSIE 9.0; Windows NT 6.1; WOW64; Trident/5.0)",
"Mozilla/5.0 (Windows NT 6.3; WOW64; rv:45.0) Gecko/20100101 Firefox/45.0",
"Mozilla/5.0 (compatible; MSIE 10.0; Windows NT 6.1; WOW64; Trident/6.0)",
"Mozilla/5.0 (Windows NT 6.1; Trident/7.0; rv:11.0) like Gecko",
"Mozilla/4.0 (compatible; MSIE 7.0; Windows NT 6.1; WOW64; Trident/5.0; SLCC2; .NET CLR 2.0.50727; .NET CLR 3.5.30729; .NET CLR 3.0.30729; Media Center PC 6.0; .NET4.0C; .NET4.0E)",
"Mozilla/5.0 (Windows NT 5.1) AppleWebKit/537.11 (KHTML like Gecko) Chrome/23.0.1271.95 Safari/537.11    ",
"Mozilla/5.0 (Macintosh; Intel Mac OS X 1094) AppleWebKit/537.77.4 (KHTML like Gecko) Version/7.0.5 Safari/537.77.4",
"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_2) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/48.0.2564.48 Safari/537.36",
"Mozilla/5.0 (Windows NT 5.1) AppleWebKit/537.11 (KHTML like Gecko) Chrome/23.0.1271.64 Safari/537.11",
"Mozilla/5.0 (Windows NT 5.1; rv:31.0) Gecko/20100101 Firefox/31.0",
"Mozilla/5.0 (Windows NT 6.3; WOW64; Trident/7.0; rv:11.0) like Gecko"
]


def sha1_b32(*args):
    """Hash all arguments in a way which avoids concatenation resulting in same hash"""
    hasher = sha1()
    for arg in args:
        hasher.update(str(arg))
        hasher.update(hasher.digest())
    return b32encode(hasher.digest())


class Worker(object):
    __slots__ = ('hose', 'domain', 'names', 'extra')

    def __init__(self, hose, domain, names, extra=None):
        self.hose = hose
        self.domain = domain
        self.names = names
        self.extra = extra

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
                        self.hose.on_result(name_url, resp, self.extra)
            except Exception:
                LOG.exception("Failed to request %r", name_url)
        self.hose.on_finish()


def _connect_beanstalk(host):
    import beanstalkc
    if ':' not in host:
        host += ':11300'
    host, port = host.split(':')
    try:
        return beanstalkc.Connection(host=host, port=port)
    except beanstalkc.SocketError:
        LOG.exception("Cannot connect to Beanstalk server @ %r:%r", host, port)
        raise


class BeanstalkChannel(object):
    __slots__ = ('beanstalk_read', 'beanstalk_write', 'rdlock', 'wrlock')

    def __init__(self, options):
        if not options.beanstalk:
            raise RuntimeError("Not enough info to create beanstalk channel!")
        self.beanstalk_read = _connect_beanstalk(options.beanstalk)
        self.beanstalk_read.watch(options.tube_fetch)
        self.beanstalk_write = _connect_beanstalk(options.beanstalk)
        self.beanstalk_write.use(options.tube_resp)
        self.rdlock = Semaphore()
        self.wrlock = Semaphore()
        LOG.info("Connected to beanstalk @ %r - fetch: %r - resp: %r",
                 options.beanstalk, options.tube_fetch, options.tube_resp)

    def get_workgenerator(self, hose):
        return ChannelWorkGenerator(hose, self)

    def put(self, data):
        with self.wrlock:
            return self.beanstalk_write.put(json.dumps(data))

    def bury(self, job):
        with self.rdlock:
            return job.bury()

    def delete(self, job):
        with self.rdlock:
            job.delete()

    def get(self):
        while True:
            with self.rdlock:
                job = self.beanstalk_read.reserve(timeout=1)
            if job is None:
                continue
            try:
                # One or more rows can exist in the job
                for data in job.body.split("\n"):
                    data = json.loads(job.body)
                    if not isinstance(data, (list, set, dict)):
                        LOG.warning('Job %r: Invalid JSON, bad type: %r',
                                    job.jid, type(data))
                        self.bury(job)
                        continue
                    if isinstance(data, dict):
                        # {'domains':[...], 'extra':{?}}
                        if 'domains' not in data:
                            LOG.warning('Invalid job dict, no domains!')
                            self.bury(job)
                            continue
                        domain_list = data['domains']
                        extra = data.get('extra')
                        if not isinstance(domain_list, (list, set)):
                            LOG.warning('Job %r: job dict, bad type for domains: %r',
                                        job.jid, type(domain_list))
                            self.bury(job)
                            continue
                        if extra and not isinstance(extra, (dict)):
                            LOG.warning('Job %r: invalid job dict! bad type for extra: %r',
                                        job.jid, type(extra))
                            self.bury(job)
                            continue
                        return job, domain_list, extra
                    else:
                        # Simple list of domains
                        return job, data, None
            except ValueError:
                LOG.exception('Job %r: error parsing job JSON', job.jid)
                self.bury(job)

    def getall(self):
        while True:
            job, data, extra = self.get()
            if not job:
                continue
            yield job, data, extra


class ChannelWorkGenerator(object):
    __slots__ = ('hose', 'channel')

    def __init__(self, hose, channel):
        self.hose = hose
        self.channel = channel

    @property
    def total(self):
        return None

    def getall(self):
        """Fetch batches of jobs from beanstalk"""
        for job, domain_list, extra in self.channel.getall():
            LOG.info("Processing job: %r", job.jid)
            try:
                for domain in domain_list:
                    yield Worker(self.hose, domain, self.hose.names, extra)
            except Exception:
                LOG.exception("While generating work from job %r", job.jid)
                self.channel.bury(job)
                continue
            self.channel.delete(job)

class ListWorkGenerator(object):
    __slots__ = ('hose', 'domains', 'names', 'total')

    def __init__(self, hose):
        self.hose = hose
        self.domains = self.hose.domains
        self.names = self.hose.names
        self.total = len(self.domains) * len(self.names)

    def getall(self):
        for domain in self.domains:
            yield Worker(self.hose, domain, self.names)


class HTTPHose(object):
    __slots__ = ('options', 'domains', 'names', 'beanstalk', 'finished',
                 'progress')

    def __init__(self, options):
        self.finished = 0
        self._setup_options(options)
        self._setup_progress(options)
        self._setup_beanstalk(options)
        if not options.beanstalk:
            LOG.info("%d file names, %d domains", len(self.names), len(self.domains))
        else:
            LOG.info("%d file names, attached to beanstalk C&C channel", len(self.names))

    def valid(self):
        return len(self.domains) or self.beanstalk

    def _setup_options(self, options):
        self.options = options
        self.domains = options.domain
        if options.domains:
            self.domains += filter(None, options.domains.read().split("\n"))
        random.shuffle(self.domains)
        self.names = [X for X in self._load_names(options.names)]

    def _setup_beanstalk(self, options):
        if options.beanstalk:
            self.beanstalk = BeanstalkChannel(options)
        else:
            self.beanstalk = None

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
        if self.beanstalk:
            self.beanstalk.put(status)

    def on_finish(self):
        if self.progress:
            try:
                self.progress.update(self.finished)
            except Exception:
                self.progress.update(progressbar.UnknownLength)
        self.finished += 1

    def run(self):
        if self.beanstalk:
            generator = self.beanstalk.get_workgenerator(self)
        else:
            generator = ListWorkGenerator(self)

        pool = gevent.pool.Pool(self.options.concurrency)
        self.finished = 0
        if self.progress:
            self.progress.start(generator.total)

        try:
            for worker in generator.getall():
                pool.add(gevent.spawn(worker.run))
        except KeyboardInterrupt:
            print("Ctrl+C caught... stopping")
        pool.join()

        if self.progress:
            self.progress.finish()
