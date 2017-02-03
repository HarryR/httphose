# HTTP Hose

This tool is somewhat like a directory buster, but aimed at larger scale operations to scan thousands and possibly millions of domains for specific files or directories and record information about successful requests. Optionally it will also store the files it downloads.

You point the hose at the internet, and HTTP requests come out :)

## Why?

Because `dirb` and other tools don't easily support thousands of domain names, nor do they offer any opportunities to easily integrate into part of a larger workflow or pipeline, and often don't provide computer readable output.

The key features of HTTP Hose are:

 * A progress bar, yay!
 * Multi-threaded and asynchronous, via `gevent`
 * Supports HTTP and SOCKS proxies, via `requests`
 * Save successful HTTP responses as JSON
 * Download content of files to content addressable storage
 * Retrieve jobs from Beanstalk queue
 * Interesting default list of filenames to check

### Using a Proxy

Proxies can be configured via the environment:

```
$ export HTTP_PROXY="http://10.10.1.10:3128"
$ export HTTPS_PROXY="http://10.10.1.10:1080"
$ python -mhttphose ...
```

If you're using Requests 2.10.0 or above, in addition to basic HTTP proxies, Requests also supports proxies using the SOCKS protocol, the Docker container and `requirements.txt` file include this for your convenience.

```
$ export HTTP_PROXY="socks5://localhost:9050"
$ export HTTPS_PROXY="socks5://localhost:9050"
$ python -mhttphose ...
```

### Beanstalk Integration

HTTP Hose will retrieve jobs from a Beanstalk queue / tube, and put answers onto another queue. To run httphose in beanstalk mode, use:

```
python -mhttphose -b localhost:11300
```

The `makework` script allows you to push jobs onto the queue, it supports a similar set of options to `httphose`, for example:

```
python -mhttphose.makework -b localhost:11300 example.com domain2.com
```

The format of job is a JSON encoded dictionary:

```json
{
	"domains": ["http://domain1", "https://domain2"],
	"extra": {"k1": "v1", "k2": "v2"}
}
```

The optional `extra` parameter will merge these keys & values into the output JSON dictionary so some kind of context can be passed from input to output.

#### Beanstalk in Docker

 * https://github.com/schickling/dockerfiles/tree/master/beanstalkd
 * https://github.com/schickling/dockerfiles/tree/master/beanstalkd-console

These can then be run in the background, however more effort is required to ensure messages persist across reboots and container restarts.

```
$ docker run -d -p 11300:11300 --name beanstalkd schickling/beanstalkd
$ docker run -d -p 2080:2080 --link beanstalkd:beanstalkd schickling/beanstalkd-console
```