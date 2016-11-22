FROM jfloff/alpine-python:2.7-onbuild

COPY . /root/httphose/

WORKDIR /root/httphose

ENTRYPOINT ["/usr/bin/python", "-mhttphose"]
