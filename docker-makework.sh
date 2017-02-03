#!/usr/bin/env bash
DOCKERTAG=harryr/httphose
docker run -ti --net=host --rm --entrypoint '/usr/bin/python' $DOCKERTAG -mhttphose.makework $*
