FROM docker.io/alpine:3.14

ARG TARGETARCH=amd64

RUN apk add --no-cache \
      python3 py3-pip py3-setuptools py3-wheel \
      py3-pillow \
      py3-aiohttp \
      py3-magic \
      py3-ruamel.yaml \
      py3-commonmark \
      py3-paho-mqtt \
      py3-prometheus-client \
      # encryption
      py3-olm \
      py3-cffi \
      py3-pycryptodome \
      py3-unpaddedbase64 \
      py3-future \
      # proxy support
      py3-aiohttp-socks \
      py3-pysocks \
      # Other dependencies
      ca-certificates \
      su-exec \
      bash \
      curl \
      jq \
      yq

COPY requirements.txt /opt/mautrix-facebook/requirements.txt
COPY optional-requirements.txt /opt/mautrix-facebook/optional-requirements.txt
WORKDIR /opt/mautrix-facebook
RUN apk add --virtual .build-deps python3-dev libffi-dev build-base \
 && pip3 install -r requirements.txt -r optional-requirements.txt \
 && apk del .build-deps

COPY . /opt/mautrix-facebook
RUN apk add --no-cache git && pip3 install .[e2be] && apk del git \
  # This doesn't make the image smaller, but it's needed so that the `version` command works properly
  && cp mautrix_facebook/example-config.yaml . && rm -rf mautrix_facebook

ENV UID=1337 GID=1337
VOLUME /data

CMD ["/opt/mautrix-facebook/docker-run.sh"]
