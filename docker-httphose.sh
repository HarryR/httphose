#!/usr/bin/env bash
DOCKERTAG=harryr/httphose

docker run -ti --net=host --rm $DOCKERTAG $*

