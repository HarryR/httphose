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

LOG = logging.getLogger(__name__)


def sha1_b32(*args):
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
        session = requests.Session()
        domain = self.domain
        url = self.domain.strip('/')
        if not url.startswith('http://') and not url.startswith('https://'):
            url = 'http://' + url
        headers = {
            'User-Agent': self.hose.options.agent,
        }
        for name in self.names:
            name_url = url + '/' + name            
            resp = session.get(name_url, headers=headers, stream=True)
            if resp.status_code >= 200 and resp.status_code < 400:
                if name in resp.url:
                    self.hose.on_result(name_url, resp)
            self.hose.on_finish()


class BeanstalkWorkGenerator(object):
    __slots__ = ('hose', 'names', 'beanstalk', 'total')

    def _connect_beanstalk(self, options):
        # TODO: hose for put, error and results
        import beanstalkc
        host = options.beanstalk
        if ':' not in host:
            host += ':14711'
        host, port = host.split(':')
        return beanstalkc.Connection(host=host, port=port)

    def __init__(self, hose):
        self.hose = hose
        self.names = self.hose.names
        self.beanstalk = self._connect_beanstalk(hose.options)
        self.total = None

    def all(self):
        """Fetch batches of jobs from beanstalk"""
        while True:
            job = self.beanstalk.reserve()
            fail = False
            try:
                domain_list = json.loads(job.body)
                if not isinstance(domain_list, (list, set)):
                    fail = True
            except ValueError:
                domain_list = [job.body]
            if not fail:
                for domain in domain_list:
                    yield Worker(self.hose, domain, self.names)
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
        self.options = options
        self.domains = options.domain
        if options.domains:
            self.domains += filter(None, options.domains.read().split("\n"))
        random.shuffle(self.domains)
        self.names = [X for X in self._load_names(options.names)]
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
        LOG.info("%d directories, %d domains",
                 len(self.names), len(self.domains))

    def _load_names(self, names_file):
        for name in names_file:
            name = name.strip()
            if not len(name) or name[0] == '#':
                continue
            yield name

    def on_result(self, url, resp):
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
        print(status)

    def on_finish(self):
        if self.progress:
            try:
                self.progress.update(self.finished)
            except Exception:
                self.progress.update(progressbar.UnknownLength)
        self.finished += 1

    def valid(self):
        return len(self.domains) > 0 or self.options.beanstalk is not None

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

