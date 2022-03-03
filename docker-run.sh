#!/bin/sh

# Define functions.
function fixperms {
	chown -R $UID:$GID /data
}

cd /opt/mautrix-facebook

if [ ! -f /data/config.yaml ]; then
	cp example-config.yaml /data/config.yaml
	sed -i "s#sqlite:///mautrix-facebook.db#sqlite:////data/mautrix-facebook.db#" /data/config.yaml
	sed -i "s#hostname: localhost#hostname: 0.0.0.0#" /data/config.yaml
	echo "Didn't find a config file."
	echo "Copied default config file to /data/config.yaml"
	echo "Modify that config file to your liking."
	echo "Start the container again after that to generate the registration file."
	fixperms
	exit
fi

if [ ! -f /data/registration.yaml ]; then
	python3 -m mautrix_facebook -g -c /data/config.yaml -r /data/registration.yaml
	fixperms
	exit
fi

fixperms
exec su-exec $UID:$GID python3 -m mautrix_facebook -c /data/config.yaml
