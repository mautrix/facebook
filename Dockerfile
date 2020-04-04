FROM docker.io/alpine:3.11

RUN echo "@edge_main http://dl-cdn.alpinelinux.org/alpine/edge/main" >> /etc/apk/repositories
RUN echo "@edge_testing http://dl-cdn.alpinelinux.org/alpine/edge/testing" >> /etc/apk/repositories
RUN echo "@edge_community http://dl-cdn.alpinelinux.org/alpine/edge/community" >> /etc/apk/repositories

RUN apk add --no-cache \
      py3-pillow \
      py3-aiohttp \
      py3-magic \
      py3-sqlalchemy \
      py3-psycopg2 \
      py3-ruamel.yaml \
      # Indirect dependencies
      py3-commonmark@edge_testing \
      py3-alembic@edge_testing \
      #fbchat
        py3-beautifulsoup4 \
        #hbmqtt
          py3-yaml \
          py3-docopt \
      py3-idna \
      # matrix-nio
      olm-dev@edge_community \
      py3-future \
      py3-atomicwrites \
      py3-pycryptodome@edge_main \
      py3-peewee@edge_community \
      py3-pyrsistent@edge_community \
      py3-jsonschema \
      py3-aiofiles \
      py3-cachetools@edge_community \
      py3-unpaddedbase64 \
      py3-pyaes@edge_testing \
      py3-logbook@edge_testing \
      # Other dependencies
      ca-certificates \
      su-exec

COPY requirements.txt /opt/mautrix-facebook/requirements.txt
COPY optional-requirements.txt /opt/mautrix-facebook/optional-requirements.txt
WORKDIR /opt/mautrix-facebook
RUN apk add --virtual .build-deps python3-dev build-base \
 && sed -Ei 's/psycopg2-binary.+//' optional-requirements.txt \
 && pip3 install -r requirements.txt -r optional-requirements.txt \
 && apk del .build-deps

COPY . /opt/mautrix-facebook
RUN apk add --no-cache git && pip3 install .[e2be] && apk del git

ENV UID=1337 GID=1337
VOLUME /data

CMD ["/opt/mautrix-facebook/docker-run.sh"]
