FROM docker.io/alpine:3.9

ENV UID=1337 \
    GID=1337

COPY . /opt/mautrix-facebook
WORKDIR /opt/mautrix-facebook
RUN apk add --no-cache \
      py3-pillow \
      py3-aiohttp \
      py3-magic \
      py3-sqlalchemy \
      py3-psycopg2 \
      # Not yet in stable repos:
      #py3-ruamel.yaml \
      # Indirect dependencies
      #alembic
        py3-mako \
        py3-dateutil \
        py3-markupsafe \
        py3-six \
      # fbchat
        py3-beautifulsoup4 \
      py3-idna \
      # Other dependencies
      ca-certificates \
      su-exec \
 && pip3 install .

VOLUME /data

CMD ["/opt/mautrix-facebook/docker-run.sh"]
