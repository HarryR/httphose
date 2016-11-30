PYTHON ?= python
DOCKERTAG ?= harryr/httphose

all:

docker-run: docker-build
	docker run -ti --rm $(DOCKERTAG)

docker-build:
	docker build -t $(DOCKERTAG) .

test:
	$(PYTHON) -mhttphose --debug -p example.com

lint:
	$(PYTHON) -mpyflakes httphose 
	$(PYTHON) -mpylint -d missing-docstring -r n httphose

clean:
	find ./ -name '*.pyc' -exec rm -f '{}' ';'
