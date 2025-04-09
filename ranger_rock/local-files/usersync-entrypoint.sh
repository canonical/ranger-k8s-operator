#!/bin/bash

cd /usr/lib/ranger/usersync && \
./setup.sh && \
./ranger-usersync-services.sh start  && \
ls -la && \
tail -f /var/log/ranger/usersync/usersync-ranger-*.log
